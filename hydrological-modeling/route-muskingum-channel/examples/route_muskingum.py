#!/usr/bin/env python3
"""Route inflow series with Muskingum: single reach, cascaded unit channels, and multi-source combination."""

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
from _muskingum_core import build_route_specs, route_cascade, route_single, validate_route_spec


SKILL_REF = "hydrological-modeling/route-muskingum-channel"
FORMATS = ("csv", "parquet", "xlsx")
EPS_SUM = 1e-12
DECLARED = [
    "routed_flow.csv",
    "routed_flow.parquet",
    "routed_flow.xlsx",
    "route_coefficients.json",
    "result.json",
]


class QualityControlFailure(RuntimeError):
    """Raised when a scientific QC gate fails."""

    def __init__(self, document: dict[str, Any]) -> None:
        super().__init__("科学 QC 失败")
        self.document = document


def run(args: argparse.Namespace) -> dict[str, Any]:
    raw_spec = read_mapping(Path(args.route_spec))
    raw_routes = raw_spec.get("routes")
    if raw_routes is None:
        raise ContractError("route-spec 必须包含 routes 数组")
    specs = build_route_specs(raw_routes)
    problems: list[str] = []
    for spec in specs:
        problems.extend(validate_route_spec(spec, args.timestep_hours))
    if problems:
        raise ContractError("路由配置校验失败: " + "; ".join(problems))

    path = Path(args.inflow)
    if not path.is_file():
        raise ContractError(f"入流文件不存在: {path}")
    fmt = args.inflow_format or path.suffix.lower().lstrip(".")
    frame = read_table(path, fmt, args.sheet)

    columns = [spec.column for spec in specs]
    require_columns(frame, [args.time_column, *columns], "入流表")
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

    missing_columns: list[str] = []
    series: dict[str, np.ndarray] = {}
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
        if np.isnan(values).any():
            missing_columns.append(column)
        series[column] = values
    if missing_columns:
        checks.append(check(
            "no_missing_inflow", "FAIL",
            f"入流列存在缺测: {', '.join(missing_columns)}；缺测必须由上游显式修复后重跑",
        ))
    else:
        checks.append(check("no_missing_inflow", "PASS", "入流列无缺测"))

    if not step_consistent or missing_columns:
        raise QualityControlFailure({"checks": checks, "warnings": warnings})

    output = pd.DataFrame({"time": time.to_numpy()})
    coefficients: dict[str, Any] = {}
    for spec in specs:
        inflow = series[spec.column]
        q0 = spec.q0 if spec.q0 is not None else (float(inflow[0]) if len(inflow) else 0.0)
        if spec.layout == "single":
            routed, info = route_single(inflow, spec.k, spec.x, args.timestep_hours, q0)
        else:
            routed, info = route_cascade(inflow, spec.reaches, spec.x, args.timestep_hours, q0, spec.output_level)

        coefficient_sum = abs(info["c0"] + info["c1"] + info["c2"] - 1.0)
        if coefficient_sum > 1e-9:
            checks.append(check(
                f"{spec.route_id}:coefficient_sum", "FAIL",
                f"C0+C1+C2 与 1 的偏差 {coefficient_sum:.3g} 超过 1e-9",
            ))
        else:
            checks.append(check(f"{spec.route_id}:coefficient_sum", "PASS", "C0+C1+C2 等于 1"))

        x_used = float(info["x_used"])
        if 0.0 <= x_used <= 0.5:
            checks.append(check(f"{spec.route_id}:stability_range", "PASS", f"X 等价取值 {x_used:.6g} 落在 [0, 0.5]"))
        else:
            checks.append(check(
                f"{spec.route_id}:stability_range", "WARN",
                f"X 等价取值 {x_used:.6g} 超出 [0, 0.5]；系数可能产生振荡或负出流，"
                f"请确认 reaches 与 X 的组合是否来自有依据的率定",
            ))
            warnings.append(f"{spec.route_id} 的 X 等价取值 {x_used:.6g} 超出经典稳定区间 [0, 0.5]")

        negatives = int(np.sum(routed < 0.0))
        if negatives:
            checks.append(check(f"{spec.route_id}:negative_outflow", "WARN", f"演进结果出现 {negatives} 个负值，已按源码行为裁剪到 0"))
            warnings.append(
                f"{spec.route_id} 出现 {negatives} 个负出流并已被裁剪为 0；"
                f"请检查 K、X 与 --timestep-hours 是否满足数值稳定条件"
            )
        else:
            checks.append(check(f"{spec.route_id}:negative_outflow", "PASS", "演进结果无负值"))

        routed = np.clip(routed, 0.0, None)
        if not np.all(np.isfinite(routed)):
            checks.append(check(f"{spec.route_id}:finite_outflow", "FAIL", "演进结果出现非有限值"))
        else:
            checks.append(check(f"{spec.route_id}:finite_outflow", "PASS", "演进结果均为有限值"))

        total_in = float(np.sum(inflow))
        if abs(total_in) > EPS_SUM:
            relative = abs(total_in - float(np.sum(routed))) / abs(total_in)
            if relative > args.volume_tolerance:
                checks.append(check(
                    f"{spec.route_id}:volume_difference", "WARN",
                    f"演进前后体积相对差 {relative:.3%} 超过容差 {args.volume_tolerance:.3%}",
                ))
                warnings.append(
                    f"{spec.route_id} 演进前后体积相对差 {relative:.3%} 超过容差；"
                    f"马斯京根存在河槽蓄量变化，请确认初值与末值蓄量是否需要单独说明"
                )
            else:
                checks.append(check(f"{spec.route_id}:volume_difference", "PASS", f"体积相对差 {relative:.3%}"))
        else:
            checks.append(check(f"{spec.route_id}:volume_difference", "WARN", "入流总量为 0，体积差不适用"))

        output[f"routed_{spec.route_id}"] = routed
        coefficients[spec.route_id] = {
            "layout": spec.layout,
            "column": spec.column,
            "k": spec.k,
            "x": spec.x,
            "reaches": spec.reaches,
            "output_level": spec.output_level,
            "q0": q0,
            "q0_source": "explicit" if spec.q0 is not None else "first-inflow",
            "c0": info["c0"],
            "c1": info["c1"],
            "c2": info["c2"],
            "x_used": x_used,
            "coefficients_clipped": bool(info["clipped"]),
            "negative_before_clip": negatives,
        }

    routed_columns = [column for column in output.columns if column.startswith("routed_")]
    if args.combine == "sum" and len(routed_columns) > 1:
        combined = np.zeros(len(output), dtype=float)
        for column in routed_columns:
            combined += output[column].to_numpy(dtype=float)
        output[args.output_column] = combined
        checks.append(check("multi_source_combination", "PASS", f"{len(routed_columns)} 条路由相加得到 {args.output_column}"))
    elif args.combine == "sum" and len(routed_columns) == 1:
        output[args.output_column] = output[routed_columns[0]].to_numpy(dtype=float)
        checks.append(check("multi_source_combination", "PASS", "单条路由，输出列等于该路由结果"))

    table_name = f"routed_flow.{args.output_format}"
    table_path = args.output_dir / table_name
    write_table(output, table_path, args.output_format)
    write_json(args.output_dir / "route_coefficients.json", coefficients)

    failed = [item for item in checks if item["status"] == "FAIL"]
    warned = [item for item in checks if item["status"] == "WARN"]
    status = "error" if failed else ("warning" if warned else "success")
    message = (
        f"马斯京根演进失败：{len(failed)} 项 QC FAIL"
        if failed
        else f"马斯京根演进完成：{len(specs)} 条路由，{len(output)} 行"
    )
    return {
        "schema_version": "1.0",
        "skill": SKILL_REF,
        "status": status,
        "message": message,
        "parameters": {
            "timestep_hours": args.timestep_hours,
            "combine": args.combine,
            "output_column": args.output_column,
            "volume_tolerance": args.volume_tolerance,
            "routes": [
                {
                    "id": spec.route_id,
                    "column": spec.column,
                    "layout": spec.layout,
                    "k": spec.k,
                    "x": spec.x,
                    "reaches": spec.reaches,
                    "output_level": spec.output_level,
                }
                for spec in specs
            ],
        },
        "inputs": {
            "inflow": file_reference(path, args.output_dir),
            "route_spec": file_reference(Path(args.route_spec), args.output_dir),
        },
        "artifacts": {
            "routed_table": file_reference(table_path, args.output_dir),
            "route_coefficients": file_reference(args.output_dir / "route_coefficients.json", args.output_dir),
        },
        "checks": checks,
        "warnings": warnings,
        "provenance": provenance(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Route inflow series with Muskingum and optionally combine multiple sources.",
    )
    parser.add_argument("--inflow", required=True, help="入流表路径，可含多条入流列")
    parser.add_argument("--inflow-format", choices=FORMATS)
    parser.add_argument("--sheet", help="xlsx 工作表名，默认第一个")
    parser.add_argument("--time-column", default="time")
    parser.add_argument("--timestep-hours", type=float, required=True)
    parser.add_argument("--route-spec", required=True, help="路由配置 JSON 或 YAML，含 routes 数组")
    parser.add_argument("--combine", choices=("sum", "none"), default="sum")
    parser.add_argument("--output-column", default="Q_total")
    parser.add_argument("--volume-tolerance", type=float, default=0.02)
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
