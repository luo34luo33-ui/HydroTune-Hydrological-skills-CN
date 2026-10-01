#!/usr/bin/env python3
"""Render the fixed HydroTune HydroBase topology, morphometry and QC atlas."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
import rasterio
from shapely.geometry import LineString, MultiLineString

from _atlas_common import (
    AtlasError, add_header, add_status_badge, artifact_path, assert_same_grid,
    configure_rendering, decorate_map, extent_from_meta, file_reference,
    linear_cmap, listed_cmap, load_result, load_style, new_figure,
    prepare_output_dir, provenance, read_raster, resolve_reference, save_figure,
    write_result,
)


SKILL_REF = "visualization-reporting/visualize-hydrobase-atlas"
BUILD_SKILL = "spatial-analysis/build-hydrological-topology"
VALIDATION_SKILL = "spatial-analysis/validate-hydrological-topology"
TEMPLATES = ["hydrobase-topology", "hydrobase-morphometry", "hydrobase-qc-dashboard"]
BUILD_ARTIFACTS = (
    "subbasins_table", "reaches_table", "topology_table", "reaches_vector",
    "stream_links_clipped", "stream_order_clipped", "subbasins_clipped",
)
QC_ARTIFACTS = ("topology_checks", "topology_report", "topology_figure")

TEXT = {
    "zh": {
        "topology_title": "HydroBase 水文拓扑",
        "topology_subtitle": "子流域分区、Strahler 分级河网、主要流向与出口",
        "morph_title": "HydroBase 形态属性",
        "morph_subtitle": "子流域面积、河段坡度与拓扑层级 · 全部来自既有成果",
        "qc_title": "HydroBase 质量控制看板",
        "qc_subtitle": "只呈现独立验证产生的检查证据，不重新计算或解释失败原因",
        "area": "子流域面积（km²）",
        "slope": "河段坡度（%）",
        "level": "拓扑层级",
        "order": "Strahler 等级",
        "subbasins": "子流域",
        "outlet": "出口",
        "checks": "检查汇总",
        "evidence": "重要证据",
        "limitations": "限制说明",
        "limitations_text": "图册不修复拓扑、不重算指标，也不隐藏上游 warning。",
        "no_issues": "未记录 WARN 或 FAIL",
    },
    "en": {
        "topology_title": "HydroBase hydrological topology",
        "topology_subtitle": "Subbasins, Strahler-ordered reaches, selected flow directions and outlets",
        "morph_title": "HydroBase morphometry",
        "morph_subtitle": "Subbasin area, reach slope and topological level · values come from existing artifacts",
        "qc_title": "HydroBase quality-control dashboard",
        "qc_subtitle": "Presents independent validation evidence only; no checks or causes are recomputed",
        "area": "Subbasin area (km²)",
        "slope": "Reach slope (%)",
        "level": "Topological level",
        "order": "Strahler order",
        "subbasins": "Subbasins",
        "outlet": "Outlet",
        "checks": "Check summary",
        "evidence": "Key evidence",
        "limitations": "Limitations",
        "limitations_text": "The atlas does not repair topology, recompute metrics, or hide upstream warnings.",
        "no_issues": "No WARN or FAIL evidence recorded",
    },
}


def _validate_vector_crs(frame: gpd.GeoDataFrame, expected: str) -> None:
    if frame.empty:
        raise AtlasError("河段矢量为空")
    if frame.crs is None:
        raise AtlasError("河段矢量缺少 CRS")
    if rasterio.crs.CRS.from_user_input(frame.crs) != rasterio.crs.CRS.from_user_input(expected):
        raise AtlasError("河段矢量 CRS 与栅格不一致")


def _line_parts(geometry):
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)
    return []


def _longest_line(geometry):
    parts = _line_parts(geometry)
    return max(parts, key=lambda line: line.length) if parts else None


def _plot_subbasins(axis, values: np.ndarray, extent: list[float], style, alpha: float = 0.9):
    identifiers = sorted(int(value) for value in np.unique(values[np.isfinite(values) & (values > 0)]))
    display = np.full(values.shape, np.nan)
    for index, identifier in enumerate(identifiers):
        display[values == identifier] = index % len(style["colors"]["subbasins"])
    axis.imshow(display, extent=extent, origin="upper", cmap=listed_cmap(style, "subbasins"), interpolation="nearest", alpha=alpha, zorder=1)
    return identifiers


def _plot_reaches(axis, reaches: gpd.GeoDataFrame, style, arrows: bool, labels: bool, text,
                  outlet_labels: bool = True):
    max_order = max(1, int(pd.to_numeric(reaches["strahler_order"], errors="coerce").fillna(1).max()))
    cmap = linear_cmap(style, "water")
    norm = Normalize(vmin=1, vmax=max_order if max_order > 1 else 2)
    ordered = reaches.sort_values(["strahler_order", "reach_id"])
    for row in ordered.itertuples():
        order = max(1, int(row.strahler_order))
        color = cmap(norm(order))
        width = style["line_widths"]["stream_base"] + order * style["line_widths"]["stream_step"]
        gpd.GeoSeries([row.geometry], crs=reaches.crs).plot(ax=axis, color=color, linewidth=width, zorder=5)
    selected = reaches.sort_values(["strahler_order", "length_m", "reach_id"], ascending=[False, False, True])
    selected = selected.head(min(len(selected), 30, style["layout"]["max_reach_labels"]))
    if arrows:
        for row in selected.itertuples():
            line = _longest_line(row.geometry)
            if line is None or line.length <= 0:
                continue
            start = line.interpolate(0.46, normalized=True)
            end = line.interpolate(0.56, normalized=True)
            axis.annotate("", xy=(end.x, end.y), xytext=(start.x, start.y),
                          arrowprops={"arrowstyle": "-|>", "color": style["colors"]["ink"], "lw": 0.65, "alpha": 0.76}, zorder=8)
    outlets = reaches[reaches["is_outlet"].astype(bool)].sort_values("reach_id")
    outlet_points = []
    for row in outlets.itertuples():
        line = _longest_line(row.geometry)
        if line is None:
            continue
        point = line.interpolate(1.0, normalized=True)
        outlet_points.append((int(row.reach_id), point))
        axis.scatter([point.x], [point.y], marker="*", s=115, facecolor="#F2C14E", edgecolor=style["colors"]["ink"], linewidth=0.7, zorder=10)
    if outlet_labels and outlet_points:
        ordered_points = sorted(outlet_points, key=lambda item: (item[1].x, item[1].y, item[0]))
        label_count = min(5, len(ordered_points))
        chosen_indices = sorted({round(index * (len(ordered_points) - 1) / max(1, label_count - 1)) for index in range(label_count)})
        for position, item_index in enumerate(chosen_indices):
            reach_id, point = ordered_points[item_index]
            y_offset = 10 + (position % 2) * 11
            axis.annotate(f"{text['outlet']} {reach_id}", (point.x, point.y), xytext=(0, y_offset),
                          textcoords="offset points", ha="center", fontsize=7.0, weight="bold", zorder=11,
                          bbox={"boxstyle": "round,pad=0.16", "facecolor": "white", "edgecolor": "none", "alpha": 0.82})
    if labels:
        major = selected[selected["strahler_order"] == selected["strahler_order"].max()].head(12)
        bounds = reaches.total_bounds
        minimum_distance = 0.055 * float(np.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1]))
        accepted_points = []
        for row in major.itertuples():
            line = _longest_line(row.geometry)
            if line is None:
                continue
            point = line.interpolate(0.5, normalized=True)
            if point.y < bounds[1] + 0.18 * (bounds[3] - bounds[1]):
                continue
            if any(point.distance(previous) < minimum_distance for previous in accepted_points):
                continue
            accepted_points.append(point)
            axis.text(point.x, point.y, str(int(row.reach_id)), fontsize=6.8, ha="center", va="center",
                      bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "none", "alpha": 0.78}, zorder=9)
    return max_order, outlets


def _legend(axis, style, max_order, text):
    cmap = linear_cmap(style, "water")
    norm = Normalize(vmin=1, vmax=max_order if max_order > 1 else 2)
    handles = [Patch(facecolor=style["colors"]["subbasins"][0], edgecolor="#A6AAA5", label=text["subbasins"])]
    for order in range(1, max_order + 1):
        handles.append(Line2D([0], [0], color=cmap(norm(order)),
                              lw=style["line_widths"]["stream_base"] + order * style["line_widths"]["stream_step"],
                              label=f"{text['order']} {order}"))
    handles.append(Line2D([0], [0], marker="*", color="none", markerfacecolor="#F2C14E", markeredgecolor=style["colors"]["ink"], markersize=10, label=text["outlet"]))
    axis.legend(handles=handles, loc="center left", bbox_to_anchor=(1.035, 0.52), frameon=False, labelspacing=0.9)


from _topology_plot import render_topology as _render_topology


from _morphometry_plot import render_morphometry as _render_morphometry


def _render_dashboard(style, text, status, subbasins, meta, reaches, validation, output_dir,
                      language, source_results, warnings, basin, sub_table):
    return _render_topology(style, text, status, subbasins, meta, reaches, output_dir,
                            language, source_results, warnings, basin, sub_table, validation=validation)


def run(args: argparse.Namespace) -> tuple[dict, int]:
    style = load_style(Path(__file__))
    font_name = configure_rendering(style, args.language)
    build_path = args.build_result.resolve()
    validation_path = args.validation_result.resolve()
    build = load_result(build_path, BUILD_SKILL)
    validation = load_result(validation_path, VALIDATION_SKILL, allow_scientific_error=True)
    validation_inputs = validation.get("inputs", {})
    if "build_result" not in validation_inputs:
        raise AtlasError("validation result 缺少 inputs.build_result，无法确认 QC 与当前 HydroBase 对应")
    linked_build = resolve_reference(validation_inputs["build_result"], validation_path, "validation input build_result")
    if linked_build != build_path:
        raise AtlasError("validation result 并非针对当前 build result 生成")
    checks = validation.get("checks")
    if not isinstance(checks, list):
        raise AtlasError("validation result 缺少 checks array")
    scientific_fail = validation.get("status") == "error" and any(check.get("status") == "FAIL" for check in checks)
    if validation.get("status") == "error" and not scientific_fail:
        raise AtlasError("validation result 为运行错误，未发现可展示的科学 QC FAIL 证据")

    build_paths = {key: artifact_path(build, build_path, key) for key in BUILD_ARTIFACTS}
    qc_paths = {key: artifact_path(validation, validation_path, key) for key in QC_ARTIFACTS}
    del qc_paths
    subbasins, sub_meta = read_raster(build_paths["subbasins_clipped"])
    _, links_meta = read_raster(build_paths["stream_links_clipped"])
    _, order_meta = read_raster(build_paths["stream_order_clipped"])
    assert_same_grid(sub_meta, [("stream_links_clipped", links_meta), ("stream_order_clipped", order_meta)])
    reaches = gpd.read_file(build_paths["reaches_vector"], layer="reaches")
    _validate_vector_crs(reaches, sub_meta["crs"])
    required_columns = {"reach_id", "strahler_order", "length_m", "slope_percent", "topo_level", "is_outlet", "geometry"}
    missing_columns = required_columns - set(reaches.columns)
    if missing_columns:
        raise AtlasError(f"reaches vector 缺少字段: {sorted(missing_columns)}")
    sub_table = pd.read_csv(build_paths["subbasins_table"])
    if not {"sub_id", "area_km2"}.issubset(sub_table.columns):
        raise AtlasError("subbasins.csv 缺少 sub_id 或 area_km2")
    source_results = {
        "build_result": file_reference(build_path),
        "validation_result": file_reference(validation_path),
    }
    warnings = list(build.get("warnings", [])) + list(validation.get("warnings", []))
    status = "error" if scientific_fail else ("warning" if validation.get("status") == "warning" or warnings else "success")
    text = TEXT[args.language]
    artifacts = {}
    rendered = []
    boundary_path = resolve_reference(build.get("inputs", {}).get("basin_boundary"), build_path, "basin boundary")
    basin_layer = build.get("parameters", {}).get("basin_layer")
    basin = gpd.read_file(boundary_path, **({"layer": basin_layer} if basin_layer else {}))
    if basin.empty or basin.crs is None or not basin.geometry.is_valid.all():
        raise AtlasError("真实流域边界为空、缺少 CRS 或几何无效")
    basin = basin.to_crs(sub_meta["crs"])
    morph_sources = {**source_results, "basin_boundary": file_reference(boundary_path)}
    if not scientific_fail:
        artifacts.update(_render_topology(style, text, status, subbasins, sub_meta, reaches, args.output_dir,
                                          args.language, morph_sources, warnings, basin, sub_table))
        artifacts.update(_render_morphometry(style, text, status, subbasins, sub_meta, reaches, sub_table,
                                             args.output_dir, args.language, morph_sources, warnings, basin))
        rendered.extend(["hydrobase-topology", "hydrobase-morphometry"])
    artifacts.update(_render_dashboard(style, text, status, subbasins, sub_meta, reaches, validation,
                                       args.output_dir, args.language, morph_sources, warnings, basin, sub_table))
    rendered.append("hydrobase-qc-dashboard")
    result_checks = [
        {"check": "input_hashes", "status": "PASS", "details": "all consumed build and validation artifacts verified"},
        {"check": "build_grid_alignment", "status": "PASS", "details": "three clipped rasters share CRS, transform and shape"},
        {"check": "upstream_scientific_qc", "status": "FAIL" if scientific_fail else ("WARN" if status == "warning" else "PASS"),
         "details": f"validation status={validation.get('status')}"},
    ]
    message = "scientific QC failed; rendered failure dashboard only" if scientific_fail else f"rendered {len(rendered)} fixed atlas templates"
    document = {
        "schema_version": "1.0", "skill": SKILL_REF, "status": status, "message": message,
        "parameters": {"language": args.language, "templates": rendered, "pixel_sizes": {template: json.loads((args.output_dir / artifacts[f"{template}_figure"]["path"]).read_text(encoding="utf-8"))["pixel_size"] for template in rendered}},
        "inputs": source_results, "artifacts": artifacts, "checks": result_checks,
        "warnings": warnings, "provenance": provenance(font_name),
    }
    return document, 2 if scientific_fail else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-result", type=Path, required=True)
    parser.add_argument("--validation-result", type=Path, required=True)
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
        document, exit_code = run(args)
        write_result(args.output_dir, document)
        print(f"{document['status']}: {document['message']}")
        return exit_code
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
