#!/usr/bin/env python3
"""Compute continuous-series performance metrics and volume bias."""

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
    infer_step_hours,
    mae,
    nse,
    parse_datetime,
    prepare_output_dir,
    provenance,
    r_squared,
    read_table,
    require_columns,
    scored_frame,
    rmse,
    write_result,
    write_table,
)


SKILL_REF = "evaluation-diagnostics/compute-continuous-series-metrics"
FORMATS = ("csv", "parquet", "xlsx")
DEFAULT_METRICS = ("nse", "rmse", "mae", "r2", "volume_bias", "relative_volume_bias")
METRIC_UNITS = {
    "nse": "dimensionless",
    "r2": "dimensionless",
    "rmse": "m3/s",
    "mae": "m3/s",
    "volume_bias": "m3",
    "relative_volume_bias": "dimensionless",
}
DECLARED = [
    "series_metrics.csv",
    "series_metrics.parquet",
    "series_metrics.xlsx",
    "result.json",
]


class QualityControlFailure(RuntimeError):
    """Raised when a scientific QC gate fails."""

    def __init__(self, document: dict[str, Any]) -> None:
        super().__init__("科学 QC 失败")
        self.document = document


def run(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.series)
    if not path.is_file():
        raise ContractError(f"序列文件不存在: {path}")
    fmt = args.series_format or path.suffix.lower().lstrip(".")
    frame = scored_frame(read_table(path, fmt, args.sheet))
    require_columns(frame, [args.time_column, args.observed_column, args.simulated_column], "序列")

    checks: list[dict[str, str]] = []
    warnings: list[str] = []

    observed = pd.to_numeric(frame[args.observed_column], errors="coerce").to_numpy(dtype=float)
    simulated = pd.to_numeric(frame[args.simulated_column], errors="coerce").to_numpy(dtype=float)
    if len(observed) != len(simulated):
        checks.append(check("series_length_match", "FAIL", "观测与模拟长度不一致"))
    elif len(observed) == 0:
        checks.append(check("series_length_match", "FAIL", "序列为空"))
    else:
        checks.append(check("series_length_match", "PASS", f"观测与模拟均为 {len(observed)} 行"))

    time = parse_datetime(frame[args.time_column], "序列")
    step, regular = infer_step_hours(time)
    if not regular or not np.isfinite(step) or abs(step - args.timestep_hours) > 1e-9:
        checks.append(check(
            "timestep_consistency", "FAIL",
            f"时间轴步长 {step!r} 小时与 --timestep-hours {args.timestep_hours} 不一致",
        ))
    else:
        checks.append(check("timestep_consistency", "PASS", f"时间轴等间隔 {step} 小时"))

    missing = int(np.isnan(observed).sum() + np.isnan(simulated).sum())
    if missing:
        checks.append(check(
            "no_missing_values", "FAIL",
            f"观测或模拟列存在 {missing} 个缺测；缺测必须由上游显式修复，不得填 0 后计算指标",
        ))
    else:
        checks.append(check("no_missing_values", "PASS", "观测与模拟均无缺测"))

    if not (np.all(np.isfinite(observed)) and np.all(np.isfinite(simulated))):
        checks.append(check("finite_values", "FAIL", "观测或模拟存在非有限值"))
    else:
        checks.append(check("finite_values", "PASS", "观测与模拟均为有限值"))

    if any(item["status"] == "FAIL" for item in checks):
        raise QualityControlFailure({"checks": checks, "warnings": warnings})

    dt_seconds = args.timestep_hours * 3600.0
    values: dict[str, float] = {
        "nse": nse(observed, simulated),
        "r2": r_squared(observed, simulated),
        "rmse": rmse(observed, simulated),
        "mae": mae(observed, simulated),
        "volume_bias": float(np.sum(simulated - observed) * dt_seconds),
    }
    observed_volume = float(np.sum(observed) * dt_seconds)
    values["relative_volume_bias"] = (
        values["volume_bias"] / observed_volume if observed_volume != 0 else float("nan")
    )

    if float(np.var(observed)) <= 0:
        warnings.append(
            f"观测序列方差为 0，nse 记为 {UNAVAILABLE}；r2 按 scikit-learn 约定给出边界值"
        )
        checks.append(check("variance_available", "WARN", "观测序列方差为 0，nse 不适用"))
    else:
        checks.append(check("variance_available", "PASS", "观测序列方差为正"))

    selected = [item.strip() for item in args.metrics.split(",") if item.strip()]
    unknown = [item for item in selected if item not in METRIC_UNITS]
    if unknown:
        raise ContractError(f"未知指标: {', '.join(unknown)}；可用指标: {', '.join(DEFAULT_METRICS)}")

    rows: list[dict[str, Any]] = []
    for name in selected:
        value = values[name]
        finite = bool(np.isfinite(value))
        rows.append({
            "metric": name,
            "value": float(value) if finite else UNAVAILABLE,
            "unit": METRIC_UNITS[name],
            "status": "ok" if finite else UNAVAILABLE,
        })
    checks.append(check("metrics_recorded", "PASS", f"已计算 {len(rows)} 项指标"))

    table = pd.DataFrame(rows)
    table_name = f"series_metrics.{args.output_format}"
    table_path = args.output_dir / table_name
    write_table(table, table_path, args.output_format)

    failed = [item for item in checks if item["status"] == "FAIL"]
    warned = [item for item in checks if item["status"] == "WARN"]
    status = "error" if failed else ("warning" if warned else "success")
    return {
        "schema_version": "1.0",
        "skill": SKILL_REF,
        "status": status,
        "message": (
            f"连续序列指标计算失败：{len(failed)} 项 QC FAIL"
            if failed
            else f"连续序列指标计算完成：{len(observed)} 行，{len(rows)} 项指标"
        ),
        "parameters": {
            "timestep_hours": args.timestep_hours,
            "metrics": selected,
            "observed_column": args.observed_column,
            "simulated_column": args.simulated_column,
            "time_column": args.time_column,
            "sheet": args.sheet,
            "scoring_policy": "exclude_unscored_and_warmup",
            "volume_unit": "m3",
        },
        "inputs": {"series": file_reference(path, args.output_dir)},
        "artifacts": {"series_metrics": file_reference(table_path, args.output_dir)},
        "checks": checks,
        "warnings": warnings,
        "provenance": provenance(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute continuous-series performance metrics.")
    parser.add_argument("--series", required=True)
    parser.add_argument("--series-format", choices=FORMATS)
    parser.add_argument("--sheet")
    parser.add_argument("--time-column", default="time")
    parser.add_argument("--observed-column", default="Q_obs")
    parser.add_argument("--simulated-column", default="Q_total")
    parser.add_argument("--timestep-hours", type=float, required=True)
    parser.add_argument("--metrics", default=",".join(DEFAULT_METRICS))
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
        return 2 if document["status"] == "error" else 0
    except QualityControlFailure as exc:
        if prepared:
            partial = dict(exc.document)
            partial.update({
                "schema_version": "1.0", "skill": SKILL_REF, "status": "error",
                "message": "科学 QC 失败，未生成指标表",
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
