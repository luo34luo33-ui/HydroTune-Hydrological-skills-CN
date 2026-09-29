#!/usr/bin/env python3
"""Build a clipped HydroBase topology from aligned Whitebox D8 products."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.crs import CRS
from rasterio.features import geometry_mask
from rasterio.transform import xy
from shapely.geometry import GeometryCollection, LineString, MultiLineString
from shapely.ops import linemerge, unary_union


SKILL_REF = "spatial-analysis/build-hydrological-topology"
D8_OFFSETS = {
    1: (-1, 1), 2: (0, 1), 4: (1, 1), 8: (1, 0),
    16: (1, -1), 32: (0, -1), 64: (-1, -1), 128: (-1, 0),
}
OUTPUT_NAMES = {
    "subbasins_table": "subbasins.csv",
    "reaches_table": "reaches.csv",
    "topology_table": "topology.csv",
    "reaches_vector": "reaches_clipped.gpkg",
    "stream_links_clipped": "stream_links_clipped.tif",
    "stream_order_clipped": "stream_order_clipped.tif",
    "subbasins_clipped": "subbasins_clipped.tif",
}
DECLARED_OUTPUTS = tuple(OUTPUT_NAMES.values()) + ("result.json",)


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


def read_raster(path: Path) -> tuple[np.ndarray, Any, CRS, float | None, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with rasterio.open(path) as source:
        if source.crs is None:
            raise ValueError(f"栅格没有 CRS: {path}")
        return source.read(1), source.transform, source.crs, source.nodata, source.profile.copy()


def assert_same_grid(reference: tuple, named: list[tuple[str, tuple]]) -> None:
    ref_array, ref_transform, ref_crs = reference[:3]
    for name, item in named:
        array, transform, crs = item[:3]
        if array.shape != ref_array.shape:
            raise ValueError(f"{name} 与 filled DEM 尺寸不一致。")
        if crs != ref_crs:
            raise ValueError(f"{name} 与 filled DEM CRS 不一致。")
        if not transform.almost_equals(ref_transform):
            raise ValueError(f"{name} 与 filled DEM 网格不一致。")


def safe_int(value: Any) -> int | None:
    try:
        if not np.isfinite(value):
            return None
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def mode_positive(values: np.ndarray, default: int = 0) -> int:
    values = values[np.isfinite(values) & (values > 0)].astype(np.int64)
    if values.size == 0:
        return default
    ids, counts = np.unique(values, return_counts=True)
    return int(ids[np.argmax(counts)])


def group_cells(grid: np.ndarray) -> dict[int, np.ndarray]:
    rows, cols = np.where(np.isfinite(grid) & (grid > 0))
    if rows.size == 0:
        return {}
    ids = grid[rows, cols].astype(np.int64)
    order = np.argsort(ids, kind="stable")
    ids, rows, cols = ids[order], rows[order], cols[order]
    split = np.flatnonzero(np.diff(ids)) + 1
    groups: dict[int, np.ndarray] = {}
    for indices in np.split(np.arange(ids.size), split):
        groups[int(ids[indices[0]])] = np.column_stack((rows[indices], cols[indices]))
    return groups


def extract_line_only(geometry: Any) -> LineString | MultiLineString | None:
    if geometry is None or geometry.is_empty:
        return None
    if isinstance(geometry, (LineString, MultiLineString)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        lines = [item for item in geometry.geoms if isinstance(item, (LineString, MultiLineString))]
        if lines:
            merged = linemerge(unary_union(lines))
            return merged if not merged.is_empty else None
    return None


class NetworkContext:
    def __init__(
        self,
        dem: np.ndarray,
        pointer: np.ndarray,
        accumulation: np.ndarray,
        links: np.ndarray,
        subbasins: np.ndarray,
        orders: np.ndarray,
        transform: Any,
        crs: CRS,
        dem_nodata: float | None,
        basin: gpd.GeoDataFrame,
        all_touched: bool,
    ) -> None:
        self.dem = dem
        self.pointer = pointer
        self.accumulation = accumulation
        self.links = links
        self.subbasins = subbasins
        self.orders = orders
        self.transform = transform
        self.crs = crs
        self.dem_nodata = dem_nodata
        self.nrows, self.ncols = dem.shape
        self.basin = basin
        self.basin_geom = basin.geometry.iloc[0]
        self.inside = geometry_mask(
            [self.basin_geom.__geo_interface__], out_shape=dem.shape,
            transform=transform, invert=True, all_touched=all_touched,
        )
        self.cells_by_reach = group_cells(links)

    def next_cell(self, row: int, col: int) -> tuple[int, int] | None:
        pointer = safe_int(self.pointer[row, col])
        if pointer not in D8_OFFSETS:
            return None
        delta_row, delta_col = D8_OFFSETS[pointer]
        next_row, next_col = row + delta_row, col + delta_col
        if not (0 <= next_row < self.nrows and 0 <= next_col < self.ncols):
            return None
        return next_row, next_col

    def cell_xy(self, cell: tuple[int, int]) -> tuple[float, float]:
        x_value, y_value = xy(self.transform, cell[0], cell[1], offset="center")
        return float(x_value), float(y_value)

    def valid_elevation(self, value: float) -> bool:
        if not np.isfinite(value):
            return False
        return self.dem_nodata is None or not math.isclose(
            value, float(self.dem_nodata), rel_tol=0.0, abs_tol=1e-12
        )


def outlet_cell(context: NetworkContext, reach_id: int) -> tuple[int, int] | None:
    cells = context.cells_by_reach.get(reach_id)
    if cells is None or len(cells) == 0:
        return None
    candidates: list[tuple[int, int]] = []
    for raw_row, raw_col in cells:
        cell = (int(raw_row), int(raw_col))
        next_cell = context.next_cell(*cell)
        if next_cell is None or safe_int(context.links[next_cell]) != reach_id:
            candidates.append(cell)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda cell: float(context.accumulation[cell])
        if np.isfinite(context.accumulation[cell]) else -math.inf,
    )


def trace_downstream_reach(context: NetworkContext, reach_id: int) -> tuple[int, str | None]:
    current = outlet_cell(context, reach_id)
    if current is None:
        return 0, "no_outlet_cell"
    visited: set[tuple[int, int]] = set()
    for _ in range(context.nrows * context.ncols):
        if current in visited:
            return 0, "d8_cell_cycle"
        visited.add(current)
        next_cell = context.next_cell(*current)
        if next_cell is None:
            return 0, None
        next_reach = safe_int(context.links[next_cell]) or 0
        if next_reach > 0 and next_reach != reach_id:
            return next_reach, None
        current = next_cell
    return 0, "trace_limit_exceeded"


def direct_downstream_candidates(context: NetworkContext, reach_id: int) -> set[int]:
    candidates: set[int] = set()
    for raw_row, raw_col in context.cells_by_reach.get(reach_id, []):
        next_cell = context.next_cell(int(raw_row), int(raw_col))
        if next_cell is not None:
            other = safe_int(context.links[next_cell]) or 0
            if other > 0 and other != reach_id:
                candidates.add(other)
    return candidates


def reach_to_subbasin(context: NetworkContext, reach_id: int) -> int:
    cells = context.cells_by_reach.get(reach_id)
    if cells is None:
        return 0
    rows, cols = cells[:, 0], cells[:, 1]
    inside = context.inside[rows, cols]
    result = mode_positive(context.subbasins[rows[inside], cols[inside]], default=0)
    return result or mode_positive(context.subbasins[rows, cols], default=0)


def ordered_reach_paths(context: NetworkContext, reach_id: int) -> list[list[tuple[int, int]]]:
    cells_array = context.cells_by_reach.get(reach_id)
    if cells_array is None or len(cells_array) == 0:
        return []
    cells = {(int(row), int(col)) for row, col in cells_array}
    incoming = {cell: 0 for cell in cells}
    for cell in cells:
        next_cell = context.next_cell(*cell)
        if next_cell in cells:
            incoming[next_cell] += 1
    starts = sorted(
        (cell for cell, count in incoming.items() if count == 0),
        key=lambda cell: float(context.accumulation[cell]),
    ) or [min(cells)]
    visited: set[tuple[int, int]] = set()
    paths: list[list[tuple[int, int]]] = []

    def walk(start: tuple[int, int]) -> list[tuple[int, int]]:
        path: list[tuple[int, int]] = []
        current = start
        local: set[tuple[int, int]] = set()
        while current in cells and current not in local:
            path.append(current)
            visited.add(current)
            local.add(current)
            next_cell = context.next_cell(*current)
            if next_cell not in cells:
                if next_cell is not None and (safe_int(context.links[next_cell]) or 0) > 0:
                    path.append(next_cell)
                break
            current = next_cell
        return path

    for start in starts:
        if start not in visited:
            paths.append(walk(start))
    for cell in sorted(cells - visited):
        paths.append(walk(cell))
    return [path for path in paths if path]


def reach_geometry(context: NetworkContext, reach_id: int) -> Any:
    lines = []
    for path in ordered_reach_paths(context, reach_id):
        coordinates = [context.cell_xy(cell) for cell in path]
        if len(coordinates) >= 2 and len(set(coordinates)) >= 2:
            lines.append(LineString(coordinates))
    if not lines:
        return None
    geometry = lines[0] if len(lines) == 1 else linemerge(unary_union(lines))
    return extract_line_only(geometry.intersection(context.basin_geom))


def reach_metrics(context: NetworkContext, reach_id: int) -> dict[str, Any]:
    length, drop = 0.0, 0.0
    step_count, negative_steps = 0, 0
    upstream_elevations: list[tuple[float, float]] = []
    downstream_elevations: list[tuple[float, float]] = []
    for raw_row, raw_col in context.cells_by_reach.get(reach_id, []):
        row, col = int(raw_row), int(raw_col)
        next_cell = context.next_cell(row, col)
        if next_cell is None or (safe_int(context.links[next_cell]) or 0) <= 0:
            continue
        x1, y1 = context.cell_xy((row, col))
        x2, y2 = context.cell_xy(next_cell)
        full_distance = math.hypot(x2 - x1, y2 - y1)
        if full_distance <= 0:
            continue
        clipped_segment = LineString([(x1, y1), (x2, y2)]).intersection(context.basin_geom)
        distance = float(clipped_segment.length)
        if distance <= 0:
            continue
        length += distance
        z1, z2 = float(context.dem[row, col]), float(context.dem[next_cell])
        if context.valid_elevation(z1) and context.valid_elevation(z2):
            delta = z1 - z2
            step_count += 1
            if delta < -1e-9:
                negative_steps += 1
            drop += max(delta, 0.0) * min(1.0, distance / full_distance)
            upstream_elevations.append((float(context.accumulation[row, col]), z1))
            downstream_elevations.append((float(context.accumulation[next_cell]), z2))
    slope = drop / length if length > 0 else math.nan
    elevation_up = min(upstream_elevations, key=lambda item: item[0])[1] if upstream_elevations else math.nan
    elevation_down = max(downstream_elevations, key=lambda item: item[0])[1] if downstream_elevations else math.nan
    return {
        "length_m": round(length, 2),
        "elev_up_m": round(elevation_up, 3) if np.isfinite(elevation_up) else np.nan,
        "elev_down_m": round(elevation_down, 3) if np.isfinite(elevation_down) else np.nan,
        "drop_m": round(drop, 3),
        "slope_m_m": round(slope, 8) if np.isfinite(slope) else np.nan,
        "slope_percent": round(slope * 100.0, 6) if np.isfinite(slope) else np.nan,
        "elevation_step_count": step_count,
        "negative_elevation_step_count": negative_steps,
    }


def find_reach_cycles(downstream: dict[int, int]) -> list[list[int]]:
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
    upstream: dict[int, list[int]] = {reach_id: [] for reach_id in downstream}
    for reach_id, next_id in downstream.items():
        if next_id in upstream:
            upstream[next_id].append(reach_id)
    indegree = {reach_id: len(items) for reach_id, items in upstream.items()}
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


def write_clipped(source_path: Path, output_path: Path, inside: np.ndarray) -> None:
    with rasterio.open(source_path) as source:
        data = source.read(1)
        output = np.where(inside, data, 0).astype(data.dtype, copy=False)
        profile = source.profile.copy()
        profile.update(nodata=0, compress="lzw")
        with rasterio.open(output_path, "w", **profile) as destination:
            destination.write(output, 1)


def load_basin(path: Path, layer: str | None, target_crs: CRS) -> gpd.GeoDataFrame:
    basin = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
    if basin.crs is None:
        raise ValueError("流域边界没有 CRS。")
    basin = basin[basin.geometry.notna() & ~basin.geometry.is_empty].copy()
    if basin.empty:
        raise ValueError("流域边界没有有效几何。")
    basin = basin.to_crs(target_crs)
    geometry = unary_union(basin.geometry)
    if geometry.is_empty:
        raise ValueError("流域边界合并后为空。")
    return gpd.GeoDataFrame({"basin_id": [1]}, geometry=[geometry], crs=target_crs)


def propagated_warnings(paths: list[Path] | None) -> tuple[list[str], dict[str, dict[str, str]]]:
    warnings: list[str] = []
    references: dict[str, dict[str, str]] = {}
    for index, path in enumerate(paths or [], start=1):
        resolved = path.resolve()
        document = json.loads(resolved.read_text(encoding="utf-8-sig"))
        if document.get("status") == "error":
            raise ValueError(f"上游 result.json 为 error: {resolved}")
        warnings.extend(str(item) for item in document.get("warnings", []))
        references[f"upstream_result_{index}"] = file_reference(resolved)
    return warnings, references


def run(args: argparse.Namespace) -> dict[str, Any]:
    paths = {
        "basin_boundary": args.basin.resolve(),
        "filled_dem": args.dem_filled.resolve(),
        "d8_pointer": args.d8_pointer.resolve(),
        "flow_accumulation": args.flow_accumulation.resolve(),
        "stream_links": args.stream_links.resolve(),
        "stream_order": args.stream_order.resolve(),
        "subbasins": args.subbasins.resolve(),
    }
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    warnings, upstream_references = propagated_warnings(args.upstream_result)
    dem_info = read_raster(paths["filled_dem"])
    pointer_info = read_raster(paths["d8_pointer"])
    accumulation_info = read_raster(paths["flow_accumulation"])
    links_info = read_raster(paths["stream_links"])
    order_info = read_raster(paths["stream_order"])
    subbasin_info = read_raster(paths["subbasins"])
    assert_same_grid(dem_info, [
        ("D8 Pointer", pointer_info), ("Flow Accumulation", accumulation_info),
        ("Stream Links", links_info), ("Stream Order", order_info),
        ("Subbasins", subbasin_info),
    ])
    pointer_values = pointer_info[0]
    positive_pointers = set(
        np.unique(pointer_values[np.isfinite(pointer_values) & (pointer_values > 0)]).astype(int).tolist()
    )
    unsupported = sorted(positive_pointers - set(D8_OFFSETS))
    if unsupported:
        raise ValueError(f"D8 Pointer 包含不支持的值，预期 Whitebox 非 ESRI 编码: {unsupported}")

    crs = dem_info[2]
    factor = crs.linear_units_factor
    if isinstance(factor, tuple):
        factor = factor[1]
    if not crs.is_projected or not math.isclose(float(factor), 1.0, abs_tol=1e-9):
        raise ValueError("HydroBase 面积和长度要求米制投影 CRS。")
    basin = load_basin(paths["basin_boundary"], args.basin_layer, crs)
    context = NetworkContext(
        dem_info[0].astype(float), pointer_info[0], accumulation_info[0].astype(float),
        links_info[0], subbasin_info[0], order_info[0], dem_info[1], crs,
        dem_info[3], basin, args.cell_inclusion == "all-touched",
    )
    all_reaches = sorted(context.cells_by_reach)
    if not all_reaches:
        raise ValueError("Stream Links 不包含河段。")
    full_downstream: dict[int, int] = {}
    trace_warnings: list[str] = []
    multiple_candidates: list[str] = []
    for reach_id in all_reaches:
        next_id, warning = trace_downstream_reach(context, reach_id)
        full_downstream[reach_id] = next_id
        if warning:
            trace_warnings.append(f"{reach_id}:{warning}")
        candidates = sorted(direct_downstream_candidates(context, reach_id))
        if len(candidates) > 1:
            multiple_candidates.append(f"{reach_id}->{','.join(map(str, candidates))}")

    retained = sorted(
        reach_id for reach_id, cells in context.cells_by_reach.items()
        if bool(np.any(context.inside[cells[:, 0], cells[:, 1]]))
    )
    if not retained:
        raise ValueError("真实流域内没有河段。")
    retained_set = set(retained)
    downstream = {
        reach_id: full_downstream.get(reach_id, 0)
        if full_downstream.get(reach_id, 0) in retained_set else 0
        for reach_id in retained
    }
    self_loops = sorted(reach_id for reach_id, next_id in downstream.items() if reach_id == next_id)
    cycles = find_reach_cycles(downstream)
    levels, unresolved = topological_levels(downstream)
    if self_loops or cycles or unresolved:
        raise ValueError(
            f"拓扑无效: self_loops={self_loops}, cycles={cycles}, unresolved={sorted(unresolved)}"
        )
    upstream: dict[int, list[int]] = {reach_id: [] for reach_id in retained}
    for reach_id, next_id in downstream.items():
        if next_id in upstream:
            upstream[next_id].append(reach_id)
    for values in upstream.values():
        values.sort()
    reach_subbasin = {reach_id: reach_to_subbasin(context, reach_id) for reach_id in retained}
    missing_subbasins = sorted(reach_id for reach_id, sub_id in reach_subbasin.items() if sub_id <= 0)
    if missing_subbasins:
        raise ValueError(f"河段缺失子流域映射: {missing_subbasins}")

    cell_area_m2 = abs(
        dem_info[1].a * dem_info[1].e - dem_info[1].b * dem_info[1].d
    )
    sub_mask = context.inside & np.isfinite(context.subbasins) & (context.subbasins > 0)
    sub_ids, counts = np.unique(context.subbasins[sub_mask].astype(np.int64), return_counts=True)
    reaches_by_subbasin: dict[int, list[int]] = defaultdict(list)
    for reach_id, sub_id in reach_subbasin.items():
        reaches_by_subbasin[sub_id].append(reach_id)
    sub_records = []
    for raw_sub_id, count in zip(sub_ids, counts):
        sub_id = int(raw_sub_id)
        reach_ids = sorted(reaches_by_subbasin.get(sub_id, []))
        reach_set = set(reach_ids)
        local_outlets = [reach_id for reach_id in reach_ids if downstream.get(reach_id, 0) not in reach_set]
        sub_records.append({
            "sub_id": sub_id,
            "area_km2": round(float(count) * cell_area_m2 / 1_000_000.0, 6),
            "cell_count": int(count),
            "reach_count": len(reach_ids),
            "reach_ids": ";".join(map(str, reach_ids)),
            "outlet_reach_ids": ";".join(map(str, local_outlets)),
        })
    if not sub_records:
        raise ValueError("真实流域内没有有效子流域像元。")
    subbasin_frame = pd.DataFrame(sub_records).sort_values("sub_id").reset_index(drop=True)

    reach_records, geometries = [], []
    for reach_id in retained:
        cells = context.cells_by_reach[reach_id]
        rows, cols = cells[:, 0], cells[:, 1]
        inside = context.inside[rows, cols]
        metrics = reach_metrics(context, reach_id)
        next_id = downstream[reach_id]
        reach_records.append({
            "reach_id": reach_id,
            "sub_id": reach_subbasin[reach_id],
            "strahler_order": mode_positive(context.orders[rows[inside], cols[inside]], default=0),
            **metrics,
            "upstream_count": len(upstream[reach_id]),
            "upstream_reach_ids": ";".join(map(str, upstream[reach_id])),
            "downstream_reach_id": next_id,
            "downstream_sub_id": reach_subbasin.get(next_id, 0) if next_id else 0,
            "topo_level": levels[reach_id],
            "is_headwater": int(not upstream[reach_id]),
            "is_outlet": int(next_id == 0),
        })
        geometries.append(reach_geometry(context, reach_id))
    reach_frame = pd.DataFrame(reach_records).sort_values("reach_id").reset_index(drop=True)
    zero_length = reach_frame.loc[reach_frame["length_m"] <= 0, "reach_id"].astype(int).tolist()
    if zero_length:
        raise ValueError(f"存在零长度河段: {zero_length}")
    reach_geo = gpd.GeoDataFrame(reach_frame.copy(), geometry=geometries, crs=crs)
    invalid_geometry = reach_geo.geometry.isna() | reach_geo.geometry.is_empty | ~reach_geo.geometry.is_valid
    if invalid_geometry.any():
        bad = reach_geo.loc[invalid_geometry, "reach_id"].astype(int).tolist()
        raise ValueError(f"无法生成有效河段几何: {bad}")
    topology_frame = reach_frame[[
        "sub_id", "reach_id", "upstream_count", "upstream_reach_ids",
        "downstream_sub_id", "downstream_reach_id", "topo_level",
        "is_headwater", "is_outlet",
    ]].copy()

    outputs = {key: args.output_dir / name for key, name in OUTPUT_NAMES.items()}
    subbasin_frame.to_csv(outputs["subbasins_table"], index=False, encoding="utf-8")
    reach_frame.to_csv(outputs["reaches_table"], index=False, encoding="utf-8")
    topology_frame.to_csv(outputs["topology_table"], index=False, encoding="utf-8")
    reach_geo.to_file(outputs["reaches_vector"], layer="reaches", driver="GPKG")
    write_clipped(paths["stream_links"], outputs["stream_links_clipped"], context.inside)
    write_clipped(paths["stream_order"], outputs["stream_order_clipped"], context.inside)
    write_clipped(paths["subbasins"], outputs["subbasins_clipped"], context.inside)

    checks = [
        {"check": "same_grid", "status": "PASS", "details": "all input rasters aligned"},
        {"check": "d8_encoding", "status": "PASS", "details": "whitebox-default-non-esri"},
        {"check": "acyclic_topology", "status": "PASS", "details": f"reaches={len(retained)}"},
        {"check": "subbasin_mapping", "status": "PASS", "details": f"subbasins={len(subbasin_frame)}"},
    ]
    if trace_warnings:
        message = "trace warnings: " + " | ".join(trace_warnings)
        warnings.append(message)
        checks.append({"check": "trace_warning", "status": "WARN", "details": message})
    if multiple_candidates:
        message = "multiple downstream candidates: " + " | ".join(multiple_candidates)
        warnings.append(message)
        checks.append({"check": "multiple_downstream_candidates", "status": "WARN", "details": message})
    negative_count = int(reach_frame["negative_elevation_step_count"].sum())
    if negative_count:
        message = f"negative elevation steps={negative_count}"
        warnings.append(message)
        checks.append({"check": "negative_elevation_steps", "status": "WARN", "details": message})
    else:
        checks.append({"check": "negative_elevation_steps", "status": "PASS", "details": "count=0"})
    inputs = {key: file_reference(path) for key, path in paths.items()}
    inputs.update(upstream_references)
    return {
        "schema_version": "1.0", "skill": SKILL_REF,
        "status": "warning" if warnings else "success",
        "message": "HydroBase topology built" if not warnings else "HydroBase topology built with warnings",
        "parameters": {
            "cell_inclusion": args.cell_inclusion,
            "d8_encoding": "whitebox-default-non-esri",
            "cell_area_m2": cell_area_m2,
        },
        "inputs": inputs,
        "artifacts": {key: file_reference(path, args.output_dir) for key, path in outputs.items()},
        "checks": checks,
        "warnings": warnings,
        "provenance": {
            "python": sys.version.split()[0], "rasterio": package_version("rasterio"),
            "geopandas": package_version("geopandas"), "pandas": package_version("pandas"),
            "numpy": package_version("numpy"), "shapely": package_version("shapely"),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--basin", type=Path, required=True)
    parser.add_argument("--basin-layer")
    parser.add_argument("--dem-filled", type=Path, required=True)
    parser.add_argument("--d8-pointer", type=Path, required=True)
    parser.add_argument("--flow-accumulation", type=Path, required=True)
    parser.add_argument("--stream-links", type=Path, required=True)
    parser.add_argument("--stream-order", type=Path, required=True)
    parser.add_argument("--subbasins", type=Path, required=True)
    parser.add_argument("--cell-inclusion", choices=("center", "all-touched"), default="center")
    parser.add_argument("--upstream-result", type=Path, action="append")
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
        document = run(args)
        write_result(args.output_dir, document)
        print(f"{document['status']}: {document['message']}")
        return 0
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
