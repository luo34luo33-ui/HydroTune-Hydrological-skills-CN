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
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as MplPath
from matplotlib.colors import LightSource
import numpy as np
import rasterio
from rasterio.features import geometry_mask, shapes
from affine import Affine
from shapely.geometry import box, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from _atlas_common import (
    AtlasError, artifact_path, assert_same_grid,
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
        "accumulation": "汇流累积量（像元数，log1p）",
        "basin": "真实流域边界",
        "analysis": "水文分析范围",
        "metric": "米制 DEM 范围",
        "subbasins": "子流域",
        "streams": "提取河网",
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
        "accumulation": "Flow accumulation (cells, log1p)",
        "basin": "True basin boundary",
        "analysis": "Hydrological analysis extent",
        "metric": "Metric DEM extent",
        "subbasins": "Subbasins",
        "streams": "Extracted network",
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


def _map_extent(basin, style):
    left, bottom, right, top = basin.total_bounds
    pad = style["layout"]["map_padding"]
    dx, dy = (right - left) * pad, (top - bottom) * pad
    if dx <= 0 or dy <= 0:
        raise AtlasError("流域边界范围无效")
    return [left - dx, right + dx, bottom - dy, top + dy]


def _basin_image(axis, data, meta, basin, **kwargs):
    inside = geometry_mask(basin.geometry, out_shape=data.shape,
                           transform=Affine(*meta["transform"][:6]), invert=True)
    masked = np.where(inside, data, np.nan)
    # Raster mask controls statistics; vector clipping trims partial edge cells
    # exactly and preserves polygon holes and disconnected basin components.
    paths = []
    for geometry in basin.geometry:
        polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
        for polygon in polygons:
            polygon = orient(polygon, sign=1)
            for ring in [polygon.exterior, *polygon.interiors]:
                coords = np.asarray(ring.coords)
                codes = np.full(len(coords), MplPath.LINETO, dtype=np.uint8)
                codes[0], codes[-1] = MplPath.MOVETO, MplPath.CLOSEPOLY
                paths.append(MplPath(coords, codes))
    patch = PathPatch(MplPath.make_compound_path(*paths), transform=axis.transData)
    image = axis.imshow(masked, extent=extent_from_meta(meta), origin="upper", interpolation="nearest", **kwargs)
    image.set_clip_path(patch)
    return image, masked


def _blank_position(axis, width, height):
    """Choose the least basin-covered inset, reserving other map furniture."""
    extent = axis._map_extent
    basin = axis._basin_geometry
    occupied = axis._map_insets
    candidates = []
    for x in (0.025, (1 - width) / 2, 0.975 - width):
        for y in (0.025, (1 - height) / 2, 0.975 - height):
            rectangle = box(x, y, x + width, y + height)
            overlap = sum(rectangle.intersection(other).area for other in occupied)
            data_rectangle = box(extent[0] + x * (extent[1] - extent[0]),
                                 extent[2] + y * (extent[3] - extent[2]),
                                 extent[0] + (x + width) * (extent[1] - extent[0]),
                                 extent[2] + (y + height) * (extent[3] - extent[2]))
            coverage = basin.intersection(data_rectangle).area / data_rectangle.area
            candidates.append((coverage + overlap * 100, x, y, rectangle))
    _, x, y, rectangle = min(candidates, key=lambda item: item[0])
    occupied.append(rectangle)
    return x, y


def _new_map(style, basin, crs, language):
    extent = _map_extent(basin, style)
    figure, (axis,) = new_figure(style, extent)
    decorate_map(axis, extent, style, crs, language)
    axis._map_extent = extent
    axis._basin_geometry = unary_union(list(basin.geometry))
    axis._map_insets = [box(0.025, 0.025, 0.31, 0.105), box(0.89, 0.81, 0.98, 0.98)]
    return figure, axis, extent


def _side_legend(axis, handles, style, title: str | None = None) -> None:
    legend = axis.legend(
        handles=handles, title=title, loc="lower left", bbox_to_anchor=(0, 0),
        frameon=True, facecolor="white", edgecolor="none", framealpha=0.94,
        fontsize=style["typography"]["legend_size"], labelspacing=0.75,
        handlelength=2.4, handleheight=1.0, borderaxespad=0,
    )
    legend.set_zorder(40)
    axis.figure.canvas.draw()
    bounds = legend.get_window_extent().transformed(axis.transAxes.inverted())
    x, y = _blank_position(axis, bounds.width, bounds.height)
    legend.set_bbox_to_anchor((x, y), transform=axis.transAxes)
    if legend.get_title() is not None:
        legend.get_title().set_weight("bold")
        legend.get_title().set_color(style["colors"]["ink"])


def _inside_colorbar(axis, image, style, label):
    width, height = 0.40, 0.15
    x, y = _blank_position(axis, width, height)
    panel = axis.inset_axes([x, y, width, height], zorder=30)
    panel.set_facecolor("white")
    panel.set_xticks([])
    panel.set_yticks([])
    for spine in panel.spines.values():
        spine.set_visible(False)
    color_axis = panel.inset_axes([0.12, 0.37, 0.76, 0.16])
    colorbar = axis.figure.colorbar(image, cax=color_axis, orientation="horizontal")
    colorbar.set_label(label, fontsize=style["typography"]["colorbar_label_size"], labelpad=8)
    colorbar.ax.xaxis.set_label_position("top")
    colorbar.ax.tick_params(labelsize=style["typography"]["colorbar_tick_size"])
    colorbar.outline.set_linewidth(0.5)


def _plot_terrain(axis, data, meta, basin, style, colorbar_label):
    image, masked = _basin_image(axis, data, meta, basin, cmap=linear_cmap(style, "terrain"), zorder=1)
    low, high = _finite_limits(masked)
    image.set_clim(low, high)
    _basin_image(axis, _hillshade(data), meta, basin, cmap="Greys", alpha=0.24, vmin=0, vmax=1, zorder=2)
    _inside_colorbar(axis, image, style, colorbar_label)
    return low, high


def _render_context(style, text, status, metric, metric_meta, analysis_meta, basin, output_dir,
                    language, source_results, warnings):
    figure, axis, extent = _new_map(style, basin, metric_meta["crs"], language)
    image, masked = _basin_image(axis, metric, metric_meta, basin, cmap=linear_cmap(style, "terrain"), zorder=1)
    low, high = _finite_limits(masked)
    image.set_clim(low, high)
    _inside_colorbar(axis, image, style, text["elevation"])
    basin.boundary.plot(ax=axis, color=style["colors"]["boundary"], linewidth=style["line_widths"]["basin"], zorder=5)
    _side_legend(axis, [
        Patch(facecolor=style["colors"]["terrain"][1], edgecolor="none", label=text["metric"]),
        Line2D([0], [0], color=style["colors"]["boundary"], lw=2.2, label=text["basin"]),
    ], style)
    return save_figure(
        figure, output_dir, "dem-basin-context", language, text["context_title"], source_results,
        [
            {"id": "metric-dem", "source": "prepare_result.artifacts.metric_dem", "display_transform": f"2-98 percentile linear stretch; vmin={low:.6g}; vmax={high:.6g}", "palette": "terrain"},
            {"id": "basin-boundary", "source": "prepare_result.artifacts.normalized_basin", "display_transform": "boundary stroke", "palette": "boundary"},
        ], metric_meta["crs"], extent, warnings, status == "success",
    )


def _render_terrain(style, text, status, analysis, analysis_meta, basin, output_dir,
                    language, source_results, warnings):
    figure, axis, extent = _new_map(style, basin, analysis_meta["crs"], language)
    low, high = _plot_terrain(axis, analysis, analysis_meta, basin, style, text["elevation"])
    basin.boundary.plot(ax=axis, color=style["colors"]["boundary"], linewidth=style["line_widths"]["basin"], zorder=5)
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
    figure, axis, extent = _new_map(style, basin, meta["crs"], language)
    _basin_image(axis, _hillshade(filled), meta, basin, cmap="Greys", vmin=0, vmax=1, alpha=0.48, zorder=1)
    transformed = np.where(np.isfinite(accumulation) & (accumulation > 0), np.log1p(accumulation), np.nan)
    flow_image, masked = _basin_image(axis, transformed, meta, basin, cmap=linear_cmap(style, "water"), alpha=0.75, zorder=2)
    low, high = _finite_limits(masked)
    flow_image.set_clim(low, high)
    stream_mask = np.where(np.isfinite(streams) & (streams > 0), 1.0, np.nan)
    _basin_image(axis, stream_mask, meta, basin, cmap=ListedColormap([style["colors"]["water"][-1]]), zorder=4)
    basin.boundary.plot(ax=axis, color=style["colors"]["boundary"], linewidth=style["line_widths"]["basin"], zorder=5)
    _inside_colorbar(axis, flow_image, style, text["accumulation"])
    _side_legend(axis, [
        Line2D([0], [0], color=style["colors"]["water"][-1], lw=2.2, label=text["streams"]),
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
    figure, axis, extent = _new_map(style, basin, meta["crs"], language)
    inside = geometry_mask(basin.geometry, out_shape=subbasins.shape,
                           transform=Affine(*meta["transform"][:6]), invert=True)
    subbasins = np.where(inside, subbasins, np.nan)
    order = np.where(inside, order, np.nan)
    sub_ids = sorted(int(value) for value in np.unique(subbasins[np.isfinite(subbasins) & (subbasins > 0)]))
    sub_display = np.full(subbasins.shape, np.nan)
    for index, identifier in enumerate(sub_ids):
        sub_display[subbasins == identifier] = index % len(style["colors"]["subbasins"])
    _basin_image(axis, sub_display, meta, basin, cmap=listed_cmap(style, "subbasins"), alpha=0.92, zorder=1)
    valid_orders = order[np.isfinite(order) & (order > 0)]
    max_order = int(valid_orders.max()) if valid_orders.size else 0
    order_cmap = linear_cmap(style, "water")
    figure.canvas.draw()
    map_units_per_point = (extent[1] - extent[0]) / axis.get_window_extent().width * figure.dpi / 72
    transform = Affine(*meta["transform"][:6])
    cell_width = min(abs(transform.a), abs(transform.e))
    for stream_order in range(1, max_order + 1):
        mask = order == stream_order
        if not mask.any():
            continue
        width = style["line_widths"]["stream_base"] + stream_order * style["line_widths"]["stream_step"]
        cells = unary_union([shape(geometry) for geometry, _ in
                             shapes(mask.astype("uint8"), mask=mask, transform=transform)])
        # Expand only the displayed footprint to the requested stroke width.
        # Source raster values, river order and topology remain unchanged.
        radius = max(0, (width * map_units_per_point - cell_width) / 2)
        display = cells.buffer(radius).intersection(axis._basin_geometry)
        color = order_cmap((stream_order - 1) / max(1, max_order - 1))
        if not display.is_empty:
            gpd.GeoSeries([display], crs=meta["crs"]).plot(ax=axis, color=color, edgecolor="none",
                                                         zorder=2 + stream_order * 0.01)
    basin.boundary.plot(ax=axis, color=style["colors"]["boundary"], linewidth=style["line_widths"]["basin"], zorder=5)
    handles = [Patch(facecolor=style["colors"]["subbasins"][0], edgecolor="#A7AAA4", label=text["subbasins"])]
    for stream_order in range(1, max_order + 1):
        color = order_cmap((stream_order - 1) / max(1, max_order - 1))
        handles.append(Line2D([0], [0], color=color, lw=style["line_widths"]["stream_base"] + stream_order * style["line_widths"]["stream_step"], label=f"{text['stream_order']} {stream_order}"))
    _side_legend(axis, handles, style)
    return save_figure(
        figure, output_dir, "stream-order-subbasins", language, text["order_title"], source_results,
        [
            {"id": "subbasins", "source": "extract_result.artifacts.subbasins", "display_transform": "stable categorical IDs", "palette": "subbasins"},
            {"id": "stream-order", "source": "extract_result.artifacts.stream_order", "display_transform": f"positive-cell categorical order; classes=1..{max_order}; display-only stroke expansion, width={style['line_widths']['stream_base']}+order*{style['line_widths']['stream_step']} pt; source values unchanged", "palette": "water"},
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
    if basin.empty or basin.geometry.is_empty.any() or basin.geometry.isna().any():
        raise AtlasError("流域边界为空")
    if not basin.geometry.is_valid.all() or not basin.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise AtlasError("流域边界必须是有效的 Polygon 或 MultiPolygon；不自动修补")
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
        "parameters": {"language": args.language, "templates": rendered, "canvas_policy": "basin-aspect, longest edge 2400 px",
                       "display_clip": "true basin polygon", "titles_in_image": False},
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
