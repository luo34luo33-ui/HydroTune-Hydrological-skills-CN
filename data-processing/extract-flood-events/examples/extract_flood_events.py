#!/usr/bin/env python3
"""Extract and validate flood events from verified flow components."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np
import pandas as pd
from scipy.signal import find_peaks
import yaml

from _timeseries_common import (
    ContractError, artifact_path, file_reference, load_result, prepare_output_dir,
    provenance, read_table, write_json, write_result, write_table,
)


SKILL_REF = "data-processing/extract-flood-events"
UPSTREAM_SKILL = "data-processing/separate-baseflow-eckhardt"
FORMATS = ("csv", "parquet", "xlsx")
DECLARED = [
    "event_summary.csv", "event_summary.parquet", "event_process.csv", "event_process.parquet",
    "annotated_series.csv", "annotated_series.parquet", "flood_event_dataset.xlsx",
    "event_config.yaml", "event_manifest.json", "event_qc.csv", "result.json",
]


class ScientificQCError(ContractError):
    """Raised when calculated event products fail scientific QC."""


@dataclass(frozen=True)
class EventConfig:
    peak_quantile: float
    prominence_factor: float
    min_peak_distance_hours: float
    boundary_fraction: float
    boundary_persistence_steps: int
    max_search_days: float
    merge_gap_hours: float
    valley_ratio_threshold: float
    min_event_duration_hours: float
    min_peak_flow_m3_s: float | None
    min_event_volume_m3: float | None
    warmup_steps: int


def load_config(path: Path) -> tuple[EventConfig, dict]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(f"无法读取事件配置: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise ContractError("事件配置 schema_version 必须为 1.0")
    expected_top = {"schema_version", "peak_detection", "boundaries", "merging", "filters", "warmup"}
    if set(raw) != expected_top:
        raise ContractError(f"事件配置顶层字段必须精确为 {sorted(expected_top)}")
    sections = {
        "peak_detection": {"peak_quantile", "prominence_factor", "min_peak_distance_hours"},
        "boundaries": {"boundary_fraction", "boundary_persistence_steps", "max_search_days"},
        "merging": {"merge_gap_hours", "valley_ratio_threshold"},
        "filters": {"min_event_duration_hours", "min_peak_flow_m3_s", "min_event_volume_m3"},
        "warmup": {"steps"},
    }
    for section, fields in sections.items():
        if not isinstance(raw.get(section), dict) or set(raw[section]) != fields:
            raise ContractError(f"配置 {section} 字段必须精确为 {sorted(fields)}")
    p, b, m, f, w = (raw[name] for name in ("peak_detection", "boundaries", "merging", "filters", "warmup"))
    cfg = EventConfig(
        float(p["peak_quantile"]), float(p["prominence_factor"]), float(p["min_peak_distance_hours"]),
        float(b["boundary_fraction"]), int(b["boundary_persistence_steps"]), float(b["max_search_days"]),
        float(m["merge_gap_hours"]), float(m["valley_ratio_threshold"]),
        float(f["min_event_duration_hours"]), None if f["min_peak_flow_m3_s"] is None else float(f["min_peak_flow_m3_s"]),
        None if f["min_event_volume_m3"] is None else float(f["min_event_volume_m3"]), int(w["steps"]),
    )
    if not 0 < cfg.peak_quantile < 1 or cfg.prominence_factor < 0 or cfg.min_peak_distance_hours <= 0:
        raise ContractError("洪峰识别参数越界")
    if not 0 <= cfg.boundary_fraction <= 1 or cfg.boundary_persistence_steps < 1 or cfg.max_search_days <= 0:
        raise ContractError("边界参数越界")
    if cfg.merge_gap_hours < 0 or not 0 <= cfg.valley_ratio_threshold <= 1:
        raise ContractError("合并参数越界")
    if cfg.min_event_duration_hours < 0 or cfg.warmup_steps < 0:
        raise ContractError("历时或 warm-up 参数越界")
    if cfg.min_peak_flow_m3_s is not None and cfg.min_peak_flow_m3_s <= 0:
        raise ContractError("min_peak_flow_m3_s 必须为正数或 null")
    if cfg.min_event_volume_m3 is not None and cfg.min_event_volume_m3 <= 0:
        raise ContractError("min_event_volume_m3 必须为正数或 null")
    return cfg, raw


def detect_peaks(quickflow: np.ndarray, dt_seconds: float, cfg: EventConfig) -> np.ndarray:
    positive = quickflow[quickflow > 0]
    if not len(positive):
        return np.array([], dtype=int)
    threshold = float(np.quantile(positive, cfg.peak_quantile))
    prominence = max(np.finfo(float).eps, cfg.prominence_factor * float(np.nanstd(quickflow)))
    distance = max(1, int(round(cfg.min_peak_distance_hours * 3600 / dt_seconds)))
    peaks, _ = find_peaks(quickflow, height=threshold, prominence=prominence, distance=distance)
    return peaks.astype(int)


def _left_boundary(peak: int, values: np.ndarray, persistence: int, fraction: float, max_steps: int) -> int:
    limit = max(0, peak - max_steps)
    segment = values[limit:peak + 1]
    baseline = float(np.nanmin(segment))
    threshold = baseline + fraction * max(float(values[peak]) - baseline, 0.0)
    for index in range(peak - 1, limit + persistence - 2, -1):
        start = index - persistence + 1
        if start < limit:
            break
        if np.all(values[start:index + 1] <= threshold):
            return index
    return limit + int(np.nanargmin(segment))


def _right_boundary(peak: int, values: np.ndarray, persistence: int, fraction: float, max_steps: int) -> int:
    limit = min(len(values) - 1, peak + max_steps)
    segment = values[peak:limit + 1]
    baseline = float(np.nanmin(segment))
    threshold = baseline + fraction * max(float(values[peak]) - baseline, 0.0)
    for index in range(peak + 1, limit - persistence + 2):
        block = values[index:index + persistence]
        if len(block) == persistence and np.all(block <= threshold):
            return index
    return peak + int(np.nanargmin(segment))


def build_raw_events(peaks: np.ndarray, discharge: np.ndarray, dt_seconds: float, cfg: EventConfig) -> list[dict]:
    maximum = max(1, int(round(cfg.max_search_days * 86400 / dt_seconds)))
    events = []
    for peak in peaks:
        start = _left_boundary(int(peak), discharge, cfg.boundary_persistence_steps, cfg.boundary_fraction, maximum)
        end = _right_boundary(int(peak), discharge, cfg.boundary_persistence_steps, cfg.boundary_fraction, maximum)
        if start <= peak <= end:
            events.append({"start_idx": int(start), "peak_idx": int(peak), "end_idx": int(end)})
    return events


def merge_events(events: list[dict], discharge: np.ndarray, dt_seconds: float, cfg: EventConfig) -> list[dict]:
    if not events:
        return []
    merged = [sorted(events, key=lambda item: item["start_idx"])[0].copy()]
    maximum_gap = max(0, int(round(cfg.merge_gap_hours * 3600 / dt_seconds)))
    for current in sorted(events, key=lambda item: item["start_idx"])[1:]:
        previous = merged[-1]
        overlap = current["start_idx"] <= previous["end_idx"]
        gap = max(0, current["start_idx"] - previous["end_idx"] - 1)
        valley_merge = False
        if not overlap and gap <= maximum_gap:
            left, right = sorted((previous["peak_idx"], current["peak_idx"]))
            valley = float(np.min(discharge[left:right + 1]))
            smaller_peak = float(min(discharge[previous["peak_idx"]], discharge[current["peak_idx"]]))
            valley_merge = smaller_peak > 0 and valley / smaller_peak >= cfg.valley_ratio_threshold
        if overlap or valley_merge:
            start = min(previous["start_idx"], current["start_idx"])
            end = max(previous["end_idx"], current["end_idx"])
            peak = start + int(np.argmax(discharge[start:end + 1]))
            merged[-1] = {"start_idx": start, "peak_idx": peak, "end_idx": end}
        else:
            merged.append(current.copy())
    return merged


def refine_events(events: list[dict], quickflow: np.ndarray, dt_seconds: float, cfg: EventConfig) -> list[dict]:
    maximum = max(1, int(round(cfg.max_search_days * 86400 / dt_seconds)))
    return [{
        "start_idx": _left_boundary(item["peak_idx"], quickflow, cfg.boundary_persistence_steps, cfg.boundary_fraction, maximum),
        "peak_idx": item["peak_idx"],
        "end_idx": _right_boundary(item["peak_idx"], quickflow, cfg.boundary_persistence_steps, cfg.boundary_fraction, maximum),
    } for item in events]


def event_products(frame: pd.DataFrame, events: list[dict], dt_seconds: float, cfg: EventConfig):
    discharge = frame["discharge_m3_s"].to_numpy(dtype=float)
    baseflow = frame["baseflow_m3_s"].to_numpy(dtype=float)
    quickflow = frame["quickflow_m3_s"].to_numpy(dtype=float)
    event_ids = np.full(len(frame), np.nan)
    summaries, processes = [], []
    event_id = 0
    for event in sorted(events, key=lambda item: (item["start_idx"], item["peak_idx"])):
        start, peak, end = event["start_idx"], event["peak_idx"], event["end_idx"]
        duration = (end - start) * dt_seconds / 3600.0
        integrate = np.trapezoid if hasattr(np, "trapezoid") else np.trapz
        total_volume = float(integrate(discharge[start:end + 1], dx=dt_seconds))
        quick_volume = float(integrate(quickflow[start:end + 1], dx=dt_seconds))
        base_volume = float(integrate(baseflow[start:end + 1], dx=dt_seconds))
        if duration < cfg.min_event_duration_hours:
            continue
        if cfg.min_peak_flow_m3_s is not None and discharge[peak] < cfg.min_peak_flow_m3_s:
            continue
        if cfg.min_event_volume_m3 is not None and total_volume < cfg.min_event_volume_m3:
            continue
        event_id += 1
        event_ids[start:end + 1] = event_id
        export_start = max(0, start - cfg.warmup_steps)
        summaries.append({
            "event_id": event_id, "warmup_start_time": frame.loc[export_start, "time"],
            "start_time": frame.loc[start, "time"], "peak_time": frame.loc[peak, "time"], "end_time": frame.loc[end, "time"],
            "warmup_start_idx": export_start, "start_idx": start, "peak_idx": peak, "end_idx": end,
            "start_flow_m3_s": discharge[start], "peak_flow_m3_s": discharge[peak], "end_flow_m3_s": discharge[end],
            "duration_hours": duration, "rise_time_hours": (peak - start) * dt_seconds / 3600.0,
            "recession_time_hours": (end - peak) * dt_seconds / 3600.0,
            "total_volume_m3": total_volume, "quickflow_volume_m3": quick_volume, "baseflow_volume_m3": base_volume,
            "quickflow_fraction": quick_volume / total_volume if total_volume > 0 else np.nan,
            "n_local_peaks": int(len(find_peaks(discharge[start:end + 1])[0])),
        })
        part = frame.iloc[export_start:end + 1].copy()
        part.insert(0, "event_id", event_id)
        indices = np.arange(export_start, end + 1)
        part["source_index"] = indices
        part["is_warmup"] = indices < start
        part["relative_step"] = indices - start
        part["hours_from_start"] = (indices - start) * dt_seconds / 3600.0
        processes.append(part)
    summary = pd.DataFrame(summaries)
    if summary.empty:
        summary = pd.DataFrame(columns=[
            "event_id", "warmup_start_time", "start_time", "peak_time", "end_time", "warmup_start_idx",
            "start_idx", "peak_idx", "end_idx", "start_flow_m3_s", "peak_flow_m3_s", "end_flow_m3_s",
            "duration_hours", "rise_time_hours", "recession_time_hours", "total_volume_m3",
            "quickflow_volume_m3", "baseflow_volume_m3", "quickflow_fraction", "n_local_peaks",
        ])
    process = pd.concat(processes, ignore_index=True) if processes else pd.DataFrame(columns=["event_id", *frame.columns, "source_index", "is_warmup", "relative_step", "hours_from_start"])
    annotated = frame.copy()
    annotated["event_id"] = pd.array(event_ids, dtype="Int64")
    return summary, process, annotated


def qc_events(summary: pd.DataFrame, process: pd.DataFrame, annotated: pd.DataFrame, cfg: EventConfig) -> list[dict]:
    checks: list[dict] = []
    ids = summary["event_id"].astype(int).tolist() if len(summary) else []
    identity_failures = []
    if ids != list(range(1, len(ids) + 1)):
        identity_failures.append("event IDs are not stable consecutive integers")
    starts = summary["start_idx"].astype(int).tolist() if len(summary) else []
    if starts != sorted(starts):
        identity_failures.append("events are not sorted by start index")
    checks.append({
        "check": "event_identity_and_order",
        "status": "FAIL" if identity_failures else "PASS",
        "details": " | ".join(identity_failures) if identity_failures else f"events={len(ids)}; stable chronological IDs",
    })

    boundary_failures = []
    threshold_failures = []
    balance_failures = []
    table_failures = []
    previous_end = -1
    for row in summary.itertuples():
        if not (0 <= int(row.start_idx) <= int(row.peak_idx) <= int(row.end_idx) < len(annotated)):
            boundary_failures.append(f"event {row.event_id} has invalid boundaries")
            continue
        if int(row.start_idx) <= previous_end:
            boundary_failures.append(f"event {row.event_id} overlaps previous event")
        previous_end = int(row.end_idx)
        for label, index, timestamp in (
            ("start", int(row.start_idx), row.start_time),
            ("peak", int(row.peak_idx), row.peak_time),
            ("end", int(row.end_idx), row.end_time),
        ):
            if pd.Timestamp(timestamp) != pd.Timestamp(annotated.loc[index, "time"]):
                boundary_failures.append(f"event {row.event_id} {label} time/index mismatch")
        expected_peak = float(annotated.loc[int(row.peak_idx), "discharge_m3_s"])
        if not math.isclose(float(row.peak_flow_m3_s), expected_peak, rel_tol=1e-12, abs_tol=1e-9):
            boundary_failures.append(f"event {row.event_id} peak value/index mismatch")
        if float(row.duration_hours) < cfg.min_event_duration_hours:
            threshold_failures.append(f"event {row.event_id} duration below configured minimum")
        if cfg.min_peak_flow_m3_s is not None and float(row.peak_flow_m3_s) < cfg.min_peak_flow_m3_s:
            threshold_failures.append(f"event {row.event_id} peak below configured minimum")
        if cfg.min_event_volume_m3 is not None and float(row.total_volume_m3) < cfg.min_event_volume_m3:
            threshold_failures.append(f"event {row.event_id} volume below configured minimum")
        balance = abs(float(row.total_volume_m3) - float(row.baseflow_volume_m3) - float(row.quickflow_volume_m3))
        if balance > max(1e-6, abs(float(row.total_volume_m3)) * 1e-9):
            balance_failures.append(f"event {row.event_id} volume balance failed")
        event_rows = process[process["event_id"] == int(row.event_id)]
        scored = event_rows[~event_rows["is_warmup"].astype(bool)]
        if len(scored) != int(row.end_idx) - int(row.start_idx) + 1:
            table_failures.append(f"event {row.event_id} process coverage mismatch")
        if not scored.empty:
            source_indices = scored["source_index"].astype(int).tolist()
            if source_indices != list(range(int(row.start_idx), int(row.end_idx) + 1)):
                table_failures.append(f"event {row.event_id} process indices mismatch")
            marked = annotated.loc[int(row.start_idx):int(row.end_idx), "event_id"].dropna().astype(int)
            if len(marked) != int(row.end_idx) - int(row.start_idx) + 1 or not (marked == int(row.event_id)).all():
                table_failures.append(f"event {row.event_id} annotated series mismatch")

    for name, failures, success in (
        ("event_boundaries", boundary_failures, "boundaries, times and peaks are consistent"),
        ("configured_filters", threshold_failures, "all retained events satisfy explicit filters"),
        ("volume_balance", balance_failures, "total = baseflow + quickflow within tolerance"),
        ("table_consistency", table_failures, f"summary={len(summary)}, process={len(process)}, series={len(annotated)}"),
    ):
        checks.append({"check": name, "status": "FAIL" if failures else "PASS", "details": " | ".join(failures) if failures else success})
    checks.append({"check": "warmup_exclusion", "status": "PASS", "details": "warm-up rows are flagged and excluded from event metrics"})
    return checks


def _write_main_tables(summary, process, annotated, output_dir: Path, output_format: str) -> dict[str, Path]:
    if output_format == "xlsx":
        path = output_dir / "flood_event_dataset.xlsx"
        for frame in (summary, process, annotated):
            if len(frame) + 1 > 1_048_576:
                raise ContractError("数据超过 Excel 工作表行数限制")
        # Excel has no timezone-aware datetime type. Preserve the exact instant and
        # offset as ISO 8601 text instead of silently stripping timezone metadata.
        def excel_safe(frame: pd.DataFrame) -> pd.DataFrame:
            safe = frame.copy()
            for column in safe.columns:
                dtype = safe[column].dtype
                if isinstance(dtype, pd.DatetimeTZDtype):
                    safe[column] = safe[column].map(
                        lambda value: value.isoformat() if pd.notna(value) else ""
                    )
            return safe

        with pd.ExcelWriter(path, engine="xlsxwriter", datetime_format="yyyy-mm-dd hh:mm:ss") as writer:
            excel_safe(summary).to_excel(writer, sheet_name="event_summary", index=False)
            excel_safe(process).to_excel(writer, sheet_name="event_process", index=False)
            excel_safe(annotated).to_excel(writer, sheet_name="annotated_series", index=False)
        return {"event_summary": path, "event_process": path, "annotated_series": path}
    paths = {
        "event_summary": output_dir / f"event_summary.{output_format}",
        "event_process": output_dir / f"event_process.{output_format}",
        "annotated_series": output_dir / f"annotated_series.{output_format}",
    }
    write_table(summary, paths["event_summary"], output_format, "event_summary")
    write_table(process, paths["event_process"], output_format, "event_process")
    write_table(annotated, paths["annotated_series"], output_format, "annotated_series")
    return paths


def _write_per_event(process, summary, output_dir: Path, output_format: str, filename_by: str) -> list[dict]:
    if output_format == "none":
        return []
    event_dir = output_dir / "event_files"
    event_dir.mkdir()
    counts: dict[str, int] = {}
    entries = []
    for row in summary.itertuples():
        event_id = int(row.event_id)
        part = process[process["event_id"] == event_id]
        base = f"event_{event_id:04d}" if filename_by == "event-id" else pd.Timestamp(row.peak_time).strftime("%Y%m%d")
        counts[base] = counts.get(base, 0) + 1
        suffix = "" if counts[base] == 1 else f"_{counts[base]:02d}"
        path = event_dir / f"{base}{suffix}.{output_format}"
        write_table(part, path, output_format, "event_process")
        entries.append({"event_id": event_id, **file_reference(path, output_dir)})
    return entries


def run(args: argparse.Namespace) -> dict:
    result_path = args.baseflow_result.resolve()
    config_path = args.config.resolve()
    upstream = load_result(result_path, UPSTREAM_SKILL)
    components_path = artifact_path(upstream, result_path, "flow_components")
    artifact_path(upstream, result_path, "baseflow_metadata")
    cfg, raw_config = load_config(config_path)
    frame = read_table(components_path)
    required = {"time", "discharge_m3_s", "baseflow_m3_s", "quickflow_m3_s", "quickflow_fraction"}
    if not required.issubset(frame.columns):
        raise ContractError(f"流量分量表缺少字段: {sorted(required - set(frame.columns))}")
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce", utc=False)
    if frame["time"].isna().any():
        raise ContractError("流量分量表存在无效时间")
    differences = frame["time"].diff().dt.total_seconds().dropna().to_numpy(dtype=float)
    dt_seconds = float(np.median(differences))
    if dt_seconds <= 0 or not np.allclose(differences, dt_seconds, atol=max(1.0, 0.01 * dt_seconds), rtol=0):
        raise ContractError("事件提取要求规则、严格递增的时间序列")
    for column in ("discharge_m3_s", "baseflow_m3_s", "quickflow_m3_s"):
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any() or (values < 0).any():
            raise ContractError(f"{column} 必须为有限非负值")
        frame[column] = values
    discharge = frame["discharge_m3_s"].to_numpy(dtype=float)
    quickflow = frame["quickflow_m3_s"].to_numpy(dtype=float)
    peaks = detect_peaks(quickflow, dt_seconds, cfg)
    raw_events = build_raw_events(peaks, discharge, dt_seconds, cfg)
    merged = merge_events(raw_events, discharge, dt_seconds, cfg)
    refined = refine_events(merged, quickflow, dt_seconds, cfg)
    summary, process, annotated = event_products(frame, refined, dt_seconds, cfg)
    checks = qc_events(summary, process, annotated, cfg)
    if any(check["status"] == "FAIL" for check in checks):
        raise ScientificQCError(next(check["details"] for check in checks if check["status"] == "FAIL"))

    main_paths = _write_main_tables(summary, process, annotated, args.output_dir, args.output_format)
    event_dir = args.output_dir / "event_files"
    if args.overwrite and event_dir.exists():
        resolved = event_dir.resolve()
        if resolved.parent != args.output_dir.resolve():
            raise ContractError("拒绝清理输出目录之外的 event_files")
        shutil.rmtree(resolved)
    per_event = _write_per_event(process, summary, args.output_dir, args.per_event_format, args.event_filename_by)
    expected_per_event = 0 if args.per_event_format == "none" else len(summary)
    file_count_status = "PASS" if len(per_event) == expected_per_event else "FAIL"
    checks.append({"check": "per_event_file_count", "status": file_count_status, "details": f"expected={expected_per_event}; actual={len(per_event)}"})
    if file_count_status == "FAIL":
        raise ScientificQCError(checks[-1]["details"])
    config_output = args.output_dir / "event_config.yaml"
    config_output.write_text(yaml.safe_dump(raw_config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    manifest_output = args.output_dir / "event_manifest.json"
    write_json(manifest_output, {"schema_version": "1.0", "event_count": len(summary), "files": per_event})
    warnings = list(upstream.get("warnings", []))
    if len(summary) == 0:
        warnings.append("no flood events satisfied the explicit configuration")
        checks.append({"check": "event_count", "status": "WARN", "details": warnings[-1]})
    else:
        checks.append({"check": "event_count", "status": "PASS", "details": f"events={len(summary)}"})
    qc_output = args.output_dir / "event_qc.csv"
    pd.DataFrame(checks).to_csv(qc_output, index=False, encoding="utf-8-sig")
    artifacts = {key: file_reference(path, args.output_dir) for key, path in main_paths.items()}
    artifacts.update({"event_config": file_reference(config_output, args.output_dir), "event_manifest": file_reference(manifest_output, args.output_dir), "event_qc": file_reference(qc_output, args.output_dir)})
    status = "warning" if warnings else "success"
    return {
        "schema_version": "1.0", "skill": SKILL_REF, "status": status,
        "message": f"extracted {len(summary)} flood events",
        "parameters": {"output_format": args.output_format, "per_event_format": args.per_event_format, "event_filename_by": args.event_filename_by, "timestep_seconds": dt_seconds, "candidate_peak_count": len(peaks), "raw_event_count": len(raw_events), "merged_event_count": len(merged), "refined_event_count": len(refined), "final_event_count": len(summary)},
        "inputs": {"baseflow_result": file_reference(result_path), "event_config": file_reference(config_path)},
        "artifacts": artifacts, "checks": checks, "warnings": warnings,
        "provenance": {**provenance(), "scipy": __import__("scipy").__version__, "event_method": "quickflow-peaks/discharge-boundaries/valley-merge/quickflow-refine"},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseflow-result", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-format", choices=FORMATS, default="csv")
    parser.add_argument("--per-event-format", choices=("none", *FORMATS), default="csv")
    parser.add_argument("--event-filename-by", choices=("event-id", "peak-time"), default="event-id")
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
    except ScientificQCError as exc:
        if prepared:
            write_result(args.output_dir, {"schema_version": "1.0", "skill": SKILL_REF, "status": "error", "message": str(exc), "parameters": {}, "inputs": {}, "artifacts": {}, "checks": [{"check": "scientific_qc", "status": "FAIL", "details": str(exc)}], "warnings": [], "provenance": provenance()})
        print(f"qc error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        if prepared:
            write_result(args.output_dir, {"schema_version": "1.0", "skill": SKILL_REF, "status": "error", "message": str(exc), "parameters": {}, "inputs": {}, "artifacts": {}, "checks": [], "warnings": [], "provenance": provenance()})
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
