"""Shared, self-contained runtime for HydroTune calibration skills."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Callable

import numpy as np
import pandas as pd


class ContractError(RuntimeError):
    """Input, adapter, or file contract error (exit 1)."""


class ScientificQCError(RuntimeError):
    """Scientific result cannot be accepted (exit 2)."""


class BudgetExhausted(RuntimeError):
    """The configured objective-evaluation budget is exhausted."""


DECLARED_OUTPUTS = [
    "best_parameters.json", "calibration_trace.csv", "calibration_metrics.json",
    "validation_metrics.json", "calibration_series.csv", "validation_series.csv",
    "result.json",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_reference(path: Path, relative_to: Path | None = None) -> dict[str, str]:
    resolved = path.resolve()
    display = str(resolved)
    if relative_to is not None:
        try:
            display = resolved.relative_to(relative_to.resolve()).as_posix()
        except ValueError:
            pass
    return {"path": display, "sha256": sha256_file(resolved)}


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return value


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(json_safe(document), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"无法读取 JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON 根节点必须是 object: {path}")
    return value


def prepare_output_dir(output_dir: Path, overwrite: bool) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise ContractError(f"输出路径不是目录: {output_dir}")
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise ContractError(f"输出目录非空；如需替换本 Skill 产物请使用 --overwrite: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if overwrite:
        for name in DECLARED_OUTPUTS:
            target = output_dir / name
            if target.is_file():
                target.unlink()


def _require_keys(document: dict[str, Any], keys: set[str], label: str) -> None:
    missing = sorted(keys - set(document))
    if missing:
        raise ContractError(f"{label} 缺少字段: {', '.join(missing)}")


def _resolve_hashed(reference: dict[str, Any], base: Path, label: str) -> Path:
    if not isinstance(reference, dict):
        raise ContractError(f"{label} 必须是 object")
    _require_keys(reference, {"path", "sha256"}, label)
    raw = Path(str(reference["path"]))
    resolved = raw.resolve() if raw.is_absolute() else (base / raw).resolve()
    if not resolved.is_file():
        raise ContractError(f"{label} 文件不存在: {resolved}")
    actual = sha256_file(resolved)
    if actual.lower() != str(reference["sha256"]).lower():
        raise ContractError(f"{label} SHA-256 不匹配: {resolved}")
    return resolved


def validate_problem(problem: dict[str, Any], problem_path: Path) -> tuple[list[str], np.ndarray, Path]:
    _require_keys(
        problem,
        {"schema_version", "adapter", "inputs", "parameters", "objective", "splits", "data_shape", "warmup", "variables", "objective_consistency_tolerance"},
        "problem",
    )
    if problem["schema_version"] != "1.0":
        raise ContractError("problem.schema_version 必须为 1.0")
    if problem["data_shape"] not in {"continuous", "event_collection"}:
        raise ContractError("data_shape 必须为 continuous 或 event_collection")
    warmup = problem["warmup"]
    if not isinstance(warmup, dict) or warmup.get("confirmed") is not True or not str(warmup.get("semantics", "")).strip():
        raise ContractError("warmup 必须 confirmed=true 且提供 semantics")
    splits = problem["splits"]
    if not isinstance(splits, dict) or set(splits) != {"calibration", "validation"}:
        raise ContractError("splits 必须且只能声明 calibration 与 validation")
    objective = problem["objective"]
    if not isinstance(objective, dict) or objective.get("direction") not in {"minimize", "maximize"} or not str(objective.get("metric", "")).strip():
        raise ContractError("objective 必须声明 metric 与 minimize/maximize direction")
    tolerance = problem["objective_consistency_tolerance"]
    if not isinstance(tolerance, (int, float)) or tolerance < 0 or not np.isfinite(tolerance):
        raise ContractError("objective_consistency_tolerance 必须是有限非负数")
    parameters = problem["parameters"]
    if not isinstance(parameters, list) or not parameters:
        raise ContractError("parameters 必须是非空数组")
    names: list[str] = []
    bounds: list[tuple[float, float]] = []
    for index, item in enumerate(parameters):
        if not isinstance(item, dict):
            raise ContractError(f"parameters[{index}] 必须是 object")
        _require_keys(item, {"name", "lower", "upper"}, f"parameters[{index}]")
        name = str(item["name"])
        lower, upper = float(item["lower"]), float(item["upper"])
        if not name or name in names:
            raise ContractError(f"参数名为空或重复: {name!r}")
        if not np.isfinite([lower, upper]).all() or lower >= upper:
            raise ContractError(f"参数 {name} 的边界必须有限且 lower < upper")
        names.append(name)
        bounds.append((lower, upper))
    if not isinstance(problem["inputs"], list):
        raise ContractError("inputs 必须是数组")
    for index, reference in enumerate(problem["inputs"]):
        if not isinstance(reference, dict) or not str(reference.get("id", "")).strip():
            raise ContractError(f"inputs[{index}] 缺少 id")
        _resolve_hashed(reference, problem_path.parent, f"inputs[{index}]")
    adapter_path = _resolve_hashed(problem["adapter"], problem_path.parent, "adapter")
    return names, np.asarray(bounds, dtype=float), adapter_path


def validate_config(config: dict[str, Any], algorithm: str) -> None:
    _require_keys(config, {"schema_version", "algorithm", "seed", "max_evaluations", "max_failed_evaluations"}, "optimizer config")
    if config["schema_version"] != "1.0" or config["algorithm"] != algorithm:
        raise ContractError(f"optimizer config 必须是 schema 1.0 且 algorithm={algorithm}")
    for key in ("seed", "max_evaluations", "max_failed_evaluations"):
        if not isinstance(config[key], int) or config[key] < 0:
            raise ContractError(f"{key} 必须是非负整数")
    if config["max_evaluations"] < 1:
        raise ContractError("max_evaluations 必须至少为 1")


def load_evaluator(adapter_path: Path, problem: dict[str, Any], problem_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("hydrotune_calibration_adapter", adapter_path)
    if spec is None or spec.loader is None:
        raise ContractError(f"无法加载评估器: {adapter_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        factory = getattr(module, "create_evaluator")
        evaluator = factory(problem, problem_path.parent)
    except Exception as exc:
        raise ContractError(f"评估器加载失败: {exc}") from exc
    if not callable(getattr(evaluator, "evaluate", None)) or not callable(getattr(evaluator, "materialize", None)):
        raise ContractError("评估器必须实现 evaluate() 与 materialize()")
    return evaluator


class EvaluationContext:
    def __init__(self, evaluator: Any, names: list[str], bounds: np.ndarray, problem: dict[str, Any], config: dict[str, Any]):
        self.evaluator = evaluator
        self.names = names
        self.bounds = bounds
        self.direction = problem["objective"]["direction"]
        self.metric = problem["objective"]["metric"]
        self.max_evaluations = config["max_evaluations"]
        self.max_failed = config["max_failed_evaluations"]
        self.evaluations = 0
        self.failures = 0
        self.trace: list[dict[str, Any]] = []
        self.best_x: np.ndarray | None = None
        self.best_loss = float("inf")
        self.best_objective: float | None = None

    def score(self, vector: np.ndarray, stage: str = "search") -> float:
        if self.evaluations >= self.max_evaluations:
            raise BudgetExhausted("objective evaluation budget exhausted")
        x = np.asarray(vector, dtype=float)
        if x.shape != (len(self.names),) or not np.isfinite(x).all():
            raise ScientificQCError("optimizer produced an invalid parameter vector")
        if ((x < self.bounds[:, 0]) | (x > self.bounds[:, 1])).any():
            raise ScientificQCError("optimizer produced parameters outside configured bounds")
        self.evaluations += 1
        params = {name: float(value) for name, value in zip(self.names, x)}
        status, message = "success", ""
        native, loss = float("nan"), float("inf")
        try:
            result = self.evaluator.evaluate(params, "calibration")
            if not isinstance(result, dict) or "objective" not in result or "metrics" not in result:
                raise ValueError("evaluate() must return objective and metrics")
            native = float(result["objective"])
            if not np.isfinite(native):
                raise ValueError("objective is not finite")
            if not isinstance(result["metrics"], dict):
                raise ValueError("metrics must be an object")
            loss = native if self.direction == "minimize" else -native
        except Exception as exc:
            self.failures += 1
            status, message = "failed", str(exc)
            if self.failures > self.max_failed:
                raise ScientificQCError(f"failed evaluations exceeded limit {self.max_failed}: {exc}") from exc
        if np.isfinite(loss) and loss < self.best_loss:
            self.best_loss, self.best_objective, self.best_x = loss, native, x.copy()
        row: dict[str, Any] = {
            "evaluation": self.evaluations, "stage": stage, "objective": native,
            "loss": loss, "status": status, "message": message,
            "best_so_far": self.best_objective if self.best_objective is not None else float("nan"),
        }
        row.update({f"param:{name}": value for name, value in params.items()})
        self.trace.append(row)
        return loss


def _finite_metrics(metrics: Any, label: str) -> dict[str, float]:
    if not isinstance(metrics, dict) or not metrics:
        raise ScientificQCError(f"{label} metrics 必须是非空 object")
    output: dict[str, float] = {}
    for key, value in metrics.items():
        numeric = float(value)
        if not np.isfinite(numeric):
            raise ScientificQCError(f"{label} metric 非有限: {key}")
        output[str(key)] = numeric
    return output


def materialize(evaluator: Any, params: dict[str, float], split: str, problem: dict[str, Any], expected: float | None = None) -> tuple[dict[str, Any], pd.DataFrame]:
    try:
        result = evaluator.materialize(params, split)
    except Exception as exc:
        raise ScientificQCError(f"{split} materialize 失败: {exc}") from exc
    if not isinstance(result, dict) or not {"objective", "metrics", "series"}.issubset(result):
        raise ScientificQCError(f"{split} materialize 必须返回 objective、metrics 和 series")
    objective = float(result["objective"])
    if not np.isfinite(objective):
        raise ScientificQCError(f"{split} objective 非有限")
    if expected is not None and abs(objective - expected) > float(problem["objective_consistency_tolerance"]):
        raise ScientificQCError(f"{split} objective 与搜索结果不一致")
    metrics = _finite_metrics(result["metrics"], split)
    series = result["series"]
    if not isinstance(series, pd.DataFrame) or series.empty:
        raise ScientificQCError(f"{split} series 必须是非空 pandas.DataFrame")
    required = {"observed", "simulated", "scored"}
    if problem["data_shape"] == "continuous":
        required.add("time")
    else:
        required.add("event_id")
        if not ({"time", "step"} & set(series.columns)):
            raise ScientificQCError(f"{split} event_collection series 缺少 time 或 step")
    if not required.issubset(series.columns):
        raise ScientificQCError(f"{split} series 缺少字段: {sorted(required - set(series.columns))}")
    for column in ("observed", "simulated"):
        values = pd.to_numeric(series[column], errors="coerce")
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ScientificQCError(f"{split} series 的 {column} 含非有限值")
    scored_values = series["scored"]
    if not scored_values.map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise ScientificQCError(f"{split} series 的 scored 必须是布尔值")
    if not bool(scored_values.any()):
        raise ScientificQCError(f"{split} series 没有 scored=true 样本")
    return {"objective": objective, "metrics": metrics}, series.copy()


def validate_split_isolation(calibration, validation, data_shape):
    a = calibration.loc[calibration['scored']]
    b = validation.loc[validation['scored']]
    if data_shape == 'event_collection':
        overlap = set(a['event_id'].astype(str)) & set(b['event_id'].astype(str))
    else:
        def timestamps(frame):
            values = [pd.Timestamp(value) for value in frame['time']]
            if any(pd.isna(t) or t.tzinfo is None for t in values):
                raise ScientificQCError('scored timestamps require explicit timezone')
            values = [t.tz_convert('UTC') for t in values]
            if len(set(values)) != len(values):
                raise ScientificQCError('duplicate scored timestamps')
            return set(values)
        overlap = timestamps(a) & timestamps(b)
    if overlap:
        raise ScientificQCError(f'calibration/validation scored samples overlap: {sorted(map(str, overlap))[:10]}')


def _write_trace(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ScientificQCError("calibration trace is empty")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def provenance() -> dict[str, str]:
    def version(name: str) -> str:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return "unavailable"
    return {"python": sys.version.split()[0], "numpy": version("numpy"), "pandas": version("pandas"), "scipy": version("scipy")}


def execute_calibration(algorithm: str, skill_ref: str, problem_path: Path, config_path: Path, output_dir: Path, overwrite: bool, optimizer: Callable[[EvaluationContext, np.ndarray, dict[str, Any], np.random.Generator], str]) -> dict[str, Any]:
    prepare_output_dir(output_dir, overwrite)
    problem, config = read_json(problem_path), read_json(config_path)
    names, bounds, adapter_path = validate_problem(problem, problem_path)
    validate_config(config, algorithm)
    evaluator = load_evaluator(adapter_path, problem, problem_path)
    context = EvaluationContext(evaluator, names, bounds, problem, config)
    rng = np.random.default_rng(config["seed"])
    termination = "optimizer_completed"
    try:
        termination = optimizer(context, bounds, config, rng)
    except BudgetExhausted:
        termination = "evaluation_budget_exhausted"
    if context.best_x is None or not np.isfinite(context.best_loss):
        raise ScientificQCError("率定未产生任何可行解")
    best_params = {name: float(value) for name, value in zip(names, context.best_x)}
    calibration_metrics, calibration_series = materialize(evaluator, best_params, "calibration", problem, context.best_objective)
    validation_metrics, validation_series = materialize(evaluator, best_params, "validation", problem)
    validate_split_isolation(calibration_series, validation_series, problem["data_shape"])
    best_document = {
        "schema_version": "1.0", "algorithm": algorithm,
        "parameters": {
            name: {
                "value": float(value), "lower": float(lower), "upper": float(upper),
                "relative_position": float((value - lower) / (upper - lower)),
                "at_lower_bound": bool(np.isclose(value, lower)), "at_upper_bound": bool(np.isclose(value, upper)),
            }
            for name, value, (lower, upper) in zip(names, context.best_x, bounds)
        },
    }
    paths = {
        "best_parameters": output_dir / "best_parameters.json",
        "calibration_trace": output_dir / "calibration_trace.csv",
        "calibration_metrics": output_dir / "calibration_metrics.json",
        "validation_metrics": output_dir / "validation_metrics.json",
        "calibration_series": output_dir / "calibration_series.csv",
        "validation_series": output_dir / "validation_series.csv",
    }
    write_json(paths["best_parameters"], best_document)
    _write_trace(paths["calibration_trace"], context.trace)
    write_json(paths["calibration_metrics"], {"schema_version": "1.0", "split": "calibration", **calibration_metrics})
    write_json(paths["validation_metrics"], {"schema_version": "1.0", "split": "validation", **validation_metrics})
    calibration_series.to_csv(paths["calibration_series"], index=False, encoding="utf-8-sig")
    validation_series.to_csv(paths["validation_series"], index=False, encoding="utf-8-sig")
    warnings: list[str] = []
    if termination == "evaluation_budget_exhausted":
        warnings.append("搜索在达到 max_evaluations 后返回当前最佳可行解")
    if context.failures:
        warnings.append(f"搜索期间有 {context.failures} 次失败评估")
    if any(item["at_lower_bound"] or item["at_upper_bound"] for item in best_document["parameters"].values()):
        warnings.append("一个或多个最优参数位于配置边界")
    status = "warning" if warnings else "success"
    result = {
        "schema_version": "1.0", "skill": skill_ref, "status": status,
        "message": f"{algorithm} calibration completed", "algorithm": algorithm,
        "data_shape": problem["data_shape"], "objective": problem["objective"],
        "parameters": {"seed": config["seed"], "max_evaluations": config["max_evaluations"], "optimizer_config": config},
        "inputs": {"problem": file_reference(problem_path), "optimizer_config": file_reference(config_path), "adapter": file_reference(adapter_path)},
        "artifacts": {key: file_reference(path, output_dir) for key, path in paths.items()},
        "checks": [
            {"check": "parameter_bounds", "status": "PASS", "details": f"parameters={len(names)}"},
            {"check": "split_isolation", "status": "PASS", "details": "validation evaluated only after parameter lock"},
            {"check": "objective_consistency", "status": "PASS", "details": f"tolerance={problem['objective_consistency_tolerance']}"},
        ],
        "warnings": warnings, "termination_reason": termination,
        "evaluation_count": context.evaluations, "failed_evaluation_count": context.failures,
        "provenance": provenance(),
    }
    write_json(output_dir / "result.json", result)
    return result


def error_result(skill_ref: str, message: str, output_dir: Path) -> None:
    if output_dir.is_dir():
        write_json(output_dir / "result.json", {
            "schema_version": "1.0", "skill": skill_ref, "status": "error", "message": message,
            "algorithm": skill_ref.rsplit("-", 1)[-1], "parameters": {}, "inputs": {}, "artifacts": {},
            "checks": [], "warnings": [], "provenance": provenance(),
        })
