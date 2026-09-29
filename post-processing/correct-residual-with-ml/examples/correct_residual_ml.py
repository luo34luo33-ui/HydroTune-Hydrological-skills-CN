#!/usr/bin/env python3
"""Train or apply a machine-learning residual correction on top of a baseline simulation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

from _ml_backends import BACKENDS, BackendError, backend_version, build_estimator
from _postprocess_common import (
    ContractError,
    check,
    file_reference,
    prepare_output_dir,
    provenance,
    read_json,
    read_table,
    require_columns,
    write_json,
    write_result,
    write_table,
)


SKILL_REF = "post-processing/correct-residual-with-ml"
FORMATS = ("csv", "parquet", "xlsx")
DECLARED = [
    "corrected_series.csv",
    "corrected_series.parquet",
    "corrected_series.xlsx",
    "model_card.json",
    "model.joblib",
    "result.json",
]
RESIDUAL_DEFINITION = "observed - base"


class QualityControlFailure(RuntimeError):
    """Raised when a scientific QC gate fails."""

    def __init__(self, document: dict[str, Any]) -> None:
        super().__init__("科学 QC 失败")
        self.document = document


def parse_lag_specs(raw: list[str] | None) -> list[tuple[str, list[int]]]:
    specs: list[tuple[str, list[int]]] = []
    for item in raw or []:
        if ":" not in item:
            raise ContractError(f"--lag-spec 必须使用 COLUMN:STEPS 形式: {item}")
        column, steps_text = item.split(":", 1)
        column = column.strip()
        if not column:
            raise ContractError(f"--lag-spec 列名不能为空: {item}")
        steps: list[int] = []
        for token in steps_text.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                step = int(token)
            except ValueError as exc:
                raise ContractError(f"--lag-spec 步数必须是整数: {item}") from exc
            if step < 0:
                raise ContractError(
                    f"--lag-spec 步数必须为非负整数，负值会使用未来信息: {item}"
                )
            steps.append(step)
        if not steps:
            raise ContractError(f"--lag-spec 至少需要一个步数: {item}")
        specs.append((column, sorted(set(steps))))
    return specs


def parse_hyperparameters(raw: str) -> dict[str, Any]:
    candidate = Path(raw)
    if candidate.is_file():
        document = read_json(candidate)
    else:
        import json

        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ContractError(f"--hyperparameters 必须是 JSON 字符串或 JSON 文件路径: {exc}") from exc
    if not isinstance(document, dict):
        raise ContractError("--hyperparameters 必须是 object")
    # 下划线开头的键只作为文件内说明，不传给估计器。
    return {key: value for key, value in document.items() if not key.startswith("_")}


def _lag_column_name(column: str, step: int) -> str:
    return f"{column}_lag_{step}"


def build_features(
    frame: pd.DataFrame,
    feature_columns: list[str],
    lag_specs: list[tuple[str, list[int]]],
    lag_policy: str,
    lag_fill_value: float | None,
) -> tuple[pd.DataFrame, list[str], int]:
    working = frame.copy()
    lag_columns: list[str] = []
    for column, steps in lag_specs:
        if column not in working.columns:
            raise ContractError(f"--lag-spec 引用的列不存在: {column}")
        for step in steps:
            name = _lag_column_name(column, step)
            working[name] = pd.to_numeric(working[column], errors="coerce").shift(step)
            lag_columns.append(name)
    used = [*feature_columns, *lag_columns]
    dropped = 0
    if lag_columns:
        incomplete = working[lag_columns].isna().any(axis=1)
        count = int(incomplete.sum())
        if count:
            if lag_policy == "fail":
                raise ContractError(
                    f"滞后特征在 {count} 行上不完整；请改用 --lag-policy drop 或 fill，或缩短滞后步数"
                )
            if lag_policy == "fill":
                if lag_fill_value is None:
                    raise ContractError("--lag-policy fill 必须同时给出 --lag-fill-value")
                for name in lag_columns:
                    working[name] = working[name].fillna(lag_fill_value)
            else:
                working = working.loc[~incomplete].reset_index(drop=True)
                dropped = count
    return working, used, dropped


def _metrics(observed: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    if observed.size == 0:
        return {"r2": float("nan"), "rmse": float("nan"), "mae": float("nan")}
    error = predicted - observed
    variation = float(np.sum((observed - observed.mean()) ** 2))
    r2 = float("nan") if variation <= 0 else float(1.0 - np.sum(error ** 2) / variation)
    return {
        "r2": r2,
        "rmse": float(np.sqrt(np.mean(error ** 2))),
        "mae": float(np.mean(np.abs(error))),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    lag_specs = parse_lag_specs(args.lag_spec)
    model_card: dict[str, Any] = {}
    if args.mode == "train":
        if not args.backend:
            raise ContractError("train 模式必须给出 --backend")
        if not args.hyperparameters:
            raise ContractError("train 模式必须给出 --hyperparameters，不接受默认值，可显式传 {}")
        hyperparameters = parse_hyperparameters(args.hyperparameters)
        backend = args.backend
    else:
        card_path = Path(args.model_in).with_name("model_card.json")
        if not card_path.is_file():
            raise ContractError(f"模型卡不存在: {card_path}")
        model_card = read_json(card_path)
        backend = args.backend or str(model_card["backend"])
        hyperparameters = (
            parse_hyperparameters(args.hyperparameters)
            if args.hyperparameters
            else dict(model_card.get("hyperparameters", {}))
        )

    table_path = Path(args.table)
    if not table_path.is_file():
        raise ContractError(f"输入表不存在: {table_path}")
    fmt = args.table_format or table_path.suffix.lower().lstrip(".")
    frame = read_table(table_path, fmt, args.sheet)

    checks: list[dict[str, str]] = []
    warnings: list[str] = []

    if args.mode == "train":
        if args.random_state is None:
            raise ContractError("train 模式必须给出 --random-state 以保证结果可复现")
        require_columns(frame, [args.time_column, args.base_column, args.observed_column], "输入表")
        feature_columns = [item.strip() for item in args.feature_columns.split(",") if item.strip()]
        if not feature_columns:
            raise ContractError("train 模式必须给出 --feature-columns")
        require_columns(frame, feature_columns, "输入表")
        model_card = {
            "skill": SKILL_REF,
            "backend": backend,
            "hyperparameters": hyperparameters,
            "random_state": args.random_state,
            "split": args.split,
            "test_size": args.test_size,
            "base_column": args.base_column,
            "observed_column": args.observed_column,
            "output_column": args.output_column,
            "feature_columns": feature_columns,
            "lag_spec": [{"column": column, "steps": steps} for column, steps in lag_specs],
            "residual_definition": RESIDUAL_DEFINITION,
        }
    else:
        feature_columns = list(model_card["feature_columns"])
        card_lag = [
            (str(item["column"]), [int(step) for step in item["steps"]])
            for item in model_card.get("lag_spec", [])
        ]
        if lag_specs and sorted(lag_specs) != sorted(card_lag):
            raise ContractError("--lag-spec 与模型卡记录不一致；predict 模式应沿用训练时的滞后配置")
        lag_specs = card_lag
        require_columns(frame, [args.time_column, args.base_column], "输入表")
        require_columns(frame, feature_columns, "输入表")

    checks.append(check("residual_definition", "PASS", f"残差定义为 {RESIDUAL_DEFINITION}"))
    checks.append(check(
        "lag_nonnegative", "PASS",
        f"滞后步数均为非负整数，共 {sum(len(steps) for _, steps in lag_specs)} 个滞后特征，不使用未来信息",
    ))
    checks.append(check(
        "feature_columns_present", "PASS",
        f"特征列 {len(feature_columns)} 个全部存在",
    ))

    working = frame.copy()
    has_observed = args.observed_column in working.columns
    if has_observed:
        # 残差先算出来，这样 --lag-spec 可以直接引用 residual 列构造残差滞后特征。
        working["residual"] = (
            pd.to_numeric(working[args.observed_column], errors="coerce")
            - pd.to_numeric(working[args.base_column], errors="coerce")
        )

    working, used_columns, dropped = build_features(
        working, feature_columns, lag_specs, args.lag_policy, args.lag_fill_value
    )
    if dropped:
        warnings.append(
            f"滞后特征不完整而丢弃 {dropped} 行；这些行位于序列开头，无法通过滞后构造获得完整特征"
        )
        checks.append(check("lag_rows_dropped", "WARN", f"丢弃 {dropped} 行不完整滞后特征"))
    else:
        checks.append(check("lag_rows_dropped", "PASS", "滞后特征完整"))

    base = pd.to_numeric(working[args.base_column], errors="coerce").to_numpy(dtype=float)
    matrix = working[used_columns].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    observed = (
        pd.to_numeric(working[args.observed_column], errors="coerce").to_numpy(dtype=float)
        if has_observed
        else None
    )
    residual = (
        pd.to_numeric(working["residual"], errors="coerce").to_numpy(dtype=float)
        if has_observed
        else None
    )

    source_missing = int(np.isnan(matrix).sum() + np.isnan(base).sum())
    if observed is not None:
        source_missing += int(np.isnan(observed).sum())
    if residual is not None:
        source_missing += int(np.isnan(residual).sum())
    if source_missing:
        if args.missing_policy == "fail":
            raise ContractError(
                f"输入列存在 {source_missing} 个缺测；缺测必须由上游显式修复，或用 --missing-policy drop-rows 并接受行数变化"
            )
        valid = ~np.isnan(matrix).any(axis=1) & ~np.isnan(base)
        if observed is not None:
            valid &= ~np.isnan(observed)
        if residual is not None:
            valid &= ~np.isnan(residual)
        removed = int((~valid).sum())
        working = working.loc[valid].reset_index(drop=True)
        base = base[valid]
        matrix = matrix[valid]
        if observed is not None:
            observed = observed[valid]
        if residual is not None:
            residual = residual[valid]
        warnings.append(f"按 --missing-policy drop-rows 丢弃 {removed} 行含缺测记录")
        checks.append(check("missing_rows", "WARN", f"丢弃 {removed} 行含缺测记录"))
    else:
        checks.append(check("missing_rows", "PASS", "输入列无缺测"))

    if args.mode == "train":
        if residual is None:
            raise ContractError("train 模式必须提供 --observed-column 才能构造残差")
        if args.split == "random":
            from sklearn.model_selection import train_test_split

            x_train, x_test, y_train, y_test, index_train, index_test = train_test_split(
                matrix, residual, np.arange(len(residual)),
                test_size=args.test_size, random_state=args.random_state,
            )
        else:
            cut = int(round(len(residual) * (1.0 - args.test_size)))
            index_train = np.arange(0, cut)
            index_test = np.arange(cut, len(residual))
            x_train, y_train = matrix[index_train], residual[index_train]
            x_test, y_test = matrix[index_test], residual[index_test]

        if len(index_train) < args.min_train_samples:
            checks.append(check(
                "train_sample_count", "WARN",
                f"训练样本 {len(index_train)} 少于 --min-train-samples {args.min_train_samples}，结果不具备统计意义",
            ))
            warnings.append(f"训练样本仅 {len(index_train)} 行，少于 {args.min_train_samples}")
        else:
            checks.append(check("train_sample_count", "PASS", f"训练样本 {len(index_train)} 行"))

        estimator = build_estimator(backend, hyperparameters, args.random_state)
        estimator.fit(x_train, y_train)
        train_metrics = _metrics(y_train, np.asarray(estimator.predict(x_train), dtype=float))
        test_metrics = _metrics(y_test, np.asarray(estimator.predict(x_test), dtype=float))
        predicted_residual = np.asarray(estimator.predict(matrix), dtype=float)
        model_card["train_rows"] = int(len(index_train))
        model_card["test_rows"] = int(len(index_test))
        model_card["train_r2"] = train_metrics["r2"]
        model_card["train_rmse"] = train_metrics["rmse"]
        model_card["test_r2"] = test_metrics["r2"]
        model_card["test_rmse"] = test_metrics["rmse"]
        model_card["backend_version"] = backend_version(backend)
        checks.append(check(
            "random_state_recorded", "PASS", f"random_state={args.random_state} 已写入模型卡"
        ))
        import joblib

        model_out = Path(args.model_out) if args.model_out else args.output_dir / "model.joblib"
        if model_out.parent != args.output_dir:
            raise ContractError("--model-out 必须位于 --output-dir 内")
        joblib.dump(estimator, model_out)
    else:
        import joblib

        model_in = Path(args.model_in)
        if not model_in.is_file():
            raise ContractError(f"模型文件不存在: {model_in}")
        estimator = joblib.load(model_in)
        predicted_residual = np.asarray(estimator.predict(matrix), dtype=float)
        model_out = None

    if not np.all(np.isfinite(predicted_residual)):
        checks.append(check("prediction_finite", "FAIL", "预测残差出现非有限值"))
        raise QualityControlFailure({"checks": checks, "warnings": warnings})
    checks.append(check("prediction_finite", "PASS", "预测残差均为有限值"))

    corrected = base + predicted_residual
    negatives = int(np.sum(corrected < 0.0))
    if negatives:
        checks.append(check("corrected_negative", "WARN", f"校正后序列出现 {negatives} 个负值"))
        warnings.append(
            f"校正后序列出现 {negatives} 个负值；本 Skill 不裁剪负值，以免破坏水量平衡，"
            f"请结合上游基流或另设下限策略处理"
        )
    else:
        checks.append(check("corrected_negative", "PASS", "校正后序列无负值"))

    output = pd.DataFrame({
        args.time_column: working[args.time_column].to_numpy(),
        args.base_column: base,
        "predicted_residual": predicted_residual,
        args.output_column: corrected,
    })
    if observed is not None and residual is not None:
        output.insert(2, args.observed_column, observed)
        output.insert(3, "residual", residual)
    for column in used_columns:
        output[column] = working[column].to_numpy()

    table_name = f"corrected_series.{args.output_format}"
    table_path_out = args.output_dir / table_name
    write_table(output, table_path_out, args.output_format)

    artifacts: dict[str, Any] = {"corrected_table": file_reference(table_path_out, args.output_dir)}
    if args.mode == "train":
        write_json(args.output_dir / "model_card.json", model_card)
        artifacts["model_card"] = file_reference(args.output_dir / "model_card.json", args.output_dir)
        if model_out is not None:
            artifacts["model"] = file_reference(model_out, args.output_dir)
    else:
        artifacts["model_used"] = file_reference(Path(args.model_in), args.output_dir)

    failed = [item for item in checks if item["status"] == "FAIL"]
    warned = [item for item in checks if item["status"] == "WARN"]
    status = "error" if failed else ("warning" if warned else "success")
    message = (
        "残差校正失败：预测残差非有限"
        if failed
        else f"残差校正完成：{args.mode} 模式，{len(output)} 行，后端 {backend}"
    )
    return {
        "schema_version": "1.0",
        "skill": SKILL_REF,
        "status": status,
        "message": message,
        "parameters": {
            "mode": args.mode,
            "backend": backend,
            "hyperparameters": hyperparameters,
            "random_state": args.random_state,
            "split": args.split,
            "test_size": args.test_size,
            "min_train_samples": args.min_train_samples,
            "missing_policy": args.missing_policy,
            "lag_policy": args.lag_policy,
            "lag_fill_value": args.lag_fill_value,
            "lag_spec": [{"column": column, "steps": steps} for column, steps in lag_specs],
            "feature_columns": feature_columns,
            "base_column": args.base_column,
            "observed_column": args.observed_column if has_observed else None,
            "output_column": args.output_column,
        },
        "inputs": {"table": file_reference(table_path, args.output_dir)},
        "artifacts": artifacts,
        "checks": checks,
        "warnings": warnings,
        "provenance": provenance(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train or apply a machine-learning residual correction for a baseline simulation.",
    )
    parser.add_argument("--mode", choices=("train", "predict"), required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--table-format", choices=FORMATS)
    parser.add_argument("--sheet")
    parser.add_argument("--time-column", default="time")
    parser.add_argument("--base-column", default="Q_total")
    parser.add_argument("--observed-column", default="Q_obs")
    parser.add_argument("--feature-columns", help="逗号分隔的原始特征列")
    parser.add_argument("--lag-spec", action="append", help="COLUMN:STEPS，可重复；STEPS 为非负整数")
    parser.add_argument("--lag-policy", choices=("drop", "fill", "fail"), default="drop")
    parser.add_argument("--lag-fill-value", type=float)
    parser.add_argument("--backend", choices=BACKENDS, help="train 模式必填；predict 模式缺省时取模型卡记录")
    parser.add_argument("--hyperparameters", help="JSON 字符串或 JSON 文件路径；train 模式必填")
    parser.add_argument("--random-state", type=int)
    parser.add_argument("--split", choices=("random", "chronological"), default="random")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--min-train-samples", type=int, default=10)
    parser.add_argument("--missing-policy", choices=("fail", "drop-rows"), default="fail")
    parser.add_argument("--model-out")
    parser.add_argument("--model-in")
    parser.add_argument("--output-column", default="corrected")
    parser.add_argument("--output-format", choices=FORMATS, default="csv")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.output_dir = args.output_dir.resolve()
    prepared = False
    try:
        if args.mode == "predict" and not args.model_in:
            raise ContractError("predict 模式必须给出 --model-in")
        if not 0.0 < args.test_size < 1.0:
            raise ContractError("--test-size 必须落在 (0, 1)")
        prepare_output_dir(args.output_dir, args.overwrite, DECLARED)
        prepared = True
        document = run(args)
        write_result(args.output_dir, document)
        print(f"{document['status']}: {document['message']}")
        return 2 if document["status"] == "error" else 0
    except BackendError as exc:
        if prepared:
            write_result(args.output_dir, {
                "schema_version": "1.0", "skill": SKILL_REF, "status": "error", "message": str(exc),
                "parameters": {}, "inputs": {}, "artifacts": {}, "checks": [], "warnings": [],
                "provenance": provenance(),
            })
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except QualityControlFailure as exc:
        if prepared:
            partial = dict(exc.document)
            partial.update({
                "schema_version": "1.0", "skill": SKILL_REF, "status": "error",
                "message": "科学 QC 失败，未生成校正序列",
                "parameters": {}, "inputs": {}, "artifacts": {}, "provenance": provenance(),
            })
            write_result(args.output_dir, partial)
        print("error: 科学 QC 失败", file=sys.stderr)
        return 2
    except Exception as exc:
        if prepared:
            write_result(args.output_dir, {
                "schema_version": "1.0", "skill": SKILL_REF, "status": "error", "message": str(exc),
                "parameters": {}, "inputs": {}, "artifacts": {}, "checks": [], "warnings": [],
                "provenance": provenance(),
            })
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
