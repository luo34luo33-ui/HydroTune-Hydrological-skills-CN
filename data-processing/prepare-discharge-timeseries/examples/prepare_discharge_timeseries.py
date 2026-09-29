#!/usr/bin/env python3
"""Standardize a continuous discharge time series with explicit QC decisions."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from _timeseries_common import (
    ContractError, file_reference, prepare_output_dir, provenance, write_json,
    write_result, write_table,
)


SKILL_REF = "data-processing/prepare-discharge-timeseries"
FORMATS = ("csv", "parquet", "xlsx")
DECLARED = [
    "discharge_timeseries.csv", "discharge_timeseries.parquet", "discharge_timeseries.xlsx",
    "series_metadata.json", "result.json",
]
UNIT_SCALE = {"m3/s": 1.0, "L/s": 0.001, "cfs": 0.028316846592}


def parse_datetime_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce")
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().mean() > 0.95 and numeric.dropna().size:
        median = float(numeric.dropna().median())
        if 20_000 <= median <= 100_000:
            return pd.to_datetime(numeric, unit="D", origin="1899-12-30", errors="coerce")
    return pd.to_datetime(series, errors="coerce")


def read_source(path: Path, input_format: str, sheet: str | None) -> tuple[pd.DataFrame, str]:
    fmt = path.suffix.lower().lstrip(".") if input_format == "auto" else input_format
    if fmt not in FORMATS:
        raise ContractError(f"无法识别输入格式: {fmt}")
    if fmt == "xlsx":
        return pd.read_excel(path, sheet_name=sheet or 0), fmt
    if sheet is not None:
        raise ContractError("--sheet 只适用于 XLSX 输入")
    if fmt == "csv":
        return pd.read_csv(path, encoding="utf-8-sig"), fmt
    return pd.read_parquet(path), fmt


def infer_regular_timestep(time: pd.Series) -> tuple[float, float]:
    differences = time.diff().dt.total_seconds().dropna().to_numpy(dtype=float)
    if not len(differences):
        raise ContractError("无法计算时间步长")
    median = float(np.median(differences))
    if median <= 0:
        raise ContractError("时间必须严格递增")
    tolerance = max(1.0, 0.01 * median)
    regularity = float(np.mean(np.abs(differences - median) <= tolerance))
    if regularity < 1.0:
        raise ContractError(f"时间序列不规则，规则度={regularity:.3%}")
    return median, regularity


def _missing_runs(mask: pd.Series) -> list[tuple[int, int]]:
    values = mask.to_numpy(dtype=bool)
    runs = []
    start = None
    for index, missing in enumerate(values):
        if missing and start is None:
            start = index
        if start is not None and (not missing or index == len(values) - 1):
            end = index if missing and index == len(values) - 1 else index - 1
            runs.append((start, end))
            start = None
    return runs


def run(args: argparse.Namespace) -> dict:
    input_path = args.input.resolve()
    if not input_path.is_file():
        raise ContractError(f"输入文件不存在: {input_path}")
    source, detected_format = read_source(input_path, args.input_format, args.sheet)
    if args.time_column not in source.columns or args.flow_column not in source.columns:
        raise ContractError(f"缺少时间或流量列；当前列={list(source.columns)}")
    reserved = {"time", "discharge_m3_s", "is_imputed"}
    extras = [str(column) for column in source.columns if column not in {args.time_column, args.flow_column}]
    conflicts = reserved.intersection(extras)
    if conflicts:
        raise ContractError(f"附加列与标准列冲突: {sorted(conflicts)}")

    work = source.copy()
    work["__time"] = parse_datetime_series(work[args.time_column])
    work["__flow"] = pd.to_numeric(work[args.flow_column], errors="coerce")
    bad_time = int(work["__time"].isna().sum())
    if bad_time:
        raise ContractError(f"存在 {bad_time} 个无法解析的时间值")
    work = work.sort_values("__time", kind="mergesort").reset_index(drop=True)
    duplicate_count = int(work["__time"].duplicated(keep=False).sum())
    warnings: list[str] = []
    checks = []
    if duplicate_count:
        if args.duplicate_policy == "error":
            raise ContractError(f"发现 {duplicate_count} 行重复时间")
        keep = "first" if args.duplicate_policy == "first" else "last"
        work = work.drop_duplicates("__time", keep=keep).reset_index(drop=True)
        warnings.append(f"duplicate timestamps resolved with policy={keep}; affected_rows={duplicate_count}")
        checks.append({"check": "duplicate_timestamps", "status": "WARN", "details": warnings[-1]})
    else:
        checks.append({"check": "duplicate_timestamps", "status": "PASS", "details": "count=0"})

    timestep_seconds, regularity = infer_regular_timestep(work["__time"])
    flow = work["__flow"].copy()
    if flow.notna().sum() == 0:
        raise ContractError("流量列没有有效值")
    first_valid = int(np.flatnonzero(flow.notna().to_numpy())[0])
    last_valid = int(np.flatnonzero(flow.notna().to_numpy())[-1])
    edge_missing = first_valid + (len(flow) - 1 - last_valid)
    if edge_missing:
        if args.edge_missing == "error":
            raise ContractError(f"序列首尾存在 {edge_missing} 个缺测；禁止外推")
        work = work.iloc[first_valid:last_valid + 1].reset_index(drop=True)
        flow = work["__flow"].copy()
        warnings.append(f"trimmed edge missing rows={edge_missing}")
        checks.append({"check": "edge_missing", "status": "WARN", "details": warnings[-1]})
    else:
        checks.append({"check": "edge_missing", "status": "PASS", "details": "count=0"})

    imputed = pd.Series(False, index=work.index)
    interior_missing = int(flow.isna().sum())
    if interior_missing:
        if args.missing_policy == "error":
            raise ContractError(f"存在 {interior_missing} 个内部流量缺测")
        if args.max_interpolation_gap_hours is None or args.max_interpolation_gap_hours <= 0:
            raise ContractError("插值需要正数 --max-interpolation-gap-hours")
        maximum_steps = args.max_interpolation_gap_hours * 3600.0 / timestep_seconds
        too_long = [(start, end) for start, end in _missing_runs(flow.isna()) if end - start + 1 > maximum_steps + 1e-9]
        if too_long:
            raise ContractError(f"内部缺口超过允许长度: {too_long}")
        imputed = flow.isna()
        flow = flow.interpolate(method="linear", limit_area="inside")
        if flow.isna().any():
            raise ContractError("内部插值后仍存在缺测")
        warnings.append(f"interpolated interior discharge values={interior_missing}")
        checks.append({"check": "interior_missing", "status": "WARN", "details": warnings[-1]})
    else:
        checks.append({"check": "interior_missing", "status": "PASS", "details": "count=0"})

    if (flow < 0).any():
        raise ContractError("发现负流量")
    if len(work) < 10:
        raise ContractError("有效数据点少于 10 个")

    time = work["__time"].copy()
    if args.timezone != "naive":
        try:
            zone = ZoneInfo(args.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ContractError(f"无效 IANA 时区: {args.timezone}") from exc
        if getattr(time.dt, "tz", None) is None:
            time = time.dt.tz_localize(zone, ambiguous="raise", nonexistent="raise")
        else:
            time = time.dt.tz_convert(zone)
    else:
        if getattr(time.dt, "tz", None) is not None:
            time = time.dt.tz_localize(None)
        warnings.append("timezone explicitly recorded as naive")
        checks.append({"check": "timezone", "status": "WARN", "details": warnings[-1]})

    output = pd.DataFrame({
        "time": time,
        "discharge_m3_s": flow.to_numpy(dtype=float) * UNIT_SCALE[args.flow_unit],
        "is_imputed": imputed.to_numpy(dtype=bool),
    })
    for column in source.columns:
        if column in {args.time_column, args.flow_column}:
            continue
        output[str(column)] = work[column].to_numpy()

    output_path = args.output_dir / f"discharge_timeseries.{args.output_format}"
    metadata_path = args.output_dir / "series_metadata.json"
    write_table(output, output_path, args.output_format, "discharge_timeseries")
    metadata = {
        "schema_version": "1.0", "time_column": args.time_column, "flow_column": args.flow_column,
        "source_format": detected_format, "source_flow_unit": args.flow_unit,
        "standard_flow_unit": "m3/s", "unit_scale": UNIT_SCALE[args.flow_unit],
        "timezone": args.timezone, "timestep_seconds": timestep_seconds,
        "time_regularity": regularity, "row_count": len(output),
        "start_time": output["time"].iloc[0], "end_time": output["time"].iloc[-1],
        "additional_columns": extras, "additional_column_semantics": "unconfirmed",
    }
    write_json(metadata_path, metadata)
    checks.extend([
        {"check": "regular_timestep", "status": "PASS", "details": f"seconds={timestep_seconds:g}"},
        {"check": "nonnegative_discharge", "status": "PASS", "details": f"rows={len(output)}"},
    ])
    status = "warning" if warnings else "success"
    return {
        "schema_version": "1.0", "skill": SKILL_REF, "status": status,
        "message": "discharge time series prepared" if status == "success" else "discharge time series prepared with warnings",
        "parameters": {key: value for key, value in vars(args).items() if key not in {"input", "output_dir"}},
        "inputs": {"source_timeseries": file_reference(input_path)},
        "artifacts": {"discharge_timeseries": file_reference(output_path, args.output_dir), "series_metadata": file_reference(metadata_path, args.output_dir)},
        "checks": checks, "warnings": warnings, "provenance": provenance(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--input-format", choices=("auto", *FORMATS), default="auto")
    parser.add_argument("--sheet")
    parser.add_argument("--time-column", required=True)
    parser.add_argument("--flow-column", required=True)
    parser.add_argument("--flow-unit", choices=tuple(UNIT_SCALE), required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--missing-policy", choices=("error", "interpolate"), default="error")
    parser.add_argument("--max-interpolation-gap-hours", type=float)
    parser.add_argument("--edge-missing", choices=("error", "trim"), default="error")
    parser.add_argument("--duplicate-policy", choices=("error", "first", "last"), default="error")
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
