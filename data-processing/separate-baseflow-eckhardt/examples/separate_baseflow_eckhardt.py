#!/usr/bin/env python3
"""Separate discharge into Eckhardt baseflow and quickflow components."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from _timeseries_common import (
    ContractError, artifact_path, file_reference, load_result, prepare_output_dir,
    provenance, read_table, write_json, write_result, write_table,
)


SKILL_REF = "data-processing/separate-baseflow-eckhardt"
UPSTREAM_SKILL = "data-processing/prepare-discharge-timeseries"
FORMATS = ("csv", "parquet", "xlsx")
DECLARED = ["flow_components.csv", "flow_components.parquet", "flow_components.xlsx", "baseflow_metadata.json", "result.json"]


def estimate_recession_alpha(discharge: np.ndarray) -> tuple[float, int]:
    previous = discharge[:-1]
    current = discharge[1:]
    mask = np.isfinite(previous) & np.isfinite(current) & (previous > 0) & (current > 0) & (current < previous)
    ratios = current[mask] / previous[mask]
    ratios = ratios[(ratios > 0) & (ratios < 1)]
    if len(ratios) < 20:
        raise ContractError(f"自动 alpha 的有效递减流量对不足 20 个: {len(ratios)}")
    raw = float(np.quantile(ratios, 0.90))
    return float(np.clip(raw, 0.80, 0.9999)), int(len(ratios))


def eckhardt_baseflow(discharge: np.ndarray, alpha: float, bfi_max: float) -> np.ndarray:
    if not 0 < alpha < 1:
        raise ContractError("alpha 必须属于 (0, 1)")
    if not 0 < bfi_max < 1:
        raise ContractError("bfi-max 必须属于 (0, 1)")
    baseflow = np.empty_like(discharge, dtype=float)
    baseflow[0] = discharge[0]
    denominator = 1.0 - alpha * bfi_max
    for index in range(1, len(discharge)):
        value = ((1.0 - bfi_max) * alpha * baseflow[index - 1] + (1.0 - alpha) * bfi_max * discharge[index]) / denominator
        baseflow[index] = min(max(value, 0.0), discharge[index])
    return baseflow


def run(args: argparse.Namespace) -> dict:
    result_path = args.prepare_result.resolve()
    upstream = load_result(result_path, UPSTREAM_SKILL)
    table_path = artifact_path(upstream, result_path, "discharge_timeseries")
    metadata_path = artifact_path(upstream, result_path, "series_metadata")
    frame = read_table(table_path)
    if not {"time", "discharge_m3_s"}.issubset(frame.columns):
        raise ContractError("标准时序缺少 time 或 discharge_m3_s")
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce", utc=False)
    discharge = pd.to_numeric(frame["discharge_m3_s"], errors="coerce").to_numpy(dtype=float)
    if len(discharge) < 2 or not np.isfinite(discharge).all() or (discharge < 0).any():
        raise ContractError("流量必须是至少两个有限非负值")
    if args.estimate_alpha:
        alpha, recession_pairs = estimate_recession_alpha(discharge)
        alpha_decision = "estimated-90th-quantile-of-recession-ratios"
    else:
        alpha = float(args.alpha)
        recession_pairs = 0
        alpha_decision = "user-provided"
    baseflow = eckhardt_baseflow(discharge, alpha, args.bfi_max)
    quickflow = np.maximum(discharge - baseflow, 0.0)
    ratio = np.divide(quickflow, discharge, out=np.zeros_like(quickflow), where=discharge > 0)
    balance_error = float(np.max(np.abs(discharge - baseflow - quickflow)))
    tolerance = max(1e-10, float(np.max(discharge)) * 1e-12)
    if balance_error > tolerance:
        raise ContractError(f"流量分量守恒失败: max_error={balance_error}")
    output = frame.copy()
    output["baseflow_m3_s"] = baseflow
    output["quickflow_m3_s"] = quickflow
    output["quickflow_fraction"] = ratio
    output_path = args.output_dir / f"flow_components.{args.output_format}"
    metadata_output = args.output_dir / "baseflow_metadata.json"
    write_table(output, output_path, args.output_format, "flow_components")
    write_json(metadata_output, {
        "schema_version": "1.0", "method": "Eckhardt digital filter", "alpha": alpha,
        "alpha_decision": alpha_decision, "recession_pair_count": recession_pairs,
        "bfi_max": args.bfi_max, "flow_unit": "m3/s", "row_count": len(output),
        "upstream_series_metadata": file_reference(metadata_path),
    })
    warnings = list(upstream.get("warnings", []))
    status = "warning" if warnings else "success"
    return {
        "schema_version": "1.0", "skill": SKILL_REF, "status": status,
        "message": "Eckhardt baseflow separated" if status == "success" else "Eckhardt baseflow separated with upstream warnings",
        "parameters": {"bfi_max": args.bfi_max, "alpha": alpha, "alpha_decision": alpha_decision, "output_format": args.output_format},
        "inputs": {"prepare_result": file_reference(result_path)},
        "artifacts": {"flow_components": file_reference(output_path, args.output_dir), "baseflow_metadata": file_reference(metadata_output, args.output_dir)},
        "checks": [
            {"check": "component_bounds", "status": "PASS", "details": "0 <= baseflow <= discharge and quickflow >= 0"},
            {"check": "component_balance", "status": "PASS", "details": f"max_absolute_error={balance_error:.6g}"},
            {"check": "finite_components", "status": "PASS", "details": f"rows={len(output)}"},
        ],
        "warnings": warnings, "provenance": provenance(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-result", type=Path, required=True)
    parser.add_argument("--bfi-max", type=float, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--alpha", type=float)
    group.add_argument("--estimate-alpha", action="store_true")
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
            write_result(args.output_dir, {"schema_version": "1.0", "skill": SKILL_REF, "status": "error", "message": str(exc), "parameters": {}, "inputs": {}, "artifacts": {}, "checks": [], "warnings": [], "provenance": provenance()})
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
