#!/usr/bin/env python3
"""Render fixed overview and per-event flood hydrographs."""

from __future__ import annotations

import argparse
import json
from zoneinfo import ZoneInfo
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from _event_atlas_common import (
    AtlasError, configure, file_reference, load_result, load_style,
    prepare_output_dir, provenance, read_artifact_table, resolve_artifact,
    save_figure, write_json,
)


from _flood_plot import render_event, render_overview, minmax_envelope


SKILL_REF = "visualization-reporting/visualize-flood-event-atlas"
TEXT = {
    "zh": {
        "overview_title": "洪水事件总览", "overview_subtitle": "完整流量序列、Eckhardt 基流与已识别洪峰",
        "event_title": "洪水事件 {event_id:04d}", "event_subtitle": "warm-up 上下文、事件窗口和既有事件指标",
        "discharge": "总流量", "baseflow": "基流", "quickflow": "直接径流", "peak": "洪峰",
        "time": "时间", "flow": "流量（m³/s）", "summary": "事件摘要", "events": "事件数",
        "period": "时段", "display": "显示点数", "peak_flow": "洪峰流量", "duration": "历时", "volume": "总洪量",
        "rainfall": "流域平均雨量", "rainfall_axis": "雨量（mm）", "volume_unit": "亿立方米",
        "quick_fraction": "直接径流比例", "start": "起点", "end": "终点", "warmup": "warm-up",
    },
    "en": {
        "overview_title": "Flood-event overview", "overview_subtitle": "Full discharge series, Eckhardt baseflow and identified event peaks",
        "event_title": "Flood event {event_id:04d}", "event_subtitle": "Warm-up context, event window and existing event metrics",
        "discharge": "Discharge", "baseflow": "Baseflow", "quickflow": "Quickflow", "peak": "Peak",
        "time": "Time", "flow": "Flow (m³/s)", "summary": "Event summary", "events": "Events",
        "period": "Period", "display": "Displayed points", "peak_flow": "Peak flow", "duration": "Duration", "volume": "Total volume",
        "rainfall": "Basin mean rainfall", "rainfall_axis": "Rainfall (mm)", "volume_unit": "10⁸ m³",
        "quick_fraction": "Quickflow fraction", "start": "Start", "end": "End", "warmup": "Warm-up",
    },
}


def load_rainfall(args, series, process, upstream):
    path, metadata_path = args.rainfall, args.rainfall_metadata
    if path is None and metadata_path is None:
        return None, None, {}
    if path is None or metadata_path is None:
        raise AtlasError("--rainfall 和 --rainfall-metadata 必须同时提供")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    if metadata.get("spatial_scope") != "basin_mean" or metadata.get("unit") != "mm/step":
        raise AtlasError("雨量必须显式声明 spatial_scope=basin_mean、unit=mm/step")
    if metadata.get("timestamp_semantics") not in {"interval_start", "interval_end"}:
        raise AtlasError("雨量必须声明 interval_start 或 interval_end 时间含义")
    try:
        zone = ZoneInfo(metadata["timezone"])
        step = float(metadata["timestep_seconds"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AtlasError("雨量必须声明有效 timezone 和 timestep_seconds") from exc
    if not np.isfinite(step) or step <= 0:
        raise AtlasError("雨量时间步必须为有限正数")
    expected_step = upstream.get("parameters", {}).get("timestep_seconds")
    if expected_step is not None and not np.isclose(step, float(expected_step)):
        raise AtlasError("雨量与流量时间步不同；先在上游显式对齐，不自动重采样")
    rain = pd.read_csv(path, encoding="utf-8-sig")
    if not {"time", "P_mm"}.issubset(rain.columns) or rain.empty:
        raise AtlasError("雨量 CSV 必须包含 time,P_mm，且不能为空")
    for raw in rain["time"]:
        stamp = pd.Timestamp(raw)
        if stamp.tzinfo is None or stamp.utcoffset() != stamp.tz_convert(zone).utcoffset():
            raise AtlasError("雨量时间戳必须带偏移且与声明时区一致")
    rain["time"] = pd.to_datetime(rain["time"], utc=True, errors="raise")
    rain["P_mm"] = pd.to_numeric(rain["P_mm"], errors="raise")
    if not np.isfinite(rain["P_mm"]).all() or (rain["P_mm"] < 0).any():
        raise AtlasError("雨量存在缺测、非有限值或负值；不填零")
    rain = rain.sort_values("time")
    if rain["time"].duplicated().any() or not np.allclose(rain["time"].diff().dropna().dt.total_seconds(), step):
        raise AtlasError("雨量时间轴重复、缺步或不符合声明时间步")
    if metadata["timestamp_semantics"] == "interval_start":
        rain["time"] += pd.Timedelta(seconds=step)
    required = pd.DatetimeIndex(pd.to_datetime(pd.concat([series["time"], process["time"]]), utc=True).unique()).sort_values()
    aligned = rain.set_index("time").reindex(required)
    if aligned["P_mm"].isna().any():
        raise AtlasError("雨量未覆盖流量序列及事件预热窗口；不自动填补")
    aligned.index = aligned.index.tz_convert(series["time"].iloc[0].tzinfo)
    aligned = aligned.reset_index(names="time")
    return aligned, step, {"rainfall": file_reference(path), "rainfall_metadata": file_reference(metadata_path)}


def run(args: argparse.Namespace) -> dict:
    style = load_style(Path(__file__))
    font = configure(style, args.language)
    result_path = args.extract_result.resolve()
    upstream = load_result(result_path)
    summary_path = resolve_artifact(upstream, result_path, "event_summary")
    process_path = resolve_artifact(upstream, result_path, "event_process")
    series_path = resolve_artifact(upstream, result_path, "annotated_series")
    summary = read_artifact_table(summary_path, "event_summary")
    process = read_artifact_table(process_path, "event_process")
    series = read_artifact_table(series_path, "annotated_series")
    required_summary = {"event_id", "start_time", "peak_time", "end_time", "peak_flow_m3_s", "duration_hours", "total_volume_m3"}
    required_process = {"event_id", "time", "discharge_m3_s", "baseflow_m3_s", "is_warmup"}
    required_series = {"time", "discharge_m3_s", "baseflow_m3_s"}
    if not required_summary.issubset(summary.columns) or not required_process.issubset(process.columns) or not required_series.issubset(series.columns):
        raise AtlasError("事件 artifacts 缺少绘图必需字段")
    for frame, columns in ((summary, ("start_time", "peak_time", "end_time")), (process, ("time",)), (series, ("time",))):
        for column in columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=False)
            if frame[column].isna().any():
                raise AtlasError(f"存在无效时间: {column}")
            if any(pd.Timestamp(value).tzinfo is None for value in frame[column]):
                raise AtlasError(f"时间戳必须含时区偏移: {column}")
    series = series.sort_values("time")
    for frame in (series, process):
        values = frame[["discharge_m3_s", "baseflow_m3_s"]].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0).any():
            raise AtlasError("流量过程存在缺测、非有限值或负值")
    if series["time"].duplicated().any() or process.duplicated(["event_id", "time"]).any():
        raise AtlasError("流量过程时间轴重复")
    summary_ids = set(summary["event_id"].astype(int))
    process_ids = set(process["event_id"].dropna().astype(int))
    if summary_ids != process_ids:
        raise AtlasError("event_summary 与 event_process 的事件 ID 不一致")
    if args.selection == "all":
        selected = summary.sort_values("event_id")
    elif args.selection == "first":
        selected = summary.sort_values("event_id").head(args.max_events)
    else:
        selected = summary.sort_values(["peak_flow_m3_s", "event_id"], ascending=[False, True]).head(args.max_events).sort_values("event_id")
    warnings = list(upstream.get("warnings", []))
    rain, rain_step, rain_sources = load_rainfall(args, series, process, upstream)
    if rain is None:
        warnings.append("未提供流域平均雨量，雨量柱及右侧雨量轴未绘制" if args.language == "zh" else "Basin mean rainfall unavailable; rainfall bars and right axis omitted")
    status = "warning" if warnings else "success"
    sources = {"extract_result": file_reference(result_path)}
    sources.update(rain_sources)
    text = TEXT[args.language]
    artifacts = {}
    artifacts.update(render_overview(style, text, status, summary, series, args.output_dir, args.language, sources, warnings,
                                     rain=rain, rain_step=rain_step))
    for row in selected.itertuples():
        artifacts.update(render_event(style, text, status, row, process, args.output_dir, args.language, sources, warnings,
                                      rain=rain, rain_step=rain_step))
    return {
        "schema_version": "1.0", "skill": SKILL_REF, "status": status,
        "message": f"rendered overview and {len(selected)} event hydrographs",
        "parameters": {"language": args.language, "selection": args.selection, "max_events": args.max_events, "rendered_event_ids": selected["event_id"].astype(int).tolist(), "pixel_size": [2400, 1200], "rainfall_available": rain is not None, "rainfall_step_seconds": rain_step, "total_volume_display_unit": "10^8 m3"},
        "inputs": sources, "artifacts": artifacts,
        "checks": [
            {"check": "input_hashes", "status": "PASS", "details": "all consumed event artifacts verified"},
            {"check": "event_table_consistency", "status": "PASS", "details": f"events={len(summary)}"},
        ],
        "warnings": warnings, "provenance": provenance(font),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract-result", type=Path, required=True)
    parser.add_argument("--rainfall", type=Path, help="basin mean rainfall CSV with time,P_mm")
    parser.add_argument("--rainfall-metadata", type=Path, help="explicit rainfall units, spatial scope and time semantics JSON")
    parser.add_argument("--language", choices=("zh", "en"), default="zh")
    parser.add_argument("--selection", choices=("all", "first", "largest"), default="all")
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.selection != "all" and (args.max_events is None or args.max_events < 1):
        print("error: first/largest 模式要求正整数 --max-events", file=sys.stderr)
        return 1
    if args.selection == "all" and args.max_events is not None:
        print("error: selection=all 时不得提供 --max-events", file=sys.stderr)
        return 1
    args.output_dir = args.output_dir.resolve()
    prepared = False
    try:
        prepare_output_dir(args.output_dir, args.overwrite)
        prepared = True
        document = run(args)
        write_json(args.output_dir / "result.json", document)
        print(f"{document['status']}: {document['message']}")
        return 0
    except Exception as exc:
        if prepared:
            write_json(args.output_dir / "result.json", {"schema_version": "1.0", "skill": SKILL_REF, "status": "error", "message": str(exc), "parameters": {}, "inputs": {}, "artifacts": {}, "checks": [], "warnings": [], "provenance": {"python": sys.version.split()[0]}})
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
