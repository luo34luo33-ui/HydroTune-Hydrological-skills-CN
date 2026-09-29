#!/usr/bin/env python3
"""Independently validate HydroBase topology tables, rasters, and geometry."""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
from typing import Any

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd
import rasterio
from shapely.geometry import LineString, MultiLineString


SKILL_REF = "spatial-analysis/validate-hydrological-topology"
REQUIRED_ARTIFACTS = (
    "subbasins_table", "reaches_table", "topology_table", "reaches_vector",
    "stream_links_clipped", "stream_order_clipped", "subbasins_clipped",
)
DECLARED_OUTPUTS = (
    "topology_checks.csv", "topology_check_report.txt",
    "network_topology_check.png", "result.json",
)


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_reference(path: Path, relative_to: Path | None = None) -> dict[str, str]:
    resolved = path.resolve()
    display = str(resolved)
    if relative_to is not None:
        try:
            display = resolved.relative_to(relative_to.resolve()).as_posix()
        except ValueError:
            pass
    return {"path": display, "sha256": sha256_file(resolved)}


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def write_result(output_dir: Path, document: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(
        json.dumps(json_safe(document), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def prepare_output_dir(output_dir: Path, overwrite: bool) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"输出路径不是目录: {output_dir}")
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"输出目录非空；如需替换本 Skill 的产物请使用 --overwrite: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if overwrite:
        for name in DECLARED_OUTPUTS:
            target = output_dir / name
            if target.is_file():
                target.unlink()


def resolve_reference(reference: dict[str, str], base: Path) -> Path:
    raw = Path(reference["path"])
    return raw.resolve() if raw.is_absolute() else (base / raw).resolve()


def parse_ids(value: Any) -> list[int]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    text = str(value).strip()
    if not text:
        return []
    return [int(item) for item in text.split(";") if item.strip()]


def find_cycles(downstream: dict[int, int]) -> list[list[int]]:
    cycles: list[list[int]] = []
    finished: set[int] = set()
    for start in downstream:
        if start in finished:
            continue
        path: list[int] = []
        positions: dict[int, int] = {}
        current = start
        while current in downstream and current != 0 and current not in finished:
            if current in positions:
                cycles.append(path[positions[current]:])
                break
            positions[current] = len(path)
            path.append(current)
            current = downstream.get(current, 0)
        finished.update(path)
    return cycles


def topological_levels(downstream: dict[int, int]) -> tuple[dict[int, int], set[int]]:
    upstream = {reach_id: [] for reach_id in downstream}
    for reach_id, next_id in downstream.items():
        if next_id in upstream:
            upstream[next_id].append(reach_id)
    indegree = {reach_id: len(values) for reach_id, values in upstream.items()}
    queue = deque(sorted(reach_id for reach_id, degree in indegree.items() if degree == 0))
    levels = {reach_id: 1 for reach_id in queue}
    processed: set[int] = set()
    while queue:
        reach_id = queue.popleft()
        processed.add(reach_id)
        next_id = downstream.get(reach_id, 0)
        if next_id in indegree:
            levels[next_id] = max(levels.get(next_id, 1), levels[reach_id] + 1)
            indegree[next_id] -= 1
            if indegree[next_id] == 0:
                queue.append(next_id)
    return levels, set(downstream) - processed


def longest_line(geometry: Any) -> LineString | None:
    if geometry is None or geometry.is_empty:
        return None
    if isinstance(geometry, LineString):
        return geometry
    if isinstance(geometry, MultiLineString):
        parts = list(geometry.geoms)
        return max(parts, key=lambda item: item.length) if parts else None
    return None


def draw_figure(
    subbasin_path: Path,
    reaches: gpd.GeoDataFrame,
    outlets: list[int],
    checks: pd.DataFrame,
    output_path: Path,
    basin_path: Path | None,
) -> None:
    with rasterio.open(subbasin_path) as source:
        subbasins = source.read(1).astype(float)
        subbasins[~np.isfinite(subbasins) | (subbasins <= 0)] = np.nan
        bounds = source.bounds
        raster_crs = source.crs
    figure, axis = plt.subplots(figsize=(12, 10))
    axis.imshow(
        subbasins,
        extent=[bounds.left, bounds.right, bounds.bottom, bounds.top],
        origin="upper", cmap="tab20", alpha=0.38, interpolation="nearest", zorder=1,
    )
    if not reaches.empty:
        reaches.plot(
            ax=axis, column="strahler_order", cmap="Blues",
            linewidth=1.0 + 0.55 * reaches["strahler_order"].clip(lower=1), zorder=5,
        )
        label_all = len(reaches) <= 120
        maximum_order = max(1, int(reaches["strahler_order"].max()))
        for row in reaches.itertuples(index=False):
            line = longest_line(row.geometry)
            if line is None:
                continue
            coordinates = list(line.coords)
            if len(coordinates) >= 2:
                axis.annotate(
                    "", xy=coordinates[-1], xytext=coordinates[-2],
                    arrowprops={"arrowstyle": "-|>", "color": "#08306b", "lw": 0.8},
                    zorder=7,
                )
            if label_all or row.strahler_order >= max(1, maximum_order - 1) or row.is_outlet:
                point = line.interpolate(0.5, normalized=True)
                axis.text(
                    point.x, point.y, str(row.reach_id), fontsize=7, color="#08306b",
                    ha="center", va="center",
                    bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.7, "pad": 0.6},
                    zorder=9,
                )
    for geometry in reaches.loc[reaches["reach_id"].isin(outlets), "geometry"]:
        line = longest_line(geometry)
        if line is not None:
            x_value, y_value = list(line.coords)[-1]
            axis.scatter(x_value, y_value, marker="*", s=150, color="#d7301f", zorder=12)
    if basin_path is not None and basin_path.is_file():
        basin = gpd.read_file(basin_path)
        if basin.crs is not None and raster_crs is not None:
            basin = basin.to_crs(raster_crs)
        basin.boundary.plot(ax=axis, color="black", linewidth=2.0, zorder=10)
    non_pass = int((checks["status"] != "PASS").sum())
    axis.set_title(
        f"HydroBase topology check\nreaches={len(reaches)}, outlets={len(outlets)}, warnings/failures={non_pass}"
    )
    axis.set_xlabel("Easting (m)")
    axis.set_ylabel("Northing (m)")
    axis.set_aspect("equal")
    axis.legend(handles=[
        Line2D([0], [0], color="black", lw=2, label="Basin boundary"),
        Line2D([0], [0], marker="*", color="w", markerfacecolor="#d7301f", markersize=13, label="Outlet"),
    ], loc="best")
    axis.grid(True, color="#dddddd", linewidth=0.4, alpha=0.6)
    figure.tight_layout()
    figure.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def run(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    build_result_path = args.build_result.resolve()
    if not build_result_path.is_file():
        raise FileNotFoundError(build_result_path)
    if args.expected_outlets <= 0:
        raise ValueError("--expected-outlets 必须是大于零的整数。")
    build_result = json.loads(build_result_path.read_text(encoding="utf-8-sig"))
    if build_result.get("status") == "error":
        raise ValueError("build result 状态为 error，不能进入独立验证。")
    artifacts = build_result.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("build result 缺少 artifacts object。")
    missing = [name for name in REQUIRED_ARTIFACTS if name not in artifacts]
    if missing:
        raise ValueError(f"build result 缺少 artifacts: {missing}")
    build_base = build_result_path.parent
    paths = {name: resolve_reference(artifacts[name], build_base) for name in REQUIRED_ARTIFACTS}
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(path)
        expected_hash = artifacts[name].get("sha256")
        actual_hash = sha256_file(path)
        if expected_hash != actual_hash:
            raise ValueError(f"artifact hash 不匹配: {name}")

    subbasins = pd.read_csv(paths["subbasins_table"])
    reaches = pd.read_csv(paths["reaches_table"])
    topology = pd.read_csv(paths["topology_table"])
    reach_geo = gpd.read_file(paths["reaches_vector"], layer="reaches")
    required_reach_columns = {
        "reach_id", "sub_id", "strahler_order", "length_m", "upstream_count",
        "upstream_reach_ids", "downstream_reach_id", "topo_level", "is_outlet",
        "negative_elevation_step_count",
    }
    if not required_reach_columns.issubset(reaches.columns):
        raise ValueError(f"reaches.csv 缺少列: {sorted(required_reach_columns - set(reaches.columns))}")
    if not {"sub_id", "area_km2", "cell_count"}.issubset(subbasins.columns):
        raise ValueError("subbasins.csv 缺少必需列。")

    records: list[dict[str, Any]] = []

    def add(check: str, status: str, details: str, count: int = 0) -> None:
        records.append({"check": check, "status": status, "count": int(count), "details": details})

    reach_ids = reaches["reach_id"].astype(int).tolist()
    reach_set = set(reach_ids)
    duplicates = sorted(reaches.loc[reaches["reach_id"].duplicated(keep=False), "reach_id"].astype(int).unique())
    add("unique_reach_id", "FAIL" if duplicates else "PASS", ";".join(map(str, duplicates)), len(duplicates))
    topology_columns = [
        "sub_id", "reach_id", "upstream_count", "upstream_reach_ids",
        "downstream_sub_id", "downstream_reach_id", "topo_level",
        "is_headwater", "is_outlet",
    ]
    missing_topology_columns = sorted(set(topology_columns) - set(topology.columns))
    topology_match = False
    if not missing_topology_columns:
        expected_topology = reaches[topology_columns].fillna("").copy()
        actual_topology = topology[topology_columns].fillna("").copy()
        numeric_columns = [column for column in topology_columns if column != "upstream_reach_ids"]
        for column in numeric_columns:
            expected_topology[column] = expected_topology[column].astype(int)
            actual_topology[column] = actual_topology[column].astype(int)
        expected_topology["upstream_reach_ids"] = expected_topology["upstream_reach_ids"].astype(str)
        actual_topology["upstream_reach_ids"] = actual_topology["upstream_reach_ids"].astype(str)
        topology_match = actual_topology.equals(expected_topology)
    topology_details = (
        "topology.csv matches reaches.csv"
        if topology_match
        else f"missing_columns={missing_topology_columns}; content_or_order_differs=true"
    )
    add("topology_table_consistency", "PASS" if topology_match else "FAIL", topology_details, 0 if topology_match else 1)

    downstream = dict(zip(reaches["reach_id"].astype(int), reaches["downstream_reach_id"].astype(int)))
    invalid_downstream = sorted({value for value in downstream.values() if value != 0 and value not in reach_set})
    add("downstream_references", "FAIL" if invalid_downstream else "PASS", ";".join(map(str, invalid_downstream)), len(invalid_downstream))
    self_loops = sorted(reach_id for reach_id, next_id in downstream.items() if reach_id == next_id)
    add("self_loop", "FAIL" if self_loops else "PASS", ";".join(map(str, self_loops)), len(self_loops))
    cycles = find_cycles(downstream)
    add("reach_cycle", "FAIL" if cycles else "PASS", " | ".join("->".join(map(str, cycle)) for cycle in cycles), len(cycles))

    expected_upstream = {reach_id: [] for reach_id in reach_ids}
    for reach_id, next_id in downstream.items():
        if next_id in expected_upstream:
            expected_upstream[next_id].append(reach_id)
    reciprocity_errors = []
    for row in reaches.itertuples(index=False):
        declared = sorted(parse_ids(row.upstream_reach_ids))
        expected = sorted(expected_upstream[int(row.reach_id)])
        if declared != expected or int(row.upstream_count) != len(expected):
            reciprocity_errors.append(int(row.reach_id))
    add("upstream_downstream_reciprocity", "FAIL" if reciprocity_errors else "PASS", ";".join(map(str, reciprocity_errors)), len(reciprocity_errors))

    calculated_levels, unresolved = topological_levels(downstream)
    level_errors = sorted(
        int(row.reach_id) for row in reaches.itertuples(index=False)
        if calculated_levels.get(int(row.reach_id), 0) != int(row.topo_level)
    )
    level_failures = sorted(set(level_errors) | unresolved)
    add("topological_levels", "FAIL" if level_failures else "PASS", ";".join(map(str, level_failures)), len(level_failures))

    outlets = sorted(reach_id for reach_id, next_id in downstream.items() if next_id == 0)
    outlet_ok = len(outlets) == args.expected_outlets
    add("expected_outlets", "PASS" if outlet_ok else "FAIL", f"expected={args.expected_outlets}; actual={outlets}", len(outlets))
    sub_ids = set(subbasins["sub_id"].astype(int))
    missing_sub = sorted(
        int(row.reach_id) for row in reaches.itertuples(index=False)
        if int(row.sub_id) <= 0 or int(row.sub_id) not in sub_ids
    )
    add("subbasin_mapping", "FAIL" if missing_sub else "PASS", ";".join(map(str, missing_sub)), len(missing_sub))
    nonpositive_length = sorted(reaches.loc[reaches["length_m"] <= 0, "reach_id"].astype(int).tolist())
    add("positive_reach_length", "FAIL" if nonpositive_length else "PASS", ";".join(map(str, nonpositive_length)), len(nonpositive_length))

    geometry_ids = set(reach_geo["reach_id"].astype(int)) if "reach_id" in reach_geo else set()
    bad_geometry = reach_geo.geometry.isna() | reach_geo.geometry.is_empty | ~reach_geo.geometry.is_valid
    geometry_errors = sorted((reach_set - geometry_ids) | set(reach_geo.loc[bad_geometry, "reach_id"].astype(int)))
    add("reach_geometry", "FAIL" if geometry_errors else "PASS", ";".join(map(str, geometry_errors)), len(geometry_errors))

    raster_info = {}
    for name in ("stream_links_clipped", "stream_order_clipped", "subbasins_clipped"):
        with rasterio.open(paths[name]) as source:
            raster_info[name] = (source.read(1), source.transform, source.crs)
    reference = raster_info["stream_links_clipped"]
    grid_errors = []
    for name, item in raster_info.items():
        if item[0].shape != reference[0].shape or item[2] != reference[2] or not item[1].almost_equals(reference[1]):
            grid_errors.append(name)
    if reach_geo.crs is None or reference[2] is None or reach_geo.crs != reference[2]:
        grid_errors.append("reaches_vector_crs")
    add("artifact_grid_and_crs", "FAIL" if grid_errors else "PASS", ";".join(grid_errors), len(grid_errors))

    link_ids = set(np.unique(reference[0][np.isfinite(reference[0]) & (reference[0] > 0)]).astype(int))
    count_match = link_ids == reach_set
    add("reach_count_matches_links", "PASS" if count_match else "FAIL", f"table={len(reach_set)}; raster={len(link_ids)}", len(reach_set ^ link_ids))
    sub_grid = raster_info["subbasins_clipped"][0]
    cell_area_m2 = abs(
        reference[1].a * reference[1].e - reference[1].b * reference[1].d
    )
    area_errors = []
    for row in subbasins.itertuples(index=False):
        count = int(np.count_nonzero(sub_grid == int(row.sub_id)))
        expected_area = count * cell_area_m2 / 1_000_000.0
        if int(row.cell_count) != count or not math.isclose(float(row.area_km2), expected_area, abs_tol=1e-6):
            area_errors.append(int(row.sub_id))
    add("subbasin_area_consistency", "FAIL" if area_errors else "PASS", ";".join(map(str, area_errors)), len(area_errors))

    negative_steps = int(reaches["negative_elevation_step_count"].sum())
    add("negative_elevation_steps", "WARN" if negative_steps else "PASS", f"count={negative_steps}", negative_steps)
    for upstream_check in build_result.get("checks", []):
        if upstream_check.get("status") == "WARN" and upstream_check.get("check") != "negative_elevation_steps":
            add(
                f"upstream_{upstream_check.get('check', 'warning')}", "WARN",
                str(upstream_check.get("details", "")), 1,
            )
    for index, warning in enumerate(build_result.get("warnings", []), start=1):
        check_name = f"upstream_warning_{index}"
        if not any(item["details"] == str(warning) for item in records):
            add(check_name, "WARN", str(warning), 1)

    check_frame = pd.DataFrame(records)
    check_path = args.output_dir / "topology_checks.csv"
    report_path = args.output_dir / "topology_check_report.txt"
    figure_path = args.output_dir / "network_topology_check.png"
    check_frame.to_csv(check_path, index=False, encoding="utf-8")
    fail_count = int((check_frame["status"] == "FAIL").sum())
    warning_count = int((check_frame["status"] == "WARN").sum())
    report_lines = [
        "HydroBase 拓扑检查报告", "=" * 40,
        f"河段数: {len(reaches)}", f"子流域数: {len(subbasins)}",
        f"出口河段: {outlets}", f"FAIL: {fail_count}", f"WARN: {warning_count}", "",
    ]
    report_lines.extend(
        f"[{row.status}] {row.check}: count={row.count}; {row.details}"
        for row in check_frame.itertuples(index=False)
    )
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    basin_path = None
    basin_reference = build_result.get("inputs", {}).get("basin_boundary")
    if isinstance(basin_reference, dict) and "path" in basin_reference:
        basin_path = resolve_reference(basin_reference, build_base)
    draw_figure(paths["subbasins_clipped"], reach_geo, outlets, check_frame, figure_path, basin_path)

    has_failures = fail_count > 0
    status = "error" if has_failures else ("warning" if warning_count else "success")
    warnings = check_frame.loc[check_frame["status"] == "WARN", "details"].astype(str).tolist()
    result_checks = [
        {"check": str(row.check), "status": str(row.status), "details": str(row.details)}
        for row in check_frame.itertuples(index=False)
    ]
    document = {
        "schema_version": "1.0", "skill": SKILL_REF, "status": status,
        "message": f"Topology validation completed: FAIL={fail_count}, WARN={warning_count}",
        "parameters": {"expected_outlets": args.expected_outlets},
        "inputs": {"build_result": file_reference(build_result_path)},
        "artifacts": {
            "topology_checks": file_reference(check_path, args.output_dir),
            "topology_report": file_reference(report_path, args.output_dir),
            "topology_figure": file_reference(figure_path, args.output_dir),
        },
        "checks": result_checks, "warnings": warnings,
        "provenance": {
            "python": sys.version.split()[0], "pandas": package_version("pandas"),
            "rasterio": package_version("rasterio"), "geopandas": package_version("geopandas"),
            "matplotlib": package_version("matplotlib"),
        },
    }
    return document, has_failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-result", type=Path, required=True)
    parser.add_argument("--expected-outlets", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.output_dir = args.output_dir.resolve()
    prepared = False
    try:
        prepare_output_dir(args.output_dir, args.overwrite)
        prepared = True
        document, has_failures = run(args)
        write_result(args.output_dir, document)
        print(f"{document['status']}: {document['message']}")
        return 2 if has_failures else 0
    except Exception as exc:
        if prepared:
            write_result(args.output_dir, {
                "schema_version": "1.0", "skill": SKILL_REF, "status": "error",
                "message": str(exc), "parameters": json_safe(vars(args)), "inputs": {},
                "artifacts": {}, "checks": [], "warnings": [],
                "provenance": {"python": sys.version.split()[0]},
            })
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
