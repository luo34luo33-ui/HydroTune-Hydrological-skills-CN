#!/usr/bin/env python3
"""Render the fixed HydroTune DEM and hydrology process atlas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import geopandas as gpd
from matplotlib.colors import ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.colors import LightSource
import numpy as np
import rasterio

from _atlas_common import (
    AtlasError, add_header, add_status_badge, artifact_path, assert_same_grid,
    configure_rendering, decorate_map, extent_from_meta, file_reference,
    linear_cmap, listed_cmap, load_result, load_style, new_figure,
    prepare_output_dir, provenance, read_raster, save_figure, write_result,
    resolve_reference,
)


SKILL_REF = "visualization-reporting/visualize-dem-hydrology-atlas"
PREPARE_SKILL = "spatial-analysis/prepare-dem-analysis-grid"
EXTRACT_SKILL = "spatial-analysis/extract-dem-stream-network"
TEMPLATES = [
    "dem-basin-context", "dem-terrain", "flow-accumulation-network",
    "stream-order-subbasins",
]

TEXT = {
    "zh": {
        "context_title": "流域与 DEM 分析范围",
        "context_subtitle": "真实流域边界、米制 DEM 与外扩水文分析范围",
        "terrain_title": "分析范围地形",
        "terrain_subtitle": "高程分层设色与固定光照地形阴影 · NoData 透明",
        "flow_title": "汇流累积与提取河网",
        "flow_subtitle": "汇流累积采用 log1p 显示变换；不改变原始像元值",
        "order_title": "Strahler 河网等级与子流域",
        "order_subtitle": "柔和子流域分区与分级水系，不进行全量标签堆叠",
        "elevation": "高程（m）",
        "accumulation": "log1p（汇流累积像元数）",
        "basin": "真实流域边界",
        "analysis": "水文分析范围",
        "metric": "米制 DEM 范围",
        "subbasins": "子流域",
        "stream_order": "Strahler 等级",
        "no_extract": "未提供 extract result；已跳过河网与子流域两张模板",
    },
    "en": {
        "context_title": "Basin and DEM analysis extent",
        "context_subtitle": "True basin boundary, metric DEM and buffered hydrological analysis extent",
        "terrain_title": "Terrain within the analysis extent",
        "terrain_subtitle": "Muted elevation tint and fixed hillshade · NoData is transparent",
        "flow_title": "Flow accumulation and extracted network",
        "flow_subtitle": "Flow accumulation uses log1p for display only; source values remain unchanged",
        "order_title": "Strahler stream order and subbasins",
        "order_subtitle": "Restrained subbasin colors and ordered network without label clutter",
        "elevation": "Elevation (m)",
        "accumulation": "log1p (flow-accumulation cells)",
        "basin": "True basin boundary",
        "analysis": "Hydrological analysis extent",
        "metric": "Metric DEM extent",
        "subbasins": "Subbasins",
        "stream_order": "Strahler order",
        "no_extract": "No extract result supplied; network and subbasin templates were skipped",
    },
}


def _finite_limits(data: np.ndarray) -> tuple[float, float]:
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        raise AtlasError("栅格没有有效像元")
    low, high = np.percentile(finite, [2, 98])
    if high <= low:
        high = low + 1.0
    return float(low), float(high)


def _hillshade(data: np.ndarray) -> np.ndarray:
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        raise AtlasError("DEM 没有有效像元")
    filled = np.where(np.isfinite(data), data, float(np.median(finite)))
    shade = LightSource(azdeg=315, altdeg=42).hillshade(filled, vert_exag=1.0, dx=1.0, dy=1.0)
    return np.where(np.isfinite(data), shade, np.nan)


def _validate_vector_crs(frame: gpd.GeoDataFrame, expected: str, label: str) -> None:
    if frame.crs is None:
        raise AtlasError(f"{label} 缺少 CRS")
    if rasterio.crs.CRS.from_user_input(frame.crs) != rasterio.crs.CRS.from_user_input(expected):
        raise AtlasError(f"{label} CRS 与栅格不一致")


def _side_legend(axis, handles, style, title: str | None = None) -> None:
    legend = axis.legend(
        handles=handles, title=title, loc="center left", bbox_to_anchor=(1.035, 0.52),
        frameon=False, labelspacing=1.1, handlelength=2.4, borderaxespad=0,
    )
    if legend.get_title() is not None:
        legend.get_title().set_weight("bold")
        legend.get_title().set_color(style["colors"]["ink"])


def _plot_terrain(axis, data, extent, style, colorbar_label: str, figure):
    low, high = _finite_limits(data)
    image = axis.imshow(data, extent=extent, origin="upper", cmap=linear_cmap(style, "terrain"), vmin=low, vmax=high, zorder=1)
    axis.imshow(_hillshade(data), extent=extent, origin="upper", cmap="Greys", alpha=0.24, vmin=0, vmax=1, zorder=2)
    color_axis = figure.add_axes([0.835, 0.23, 0.018, 0.36])
    colorbar = figure.colorbar(image, cax=color_axis)
    colorbar.set_label(colorbar_label, fontsize=9)
    colorbar.outline.set_linewidth(0.5)
    return low, high


def _render_context(style, text, status, metric, metric_meta, analysis_meta, basin, output_dir,
                    language, source_results, warnings):
    extent = extent_from_meta(metric_meta)
    figure, (axis,) = new_figure(style)
    add_header(figure, text["context_title"], text["context_subtitle"], style)
    add_status_badge(figure, status, style)
    low, high = _finite_limits(metric)
    axis.imshow(metric, extent=extent, origin="upper", cmap=linear_cmap(style, "terrain"), vmin=low, vmax=high, alpha=0.92, zorder=1)
    analysis_extent = extent_from_meta(analysis_meta)
    axis.add_patch(Rectangle(
        (analysis_extent[0], analysis_extent[2]), analysis_extent[1] - analysis_extent[0],
        analysis_extent[3] - analysis_extent[2], fill=False, linestyle=(0, (6, 3)),
        edgecolor=style["colors"]["analysis_extent"], lw=style["line_widths"]["analysis_extent"], zorder=4,
    ))
    basin.boundary.plot(ax=axis, color=style["colors"]["boundary"], linewidth=style["line_widths"]["basin"], zorder=5)
    decorate_map(axis, extent, style, metric_meta["crs"], language)
    _side_legend(axis, [
        Patch(facecolor=style["colors"]["terrain"][1], edgecolor="none", label=text["metric"]),
        Line2D([0], [0], color=style["colors"]["analysis_extent"], lw=1.8, linestyle="--", label=text["analysis"]),
        Line2D([0], [0], color=style["colors"]["boundary"], lw=2.2, label=text["basin"]),
    ], style)
    return save_figure(
        figure, output_dir, "dem-basin-context", language, text["context_title"], source_results,
        [
            {"id": "metric-dem", "source": "prepare_result.artifacts.metric_dem", "display_transform": f"2-98 percentile linear stretch; vmin={low:.6g}; vmax={high:.6g}", "palette": "terrain"},
            {"id": "analysis-extent", "source": "prepare_result.artifacts.analysis_dem", "display_transform": "raster bounds only", "palette": "analysis_extent"},
            {"id": "basin-boundary", "source": "prepare_result.artifacts.normalized_basin", "display_transform": "boundary stroke", "palette": "boundary"},
        ], metric_meta["crs"], extent, warnings, status == "success",
    )


def _render_terrain(style, text, status, analysis, analysis_meta, basin, output_dir,
                    language, source_results, warnings):
    extent = extent_from_meta(analysis_meta)
    figure, (axis,) = new_figure(style)
    add_header(figure, text["terrain_title"], text["terrain_subtitle"], style)
    add_status_badge(figure, status, style)
    low, high = _plot_terrain(axis, analysis, extent, style, text["elevation"], figure)
    basin.boundary.plot(ax=axis, color=style["colors"]["boundary"], linewidth=style["line_widths"]["basin"], zorder=5)
    decorate_map(axis, extent, style, analysis_meta["crs"], language)
    _side_legend(axis, [Line2D([0], [0], color=style["colors"]["boundary"], lw=2.2, label=text["basin"])], style)
    return save_figure(
        figure, output_dir, "dem-terrain", language, text["terrain_title"], source_results,
        [
            {"id": "analysis-dem", "source": "prepare_result.artifacts.analysis_dem", "display_transform": f"2-98 percentile terrain tint plus fixed hillshade; vmin={low:.6g}; vmax={high:.6g}", "palette": "terrain"},
            {"id": "basin-boundary", "source": "prepare_result.artifacts.normalized_basin", "display_transform": "boundary stroke", "palette": "boundary"},
        ], analysis_meta["crs"], extent, warnings, status == "success",
    )


def _render_flow(style, text, status, filled, accumulation, streams, meta, basin, output_dir,
                 language, source_results, warnings):
    extent = extent_from_meta(meta)
    figure, (axis,) = new_figure(style)
    add_header(figure, text["flow_title"], text["flow_subtitle"], style)
    add_status_badge(figure, status, style)
    axis.imshow(_hillshade(filled), extent=extent, origin="upper", cmap="Greys", vmin=0, vmax=1, alpha=0.48, zorder=1)
    transformed = np.where(np.isfinite(accumulation) & (accumulation > 0), np.log1p(accumulation), np.nan)
    low, high = _finite_limits(transformed)
    flow_image = axis.imshow(transformed, extent=extent, origin="upper", cmap=linear_cmap(style, "water"), vmin=low, vmax=high, alpha=0.75, zorder=2)
    stream_mask = np.where(np.isfinite(streams) & (streams > 0), 1.0, np.nan)
    axis.imshow(stream_mask, extent=extent, origin="upper", cmap=ListedColormap([style["colors"]["water"][-1]]), interpolation="nearest", zorder=4)
    basin.boundary.plot(ax=axis, color=style["colors"]["boundary"], linewidth=style["line_widths"]["basin"], zorder=5)
    decorate_map(axis, extent, style, meta["crs"], language)
    color_axis = figure.add_axes([0.835, 0.23, 0.018, 0.36])
    colorbar = figure.colorbar(flow_image, cax=color_axis)
    colorbar.set_label(text["accumulation"], fontsize=9)
    colorbar.outline.set_linewidth(0.5)
    _side_legend(axis, [
        Line2D([0], [0], color=style["colors"]["water"][-1], lw=2.2, label=text["stream_order"]),
        Line2D([0], [0], color=style["colors"]["boundary"], lw=2.0, label=text["basin"]),
    ], style)
    return save_figure(
        figure, output_dir, "flow-accumulation-network", language, text["flow_title"], source_results,
        [
            {"id": "filled-dem-hillshade", "source": "extract_result.artifacts.filled_dem", "display_transform": "fixed hillshade", "palette": "grayscale"},
            {"id": "flow-accumulation", "source": "extract_result.artifacts.flow_accumulation", "display_transform": f"log1p then 2-98 percentile stretch; vmin={low:.6g}; vmax={high:.6g}", "palette": "water"},
            {"id": "streams", "source": "extract_result.artifacts.streams", "display_transform": "positive-cell mask", "palette": "deep-water"},
            {"id": "basin-boundary", "source": "prepare_result.artifacts.normalized_basin", "display_transform": "boundary stroke", "palette": "boundary"},
        ], meta["crs"], extent, warnings, status == "success",
    )


def _render_order(style, text, status, subbasins, order, meta, basin, output_dir,
                  language, source_results, warnings):
    extent = extent_from_meta(meta)
    figure, (axis,) = new_figure(style)
    add_header(figure, text["order_title"], text["order_subtitle"], style)
    add_status_badge(figure, status, style)
    sub_ids = sorted(int(value) for value in np.unique(subbasins[np.isfinite(subbasins) & (subbasins > 0)]))
    sub_display = np.full(subbasins.shape, np.nan)
    for index, identifier in enumerate(sub_ids):
        sub_display[subbasins == identifier] = index % len(style["colors"]["subbasins"])
    axis.imshow(sub_display, extent=extent, origin="upper", cmap=listed_cmap(style, "subbasins"), interpolation="nearest", alpha=0.92, zorder=1)
    max_order = max(1, int(np.nanmax(np.where(order > 0, order, np.nan))))
    order_cmap = linear_cmap(style, "water")
    for stream_order in range(1, max_order + 1):
        mask = np.where(order == stream_order, stream_order, np.nan)
        axis.imshow(mask, extent=extent, origin="upper", cmap=order_cmap, vmin=1, vmax=max_order,
                    interpolation="nearest", alpha=0.92, zorder=2 + stream_order * 0.01)
    basin.boundary.plot(ax=axis, color=style["colors"]["boundary"], linewidth=style["line_widths"]["basin"], zorder=5)
    decorate_map(axis, extent, style, meta["crs"], language)
    handles = [Patch(facecolor=style["colors"]["subbasins"][0], edgecolor="#A7AAA4", label=text["subbasins"])]
    for stream_order in range(1, max_order + 1):
        color = order_cmap((stream_order - 1) / max(1, max_order - 1))
        handles.append(Line2D([0], [0], color=color, lw=style["line_widths"]["stream_base"] + stream_order * style["line_widths"]["stream_step"], label=f"{text['stream_order']} {stream_order}"))
    _side_legend(axis, handles, style)
    return save_figure(
        figure, output_dir, "stream-order-subbasins", language, text["order_title"], source_results,
        [
            {"id": "subbasins", "source": "extract_result.artifacts.subbasins", "display_transform": "stable categorical IDs", "palette": "subbasins"},
            {"id": "stream-order", "source": "extract_result.artifacts.stream_order", "display_transform": f"positive-cell categorical order; classes=1..{max_order}", "palette": "water"},
            {"id": "basin-boundary", "source": "prepare_result.artifacts.normalized_basin", "display_transform": "boundary stroke", "palette": "boundary"},
        ], meta["crs"], extent, warnings, status == "success",
    )


def run(args: argparse.Namespace) -> dict:
    style = load_style(Path(__file__))
    font_name = configure_rendering(style, args.language)
    prepare_result_path = args.prepare_result.resolve()
    prepare = load_result(prepare_result_path, PREPARE_SKILL)
    warnings = list(prepare.get("warnings", []))
    source_results = {"prepare_result": file_reference(prepare_result_path)}
    metric_path = artifact_path(prepare, prepare_result_path, "metric_dem")
    analysis_path = artifact_path(prepare, prepare_result_path, "analysis_dem")
    basin_path = artifact_path(prepare, prepare_result_path, "normalized_basin")
    metric, metric_meta = read_raster(metric_path)
    analysis, analysis_meta = read_raster(analysis_path)
    if metric_meta["crs"] != analysis_meta["crs"]:
        raise AtlasError("metric DEM 与 analysis DEM 的 CRS 不一致")
    basin = gpd.read_file(basin_path)
    if basin.empty:
        raise AtlasError("流域边界为空")
    _validate_vector_crs(basin, metric_meta["crs"], "流域边界")

    extract = None
    extract_paths = {}
    if args.extract_result is None:
        warnings.append(TEXT[args.language]["no_extract"])
    else:
        extract_result_path = args.extract_result.resolve()
        extract = load_result(extract_result_path, EXTRACT_SKILL)
        extract_inputs = extract.get("inputs", {})
        if "analysis_dem" not in extract_inputs:
            raise AtlasError("extract result 缺少 inputs.analysis_dem，无法确认与 prepare result 的衔接")
        linked_analysis = resolve_reference(extract_inputs["analysis_dem"], extract_result_path, "extract input analysis_dem")
        if linked_analysis != analysis_path:
            raise AtlasError("extract result 并非由当前 prepare result 的 analysis DEM 生成")
        source_results["extract_result"] = file_reference(extract_result_path)
        warnings.extend(extract.get("warnings", []))
        for key in ("filled_dem", "flow_accumulation", "streams", "stream_order", "subbasins"):
            extract_paths[key] = artifact_path(extract, extract_result_path, key)

    status = "warning" if warnings else "success"
    artifacts = {}
    text = TEXT[args.language]
    artifacts.update(_render_context(style, text, status, metric, metric_meta, analysis_meta, basin,
                                     args.output_dir, args.language, source_results, warnings))
    artifacts.update(_render_terrain(style, text, status, analysis, analysis_meta, basin,
                                     args.output_dir, args.language, source_results, warnings))
    rendered = ["dem-basin-context", "dem-terrain"]
    checks = [
        {"check": "input_hashes", "status": "PASS", "details": "all consumed prepare artifacts verified"},
        {"check": "prepare_crs", "status": "PASS", "details": metric_meta["crs"]},
    ]
    if extract is not None:
        filled, filled_meta = read_raster(extract_paths["filled_dem"])
        accumulation, accumulation_meta = read_raster(extract_paths["flow_accumulation"])
        streams, streams_meta = read_raster(extract_paths["streams"])
        order, order_meta = read_raster(extract_paths["stream_order"])
        subbasins, subbasins_meta = read_raster(extract_paths["subbasins"])
        assert_same_grid(filled_meta, [
            ("flow_accumulation", accumulation_meta), ("streams", streams_meta),
            ("stream_order", order_meta), ("subbasins", subbasins_meta),
        ])
        if filled_meta["crs"] != metric_meta["crs"]:
            raise AtlasError("extract 栅格与 prepare 栅格 CRS 不一致")
        artifacts.update(_render_flow(style, text, status, filled, accumulation, streams, filled_meta,
                                      basin, args.output_dir, args.language, source_results, warnings))
        artifacts.update(_render_order(style, text, status, subbasins, order, filled_meta,
                                       basin, args.output_dir, args.language, source_results, warnings))
        rendered.extend(["flow-accumulation-network", "stream-order-subbasins"])
        checks.append({"check": "extract_grid_alignment", "status": "PASS", "details": "five extraction rasters share CRS, transform and shape"})
    else:
        checks.append({"check": "extract_result", "status": "WARN", "details": text["no_extract"]})
    return {
        "schema_version": "1.0", "skill": SKILL_REF, "status": status,
        "message": f"rendered {len(rendered)} fixed atlas templates",
        "parameters": {"language": args.language, "templates": rendered, "pixel_size": [2400, 1600]},
        "inputs": source_results, "artifacts": artifacts, "checks": checks,
        "warnings": warnings, "provenance": provenance(font_name),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-result", type=Path, required=True)
    parser.add_argument("--extract-result", type=Path)
    parser.add_argument("--language", choices=("zh", "en"), default="zh")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.output_dir = args.output_dir.resolve()
    prepared = False
    try:
        prepare_output_dir(args.output_dir, args.overwrite, TEMPLATES)
        prepared = True
        document = run(args)
        write_result(args.output_dir, document)
        print(f"{document['status']}: {document['message']}")
        return 0
    except Exception as exc:
        if prepared:
            write_result(args.output_dir, {
                "schema_version": "1.0", "skill": SKILL_REF, "status": "error",
                "message": str(exc), "parameters": {"language": args.language},
                "inputs": {}, "artifacts": {}, "checks": [], "warnings": [],
                "provenance": {"python": sys.version.split()[0]},
            })
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
