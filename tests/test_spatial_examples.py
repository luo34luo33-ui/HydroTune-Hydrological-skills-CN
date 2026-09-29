from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import geopandas as gpd
from jsonschema import Draft202012Validator
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import box


EXAMPLE_PATHS = {
    "prepare": Path("spatial-analysis/prepare-dem-analysis-grid/examples/prepare_dem_analysis_grid.py"),
    "extract": Path("spatial-analysis/extract-dem-stream-network/examples/extract_dem_stream_network.py"),
    "build": Path("spatial-analysis/build-hydrological-topology/examples/build_hydrological_topology.py"),
    "validate": Path("spatial-analysis/validate-hydrological-topology/examples/validate_hydrological_topology.py"),
}


def _load_module(repo_root: Path, key: str):
    path = repo_root / EXAMPLE_PATHS[key]
    spec = importlib.util.spec_from_file_location(f"hydrotune_test_{key}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(repo_root: Path, key: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(repo_root / EXAMPLE_PATHS[key]), *arguments],
        cwd=repo_root,
        text=True,
        capture_output=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


def _write_raster(path: Path, values: np.ndarray, transform, crs: str | None) -> None:
    profile = {
        "driver": "GTiff",
        "height": values.shape[0],
        "width": values.shape[1],
        "count": 1,
        "dtype": str(values.dtype),
        "transform": transform,
        "nodata": -9999.0,
    }
    if crs is not None:
        profile["crs"] = crs
    with rasterio.open(path, "w", **profile) as destination:
        destination.write(values, 1)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_examples_support_help(repo_root: Path) -> None:
    for key in EXAMPLE_PATHS:
        result = _run(repo_root, key, "--help")
        assert result.returncode == 0, result.stderr
        assert "--output-dir" in result.stdout


def test_crs_threshold_and_topology_helpers(repo_root: Path) -> None:
    prepare = _load_module(repo_root, "prepare")
    extract = _load_module(repo_root, "extract")
    build = _load_module(repo_root, "build")

    assert prepare.is_metric_projected(CRS.from_epsg(32649))
    assert not prepare.is_metric_projected(CRS.from_epsg(4326))
    assert prepare.local_utm_crs(
        CRS.from_epsg(4326), rasterio.coords.BoundingBox(113, 22, 114, 23)
    ).to_epsg() == 32649
    assert extract.stream_threshold_cells(0.025, 10_000.0) == 3
    with pytest.raises(ValueError):
        extract.stream_threshold_cells(0, 10_000.0)

    groups = build.group_cells(np.array([[1, 1, 0], [2, 2, 3]], dtype=float))
    assert sorted(groups) == [1, 2, 3]
    assert build.find_reach_cycles({1: 2, 2: 3, 3: 0}) == []
    assert build.find_reach_cycles({1: 2, 2: 1}) == [[1, 2]]
    levels, unresolved = build.topological_levels({1: 3, 2: 3, 3: 0})
    assert levels == {1: 1, 2: 1, 3: 2}
    assert unresolved == set()


def test_grid_mismatch_is_rejected(repo_root: Path, tmp_path: Path) -> None:
    extract = _load_module(repo_root, "extract")
    first = tmp_path / "first.tif"
    second = tmp_path / "second.tif"
    values = np.ones((3, 3), dtype="float32")
    _write_raster(first, values, from_origin(0, 300, 100, 100), "EPSG:32649")
    _write_raster(second, values, from_origin(1, 300, 100, 100), "EPSG:32649")
    with pytest.raises(ValueError, match="网格不一致"):
        extract.assert_same_grid(first, [second])


def test_prepare_rejects_missing_crs(repo_root: Path, tmp_path: Path) -> None:
    dem = tmp_path / "dem-no-crs.tif"
    basin = tmp_path / "basin.gpkg"
    output = tmp_path / "output"
    _write_raster(dem, np.ones((4, 4), dtype="float32"), from_origin(0, 4, 1, 1), None)
    gpd.GeoDataFrame({"id": [1]}, geometry=[box(0.5, 0.5, 3.5, 3.5)], crs="EPSG:32649").to_file(
        basin, layer="basin", driver="GPKG"
    )
    result = _run(
        repo_root, "prepare", "--dem", str(dem), "--basin", str(basin),
        "--analysis-buffer-km", "0", "--vertical-unit", "m",
        "--auto-utm", "--output-dir", str(output),
    )
    assert result.returncode == 1
    document = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert document["status"] == "error"
    assert "CRS" in document["message"]


@pytest.mark.integration
def test_synthetic_spatial_pipeline_and_qc_failure(repo_root: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "合成 空间（验收）"
    workspace.mkdir()
    source_dem = workspace / "source-dem.tif"
    source_basin = workspace / "source-basin.gpkg"
    size = 40
    rows, cols = np.indices((size, size))
    center = (size - 1) / 2.0
    elevation = ((size - rows) * 2.0 + np.abs(cols - center) * 3.0 + 100.0).astype("float32")
    transform = from_origin(500_000, 4_000_000, 100, 100)
    _write_raster(source_dem, elevation, transform, "EPSG:32649")
    basin_geometry = box(500_500, 3_996_500, 503_500, 3_999_500)
    gpd.GeoDataFrame({"basin_id": [1]}, geometry=[basin_geometry], crs="EPSG:32649").to_file(
        source_basin, layer="basin", driver="GPKG"
    )

    prepared = workspace / "01 prepared（网格）"
    extracted = workspace / "02 extracted（河网）"
    built = workspace / "03 built（拓扑）"
    validated = workspace / "04 validated（质检）"
    prepare_result = _run(
        repo_root, "prepare", "--dem", str(source_dem), "--basin", str(source_basin),
        "--analysis-buffer-km", "0.3", "--vertical-unit", "m",
        "--output-dir", str(prepared),
    )
    assert prepare_result.returncode == 0, prepare_result.stderr
    extract_result = _run(
        repo_root, "extract", "--analysis-dem", str(prepared / "dem_analysis_buffer.tif"),
        "--stream-init-area-km2", "0.1", "--output-dir", str(extracted),
    )
    assert extract_result.returncode == 0, extract_result.stderr
    build_result = _run(
        repo_root, "build",
        "--basin", str(prepared / "basin_normalized.gpkg"),
        "--dem-filled", str(extracted / "dem_filled.tif"),
        "--d8-pointer", str(extracted / "d8_pointer.tif"),
        "--flow-accumulation", str(extracted / "flow_accumulation_cells.tif"),
        "--stream-links", str(extracted / "stream_links.tif"),
        "--stream-order", str(extracted / "strahler_order.tif"),
        "--subbasins", str(extracted / "subbasins.tif"),
        "--cell-inclusion", "center",
        "--upstream-result", str(prepared / "result.json"),
        "--upstream-result", str(extracted / "result.json"),
        "--output-dir", str(built),
    )
    assert build_result.returncode == 0, build_result.stderr

    reaches = gpd.read_file(built / "reaches_clipped.gpkg", layer="reaches")
    expected_outlets = int(reaches["is_outlet"].sum())
    assert expected_outlets > 0
    validation_result = _run(
        repo_root, "validate", "--build-result", str(built / "result.json"),
        "--expected-outlets", str(expected_outlets), "--output-dir", str(validated),
    )
    assert validation_result.returncode == 0, validation_result.stderr
    validation_document = json.loads((validated / "result.json").read_text(encoding="utf-8"))
    assert validation_document["status"] == "success"

    schema = json.loads(
        (repo_root / "resources/schemas/spatial-run-result.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    for directory in (prepared, extracted, built, validated):
        validator.validate(json.loads((directory / "result.json").read_text(encoding="utf-8")))

    topology_table = built / "topology.csv"
    frame = pd.read_csv(topology_table)
    frame.loc[0, "downstream_reach_id"] = int(frame.loc[0, "reach_id"])
    frame.to_csv(topology_table, index=False, encoding="utf-8")
    build_document = json.loads((built / "result.json").read_text(encoding="utf-8"))
    build_document["artifacts"]["topology_table"]["sha256"] = _sha256(topology_table)
    (built / "result.json").write_text(
        json.dumps(build_document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    invalid_qc = workspace / "05 invalid-qc（失败）"
    invalid_result = _run(
        repo_root, "validate", "--build-result", str(built / "result.json"),
        "--expected-outlets", str(expected_outlets), "--output-dir", str(invalid_qc),
    )
    assert invalid_result.returncode == 2, invalid_result.stderr
    invalid_document = json.loads((invalid_qc / "result.json").read_text(encoding="utf-8"))
    assert invalid_document["status"] == "error"
    assert any(check["status"] == "FAIL" for check in invalid_document["checks"])
