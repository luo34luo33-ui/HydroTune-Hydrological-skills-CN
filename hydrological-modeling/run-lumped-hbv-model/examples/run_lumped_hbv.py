#!/usr/bin/env python3
"""Run the lumped HBV model on one continuous series or on a set of flood events."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

from _modeling_common import (
    ContractError,
    check,
    file_reference,
    infer_step_hours,
    parse_datetime,
    prepare_output_dir,
    provenance,
    read_mapping,
    read_table,
    require_columns,
    write_result,
    write_table,
)
from _hbv_core import build_parameters, parameter_range_warnings, run_lumped_hbv, validate_parameters


SKILL_REF = "hydrological-modeling/run-lumped-hbv-model"
FORMATS = ("csv", "parquet", "xlsx")
TABLE_SUFFIXES = {".csv", ".parquet", ".xlsx"}
DECLARED = [
    "lumped_hbv_runoff.csv",
    "lumped_hbv_runoff.parquet",
    "lumped_hbv_runoff.xlsx",
    "event_index.csv",
    "result.json",
]
STATE_COLUMNS = ["P", "PET", "EA", "SM", "RECHARGE", "SUZ", "SLZ", "Q0", "Q1", "Q2", "Q"]


class QualityControlFailure(RuntimeError):
    """Raised when a scientific QC gate fails."""

    def __init__(self, document: dict[str, Any]) -> None:
        super().__init__("科学 QC 失败")
        self.document = document


def _collect_events(args: argparse.Namespace) -> list[tuple[str, pd.DataFrame]]:
    if args.mode == "continuous":
        path = Path(args.forcing)
        if not path.is_file():
            raise ContractError(f"forcing 文件不存在: {path}")
        fmt = args.forcing_format or path.suffix.lower().lstrip(".")
        return [("continuous", read_table(path, fmt, args.sheet))]
    if args.events_workbook is not None:
        path = Path(args.events_workbook)
        if not path.is_file():
            raise ContractError(f"场次工作簿不存在: {path}")
        book = pd.ExcelFile(path)
        events = [(str(sheet), read_table(path, "xlsx", sheet)) for sheet in book.sheet_names]
        if not events:
            raise ContractError(f"场次工作簿没有工作表: {path}")
        return events
    if args.events_dir is not None:
        directory = Path(args.events_dir)
        if not directory.is_dir():
            raise ContractError(f"场次目录不存在: {directory}")
        paths = sorted(item for item in directory.iterdir() if item.is_file() and item.suffix.lower() in TABLE_SUFFIXES)
        if not paths:
            raise ContractError(f"场次目录内没有 csv、parquet 或 xlsx 文件: {directory}")
        return [(path.stem, read_table(path)) for path in paths]
    raise ContractError("event 模式必须提供 --events-dir 或 --events-workbook")


def _carry_columns(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _simulate_event(
    frame: pd.DataFrame,
    label: str,
    args: argparse.Namespace,
    parameters: Any,
    carry: list[str],
) -> tuple[pd.DataFrame, list[dict[str, str]], list[str], dict[str, Any]]:
    require_columns(frame, [args.time_column, args.precipitation_column, args.evaporation_column], label)
    time = parse_datetime(frame[args.time_column], label)
    if time.isna().any():
        raise ContractError(f"{label} 时间列存在无法解析的值")
    if not time.is_monotonic_increasing:
        raise ContractError(f"{label} 时间列必须严格递增")
    if time.duplicated().any():
        raise ContractError(f"{label} 时间列存在重复值")

    checks: list[dict[str, str]] = []
    warnings: list[str] = []
    step, regular = infer_step_hours(time)
    step_consistent = bool(regular and np.isfinite(step) and abs(step - args.timestep_hours) <= 1e-9)
    if step_consistent:
        checks.append(check(f"{label}:time_axis_regular", "PASS", f"时间轴等间隔 {step} 小时"))
    else:
        checks.append(check(
            f"{label}:time_axis_regular", "FAIL",
            f"时间轴步长 {step!r} 小时与 --timestep-hours {args.timestep_hours} 不一致",
        ))

    precipitation = pd.to_numeric(frame[args.precipitation_column], errors="coerce").to_numpy(dtype=float)
    evaporation = pd.to_numeric(frame[args.evaporation_column], errors="coerce").to_numpy(dtype=float)
    missing = int(np.isnan(precipitation).sum() + np.isnan(evaporation).sum())
    if missing:
        checks.append(check(
            f"{label}:no_missing_forcing", "FAIL",
            f"降雨或蒸发能力列存在 {missing} 个缺测；缺测必须由上游显式修复后重跑",
        ))
    else:
        checks.append(check(f"{label}:no_missing_forcing", "PASS", "forcing 无缺测"))

    for column in carry:
        if column not in frame.columns:
            raise ContractError(f"{label} 透传列不存在: {column}")
        holes = int(pd.to_numeric(frame[column], errors="coerce").isna().sum())
        if holes:
            warnings.append(f"{label} 透传列 {column} 有 {holes} 个缺测，原样保留未参与计算")

    steps = len(frame)
    warmup = args.warmup_steps
    if warmup >= steps:
        checks.append(check(
            f"{label}:warmup_coverage", "FAIL",
            f"预热步数 {warmup} 不小于序列长度 {steps}，模拟期为空",
        ))
    else:
        checks.append(check(f"{label}:warmup_coverage", "PASS", f"预热 {warmup} 步，模拟期 {steps - warmup} 步"))

    range_warnings = parameter_range_warnings(parameters)
    for warning in range_warnings:
        warnings.append(f"{label} {warning}")
    checks.append(check(
        f"{label}:parameter_range",
        "WARN" if range_warnings else "PASS",
        "全部参数落在源工程率定范围内" if not range_warnings else "存在超出源工程率定范围的参数",
    ))
    checks.append(check(
        f"{label}:evaporation_coefficient", "PASS",
        f"蒸发能力按 c={parameters.c} 缩放，实际蒸发 AE 受 lp×fc 调蓄",
    ))

    if missing or warmup >= steps or not step_consistent:
        raise QualityControlFailure({"checks": checks, "warnings": warnings})

    states = run_lumped_hbv(precipitation, evaporation, parameters, args.area_km2, args.timestep_hours)

    result = pd.DataFrame({"time": time.to_numpy(), "is_warmup": np.arange(steps) < warmup})
    for column in STATE_COLUMNS:
        result[column] = states[column]
    for column in carry:
        result[column] = frame[column].to_numpy()

    finite_problem = [
        name for name in ("SM", "SUZ", "SLZ", "Q0", "Q1", "Q2", "Q")
        if not np.all(np.isfinite(result[name].to_numpy(dtype=float)))
    ]
    if finite_problem:
        checks.append(check(
            f"{label}:finite_states", "FAIL",
            f"状态或通量出现非有限值: {', '.join(finite_problem)}",
        ))
    else:
        checks.append(check(f"{label}:finite_states", "PASS", "状态与通量均为有限值"))

    epsilon = 1e-9
    bounds_violated = (
        bool(np.any(result["SM"].to_numpy(dtype=float) < -epsilon))
        or bool(np.any(result["SUZ"].to_numpy(dtype=float) < -epsilon))
        or bool(np.any(result["SLZ"].to_numpy(dtype=float) < -epsilon))
    )
    if bounds_violated:
        checks.append(check(
            f"{label}:state_bounds", "FAIL",
            "蓄水状态越界：要求 SM、SUZ、SLZ 均非负",
        ))
    else:
        checks.append(check(f"{label}:state_bounds", "PASS", "蓄水状态非负"))

    clip_count = int(states["clip_count"][0])
    if clip_count > 0:
        warnings.append(
            f"{label} 发生 {clip_count} 次数值截断（状态 floor 0 或负流量截断），"
            f"说明 max(·,0) 兜底被触发，会损耗水量，请检查参数与降雨量级"
        )
        checks.append(check(f"{label}:numerical_clipping", "WARN", f"数值截断 {clip_count} 次"))
    else:
        checks.append(check(f"{label}:numerical_clipping", "PASS", "无数值截断"))

    start = warmup
    water_in = float(np.sum(precipitation[start:]))
    actual_evap = float(np.sum(result["EA"].to_numpy(dtype=float)[start:]))
    conversion = (args.area_km2 * 1000.0) / (args.timestep_hours * 3600.0)
    discharge_mm = float(np.sum(result["Q"].to_numpy(dtype=float)[start:]) / conversion)
    storage_change = (
        float(result["SM"].iloc[-1]) + float(result["SUZ"].iloc[-1]) + float(result["SLZ"].iloc[-1])
        - 0.0
    )
    residual = abs(water_in - actual_evap - discharge_mm - storage_change)
    denominator = max(water_in, 1e-9)
    fraction = residual / denominator
    if fraction > args.balance_tolerance_fraction:
        checks.append(check(
            f"{label}:water_balance", "WARN",
            f"水量平衡残差 {residual:.6g} mm，占降雨 {fraction:.3%}，超过容差 {args.balance_tolerance_fraction:.3%}；"
            f"源码的 Q0 不从 SM 扣水且 effective→recharge 存在消减项，残差包含这些结构性损耗",
        ))
        warnings.append(
            f"{label} 水量平衡残差占降雨 {fraction:.3%}；源码 HBV 的 Q0 不从 SM 扣水、"
            f"effective 到 recharge 有消减项，属于模型结构固有的水量损耗"
        )
    else:
        checks.append(check(f"{label}:water_balance", "PASS", f"水量平衡残差占降雨 {fraction:.3%}"))

    summary = {
        "rows": steps,
        "warmup_steps": warmup,
        "simulation_steps": steps - warmup,
        "precipitation_total_mm": float(np.sum(precipitation[start:])),
        "runoff_total_mm": discharge_mm,
        "peak_q_m3_s": float(np.max(result["Q"].to_numpy(dtype=float)[start:])) if steps - warmup > 0 else 0.0,
        "clip_count": clip_count,
    }
    return result, checks, warnings, summary


def run(args: argparse.Namespace) -> dict[str, Any]:
    raw_parameters = read_mapping(Path(args.params))
    parameters = build_parameters(raw_parameters)
    problems = validate_parameters(parameters, args.area_km2, args.timestep_hours)
    if problems:
        raise ContractError("参数校验失败: " + "; ".join(problems))
    carry = _carry_columns(args.carry_columns)

    events = _collect_events(args)
    frames: list[pd.DataFrame] = []
    all_checks: list[dict[str, str]] = []
    all_warnings: list[str] = []
    index_rows: list[dict[str, Any]] = []

    for label, frame in events:
        simulated, checks, warnings, summary = _simulate_event(frame, label, args, parameters, carry)
        all_checks.extend(checks)
        all_warnings.extend(warnings)
        if args.mode == "event":
            simulated.insert(1, args.event_id_column, label)
        frames.append(simulated)
        index_rows.append({
            args.event_id_column: label,
            "rows": summary["rows"],
            "start": str(pd.Timestamp(simulated["time"].iloc[0])),
            "end": str(pd.Timestamp(simulated["time"].iloc[-1])),
            "warmup_steps": summary["warmup_steps"],
        })

    combined = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    table_name = f"lumped_hbv_runoff.{args.output_format}"
    table_path = args.output_dir / table_name
    write_table(combined, table_path, args.output_format)

    artifacts = {"simulation_table": file_reference(table_path, args.output_dir)}
    if args.mode == "event":
        index = pd.DataFrame(index_rows)
        index_path = args.output_dir / "event_index.csv"
        index.to_csv(index_path, index=False, encoding="utf-8-sig")
        artifacts["event_index"] = file_reference(index_path, args.output_dir)

    failed = [item for item in all_checks if item["status"] == "FAIL"]
    warned = [item for item in all_checks if item["status"] == "WARN"]
    status = "error" if failed else ("warning" if warned else "success")
    message = (
        f"集总式 HBV 前向模拟失败：{len(failed)} 项 QC FAIL"
        if failed
        else f"集总式 HBV 前向模拟完成：{len(events)} 场（或段），{len(combined)} 行"
    )
    return {
        "schema_version": "1.0",
        "skill": SKILL_REF,
        "status": status,
        "message": message,
        "parameters": {
            "mode": args.mode,
            "area_km2": args.area_km2,
            "timestep_hours": args.timestep_hours,
            "warmup_steps": args.warmup_steps,
            "balance_tolerance_fraction": args.balance_tolerance_fraction,
            "carry_columns": carry,
            "parameter_set": raw_parameters,
        },
        "inputs": {"params": file_reference(Path(args.params), args.output_dir)},
        "artifacts": artifacts,
        "checks": all_checks,
        "warnings": all_warnings,
        "provenance": provenance(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the lumped HBV model (saturation-excess runoff with linear reservoirs).",
    )
    parser.add_argument("--forcing", help="continuous 模式的 forcing 表路径")
    parser.add_argument("--forcing-format", choices=FORMATS)
    parser.add_argument("--sheet", help="xlsx 工作表名，默认第一个")
    parser.add_argument("--time-column", default="time")
    parser.add_argument("--precipitation-column", default="P")
    parser.add_argument("--evaporation-column", default="E0")
    parser.add_argument("--carry-columns", help="逗号分隔，原样透传且参与校验的列")
    parser.add_argument("--params", required=True, help="参数集 JSON 或 YAML（9 个参数，全部必填）")
    parser.add_argument("--area-km2", type=float, required=True)
    parser.add_argument("--timestep-hours", type=float, required=True)
    parser.add_argument("--mode", choices=("event", "continuous"), required=True)
    parser.add_argument("--events-dir", help="event 模式：目录下每个表格文件为一场")
    parser.add_argument("--events-workbook", help="event 模式：xlsx 每个 sheet 为一场")
    parser.add_argument("--event-id-column", default="event_id")
    parser.add_argument("--warmup-steps", type=int, default=0)
    parser.add_argument("--balance-tolerance-fraction", type=float, default=0.05)
    parser.add_argument("--output-format", choices=FORMATS, default="csv")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.output_dir = args.output_dir.resolve()
    prepared = False
    try:
        if args.mode == "continuous" and not args.forcing:
            raise ContractError("continuous 模式必须提供 --forcing")
        if args.mode == "event" and not (args.events_dir or args.events_workbook):
            raise ContractError("event 模式必须提供 --events-dir 或 --events-workbook")
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
                "schema_version": "1.0",
                "skill": SKILL_REF,
                "status": "error",
                "message": "科学 QC 失败，未生成模拟表",
                "parameters": {},
                "inputs": {},
                "artifacts": {},
                "provenance": provenance(),
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
