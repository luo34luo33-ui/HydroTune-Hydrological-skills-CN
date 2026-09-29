#!/usr/bin/env python3
"""Render fixed overview and per-event flood hydrographs."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd

from _event_atlas_common import (
    AtlasError, configure, file_reference, load_result, load_style,
    prepare_output_dir, provenance, read_artifact_table, resolve_artifact,
    save_figure, write_json,
)


SKILL_REF = "visualization-reporting/visualize-flood-event-atlas"
TEXT = {
    "zh": {
        "overview_title": "洪水事件总览", "overview_subtitle": "完整流量序列、Eckhardt 基流与已识别洪峰",
        "event_title": "洪水事件 {event_id:04d}", "event_subtitle": "warm-up 上下文、事件窗口和既有事件指标",
        "discharge": "总流量", "baseflow": "基流", "quickflow": "直接径流", "peak": "洪峰",
        "time": "时间", "flow": "流量（m³/s）", "summary": "事件摘要", "events": "事件数",
        "period": "时段", "display": "显示点数", "peak_flow": "洪峰流量", "duration": "历时", "volume": "总洪量",
        "quick_fraction": "直接径流比例", "start": "起点", "end": "终点", "warmup": "warm-up",
    },
    "en": {
        "overview_title": "Flood-event overview", "overview_subtitle": "Full discharge series, Eckhardt baseflow and identified event peaks",
        "event_title": "Flood event {event_id:04d}", "event_subtitle": "Warm-up context, event window and existing event metrics",
        "discharge": "Discharge", "baseflow": "Baseflow", "quickflow": "Quickflow", "peak": "Peak",
        "time": "Time", "flow": "Flow (m³/s)", "summary": "Event summary", "events": "Events",
        "period": "Period", "display": "Displayed points", "peak_flow": "Peak flow", "duration": "Duration", "volume": "Total volume",
        "quick_fraction": "Quickflow fraction", "start": "Start", "end": "End", "warmup": "Warm-up",
    },
}


def _figure(style):
    canvas = style["canvas"]
    figure = plt.figure(figsize=canvas["figsize_inches"], dpi=canvas["dpi"], facecolor=canvas["background"])
    axis = figure.add_axes([0.065, 0.14, 0.69, 0.70], facecolor=canvas["panel_background"])
    card = figure.add_axes([0.79, 0.14, 0.17, 0.70], facecolor=canvas["panel_background"])
    return figure, axis, card


def _header(figure, style, title: str, subtitle: str, status: str):
    figure.text(0.055, 0.945, title, fontsize=style["typography"]["title_size"], weight="bold", va="top")
    figure.text(0.057, 0.902, subtitle, fontsize=style["typography"]["subtitle_size"], color=style["colors"]["muted_ink"], va="top")
    figure.add_artist(plt.Line2D([0.055, 0.945], [0.88, 0.88], transform=figure.transFigure, color=style["colors"]["grid"], lw=0.8))
    if status == "warning":
        figure.text(0.94, 0.942, "UPSTREAM WARNING", ha="right", va="top", color="white", fontsize=8.5, weight="bold",
                    bbox={"boxstyle": "round,pad=0.42", "facecolor": style["colors"]["warn"], "edgecolor": "none"})


def _style_axis(axis, style, text):
    axis.set_xlabel(text["time"])
    axis.set_ylabel(text["flow"])
    axis.grid(True, axis="y", color=style["colors"]["grid"], lw=0.6, alpha=0.7)
    axis.spines[["top", "right"]].set_visible(False)
    axis.margins(x=0.01)


def _style_card(card, style, heading: str):
    card.set_xticks([])
    card.set_yticks([])
    for spine in card.spines.values():
        spine.set_color(style["colors"]["grid"])
        spine.set_linewidth(0.7)
    card.text(0.08, 0.94, heading, transform=card.transAxes, fontsize=12, weight="bold", va="top")


def minmax_envelope(frame: pd.DataFrame, maximum_bins: int = 1800) -> tuple[pd.DataFrame, str]:
    if len(frame) <= maximum_bins * 2:
        return frame, f"none; points={len(frame)}"
    indices = np.arange(len(frame))
    selected = []
    for group in np.array_split(indices, maximum_bins):
        values = frame.iloc[group]["discharge_m3_s"].to_numpy(dtype=float)
        selected.extend([int(group[int(np.argmin(values))]), int(group[int(np.argmax(values))])])
    selected = sorted(set(selected))
    return frame.iloc[selected], f"deterministic min-max envelope; original_points={len(frame)}; displayed_points={len(selected)}; bins={maximum_bins}"


def render_overview(style, text, status, summary, series, output_dir, language, sources, warnings):
    display, transform = minmax_envelope(series)
    display_note = (
        f"原始 {len(series):,} 点\n显示 {len(display):,} 点"
        if language == "zh"
        else f"Original {len(series):,}\nDisplayed {len(display):,}"
    )
    figure, axis, card = _figure(style)
    _header(figure, style, text["overview_title"], text["overview_subtitle"], status)
    axis.plot(display["time"], display["discharge_m3_s"], color=style["colors"]["discharge"], lw=0.8, label=text["discharge"])
    axis.plot(display["time"], display["baseflow_m3_s"], color=style["colors"]["baseflow"], lw=0.8, label=text["baseflow"])
    if len(summary):
        axis.scatter(summary["peak_time"], summary["peak_flow_m3_s"], marker="^", s=28, color=style["colors"]["peak"], edgecolor="white", linewidth=0.4, label=text["peak"], zorder=6)
    _style_axis(axis, style, text)
    axis.legend(frameon=False, loc="upper left", ncol=3)
    _style_card(card, style, text["summary"])
    start, end = series["time"].iloc[0], series["time"].iloc[-1]
    values = [
        (text["events"], str(len(summary))),
        (text["period"], f"{start:%Y-%m-%d}\n—\n{end:%Y-%m-%d}"),
        (text["display"], display_note),
    ]
    y = 0.82
    for label, value in values:
        card.text(0.08, y, label, transform=card.transAxes, fontsize=8, color=style["colors"]["muted_ink"], va="top")
        card.text(
            0.08, y - 0.045, value, transform=card.transAxes, fontsize=10,
            weight="normal" if label == text["display"] else "bold", va="top", wrap=True,
        )
        y -= 0.20
    return save_figure(
        figure, output_dir, "flood-event-overview", None, language, text["overview_title"], sources,
        [
            {"id": "discharge", "source": "extract_result.artifacts.annotated_series", "display_transform": transform, "palette": "discharge"},
            {"id": "baseflow", "source": "extract_result.artifacts.annotated_series", "display_transform": transform, "palette": "baseflow"},
            {"id": "event-peaks", "source": "extract_result.artifacts.event_summary", "display_transform": "existing peak_time and peak_flow_m3_s", "palette": "peak"},
        ], warnings, status == "success",
    )


def _bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series
    return series.astype(str).str.lower().map({"true": True, "false": False, "1": True, "0": False}).fillna(False)


def render_event(style, text, status, row, process, output_dir, language, sources, warnings):
    event_id = int(row.event_id)
    subset = process[process["event_id"].astype(int) == event_id].copy()
    if subset.empty:
        raise AtlasError(f"事件 {event_id} 缺少过程数据")
    warmup = _bool_series(subset["is_warmup"])
    figure, axis, card = _figure(style)
    title = text["event_title"].format(event_id=event_id)
    _header(figure, style, title, text["event_subtitle"], status)
    axis.axvspan(subset["time"].iloc[0], pd.Timestamp(row.start_time), color=style["colors"]["warmup_band"], alpha=0.75, label=text["warmup"])
    axis.axvspan(pd.Timestamp(row.start_time), pd.Timestamp(row.end_time), color=style["colors"]["event_band"], alpha=0.52)
    axis.fill_between(subset["time"], subset["baseflow_m3_s"], subset["discharge_m3_s"], color=style["colors"]["quickflow"], alpha=0.42, label=text["quickflow"])
    axis.plot(subset["time"], subset["discharge_m3_s"], color=style["colors"]["discharge"], lw=1.7, label=text["discharge"])
    axis.plot(subset["time"], subset["baseflow_m3_s"], color=style["colors"]["baseflow"], lw=1.3, label=text["baseflow"])
    axis.scatter([pd.Timestamp(row.peak_time)], [float(row.peak_flow_m3_s)], marker="^", s=70, color=style["colors"]["peak"], edgecolor="white", linewidth=0.6, zorder=8, label=text["peak"])
    for instant in (pd.Timestamp(row.start_time), pd.Timestamp(row.end_time)):
        axis.axvline(instant, color=style["colors"]["muted_ink"], lw=0.9, linestyle="--")
    _style_axis(axis, style, text)
    axis.legend(frameon=False, loc="upper left", ncol=3)
    _style_card(card, style, text["summary"])
    values = [
        (text["peak_flow"], f"{float(row.peak_flow_m3_s):,.3g} m³/s"),
        (text["duration"], f"{float(row.duration_hours):,.2f} h"),
        (text["volume"], f"{float(row.total_volume_m3):,.4g} m³"),
        (text["quick_fraction"], f"{float(row.quickflow_fraction):.1%}"),
        (text["start"], pd.Timestamp(row.start_time).strftime("%Y-%m-%d\n%H:%M")),
        (text["end"], pd.Timestamp(row.end_time).strftime("%Y-%m-%d\n%H:%M")),
    ]
    y = 0.84
    for label, value in values:
        card.text(0.08, y, label, transform=card.transAxes, fontsize=8, color=style["colors"]["muted_ink"], va="top")
        card.text(0.08, y - 0.043, value, transform=card.transAxes, fontsize=10, weight="bold", va="top")
        y -= 0.125
    template = f"event-{event_id:04d}-hydrograph"
    return save_figure(
        figure, output_dir, template, event_id, language, title, sources,
        [
            {"id": "warmup", "source": "extract_result.artifacts.event_process", "display_transform": "existing is_warmup flag", "palette": "warmup_band"},
            {"id": "event-window", "source": "extract_result.artifacts.event_summary", "display_transform": "existing start_time to end_time", "palette": "event_band"},
            {"id": "flow-components", "source": "extract_result.artifacts.event_process", "display_transform": "no value transform", "palette": "discharge-baseflow-quickflow"},
            {"id": "event-metrics", "source": "extract_result.artifacts.event_summary", "display_transform": "display existing metrics only", "palette": "ink"},
        ], warnings, status == "success",
    )


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
    required_summary = {"event_id", "start_time", "peak_time", "end_time", "peak_flow_m3_s", "duration_hours", "total_volume_m3", "quickflow_fraction"}
    required_process = {"event_id", "time", "discharge_m3_s", "baseflow_m3_s", "quickflow_m3_s", "is_warmup"}
    required_series = {"time", "discharge_m3_s", "baseflow_m3_s"}
    if not required_summary.issubset(summary.columns) or not required_process.issubset(process.columns) or not required_series.issubset(series.columns):
        raise AtlasError("事件 artifacts 缺少绘图必需字段")
    for frame, columns in ((summary, ("start_time", "peak_time", "end_time")), (process, ("time",)), (series, ("time",))):
        for column in columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=False)
            if frame[column].isna().any():
                raise AtlasError(f"存在无效时间: {column}")
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
    status = "warning" if warnings else "success"
    sources = {"extract_result": file_reference(result_path)}
    text = TEXT[args.language]
    artifacts = {}
    artifacts.update(render_overview(style, text, status, summary, series, args.output_dir, args.language, sources, warnings))
    for row in selected.itertuples():
        artifacts.update(render_event(style, text, status, row, process, args.output_dir, args.language, sources, warnings))
    return {
        "schema_version": "1.0", "skill": SKILL_REF, "status": status,
        "message": f"rendered overview and {len(selected)} event hydrographs",
        "parameters": {"language": args.language, "selection": args.selection, "max_events": args.max_events, "rendered_event_ids": selected["event_id"].astype(int).tolist(), "pixel_size": [2400, 1600]},
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
