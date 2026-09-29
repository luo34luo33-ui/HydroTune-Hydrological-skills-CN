from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import geopandas as gpd
from jsonschema import Draft202012Validator
import numpy as np
import pandas as pd
from PIL import Image
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box


EXAMPLES = {
    "prepare": Path("spatial-analysis/prepare-dem-analysis-grid/examples/prepare_dem_analysis_grid.py"),
    "extract": Path("spatial-analysis/extract-dem-stream-network/examples/extract_dem_stream_network.py"),
    "build": Path("spatial-analysis/build-hydrological-topology/examples/build_hydrological_topology.py"),
    "validate": Path("spatial-analysis/validate-hydrological-topology/examples/validate_hydrological_topology.py"),
    "dem_atlas": Path("visualization-reporting/visualize-dem-hydrology-atlas/examples/render_dem_hydrology_atlas.py"),
    "hydrobase_atlas": Path("visualization-reporting/visualize-hydrobase-atlas/examples/render_hydrobase_atlas.py"),
}


def _run(repo_root: Path, key: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(repo_root / EXAMPLES[key]), *arguments],
        cwd=repo_root, text=True, capture_output=True, encoding="utf-8",
        env=environment, check=False,
    )


def _write_raster(path: Path, values: np.ndarray) -> None:
    with rasterio.open(
        path, "w", driver="GTiff", height=values.shape[0], width=values.shape[1],
        count=1, dtype=str(values.dtype), transform=from_origin(500_000, 4_000_000, 100, 100),
        crs="EPSG:32649", nodata=-9999.0,
    ) as destination:
        destination.write(values, 1)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_atlas(directory: Path, templates: list[str], language: str, repo_root: Path) -> None:
    figure_schema = json.loads((repo_root / "resources/schemas/visualization-figure.schema.json").read_text(encoding="utf-8"))
    run_schema = json.loads((repo_root / "resources/schemas/visualization-run-result.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(run_schema).validate(json.loads((directory / "result.json").read_text(encoding="utf-8")))
    for template in templates:
        png = directory / f"{template}.png"
        svg = directory / f"{template}.svg"
        metadata = directory / f"{template}.figure.json"
        assert png.is_file() and svg.is_file() and metadata.is_file()
        with Image.open(png) as image:
            assert image.size == (2400, 1600)
            assert np.asarray(image.convert("RGB")).std() > 5
        ET.parse(svg)
        svg_text = svg.read_text(encoding="utf-8")
        assert "HydroTune spatial atlas" not in svg_text
        document = json.loads(metadata.read_text(encoding="utf-8"))
        Draft202012Validator(figure_schema).validate(document)
        assert document["language"] == language
        assert document["template_id"] == template


def test_visualization_scripts_support_help(repo_root: Path) -> None:
    for key in ("dem_atlas", "hydrobase_atlas"):
        result = _run(repo_root, key, "--help")
        assert result.returncode == 0, result.stderr
        assert "--language" in result.stdout
        assert "--output-dir" in result.stdout


def test_atlas_style_assets_are_identical_and_valid(repo_root: Path) -> None:
    dem_style = repo_root / "visualization-reporting/visualize-dem-hydrology-atlas/assets/atlas-style-v1.json"
    hydro_style = repo_root / "visualization-reporting/visualize-hydrobase-atlas/assets/atlas-style-v1.json"
    assert dem_style.read_bytes() == hydro_style.read_bytes()
    style = json.loads(dem_style.read_text(encoding="utf-8"))
    assert style["template_version"] == "hydrotune.spatial-atlas.v1"
    assert style["canvas"]["width_px"] == 2400
    assert style["canvas"]["height_px"] == 1600


@pytest.mark.integration
def test_spatial_atlases_end_to_end(repo_root: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "图册 合成（中英文）"
    workspace.mkdir()
    source_dem = workspace / "source-dem.tif"
    source_basin = workspace / "source-basin.gpkg"
    size = 40
    rows, cols = np.indices((size, size))
    center = (size - 1) / 2.0
    elevation = ((size - rows) * 2.0 + np.abs(cols - center) * 3.0 + 100.0).astype("float32")
    _write_raster(source_dem, elevation)
    gpd.GeoDataFrame(
        {"basin_id": [1]}, geometry=[box(500_500, 3_996_500, 503_500, 3_999_500)], crs="EPSG:32649",
    ).to_file(source_basin, layer="basin", driver="GPKG")

    prepared = workspace / "01 prepared（网格）"
    extracted = workspace / "02 extracted（河网）"
    built = workspace / "03 built（拓扑）"
    validated = workspace / "04 validated（质检）"
    result = _run(repo_root, "prepare", "--dem", str(source_dem), "--basin", str(source_basin),
                  "--analysis-buffer-km", "0.3", "--vertical-unit", "m", "--output-dir", str(prepared))
    assert result.returncode == 0, result.stderr
    result = _run(repo_root, "extract", "--analysis-dem", str(prepared / "dem_analysis_buffer.tif"),
                  "--stream-init-area-km2", "0.1", "--output-dir", str(extracted))
    assert result.returncode == 0, result.stderr
    result = _run(
        repo_root, "build", "--basin", str(prepared / "basin_normalized.gpkg"),
        "--dem-filled", str(extracted / "dem_filled.tif"), "--d8-pointer", str(extracted / "d8_pointer.tif"),
        "--flow-accumulation", str(extracted / "flow_accumulation_cells.tif"),
        "--stream-links", str(extracted / "stream_links.tif"), "--stream-order", str(extracted / "strahler_order.tif"),
        "--subbasins", str(extracted / "subbasins.tif"), "--cell-inclusion", "center",
        "--upstream-result", str(prepared / "result.json"), "--upstream-result", str(extracted / "result.json"),
        "--output-dir", str(built),
    )
    assert result.returncode == 0, result.stderr
    reaches = gpd.read_file(built / "reaches_clipped.gpkg", layer="reaches")
    expected_outlets = int(reaches["is_outlet"].sum())
    result = _run(repo_root, "validate", "--build-result", str(built / "result.json"),
                  "--expected-outlets", str(expected_outlets), "--output-dir", str(validated))
    assert result.returncode == 0, result.stderr

    dem_en = workspace / "05 DEM atlas EN"
    result = _run(repo_root, "dem_atlas", "--prepare-result", str(prepared / "result.json"),
                  "--extract-result", str(extracted / "result.json"), "--language", "en", "--output-dir", str(dem_en))
    assert result.returncode == 0, result.stderr
    dem_templates = ["dem-basin-context", "dem-terrain", "flow-accumulation-network", "stream-order-subbasins"]
    _assert_atlas(dem_en, dem_templates, "en", repo_root)

    dem_repeat = workspace / "06 DEM atlas EN repeat"
    result = _run(repo_root, "dem_atlas", "--prepare-result", str(prepared / "result.json"),
                  "--extract-result", str(extracted / "result.json"), "--language", "en", "--output-dir", str(dem_repeat))
    assert result.returncode == 0, result.stderr
    for template in dem_templates:
        assert _sha256(dem_en / f"{template}.png") == _sha256(dem_repeat / f"{template}.png")
        assert _sha256(dem_en / f"{template}.svg") == _sha256(dem_repeat / f"{template}.svg")

    dem_zh = workspace / "07 DEM atlas 中文"
    result = _run(repo_root, "dem_atlas", "--prepare-result", str(prepared / "result.json"),
                  "--extract-result", str(extracted / "result.json"), "--language", "zh", "--output-dir", str(dem_zh))
    assert result.returncode == 0, result.stderr
    assert "Glyph" not in result.stderr
    _assert_atlas(dem_zh, dem_templates, "zh", repo_root)
    assert "流域与 DEM 分析范围" in (dem_zh / "dem-basin-context.svg").read_text(encoding="utf-8")

    hydro_en = workspace / "08 HydroBase atlas EN"
    result = _run(repo_root, "hydrobase_atlas", "--build-result", str(built / "result.json"),
                  "--validation-result", str(validated / "result.json"), "--language", "en", "--output-dir", str(hydro_en))
    assert result.returncode == 0, result.stderr
    hydro_templates = ["hydrobase-topology", "hydrobase-morphometry", "hydrobase-qc-dashboard"]
    _assert_atlas(hydro_en, hydro_templates, "en", repo_root)

    hydro_zh = workspace / "09 HydroBase atlas 中文"
    result = _run(repo_root, "hydrobase_atlas", "--build-result", str(built / "result.json"),
                  "--validation-result", str(validated / "result.json"), "--language", "zh", "--output-dir", str(hydro_zh))
    assert result.returncode == 0, result.stderr
    assert "Glyph" not in result.stderr
    _assert_atlas(hydro_zh, hydro_templates, "zh", repo_root)

    hydro_repeat = workspace / "09b HydroBase atlas EN repeat"
    result = _run(repo_root, "hydrobase_atlas", "--build-result", str(built / "result.json"),
                  "--validation-result", str(validated / "result.json"), "--language", "en", "--output-dir", str(hydro_repeat))
    assert result.returncode == 0, result.stderr
    for template in hydro_templates:
        assert _sha256(hydro_en / f"{template}.png") == _sha256(hydro_repeat / f"{template}.png")
        assert _sha256(hydro_en / f"{template}.svg") == _sha256(hydro_repeat / f"{template}.svg")

    dem_partial = workspace / "10 DEM partial"
    result = _run(repo_root, "dem_atlas", "--prepare-result", str(prepared / "result.json"),
                  "--language", "en", "--output-dir", str(dem_partial))
    assert result.returncode == 0, result.stderr
    partial_document = json.loads((dem_partial / "result.json").read_text(encoding="utf-8"))
    assert partial_document["status"] == "warning"
    _assert_atlas(dem_partial, ["dem-basin-context", "dem-terrain"], "en", repo_root)
    assert not (dem_partial / "flow-accumulation-network.png").exists()

    topology_table = built / "topology.csv"
    frame = pd.read_csv(topology_table)
    frame.loc[0, "downstream_reach_id"] = int(frame.loc[0, "reach_id"])
    frame.to_csv(topology_table, index=False, encoding="utf-8")
    build_document = json.loads((built / "result.json").read_text(encoding="utf-8"))
    build_document["artifacts"]["topology_table"]["sha256"] = _sha256(topology_table)
    (built / "result.json").write_text(json.dumps(build_document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    invalid_qc = workspace / "11 invalid QC"
    result = _run(repo_root, "validate", "--build-result", str(built / "result.json"),
                  "--expected-outlets", str(expected_outlets), "--output-dir", str(invalid_qc))
    assert result.returncode == 2, result.stderr
    failed_atlas = workspace / "12 failed atlas"
    result = _run(repo_root, "hydrobase_atlas", "--build-result", str(built / "result.json"),
                  "--validation-result", str(invalid_qc / "result.json"), "--language", "en", "--output-dir", str(failed_atlas))
    assert result.returncode == 2, result.stderr
    failed_document = json.loads((failed_atlas / "result.json").read_text(encoding="utf-8"))
    assert failed_document["status"] == "error"
    _assert_atlas(failed_atlas, ["hydrobase-qc-dashboard"], "en", repo_root)
    assert not (failed_atlas / "hydrobase-topology.png").exists()

    extract_result_path = extracted / "result.json"
    original_extract_result = extract_result_path.read_text(encoding="utf-8")
    missing_layer_document = json.loads(original_extract_result)
    del missing_layer_document["artifacts"]["stream_order"]
    extract_result_path.write_text(json.dumps(missing_layer_document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    missing_layer = workspace / "13 missing layer"
    result = _run(repo_root, "dem_atlas", "--prepare-result", str(prepared / "result.json"),
                  "--extract-result", str(extract_result_path), "--language", "en", "--output-dir", str(missing_layer))
    assert result.returncode == 1
    assert not any(missing_layer.glob("*.png"))
    extract_result_path.write_text(original_extract_result, encoding="utf-8")

    with (extracted / "streams.tif").open("ab") as handle:
        handle.write(b"tampered")
    tampered = workspace / "14 tampered"
    result = _run(repo_root, "dem_atlas", "--prepare-result", str(prepared / "result.json"),
                  "--extract-result", str(extracted / "result.json"), "--language", "en", "--output-dir", str(tampered))
    assert result.returncode == 1
    assert not any(tampered.glob("*.png"))

    prepare_result_path = prepared / "result.json"
    prepare_document = json.loads(prepare_result_path.read_text(encoding="utf-8"))
    prepare_document["status"] = "error"
    prepare_document["message"] = "synthetic upstream error"
    prepare_result_path.write_text(json.dumps(prepare_document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    upstream_error = workspace / "15 upstream error"
    result = _run(repo_root, "dem_atlas", "--prepare-result", str(prepare_result_path),
                  "--language", "en", "--output-dir", str(upstream_error))
    assert result.returncode == 1
    assert not any(upstream_error.glob("*.png"))
