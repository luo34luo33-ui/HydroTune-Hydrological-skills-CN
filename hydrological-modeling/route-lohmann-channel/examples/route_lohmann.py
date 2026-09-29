#!/usr/bin/env python3
"""Route direct and base inflow series through the standard Lohmann channel routing."""

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
    write_json,
    write_result,
    write_table,
)
from _lohmann_core import build_parameters, run_lohmann_routing, validate_parameters


SKILL_REF = "hydrological-modeling/route-lohmann-channel"
FORMATS = ("csv", "parquet", "xlsx")
DAILY_TIMESTEP_HOURS = 24.0
NORMALIZATION_EPSILON = 1e-9
DECLARED = [
    "routed_lohmann.csv",
    "routed_lohmann.parquet",
    "routed_lohmann.xlsx",
    "lohmann_unit_hydrographs.json",
    "result.json",
]


class QualityControlFailure(RuntimeError):
    """Raised when a scientific QC gate fails."""

    def __init__(self, document: dict[str, Any]) -> None:
        super().__init__("科学 QC 失败")
        self.document = document


def run(args: argparse.Namespace) -> dict[str, Any]:
    raw_parameters = read_mapping(Path(args.route_params))
    parameters = build_parameters(raw_parameters)
    problems = validate_parameters(parameters, args.flowlen_m)
    if problems:
        raise ContractError("参数校验失败: " + "; ".join(problems))

    inflow_path = Path(args.inflow)
    if not inflow_path.is_file():
        raise ContractError(f"入流表不存在: {inflow_path}")
    fmt = args.inflow_format or inflow_path.suffix.lower().lstrip(".")
    frame = read_table(inflow_path, fmt, args.sheet)
    require_columns(
        frame,
        [args.time_column, args.direct_column, args.base_column],
        "入流表",
    )
    time = parse_datetime(frame[args.time_column], "入流表")
    if time.isna().any():
        raise ContractError("入流表时间列存在无法解析的值")
    if not time.is_monotonic_increasing:
        raise ContractError("入流表时间列必须严格递增")
    if time.duplicated().any():
        raise ContractError("入流表时间列存在重复值")

    checks: list[dict[str, str]] = []
    warnings: list[str] = []
    step, regular = infer_step_hours(time)
    step_consistent = bool(regular and np.isfinite(step) and abs(step - args.timestep_hours) <= 1e-9)
    if step_consistent:
        checks.append(check("time_axis_regular", "PASS", f"时间轴等间隔 {step} 小时"))
    else:
        checks.append(check(
            "time_axis_regular", "FAIL",
            f"时间轴步长 {step!r} 小时与 --timestep-hours {args.timestep_hours} 不一致",
        ))

    daily_consistent = bool(np.isfinite(args.timestep_hours) and abs(args.timestep_hours - DAILY_TIMESTEP_HOURS) <= 1e-9)
    if daily_consistent:
        checks.append(check("daily_timestep", "PASS", "时间步长为 24 小时，与 UH 的日尺度语义一致"))
    else:
        checks.append(check(
            "daily_timestep", "WARN",
            f"时间步长 {args.timestep_hours} 小时非日步长：河道与 HRU 单位线均为日尺度"
            "（内部小时积分聚合 96 天），非日步长行为未经验证且参数不换算",
        ))
        warnings.append(
            f"使用非日步长（{args.timestep_hours} 小时）：Lohmann UH 为日尺度，"
            "结果仅供流程贯通，不作水文结论"
        )

    direct = pd.to_numeric(frame[args.direct_column], errors="coerce").to_numpy(dtype=float)
    base = pd.to_numeric(frame[args.base_column], errors="coerce").to_numpy(dtype=float)
    missing = int(np.isnan(direct).sum() + np.isnan(base).sum())
    if missing:
        checks.append(check(
            "no_missing_inflow", "FAIL",
            f"入流列存在 {missing} 个缺测；缺测必须由上游显式修复后重跑",
        ))
    else:
        checks.append(check("no_missing_inflow", "PASS", "入流列无缺测"))

    if missing or not step_consistent:
        raise QualityControlFailure({"checks": checks, "warnings": warnings})

    negative_direct = int(np.sum(direct < 0))
    negative_base = int(np.sum(base < 0))
    if negative_direct or negative_base:
        checks.append(check(
            "negative_inflow", "WARN",
            f"入流含负值（direct {negative_direct} 个、base {negative_base} 个）；"
            "卷积是线性的，负值会按 UH 传播到出流",
        ))
        warnings.append("入流含负值，出流可能为负；本 Skill 不做静默截断")
    else:
        checks.append(check("negative_inflow", "PASS", "入流列非负"))

    routed = run_lohmann_routing(direct, base, parameters, args.flowlen_m)

    for name, uh in (
        ("uh_river", routed["uh_river"]),
        ("uh_direct", routed["uh_direct"]),
        ("uh_base", routed["uh_base"]),
    ):
        deviation = abs(float(np.sum(uh)) - 1.0)
        if deviation > NORMALIZATION_EPSILON:
            checks.append(check(
                "uh_normalization", "FAIL",
                f"{name} 的纵坐标之和偏离 1 达 {deviation:.3g}，超过 {NORMALIZATION_EPSILON}",
            ))
        else:
            checks.append(check("uh_normalization", "PASS", f"{name} 纵坐标之和为 1（偏差 {deviation:.2g}）"))

    result = pd.DataFrame({
        "time": time.to_numpy(),
        "routed_direct": routed["routed_direct"],
        "routed_base": routed["routed_base"],
    })
    result["Q_total"] = result["routed_direct"] + result["routed_base"]

    finite_problem = [
        name for name in ("routed_direct", "routed_base", "Q_total")
        if not np.all(np.isfinite(result[name].to_numpy(dtype=float)))
    ]
    if finite_problem:
        checks.append(check(
            "finite_outflow", "FAIL",
            f"出流出现非有限值: {', '.join(finite_problem)}",
        ))
    else:
        checks.append(check("finite_outflow", "PASS", "出流均为有限值"))

    steps = len(frame)
    for label, inflow_total, outflow_total in (
        ("direct", float(np.sum(direct)), float(np.sum(result["routed_direct"]))),
        ("base", float(np.sum(base)), float(np.sum(result["routed_base"]))),
    ):
        denominator = max(abs(inflow_total), 1e-9)
        fraction = abs(inflow_total - outflow_total) / denominator
        if inflow_total == 0:
            checks.append(check(
                f"volume_difference:{label}", "WARN",
                f"{label} 入流总量为 0，体积差不适用",
            ))
            warnings.append(f"{label} 入流总量为 0，体积差检查不适用")
        elif fraction > args.volume_tolerance:
            checks.append(check(
                f"volume_difference:{label}", "WARN",
                f"{label} 出入流体积相对差 {fraction:.3%} 超过容差 {args.volume_tolerance:.3%}；"
                "含 UH 尾部截断滞留（零初始历史 + 序列末端水量仍留在单位线中，属预期行为）",
            ))
            warnings.append(
                f"{label} 出入流体积相对差 {fraction:.3%}，超出 {args.volume_tolerance:.3%}；"
                "若序列长度接近或短于 UH 底长（107 步），缺口主要来自尾部滞留"
            )
        else:
            checks.append(check(
                f"volume_difference:{label}", "PASS",
                f"{label} 出入流体积相对差 {fraction:.3%}（含 UH 尾部截断滞留）",
            ))
    total_in = float(np.sum(direct) + np.sum(base))
    total_out = float(np.sum(result["Q_total"]))
    _ = steps

    failed = [item for item in checks if item["status"] == "FAIL"]
    warned = [item for item in checks if item["status"] == "WARN"]
    status = "error" if failed else ("warning" if warned else "success")
    message = (
        f"Lohmann 汇流失败：{len(failed)} 项 QC FAIL"
        if failed
        else f"Lohmann 汇流完成：{steps} 步，出流总入比 {total_out / max(abs(total_in), 1e-9):.4f}"
    )

    table_name = f"routed_lohmann.{args.output_format}"
    table_path = args.output_dir / table_name
    write_table(result, table_path, args.output_format)

    uh_document = {
        "schema_version": "1.0",
        "skill": SKILL_REF,
        "parameters": {"N": parameters.n, "K": parameters.k, "VELO": parameters.velo, "DIFF": parameters.diff},
        "flowlen_m": args.flowlen_m,
        "uh_river_days": routed["uh_river"].tolist(),
        "uh_direct_steps": routed["uh_direct"].tolist(),
        "uh_base_steps": routed["uh_base"].tolist(),
        "uh_hru_direct_days": routed["uh_hru_direct"].tolist(),
    }
    uh_path = args.output_dir / "lohmann_unit_hydrographs.json"
    write_json(uh_path, uh_document)

    return {
        "schema_version": "1.0",
        "skill": SKILL_REF,
        "status": status,
        "message": message,
        "parameters": {
            "timestep_hours": args.timestep_hours,
            "flowlen_m": args.flowlen_m,
            "direct_column": args.direct_column,
            "base_column": args.base_column,
            "volume_tolerance": args.volume_tolerance,
            "route_parameters": raw_parameters,
        },
        "inputs": {
            "inflow": file_reference(inflow_path, args.output_dir),
            "route_params": file_reference(Path(args.route_params), args.output_dir),
        },
        "artifacts": {
            "routed_table": file_reference(table_path, args.output_dir),
            "unit_hydrographs": file_reference(uh_path, args.output_dir),
        },
        "checks": checks,
        "warnings": warnings,
        "provenance": provenance(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Route direct and base inflow series with the standard Lohmann channel routing.",
    )
    parser.add_argument("--inflow", required=True, help="入流表路径，须含 direct 与 base 两列")
    parser.add_argument("--inflow-format", choices=FORMATS)
    parser.add_argument("--sheet", help="xlsx 工作表名，默认第一个")
    parser.add_argument("--time-column", default="time")
    parser.add_argument("--direct-column", default="direct", help="直接径流入流列名")
    parser.add_argument("--base-column", default="base", help="基流入流列名")
    parser.add_argument("--timestep-hours", type=float, required=True)
    parser.add_argument("--flowlen-m", type=float, required=True, help="河长（m）；0 表示河道 UH 退化为脉冲")
    parser.add_argument("--route-params", required=True, help="路由参数 JSON 或 YAML（N/K/VELO/DIFF，全部必填）")
    parser.add_argument("--volume-tolerance", type=float, default=0.05)
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
                "schema_version": "1.0",
                "skill": SKILL_REF,
                "status": "error",
                "message": "科学 QC 失败，未生成演进结果",
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
