#!/usr/bin/env python3
"""Extract an unburned D8 stream network and subbasins with WhiteboxTools."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable

import geopandas as gpd
import numpy as np
import rasterio
import whitebox


SKILL_REF = "spatial-analysis/extract-dem-stream-network"
RASTER_NAMES = {
    "filled_dem": "dem_filled.tif",
    "d8_pointer": "d8_pointer.tif",
    "flow_accumulation": "flow_accumulation_cells.tif",
    "streams": "streams.tif",
    "stream_links": "stream_links.tif",
    "stream_order": "strahler_order.tif",
    "subbasins": "subbasins.tif",
}
DECLARED_OUTPUTS = tuple(RASTER_NAMES.values()) + ("streams.gpkg", "result.json")


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


def grid_signature(path: Path) -> tuple[tuple[int, int], Any, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with rasterio.open(path) as dataset:
        if dataset.crs is None:
            raise ValueError(f"栅格没有 CRS: {path}")
        return (dataset.height, dataset.width), dataset.transform, dataset.crs


def assert_same_grid(reference: Path, others: list[Path]) -> None:
    ref_shape, ref_transform, ref_crs = grid_signature(reference)
    for path in others:
        shape, transform, crs = grid_signature(path)
        if shape != ref_shape or crs != ref_crs or not transform.almost_equals(ref_transform):
            raise ValueError(f"栅格与分析 DEM 网格不一致: {path}")


def stream_threshold_cells(area_km2: float, cell_area_m2: float) -> int:
    if not math.isfinite(area_km2) or area_km2 <= 0:
        raise ValueError("河网起始汇水面积必须是大于零的有限数。")
    if not math.isfinite(cell_area_m2) or cell_area_m2 <= 0:
        raise ValueError("DEM 像元面积无效。")
    return math.ceil(area_km2 * 1_000_000.0 / cell_area_m2)


def run_step(name: str, output: Path, operation: Callable[[], int | None]) -> None:
    code = operation()
    if code not in (0, None):
        raise RuntimeError(f"{name} 执行失败，WhiteboxTools 返回码: {code}")
    if not output.is_file():
        raise RuntimeError(f"{name} 未生成预期文件: {output}")


def run(args: argparse.Namespace) -> dict[str, Any]:
    analysis_dem = args.analysis_dem.resolve()
    if not analysis_dem.is_file():
        raise FileNotFoundError(analysis_dem)
    outputs = {key: args.output_dir / name for key, name in RASTER_NAMES.items()}
    streams_gpkg = args.output_dir / "streams.gpkg"
    with rasterio.open(analysis_dem) as source:
        if source.count != 1 or source.crs is None or not source.crs.is_projected:
            raise ValueError("分析 DEM 必须是具有投影 CRS 的单波段栅格。")
        factor = source.crs.linear_units_factor
        if isinstance(factor, tuple):
            factor = factor[1]
        if not math.isclose(float(factor), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("分析 DEM 的水平单位必须为米。")
        cell_area_m2 = abs(
            source.transform.a * source.transform.e
            - source.transform.b * source.transform.d
        )
        crs_wkt = source.crs.to_wkt()
    threshold_cells = stream_threshold_cells(args.stream_init_area_km2, cell_area_m2)

    wbt = whitebox.WhiteboxTools()
    if args.whitebox_dir:
        whitebox_dir = args.whitebox_dir.resolve()
        if not whitebox_dir.is_dir():
            raise FileNotFoundError(whitebox_dir)
        wbt.set_whitebox_dir(str(whitebox_dir))
    wbt.verbose = True
    wbt.set_compress_rasters(True)

    run_step(
        "Fill Depressions", outputs["filled_dem"],
        lambda: wbt.fill_depressions(str(analysis_dem), str(outputs["filled_dem"]), fix_flats=True),
    )
    run_step(
        "D8 Pointer", outputs["d8_pointer"],
        lambda: wbt.d8_pointer(str(outputs["filled_dem"]), str(outputs["d8_pointer"]), esri_pntr=False),
    )
    run_step(
        "D8 Flow Accumulation", outputs["flow_accumulation"],
        lambda: wbt.d8_flow_accumulation(
            str(outputs["d8_pointer"]), str(outputs["flow_accumulation"]),
            out_type="cells", log=False, clip=False, pntr=True, esri_pntr=False,
        ),
    )
    run_step(
        "Extract Streams", outputs["streams"],
        lambda: wbt.extract_streams(
            str(outputs["flow_accumulation"]), str(outputs["streams"]),
            threshold=threshold_cells, zero_background=True,
        ),
    )
    with rasterio.open(outputs["streams"]) as dataset:
        stream_cells = int(np.count_nonzero(dataset.read(1) > 0))
    if stream_cells == 0:
        raise RuntimeError(
            f"没有提取到河网；area={args.stream_init_area_km2} km2, threshold={threshold_cells} cells"
        )
    run_step(
        "Stream Link Identifier", outputs["stream_links"],
        lambda: wbt.stream_link_identifier(
            str(outputs["d8_pointer"]), str(outputs["streams"]), str(outputs["stream_links"]),
            esri_pntr=False, zero_background=True,
        ),
    )
    run_step(
        "Strahler Stream Order", outputs["stream_order"],
        lambda: wbt.strahler_stream_order(
            str(outputs["d8_pointer"]), str(outputs["streams"]), str(outputs["stream_order"]),
            esri_pntr=False, zero_background=True,
        ),
    )
    run_step(
        "Subbasins", outputs["subbasins"],
        lambda: wbt.subbasins(
            str(outputs["d8_pointer"]), str(outputs["streams"]), str(outputs["subbasins"]),
            esri_pntr=False,
        ),
    )
    with tempfile.TemporaryDirectory(prefix="hydrotune-stream-vector-") as temp_dir:
        temporary_shape = Path(temp_dir) / "streams.shp"
        run_step(
            "Raster Streams to Vector", temporary_shape,
            lambda: wbt.raster_streams_to_vector(
                str(outputs["streams"]), str(outputs["d8_pointer"]), str(temporary_shape),
                esri_pntr=False,
            ),
        )
        projection = temporary_shape.with_suffix(".prj")
        if not projection.exists():
            projection.write_text(crs_wkt, encoding="utf-8")
        stream_vectors = gpd.read_file(temporary_shape)
        if stream_vectors.empty:
            raise RuntimeError("Whitebox 河网矢量为空。")
        if stream_vectors.crs is None:
            stream_vectors = stream_vectors.set_crs(crs_wkt)
        stream_vectors.to_file(streams_gpkg, layer="streams", driver="GPKG")

    assert_same_grid(analysis_dem, list(outputs.values()))
    with rasterio.open(outputs["stream_links"]) as dataset:
        link_ids = np.unique(dataset.read(1))
        link_count = int(np.count_nonzero(np.isfinite(link_ids) & (link_ids > 0)))
    if link_count == 0:
        raise RuntimeError("Stream Links 不包含正整数河段编号。")

    artifacts = {key: file_reference(path, args.output_dir) for key, path in outputs.items()}
    artifacts["streams_vector"] = file_reference(streams_gpkg, args.output_dir)
    checks = [
        {"check": "metric_grid", "status": "PASS", "details": f"cell_area_m2={cell_area_m2:.9g}"},
        {"check": "threshold_conversion", "status": "PASS", "details": f"threshold_cells={threshold_cells}"},
        {"check": "same_grid", "status": "PASS", "details": "all core rasters aligned"},
        {"check": "nonempty_stream_network", "status": "PASS", "details": f"stream_cells={stream_cells}; reaches={link_count}"},
    ]
    return {
        "schema_version": "1.0", "skill": SKILL_REF, "status": "success",
        "message": "Unburned D8 stream network extracted",
        "parameters": {
            "stream_init_area_km2": args.stream_init_area_km2,
            "cell_area_m2": cell_area_m2,
            "threshold_cells": threshold_cells,
            "d8_encoding": "whitebox-default-non-esri",
        },
        "inputs": {"analysis_dem": file_reference(analysis_dem)},
        "artifacts": artifacts,
        "checks": checks,
        "warnings": [],
        "provenance": {
            "python": sys.version.split()[0],
            "whitebox_python": package_version("whitebox"),
            "whitebox_tools": str(wbt.version()).splitlines()[0],
            "rasterio": package_version("rasterio"),
            "geopandas": package_version("geopandas"),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dem", type=Path, required=True)
    parser.add_argument("--stream-init-area-km2", type=float, required=True)
    parser.add_argument("--whitebox-dir", type=Path)
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
        print(f"success: {document['message']}")
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
