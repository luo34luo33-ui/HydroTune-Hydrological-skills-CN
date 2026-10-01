"""Publication-style flood plots; display changes never alter source metrics."""
from __future__ import annotations

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch
import numpy as np
import pandas as pd

from _event_atlas_common import AtlasError, save_figure


def minmax_envelope(frame, maximum_bins=1800):
    if len(frame) <= maximum_bins * 2:
        return frame, f"none; points={len(frame)}"
    selected = []
    for group in np.array_split(np.arange(len(frame)), maximum_bins):
        values = frame.iloc[group]["discharge_m3_s"].to_numpy(dtype=float)
        selected.extend([int(group[np.argmin(values)]), int(group[np.argmax(values)])])
    indices = sorted(set(selected))
    return frame.iloc[indices], f"deterministic min-max envelope; original_points={len(frame)}; displayed_points={len(indices)}; bins={maximum_bins}"


def _figure(style, text, frame):
    canvas = style["canvas"]
    figure = plt.figure(figsize=canvas["figsize_inches"], dpi=canvas["dpi"], facecolor="white")
    axis = figure.add_axes(style["layout"]["main_axes"], facecolor="white")
    axis.set_xlabel(text["time"], fontsize=style["typography"]["axis_label_size"])
    axis.set_ylabel(text["flow"], fontsize=style["typography"]["axis_label_size"])
    axis.tick_params(labelsize=style["typography"]["tick_size"], direction="in", length=6)
    for spine in axis.spines.values():
        spine.set_color("black")
        spine.set_linewidth(1)
    axis.grid(axis="y", color=style["colors"]["grid"], linewidth=0.5, alpha=0.4)
    locator = mdates.AutoDateLocator(minticks=3, maxticks=6, tz=frame["time"].iloc[0].tzinfo)
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator, tz=frame["time"].iloc[0].tzinfo))
    axis.xaxis.get_offset_text().set_fontsize(14)
    axis.set_ylim(0, max(1, float(frame["discharge_m3_s"].max())) * 1.22)
    axis.margins(x=0.01)
    return figure, axis


def _rainfall(axis, rain, step, style, text, overview=False):
    if rain is None:
        return None, []
    display = rain.copy()
    group_size = max(1, int(np.ceil(len(display) / 1800))) if overview else 1
    if group_size > 1:
        groups = np.arange(len(display)) // group_size
        display = display.groupby(groups, sort=True).agg(time=("time", "last"), P_mm=("P_mm", "sum"), steps=("P_mm", "size"))
    else:
        display["steps"] = 1
    rain_axis = axis.twinx()
    rain_axis.set_zorder(0)
    axis.set_zorder(2)
    axis.patch.set_visible(False)
    rain_axis.patch.set_facecolor("white")
    widths = display["steps"].to_numpy() * step / 86400
    ends = mdates.date2num(display["time"].tolist())
    rain_axis.bar(ends - widths, display["P_mm"], width=widths * 0.92, align="edge",
                  color=style["colors"]["rainfall"], alpha=style["layout"]["rainfall_alpha"],
                  edgecolor="none", zorder=1)
    rain_axis.set_ylim(max(1, float(display["P_mm"].max())) * 3.3, 0)
    rain_axis.set_ylabel(text["rainfall_axis"], fontsize=style["typography"]["axis_label_size"])
    rain_axis.tick_params(labelsize=style["typography"]["tick_size"], direction="in", length=6)
    rain_axis.spines["right"].set_color("black")
    rain_axis.spines["right"].set_linewidth(1)
    axis.set_xlim(rain["time"].iloc[0] - pd.Timedelta(seconds=step), rain["time"].iloc[-1])
    note = f"basin mean depth mm/step; interval_end; step_seconds={step}; display bars sum {group_size} consecutive steps (last bin may be shorter); no interpolation"
    return Patch(facecolor=style["colors"]["rainfall"], alpha=style["layout"]["rainfall_alpha"], label=text["rainfall"]), [
        {"id": "basin-rainfall", "source": "rainfall", "display_transform": note, "palette": "light-blue"}]


def _lines(axis, frame, style, text, overview=False):
    axis.plot(frame["time"], frame["discharge_m3_s"], color="black", linestyle="-",
              linewidth=1.1 if overview else 1.8, label=text["discharge"], zorder=5)
    axis.plot(frame["time"], frame["baseflow_m3_s"], color=style["colors"]["baseflow"], linestyle="--",
              linewidth=1 if overview else 1.5, label=text["baseflow"], zorder=5)


def _legend(axis, style, rain_handle, loc="upper left"):
    handles, labels = axis.get_legend_handles_labels()
    if rain_handle is not None:
        handles.append(rain_handle)
    legend = axis.legend(handles=handles, fontsize=style["typography"]["legend_size"],
                         loc=loc, frameon=True, facecolor="white", edgecolor="none", framealpha=0.9,
                         handlelength=2.5, labelspacing=0.55)
    legend.set_zorder(20)


def render_overview(style, text, status, summary, series, output_dir, language, sources, warnings, rain=None, rain_step=None):
    display, transform = minmax_envelope(series)
    figure, axis = _figure(style, text, series)
    rain_handle, layers = _rainfall(axis, rain, rain_step, style, text, overview=True)
    _lines(axis, display, style, text, overview=True)
    if len(summary):
        axis.scatter(summary["peak_time"], summary["peak_flow_m3_s"], marker="^", s=45,
                     color=style["colors"]["peak"], edgecolor="none", label=text["peak"], zorder=8)
    _legend(axis, style, rain_handle)
    return save_figure(figure, output_dir, "flood-event-overview", None, language, text["overview_title"], sources,
                       [{"id": "discharge-baseflow", "source": "extract_result.artifacts.annotated_series", "display_transform": transform, "palette": "black-darkgray"},
                        {"id": "event-peaks", "source": "extract_result.artifacts.event_summary", "display_transform": "existing peak_time and peak_flow_m3_s", "palette": "red"}, *layers], warnings, status == "success")


def render_event(style, text, status, row, process, output_dir, language, sources, warnings, rain=None, rain_step=None):
    event_id = int(row.event_id)
    subset = process[process["event_id"].astype(int) == event_id].sort_values("time").copy()
    if subset.empty:
        raise AtlasError(f"事件 {event_id} 缺少过程数据")
    warmup = subset["is_warmup"].astype(str).str.lower().map({"true": True, "false": False, "1": True, "0": False})
    if warmup.isna().any():
        raise AtlasError(f"事件 {event_id} 的 is_warmup 无效")
    figure, axis = _figure(style, text, subset)
    if rain is not None:
        rain = rain[rain["time"].isin(subset["time"])].copy()
    rain_handle, layers = _rainfall(axis, rain, rain_step, style, text)
    if warmup.any():
        axis.axvspan(subset["time"].iloc[0], pd.Timestamp(row.start_time), color=style["colors"]["warmup_band"],
                     alpha=0.75, linewidth=0, zorder=0)
    _lines(axis, subset, style, text)
    axis.scatter([pd.Timestamp(row.peak_time)], [float(row.peak_flow_m3_s)], marker="^", s=95,
                 color=style["colors"]["peak"], edgecolor="none", label=text["peak"], zorder=8)
    separator = "：" if language == "zh" else ": "
    values = [f"{text['peak_flow']}{separator}{float(row.peak_flow_m3_s):,.1f} m³/s",
              f"{text['volume']}{separator}{float(row.total_volume_m3) / 100000000:,.2f} {text['volume_unit']}",
              f"{text['duration']}{separator}{float(row.duration_hours):,.1f} h"]
    # Use the quieter upper corner for the three existing metrics.
    count = max(1, len(subset) // 4)
    left_busy = subset["discharge_m3_s"].iloc[:count].max()
    right_busy = subset["discharge_m3_s"].iloc[-count:].max()
    summary_right = right_busy <= left_busy
    axis.text(0.975 if summary_right else 0.025, 0.97, "\n".join(values), transform=axis.transAxes,
              ha="right" if summary_right else "left", va="top", fontsize=style["typography"]["summary_size"],
              linespacing=1.5, bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.9, "pad": 5}, zorder=20)
    peak_x = (mdates.date2num(pd.Timestamp(row.peak_time)) - axis.get_xlim()[0]) / np.diff(axis.get_xlim())[0]
    legend_loc = "upper left" if summary_right else "upper right"
    if summary_right and peak_x < 0.35:
        legend_loc = "lower right"
    elif not summary_right and peak_x > 0.65:
        legend_loc = "lower left"
    _legend(axis, style, rain_handle, loc=legend_loc)
    return save_figure(figure, output_dir, f"event-{event_id:04d}-hydrograph", event_id, language,
                       text["event_title"].format(event_id=event_id), sources,
                       [{"id": "warmup", "source": "extract_result.artifacts.event_process", "display_transform": "existing is_warmup; light gray warmup, white event background", "palette": "warmup-gray"},
                        {"id": "discharge-baseflow", "source": "extract_result.artifacts.event_process", "display_transform": "no value transform; black solid total flow, dark gray dashed baseflow", "palette": "black-darkgray"},
                        {"id": "event-peaks", "source": "extract_result.artifacts.event_summary", "display_transform": "existing peak_time and peak_flow_m3_s", "palette": "red"},
                        {"id": "event-metrics", "source": "extract_result.artifacts.event_summary", "display_transform": f"peak_flow_m3_s={row.peak_flow_m3_s}; total_volume_m3={row.total_volume_m3}; duration_hours={row.duration_hours}; volume display=raw m3/100000000, rounded 2 decimals; other displays rounded 1 decimal; no recomputation", "palette": "black"}, *layers], warnings, status == "success")
