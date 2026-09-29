#!/usr/bin/env python3
"""Prepare a metric DEM, normalized basin, and buffered analysis grid."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.mask import mask as raster_mask
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds
from shapely.geometry import box
from shapely.ops import unary_union


SKILL_REF = "spatial-analysis/prepare-dem-analysis-grid"
DECLARED_OUTPUTS = (
    "dem_metric.tif",
    "dem_analysis_buffer.tif",
    "basin_normalized.gpkg",
    "result.json",
)


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
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


def is_metric_projected(crs: CRS | None) -> bool:
    if crs is None or not crs.is_projected:
        return False
    try:
        factor = crs.linear_units_factor
        if isinstance(factor, tuple):
            factor = factor[1]
        return math.isclose(float(factor), 1.0, rel_tol=0.0, abs_tol=1e-9)
    except Exception:
        return str(getattr(crs, "linear_units", "")).lower() in {
            "metre", "meter", "metres", "meters", "m"
        }


def local_utm_crs(src_crs: CRS, bounds: rasterio.coords.BoundingBox) -> CRS:
    left, bottom, right, top = transform_bounds(
        src_crs, "EPSG:4326", *bounds, densify_pts=21
    )
    longitude = (left + right) / 2.0
    latitude = (bottom + top) / 2.0
    if latitude < -80.0 or latitude > 84.0:
        raise ValueError("研究区超出常规 UTM 纬度范围；请显式提供适用的 --target-crs。")
    zone = max(1, min(60, int(math.floor((longitude + 180.0) / 6.0) + 1)))
    return CRS.from_epsg((32600 if latitude >= 0 else 32700) + zone)


def normalize_dem(source_path: Path, output_path: Path, target_crs: CRS, scale: float) -> None:
    with rasterio.open(source_path) as source:
        if source.count != 1:
            raise ValueError("DEM 必须是单波段栅格。")
        if source.crs is None:
            raise ValueError("DEM 没有 CRS。")
        if source.crs == target_crs:
            transform, width, height = source.transform, source.width, source.height
        else:
            transform, width, height = calculate_default_transform(
                source.crs, target_crs, source.width, source.height, *source.bounds
            )
        profile = source.profile.copy()
        profile.update(
            driver="GTiff",
            crs=target_crs,
            transform=transform,
            width=width,
            height=height,
            count=1,
            dtype="float32",
            nodata=-9999.0,
            compress="lzw",
        )
        with rasterio.open(output_path, "w", **profile) as destination:
            reproject(
                source=rasterio.band(source, 1),
                destination=rasterio.band(destination, 1),
                src_transform=source.transform,
                src_crs=source.crs,
                src_nodata=source.nodata,
                dst_transform=transform,
                dst_crs=target_crs,
                dst_nodata=-9999.0,
                resampling=Resampling.bilinear,
            )
    if not math.isclose(scale, 1.0):
        with rasterio.open(output_path, "r+") as dataset:
            nodata = dataset.nodata
            for _, window in dataset.block_windows(1):
                values = dataset.read(1, window=window)
                valid = np.isfinite(values)
                if nodata is not None:
                    valid &= values != nodata
                values[valid] *= scale
                dataset.write(values.astype("float32"), 1, window=window)


def read_normalized_basin(path: Path, layer: str | None, target_crs: CRS) -> gpd.GeoDataFrame:
    basin = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
    if basin.crs is None:
        raise ValueError("流域边界没有 CRS。")
    basin = basin[basin.geometry.notna() & ~basin.geometry.is_empty].copy()
    if basin.empty:
        raise ValueError("流域边界不包含有效几何。")
    try:
        basin["geometry"] = basin.geometry.make_valid()
    except Exception:
        basin["geometry"] = basin.geometry.buffer(0)
    basin = basin[basin.geometry.notna() & ~basin.geometry.is_empty].to_crs(target_crs)
    geometry = unary_union(basin.geometry)
    if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("修复后的流域边界不是有效多边形。")
    return gpd.GeoDataFrame({"basin_id": [1]}, geometry=[geometry], crs=target_crs)


def run(args: argparse.Namespace) -> dict[str, Any]:
    dem_path = args.dem.resolve()
    basin_path = args.basin.resolve()
    for path in (dem_path, basin_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    if args.analysis_buffer_km < 0:
        raise ValueError("--analysis-buffer-km 不能小于 0。")

    with rasterio.open(dem_path) as source:
        if source.crs is None:
            raise ValueError("DEM 没有 CRS。")
        source_crs = source.crs
        if args.target_crs:
            target_crs = CRS.from_user_input(args.target_crs)
            crs_decision = "user-specified"
        elif is_metric_projected(source_crs):
            target_crs = source_crs
            crs_decision = "preserved-source"
        elif args.auto_utm:
            target_crs = local_utm_crs(source_crs, source.bounds)
            crs_decision = "auto-utm"
        else:
            raise ValueError("DEM 不是米制投影；必须提供 --target-crs 或显式使用 --auto-utm。")
    if not is_metric_projected(target_crs):
        raise ValueError("目标 CRS 必须是以米为线性单位的投影坐标系。")

    scale = 1.0 if args.vertical_unit == "m" else 0.3048
    metric_dem = args.output_dir / "dem_metric.tif"
    analysis_dem = args.output_dir / "dem_analysis_buffer.tif"
    basin_output = args.output_dir / "basin_normalized.gpkg"
    normalize_dem(dem_path, metric_dem, target_crs, scale)
    basin = read_normalized_basin(basin_path, args.basin_layer, target_crs)
    basin.to_file(basin_output, layer="basin", driver="GPKG")

    warnings: list[str] = []
    checks: list[dict[str, str]] = [
        {"check": "metric_projected_crs", "status": "PASS", "details": target_crs.to_string()},
        {"check": "vertical_unit", "status": "PASS", "details": f"{args.vertical_unit} -> m; scale={scale}"},
    ]
    buffered = basin.geometry.iloc[0].buffer(args.analysis_buffer_km * 1000.0)
    with rasterio.open(metric_dem) as source:
        raster_extent = box(*source.bounds)
        if not raster_extent.covers(basin.geometry.iloc[0]):
            raise ValueError("DEM 未完整覆盖真实流域边界。")
        checks.append({"check": "true_basin_coverage", "status": "PASS", "details": "DEM covers basin"})
        if not raster_extent.covers(buffered):
            message = "DEM 未完整覆盖分析 buffer；边界附近拓扑和出口判定可能不可靠。"
            warnings.append(message)
            checks.append({"check": "analysis_buffer_coverage", "status": "WARN", "details": message})
        else:
            checks.append({"check": "analysis_buffer_coverage", "status": "PASS", "details": "DEM covers buffer"})
        data, transform = raster_mask(
            source,
            [buffered.__geo_interface__],
            crop=True,
            filled=False,
            all_touched=False,
        )
        data = data.astype("float32").filled(-9999.0)
        profile = source.profile.copy()
        profile.update(
            height=data.shape[1], width=data.shape[2], transform=transform,
            dtype="float32", nodata=-9999.0, compress="lzw"
        )
        with rasterio.open(analysis_dem, "w", **profile) as destination:
            destination.write(data)

    artifacts = {
        "metric_dem": file_reference(metric_dem, args.output_dir),
        "analysis_dem": file_reference(analysis_dem, args.output_dir),
        "normalized_basin": file_reference(basin_output, args.output_dir),
    }
    status = "warning" if warnings else "success"
    return {
        "schema_version": "1.0",
        "skill": SKILL_REF,
        "status": status,
        "message": "DEM analysis grid prepared" if not warnings else "DEM analysis grid prepared with coverage warning",
        "parameters": {
            "analysis_buffer_km": args.analysis_buffer_km,
            "vertical_unit": args.vertical_unit,
            "vertical_scale_to_m": scale,
            "target_crs": target_crs.to_string(),
            "crs_decision": crs_decision,
            "mask_rule": "center",
        },
        "inputs": {
            "source_dem": file_reference(dem_path),
            "basin_boundary": file_reference(basin_path),
        },
        "artifacts": artifacts,
        "checks": checks,
        "warnings": warnings,
        "provenance": {
            "python": sys.version.split()[0],
            "rasterio": package_version("rasterio"),
            "geopandas": package_version("geopandas"),
            "shapely": package_version("shapely"),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dem", type=Path, required=True)
    parser.add_argument("--basin", type=Path, required=True)
    parser.add_argument("--basin-layer")
    parser.add_argument("--analysis-buffer-km", type=float, required=True)
    parser.add_argument("--vertical-unit", choices=("m", "ft"), required=True)
    crs_group = parser.add_mutually_exclusive_group()
    crs_group.add_argument("--target-crs")
    crs_group.add_argument("--auto-utm", action="store_true")
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
            document = {
                "schema_version": "1.0", "skill": SKILL_REF, "status": "error",
                "message": str(exc), "parameters": json_safe(vars(args)),
                "inputs": {}, "artifacts": {}, "checks": [], "warnings": [],
                "provenance": {"python": sys.version.split()[0]},
            }
            write_result(args.output_dir, document)
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
