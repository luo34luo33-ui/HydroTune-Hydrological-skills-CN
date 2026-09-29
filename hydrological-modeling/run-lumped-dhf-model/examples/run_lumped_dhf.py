#!/usr/bin/env python3
"""Run the lumped DHF (Dahuofang) model on one continuous series or on a set of flood events."""

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
from _dhf_core import build_parameters, parameter_range_warnings, run_lumped_dhf, validate_parameters


SKILL_REF = "hydrological-modeling/run-lumped-dhf-model"
FORMATS = ("csv", "parquet", "xlsx")
TABLE_SUFFIXES = {".csv", ".parquet", ".xlsx"}
DECLARED = [
    "lumped_dhf_runoff.csv",
    "lumped_dhf_runoff.parquet",
    "lumped_dhf_runoff.xlsx",
    "event_index.csv",
    "result.json",
]
STATE_COLUMNS = [
    "P", "PET", "E", "PE", "PC", "Y0", "EU", "EL", "RR", "Y", "YU", "YL",
    "RUNOFF", "QS", "QL", "Q", "SA", "UA", "YA", "EB",
]
INITIAL_STATE_KEYS = ("sa0", "ua0", "ya0")


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


def _load_initial_states(path: str | None) -> dict[str, float] | None:
    if not path:
        return None
    document = read_mapping(Path(path))
    unknown = [key for key in document if key not in INITIAL_STATE_KEYS]
    if unknown:
        raise ContractError(f"--initial-state 只允许键 {INITIAL_STATE_KEYS}，发现: {', '.join(unknown)}")
    return {key: float(document[key]) for key in document}


def _simulate_event(
    frame: pd.DataFrame,
    label: str,
    args: argparse.Namespace,
    parameters: Any,
    carry: list[str],
    initial_states: dict[str, float] | None,
) -> tuple[pd.DataFrame, list[dict[str, str]], list[str], dict[str, Any]]:
    require_columns(frame, [args.time_column, args.precipitation_column, args.pet_column], label)
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
    pet = pd.to_numeric(frame[args.pet_column], errors="coerce").to_numpy(dtype=float)
    missing = int(np.isnan(precipitation).sum() + np.isnan(pet).sum())
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

    for warning in parameter_range_warnings(parameters):
        warnings.append(f"{label} {warning}")
    checks.append(check(
        f"{label}:parameter_range",
        "WARN" if parameter_range_warnings(parameters) else "PASS",
        "全部参数落在源工程率定范围内" if not parameter_range_warnings(parameters) else "存在超出源工程率定范围的参数",
    ))

    if missing or warmup >= steps or not step_consistent:
        raise QualityControlFailure({"checks": checks, "warnings": warnings})

    # warmup semantics follow the source: the warmup pass starts from the default
    # states, its end states seed the simulation pass, and --initial-state overrides
    # apply only after the warmup pass (i.e. at the simulation start).
    if warmup > 0:
        warmup_states = run_lumped_dhf(
            precipitation[:warmup], pet[:warmup], parameters,
            args.river_length_km, args.area_km2, args.timestep_hours,
        )
        start = {
            "sa0": float(warmup_states["SA"][-1]),
            "ua0": float(warmup_states["UA"][-1]),
            "ya0": float(warmup_states["YA"][-1]),
        }
        if initial_states:
            start.update(initial_states)
    else:
        start = dict(initial_states) if initial_states else {"sa0": 0.0, "ua0": 0.0, "ya0": 0.5}

    states = run_lumped_dhf(
        precipitation[warmup:], pet[warmup:], parameters,
        args.river_length_km, args.area_km2, args.timestep_hours,
        initial_states=start,
    )
    initial_total_storage = start["sa0"] + start["ua0"]

    result = pd.DataFrame({
        "time": time.to_numpy(),
        "is_warmup": np.arange(steps) < warmup,
    })
    for column in STATE_COLUMNS:
        values = np.zeros(steps)
        if warmup > 0:
            values[:warmup] = warmup_states[column]
        values[warmup:] = states[column]
        result[column] = values
    for column in carry:
        result[column] = frame[column].to_numpy()

    finite_problem = [
        name for name in ("SA", "UA", "YA", "RUNOFF", "Q", "QS", "QL")
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
        bool(np.any(result["SA"].to_numpy(dtype=float) < -epsilon))
        or bool(np.any(result["SA"].to_numpy(dtype=float) > parameters.s0 + epsilon))
        or bool(np.any(result["UA"].to_numpy(dtype=float) < -epsilon))
        or bool(np.any(result["UA"].to_numpy(dtype=float) > parameters.u0 + epsilon))
        or bool(np.any(result["YA"].to_numpy(dtype=float) < -epsilon))
    )
    if bounds_violated:
        checks.append(check(
            f"{label}:state_bounds", "FAIL",
            "蓄水状态越界：要求 0<=SA<=S0、0<=UA<=U0、YA>=0",
        ))
    else:
        checks.append(check(f"{label}:state_bounds", "PASS", "蓄水状态在容量范围内"))

    clip_count = int(states["clip_count"][0])
    if clip_count > 0:
        warnings.append(
            f"{label} 发生 {clip_count} 次数值裁剪（状态钳制、负值截断或 NaN 置零），"
            f"说明参数或 forcing 使公式越过定义域，请检查蓄水容量与降雨量级"
        )
        checks.append(check(f"{label}:numerical_clipping", "WARN", f"数值裁剪 {clip_count} 次"))
    else:
        checks.append(check(f"{label}:numerical_clipping", "PASS", "无数值裁剪"))

    start_index = warmup
    water_in = float(np.sum(precipitation[start_index:]))
    water_out = float(np.sum(result["RUNOFF"].to_numpy(dtype=float)[start_index:]))
    evaporation = float(
        np.sum(result["EU"].to_numpy(dtype=float)[start_index:])
        + np.sum(result["EL"].to_numpy(dtype=float)[start_index:])
    )
    storage_change = (
        float(result["SA"].iloc[-1]) + float(result["UA"].iloc[-1]) - initial_total_storage
    )
    residual = abs(water_in - water_out - evaporation - storage_change)
    denominator = max(water_in, 1e-9)
    fraction = residual / denominator
    if fraction > args.balance_tolerance_fraction:
        checks.append(check(
            f"{label}:water_balance", "WARN",
            f"水量平衡残差 {residual:.6g} mm，占降雨 {fraction:.3%}，超过容差 {args.balance_tolerance_fraction:.3%}；"
            f"单位线卷积在序列末端截断，残差包含该截断水量，请结合序列长度解释",
        ))
        warnings.append(f"{label} 水量平衡残差占降雨 {fraction:.3%}，包含单位线卷积的末端截断")
    else:
        checks.append(check(f"{label}:water_balance", "PASS", f"水量平衡残差占降雨 {fraction:.3%}"))

    is_warmup_values = result["is_warmup"].to_numpy(dtype=bool)
    summary = {
        "rows": steps,
        "warmup_steps": warmup,
        "simulation_steps": steps - warmup,
        "precipitation_total_mm": float(np.sum(precipitation[start_index:])),
        "runoff_total_mm": float(np.sum(states["RUNOFF"])),
        "peak_q_m3_s": float(np.max(states["Q"])) if steps - warmup > 0 else 0.0,
        "clip_count": clip_count,
    }
    return result, checks, warnings, summary


def run(args: argparse.Namespace) -> dict[str, Any]:
    raw_parameters = read_mapping(Path(args.params))
    parameters = build_parameters(raw_parameters, args.param_scale)
    problems = validate_parameters(parameters, args.area_km2, args.river_length_km, args.timestep_hours)
    if problems:
        raise ContractError("参数校验失败: " + "; ".join(problems))
    initial_states = _load_initial_states(args.initial_state)
    carry = _carry_columns(args.carry_columns)

    events = _collect_events(args)
    frames: list[pd.DataFrame] = []
    all_checks: list[dict[str, str]] = []
    all_warnings: list[str] = []
    index_rows: list[dict[str, Any]] = []

    for label, frame in events:
        simulated, checks, warnings, summary = _simulate_event(frame, label, args, parameters, carry, initial_states)
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
    table_name = f"lumped_dhf_runoff.{args.output_format}"
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
        f"集总式 DHF 前向模拟失败：{len(failed)} 项 QC FAIL"
        if failed
        else f"集总式 DHF 前向模拟完成：{len(events)} 场（或段），{len(combined)} 行"
    )
    inputs: dict[str, Any] = {
        "params": file_reference(Path(args.params), args.output_dir),
        "param_scale": args.param_scale,
    }
    if args.initial_state:
        inputs["initial_state"] = file_reference(Path(args.initial_state), args.output_dir)
    if args.mode == "continuous":
        inputs["forcing"] = file_reference(Path(args.forcing), args.output_dir)
    elif args.events_workbook is not None:
        inputs["events_workbook"] = file_reference(Path(args.events_workbook), args.output_dir)
    else:
        inputs["events_dir"] = str(Path(args.events_dir))

    return {
        "schema_version": "1.0",
        "skill": SKILL_REF,
        "status": status,
        "message": message,
        "parameters": {
            "mode": args.mode,
            "area_km2": args.area_km2,
            "river_length_km": args.river_length_km,
            "timestep_hours": args.timestep_hours,
            "warmup_steps": args.warmup_steps,
            "balance_tolerance_fraction": args.balance_tolerance_fraction,
            "carry_columns": carry,
            "initial_states": initial_states,
            "parameter_set": raw_parameters,
        },
        "inputs": inputs,
        "artifacts": artifacts,
        "checks": all_checks,
        "warnings": all_warnings,
        "provenance": provenance(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the lumped DHF (Dahuofang) model and discharge at the basin outlet.",
    )
    parser.add_argument("--forcing", help="continuous 模式的 forcing 表路径")
    parser.add_argument("--forcing-format", choices=FORMATS)
    parser.add_argument("--sheet", help="xlsx 工作表名，默认第一个")
    parser.add_argument("--time-column", default="time")
    parser.add_argument("--precipitation-column", default="P")
    parser.add_argument("--pet-column", default="PET")
    parser.add_argument("--carry-columns", help="逗号分隔，原样透传且参与校验的列")
    parser.add_argument("--params", required=True, help="参数集 JSON 或 YAML（18 个参数；KC 亦接受源脚本别名 K）")
    parser.add_argument("--param-scale", choices=("original", "normalized"), default="original")
    parser.add_argument("--river-length-km", type=float, required=True)
    parser.add_argument("--area-km2", type=float, required=True)
    parser.add_argument("--timestep-hours", type=float, required=True)
    parser.add_argument("--mode", choices=("event", "continuous"), required=True)
    parser.add_argument("--events-dir", help="event 模式：目录下每个表格文件为一场")
    parser.add_argument("--events-workbook", help="event 模式：xlsx 每个 sheet 为一场")
    parser.add_argument("--event-id-column", default="event_id")
    parser.add_argument("--warmup-steps", type=int, default=0)
    parser.add_argument("--initial-state", help="JSON 文件，键 sa0/ua0/ya0，覆盖 warmup 之后的初始状态")
    parser.add_argument("--balance-tolerance-fraction", type=float, default=0.01)
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
