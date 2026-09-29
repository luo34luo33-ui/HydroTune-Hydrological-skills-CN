#!/usr/bin/env python3
"""Compute per-event flood metrics: volume, peak, peak timing, NSE and R2."""

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
    read_json,
    read_table,
    require_columns,
    rmse,
    write_result,
    write_table,
)


SKILL_REF = "evaluation-diagnostics/compute-event-flood-metrics"
FORMATS = ("csv", "parquet", "xlsx")
TABLE_SUFFIXES = {".csv", ".parquet", ".xlsx"}
VOLUME_SCALE = {"m3": 1.0, "10k-m3": 1e-4}
VOLUME_UNITS = {"m3": "m3", "10k-m3": "10k m3"}
DECLARED = [
    "event_metrics.csv",
    "event_metrics.parquet",
    "event_metrics.xlsx",
    "result.json",
]


class QualityControlFailure(RuntimeError):
    """Raised when a scientific QC gate fails."""

    def __init__(self, document: dict[str, Any]) -> None:
        super().__init__("科学 QC 失败")
        self.document = document


def _collect_events(args: argparse.Namespace) -> list[tuple[str, pd.DataFrame]]:
    if args.events is not None:
        path = Path(args.events)
        if not path.is_file():
            raise ContractError(f"场次文件不存在: {path}")
        fmt = args.events_format or path.suffix.lower().lstrip(".")
        if fmt == "xlsx":
            book = pd.ExcelFile(path)
            return [(str(sheet), read_table(path, "xlsx", sheet)) for sheet in book.sheet_names]
        return [(path.stem, read_table(path, fmt, args.sheet))]
    directory = Path(args.events_dir)
    if not directory.is_dir():
        raise ContractError(f"场次目录不存在: {directory}")
    paths = sorted(item for item in directory.iterdir() if item.is_file() and item.suffix.lower() in TABLE_SUFFIXES)
    if not paths:
        raise ContractError(f"场次目录内没有 csv、parquet 或 xlsx 文件: {directory}")
    return [(path.stem, read_table(path)) for path in paths]


def _load_thresholds(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    candidate = Path(raw)
    document = read_json(candidate) if candidate.is_file() else _parse_json_text(raw)
    if not isinstance(document, dict):
        raise ContractError("--thresholds 必须是 object")
    cleaned: dict[str, float] = {}
    for key, value in document.items():
        if key.startswith("_"):
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ContractError(f"--thresholds 的 {key} 必须是数值")
        cleaned[key] = float(value)
    return cleaned


def _parse_json_text(raw: str) -> dict[str, Any]:
    import json

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError(f"--thresholds 必须是 JSON 字符串或 JSON 文件路径: {exc}") from exc


def _qualified(error: float, threshold: float | None) -> str:
    if threshold is None:
        return UNAVAILABLE
    return "是" if abs(error) <= threshold else "否"


def _evaluate_event(
    frame: pd.DataFrame,
    label: str,
    args: argparse.Namespace,
    thresholds: dict[str, float],
) -> tuple[dict[str, Any], list[dict[str, str]], list[str]]:
    require_columns(frame, [args.time_column, args.observed_column, args.simulated_column], label)
    checks: list[dict[str, str]] = []
    warnings: list[str] = []

    observed = pd.to_numeric(frame[args.observed_column], errors="coerce").to_numpy(dtype=float)
    simulated = pd.to_numeric(frame[args.simulated_column], errors="coerce").to_numpy(dtype=float)
    if len(observed) != len(simulated):
        checks.append(check(f"{label}:series_length_match", "FAIL", "观测与模拟长度不一致"))
    elif len(observed) == 0:
        checks.append(check(f"{label}:series_length_match", "FAIL", "序列为空"))
    else:
        checks.append(check(f"{label}:series_length_match", "PASS", f"观测与模拟均为 {len(observed)} 行"))

    time = parse_datetime(frame[args.time_column], label)
    step, regular = infer_step_hours(time)
    if not regular or not np.isfinite(step) or abs(step - args.timestep_hours) > 1e-9:
        checks.append(check(
            f"{label}:timestep_consistency", "FAIL",
            f"时间轴步长 {step!r} 小时与 --timestep-hours {args.timestep_hours} 不一致",
        ))
    else:
        checks.append(check(f"{label}:timestep_consistency", "PASS", f"时间轴等间隔 {step} 小时"))

    missing = int(np.isnan(observed).sum() + np.isnan(simulated).sum())
    if missing:
        checks.append(check(
            f"{label}:no_missing_values", "FAIL",
            f"观测或模拟列存在 {missing} 个缺测；缺测必须由上游显式修复，不得填 0 后计算指标",
        ))
    else:
        checks.append(check(f"{label}:no_missing_values", "PASS", "观测与模拟均无缺测"))

    if not (np.all(np.isfinite(observed)) and np.all(np.isfinite(simulated))):
        checks.append(check(f"{label}:finite_values", "FAIL", "观测或模拟存在非有限值"))
    else:
        checks.append(check(f"{label}:finite_values", "PASS", "观测与模拟均为有限值"))

    if any(item["status"] == "FAIL" for item in checks):
        return {}, checks, warnings

    dt_seconds = args.timestep_hours * 3600.0
    scale = VOLUME_SCALE[args.volume_unit]
    observed_volume = float(np.sum(observed) * dt_seconds * scale)
    simulated_volume = float(np.sum(simulated) * dt_seconds * scale)
    volume_error = (
        (observed_volume - simulated_volume) / observed_volume if observed_volume != 0 else float("nan")
    )

    observed_peak = float(np.max(observed))
    simulated_peak = float(np.max(simulated))
    peak_error = (observed_peak - simulated_peak) / observed_peak if observed_peak != 0 else float("nan")

    observed_ties = int(np.sum(observed == observed_peak))
    simulated_ties = int(np.sum(simulated == simulated_peak))
    if observed_ties > 1 or simulated_ties > 1:
        warnings.append(
            f"{label} 出现并列峰（观测 {observed_ties} 个、模拟 {simulated_ties} 个），"
            f"峰现时间取首个最大值，结论需谨慎"
        )
        checks.append(check(
            f"{label}:peak_tie", "WARN",
            f"并列峰：观测 {observed_ties} 个、模拟 {simulated_ties} 个",
        ))
    else:
        checks.append(check(f"{label}:peak_tie", "PASS", "峰值唯一"))

    observed_index = int(np.argmax(observed))
    simulated_index = int(np.argmax(simulated))
    peak_time_error = (
        time.iloc[simulated_index] - time.iloc[observed_index]
    ).total_seconds() / 3600.0

    value = nse(observed, simulated)
    row: dict[str, Any] = {
        args.event_id_column: label,
        "rows": int(len(observed)),
        "start": str(time.iloc[0]),
        "end": str(time.iloc[-1]),
        "observed_volume": observed_volume,
        "simulated_volume": simulated_volume,
        "volume_error_relative": volume_error,
        "observed_peak": observed_peak,
        "simulated_peak": simulated_peak,
        "peak_error_relative": peak_error,
        "peak_time_error_hours": float(peak_time_error),
        "peak_tie_count": observed_ties + simulated_ties,
        "nse": value,
        "r2": r_squared(observed, simulated),
        "rmse": rmse(observed, simulated),
        "mae": mae(observed, simulated),
        "qualified_volume": _qualified(volume_error, thresholds.get("relative_volume_error")),
        "qualified_peak": _qualified(peak_error, thresholds.get("relative_peak_error")),
        "qualified_peak_time": _qualified(
            float(peak_time_error), thresholds.get("peak_time_error_hours")
        ),
    }
    if not np.isfinite(value):
        warnings.append(f"{label} 观测序列方差为 0，NSE 记为 {UNAVAILABLE}")
        row["nse"] = UNAVAILABLE
    return row, checks, warnings


def run(args: argparse.Namespace) -> dict[str, Any]:
    thresholds = _load_thresholds(args.thresholds)
    events = _collect_events(args)
    rows: list[dict[str, Any]] = []
    all_checks: list[dict[str, str]] = []
    all_warnings: list[str] = []
    for label, frame in events:
        row, checks, warnings = _evaluate_event(frame, label, args, thresholds)
        all_checks.extend(checks)
        all_warnings.extend(warnings)
        if row:
            rows.append(row)
        else:
            rows.append({args.event_id_column: label, "rows": 0})

    if not thresholds:
        all_warnings.append(
            "未提供 --thresholds，合格判定列记为 unavailable；合格阈值必须由使用者显式给出"
        )
    all_checks.append(check(
        "thresholds_provided",
        "PASS" if thresholds else "WARN",
        f"合格阈值 {sorted(thresholds)}" if thresholds else "未提供合格阈值，不判定合格",
    ))
    all_checks.append(check("volume_unit_recorded", "PASS", f"洪量单位为 {VOLUME_UNITS[args.volume_unit]}"))

    table = pd.DataFrame(rows)
    if not rows:
        raise QualityControlFailure({"checks": all_checks, "warnings": all_warnings})
    table_name = f"event_metrics.{args.output_format}"
    table_path = args.output_dir / table_name
    write_table(table, table_path, args.output_format)

    failed = [item for item in all_checks if item["status"] == "FAIL"]
    warned = [item for item in all_checks if item["status"] == "WARN"]
    status = "error" if failed else ("warning" if warned else "success")
    message = (
        f"场次指标计算失败：{len(failed)} 项 QC FAIL"
        if failed
        else f"场次指标计算完成：{len(rows)} 场"
    )
    inputs: dict[str, Any] = {}
    if args.events is not None:
        inputs["events"] = file_reference(Path(args.events), args.output_dir)
    else:
        inputs["events_dir"] = str(Path(args.events_dir))
    return {
        "schema_version": "1.0",
        "skill": SKILL_REF,
        "status": status,
        "message": message,
        "parameters": {
            "timestep_hours": args.timestep_hours,
            "volume_unit": VOLUME_UNITS[args.volume_unit],
            "thresholds": thresholds,
            "observed_column": args.observed_column,
            "simulated_column": args.simulated_column,
            "time_column": args.time_column,
        },
        "inputs": inputs,
        "artifacts": {"event_metrics": file_reference(table_path, args.output_dir)},
        "checks": all_checks,
        "warnings": all_warnings,
        "provenance": provenance(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute per-event flood metrics.")
    parser.add_argument("--events", help="场次文件；xlsx 时每个 sheet 为一场")
    parser.add_argument("--events-dir", help="场次目录，目录下每个表格文件为一场")
    parser.add_argument("--events-format", choices=FORMATS)
    parser.add_argument("--sheet")
    parser.add_argument("--event-id-column", default="event_id")
    parser.add_argument("--time-column", default="time")
    parser.add_argument("--observed-column", default="Q_obs")
    parser.add_argument("--simulated-column", default="Q_total")
    parser.add_argument("--timestep-hours", type=float, required=True)
    parser.add_argument("--volume-unit", choices=tuple(VOLUME_SCALE), default="10k-m3")
    parser.add_argument("--thresholds", help="合格阈值 JSON 字符串或文件路径")
    parser.add_argument("--output-format", choices=FORMATS, default="csv")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.output_dir = args.output_dir.resolve()
    prepared = False
    try:
        if not args.events and not args.events_dir:
            raise ContractError("必须提供 --events 或 --events-dir")
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
