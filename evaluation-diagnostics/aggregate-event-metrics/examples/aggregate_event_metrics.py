#!/usr/bin/env python3
"""Aggregate per-event metric tables into statistics and qualification rates."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

from _diagnostics_common import (
    UNAVAILABLE,
    ContractError,
    check,
    file_reference,
    prepare_output_dir,
    provenance,
    read_json,
    read_table,
    require_columns,
    write_result,
    write_table,
)


SKILL_REF = "evaluation-diagnostics/aggregate-event-metrics"
FORMATS = ("csv", "parquet", "xlsx")
STATISTICS = ("mean", "median", "p10", "p90", "min", "max", "count")
DECLARED = [
    "aggregate_metrics.csv",
    "aggregate_metrics.parquet",
    "aggregate_metrics.xlsx",
    "result.json",
]


def _load_thresholds(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    candidate = Path(raw)
    if candidate.is_file():
        document = read_json(candidate)
    else:
        import json

        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ContractError(f"--pass-thresholds 必须是 JSON 字符串或 JSON 文件路径: {exc}") from exc
    if not isinstance(document, dict):
        raise ContractError("--pass-thresholds 必须是 object")
    cleaned: dict[str, float] = {}
    for key, value in document.items():
        if key.startswith("_"):
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ContractError(f"--pass-thresholds 的 {key} 必须是数值")
        cleaned[key] = float(value)
    return cleaned


def _statistic(values: np.ndarray, name: str) -> float:
    if name == "mean":
        return float(np.mean(values))
    if name == "median":
        return float(np.median(values))
    if name == "p10":
        return float(np.percentile(values, 10))
    if name == "p90":
        return float(np.percentile(values, 90))
    if name == "min":
        return float(np.min(values))
    if name == "max":
        return float(np.max(values))
    return float(len(values))


def run(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.metrics_table)
    if not path.is_file():
        raise ContractError(f"指标表不存在: {path}")
    fmt = args.table_format or path.suffix.lower().lstrip(".")
    frame = read_table(path, fmt, args.sheet)

    value_columns = [item.strip() for item in args.value_columns.split(",") if item.strip()]
    if not value_columns:
        raise ContractError("必须给出 --value-columns")
    pass_columns = [item.strip() for item in args.pass_columns.split(",") if item.strip()]
    require_columns(frame, value_columns + pass_columns, "指标表")
    if args.group_by:
        require_columns(frame, [args.group_by], "指标表")
    thresholds = _load_thresholds(args.pass_thresholds)

    checks: list[dict[str, str]] = []
    warnings: list[str] = []
    checks.append(check("columns_present", "PASS", f"聚合列 {len(value_columns)} 个、合格列 {len(pass_columns)} 个"))

    if frame.empty:
        warnings.append("指标表为空行；聚合结果将为空，请确认上游是否正确产出逐场次指标")
        checks.append(check("row_count", "WARN", "指标表行数为 0"))
    else:
        checks.append(check("row_count", "PASS", f"指标表 {len(frame)} 行"))

    statistics = [item.strip() for item in args.statistics.split(",") if item.strip()]
    unknown = [item for item in statistics if item not in STATISTICS]
    if unknown:
        raise ContractError(f"未知统计量: {', '.join(unknown)}；可用统计量: {', '.join(STATISTICS)}")

    groups: list[tuple[str, pd.DataFrame]] = (
        [("all", frame)]
        if not args.group_by
        else [(str(key), group) for key, group in frame.groupby(args.group_by, sort=True)]
    )

    rows: list[dict[str, Any]] = []
    unavailable_total = 0
    for label, group in groups:
        row: dict[str, Any] = {"group": label, "events": int(len(group))}
        for column in value_columns:
            numeric = pd.to_numeric(group[column], errors="coerce")
            valid = numeric.dropna()
            unavailable_total += int(numeric.isna().sum())
            for name in statistics:
                if name == "count":
                    row[f"{column}_count"] = int(len(valid))
                elif valid.empty:
                    row[f"{column}_{name}"] = UNAVAILABLE
                else:
                    row[f"{column}_{name}"] = _statistic(valid.to_numpy(dtype=float), name)
        for column in pass_columns:
            threshold = thresholds.get(column)
            if threshold is None:
                row[f"{column}_qualified_rate"] = UNAVAILABLE
                continue
            qualified = group[column].astype(str).map(lambda value: value == "是")
            row[f"{column}_qualified_rate"] = float(qualified.sum()) / len(group) if len(group) else UNAVAILABLE
        rows.append(row)

    checks.append(check(
        "unavailable_accounted", "PASS",
        f"无法解析为数值的单元共 {unavailable_total} 个，已排除并计数",
    ))
    if pass_columns and not thresholds:
        warnings.append(
            f"提供了 --pass-columns 但未给出 --pass-thresholds，合格率记为 {UNAVAILABLE}；"
            f"合格阈值必须由使用者显式给出"
        )
        checks.append(check("thresholds_provided", "WARN", "未提供合格阈值，不判定合格"))
    else:
        checks.append(check("thresholds_provided", "PASS", f"合格阈值 {sorted(thresholds)}"))
    checks.append(check("statistics_recorded", "PASS", f"统计量 {statistics}"))

    table = pd.DataFrame(rows)
    table_name = f"aggregate_metrics.{args.output_format}"
    table_path = args.output_dir / table_name
    write_table(table, table_path, args.output_format)

    warned = [item for item in checks if item["status"] == "WARN"]
    status = "warning" if warned else "success"
    return {
        "schema_version": "1.0",
        "skill": SKILL_REF,
        "status": status,
        "message": f"场次指标汇总完成：{len(rows)} 个分组，{len(frame)} 场",
        "parameters": {
            "group_by": args.group_by,
            "value_columns": value_columns,
            "pass_columns": pass_columns,
            "statistics": statistics,
            "pass_thresholds": thresholds,
        },
        "inputs": {"metrics_table": file_reference(path, args.output_dir)},
        "artifacts": {"aggregate_metrics": file_reference(table_path, args.output_dir)},
        "checks": checks,
        "warnings": warnings,
        "provenance": provenance(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate per-event metrics into statistics and rates.")
    parser.add_argument("--metrics-table", required=True)
    parser.add_argument("--table-format", choices=FORMATS)
    parser.add_argument("--sheet")
    parser.add_argument("--group-by")
    parser.add_argument("--value-columns", required=True, help="逗号分隔的待聚合数值列")
    parser.add_argument("--statistics", default="mean,median,p10,p90,min,max,count")
    parser.add_argument("--pass-columns", default="", help="逗号分隔的合格判定列")
    parser.add_argument("--pass-thresholds", help="合格阈值 JSON 字符串或文件路径")
    parser.add_argument("--output-format", choices=FORMATS, default="csv")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.output_dir = args.output_dir.resolve()
    prepared = False
    try:
        prepare_output_dir(args.output_dir, args.overwrite, DECLARED)
        prepared = True
        document = run(args)
        write_result(args.output_dir, document)
        print(f"{document['status']}: {document['message']}")
        return 0
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
