from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
import rasterio
from jsonschema import Draft202012Validator
from rasterio.transform import from_origin


def load(path: Path, name: str):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scripts(repo_root: Path):
    terrain = load(repo_root / "spatial-analysis/derive-topmodel-terrain-inputs/examples/derive_topmodel_terrain_inputs.py", "terrain_example")
    model = load(repo_root / "hydrological-modeling/run-lumped-topmodel/examples/run_lumped_topmodel.py", "model_example")
    return terrain, model


def write_raster(path: Path, values):
    values = np.asarray(values, dtype="float32")
    with rasterio.open(path, "w", driver="GTiff", width=values.shape[1], height=values.shape[0],
                       count=1, dtype="float32", crs="EPSG:32650", transform=from_origin(0, 10, 10, 10), nodata=-9999) as ds:
        ds.write(values, 1)


def terrain_fixture(tmp_path: Path, terrain):
    source = tmp_path / "source"
    source.mkdir()
    dem = source / "dem.tif"
    pointer = source / "pointer.tif"
    accum = source / "accum.tif"
    basin = source / "basin.tif"
    write_raster(dem, [[3, 2, 1]])
    write_raster(pointer, [[2, 2, 2]])
    write_raster(accum, [[1, 2, 3]])
    write_raster(basin, [[1, 1, 1]])
    stream = source / "stream.json"
    build = source / "build.json"
    qc = source / "qc.json"
    stream.write_text(json.dumps({"skill": "spatial-analysis/extract-dem-stream-network", "status": "success",
                                  "artifacts": {"filled_dem": terrain.ref(dem), "d8_pointer": terrain.ref(pointer),
                                                "flow_accumulation": terrain.ref(accum)}}), encoding="utf-8")
    build.write_text(json.dumps({"skill": "spatial-analysis/build-hydrological-topology", "status": "success",
                                 "inputs": {"filled_dem": terrain.ref(dem), "d8_pointer": terrain.ref(pointer),
                                            "flow_accumulation": terrain.ref(accum)},
                                 "artifacts": {"subbasins_clipped": terrain.ref(basin)}}), encoding="utf-8")
    qc.write_text(json.dumps({"skill": "spatial-analysis/validate-hydrological-topology", "status": "success",
                              "parameters": {"expected_outlets": 1}, "checks": [{"status": "PASS"}],
                              "inputs": {"build_result": terrain.ref(build)}}), encoding="utf-8")
    return stream, build, qc


def config():
    return {"schema_version": "1.0", "timestep_hours": 1, "timezone": "Asia/Shanghai",
            "timestamp_semantics": "interval_end", "forcing_unit": "mm/step", "warmup_steps": 1,
            "simulation_start": "2020-01-01T02:00:00+08:00", "simulation_end": "2020-01-01T05:00:00+08:00",
            "water_balance_tolerance_m": 1e-8,
            "parameters": {"qs0": 1e-7, "lnTe": -8.0, "m": 0.05, "Sr0": 0.03, "Srmax": 0.1,
                           "td": 5.0, "vch": 1000.0, "vr": 100.0, "infiltration_excess": False}}


def test_terrain_and_model_chain(tmp_path: Path, scripts):
    from argparse import Namespace
    terrain, model = scripts
    stream, build, qc = terrain_fixture(tmp_path, terrain)
    tr = terrain.run(Namespace(stream_result=stream, topology_result=build, topology_qc_result=qc,
                               classes=2, distance_bins=2, minimum_slope=0.01,
                               output_dir=tmp_path / "terrain", overwrite=False))
    assert tr["parameters"]["basin_area_m2"] == 300
    assert tr["parameters"]["slope_clipped_cells"] == 1
    schema_path = Path(__file__).parents[1] / "resources/schemas/spatial-run-result.schema.json"
    Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(tr)
    classes = pd.read_csv(tmp_path / "terrain/topographic_index_classes.csv")
    assert classes.area_fraction.sum() == pytest.approx(1)
    curve = pd.read_csv(tmp_path / "terrain/distance_area.csv")
    assert curve.cumulative_area_m2.iloc[-1] == 300
    assert curve.distance_m.iloc[-1] == pytest.approx(20)
    forcing = tmp_path / "forcing.csv"
    pd.DataFrame({"time": pd.date_range("2020-01-01 01:00", periods=5, freq="h", tz="Asia/Shanghai").astype(str),
                  "P": [0, 5, 0, 100, 0], "PET": [1, 1, 1, 1, 1]}).to_csv(forcing, index=False)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(config()), encoding="utf-8")
    out = tmp_path / "model"
    result = model.run(Namespace(terrain_result=tmp_path / "terrain/result.json", forcing=forcing,
                                 params=cfg, output_dir=out, overwrite=False))
    process = pd.read_csv(out / "process.csv")
    assert result["status"] == "success"
    assert process.warmup.sum() == 1
    assert process.outlet_m3_s.ge(0).all()
    assert process.water_balance_residual_mm.abs().max() < 1e-5
    assert (process.generated_mm - process.saturation_excess_mm - process.infiltration_excess_mm - process.baseflow_mm).abs().max() < 1e-8
    assert json.loads((out / "final_state.json").read_text())["routing_storage_m"] >= 0


def test_invalid_d8_path_fails(scripts):
    terrain, _ = scripts
    with pytest.raises(terrain.QCError):
        terrain.terrain_arrays(np.array([[3., 2., 1.]]), np.array([[2, 32, 2]]),
                               np.array([[1., 2., 3.]]), np.array([[True, True, True]]), 10, 10, 0.01)
    with pytest.raises(terrain.QCError, match="exactly one exit"):
        terrain.terrain_arrays(np.array([[3., 2., 1.]]), np.array([[32, 2, 2]]),
                               np.array([[1., 2., 3.]]), np.array([[True, True, True]]), 10, 10, 0.01)


def test_green_ampt_branch_and_config_gate(tmp_path: Path, scripts):
    _, model = scripts
    cfg = config()
    cfg["parameters"].update(infiltration_excess=True, K0=0.001, psi=0.1, dtheta=0.2)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert model.load_config(path)["parameters"]["infiltration_excess"]
    cfg["parameters"].pop("K0")
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(model.QCError):
        model.load_config(path)


def test_green_ampt_strong_rain_and_dry_recession(scripts):
    _, model = scripts
    from _topmodel_core import green_ampt, initial_state, routing_kernel, step
    infiltrated, cumulative = green_ampt(0.1, 1, 0.001, 0.1, 0.2, 0)
    assert 0 < infiltrated < 0.1
    assert cumulative == pytest.approx(infiltrated)
    ti = np.array([5.0, 6.0])
    weights = np.array([0.5, 0.5])
    p = config()["parameters"] | {"infiltration_excess": True, "K0": 0.001, "psi": 0.1, "dtheta": 0.2}
    kernel = routing_kernel(np.array([10., 20.]), np.array([0.5, 1.]), 1, p["vch"], p["vr"])
    state = initial_state(ti, weights, p, 1, kernel)
    wet = step(state, 0.1, 0, p, ti, weights, 1, kernel)
    dry = step(state, 0, 0, p, ti, weights, 1, kernel)
    assert wet["infiltration_excess_m"] > 0
    assert dry["infiltration_excess_m"] == 0
    assert dry["baseflow_m"] > 0


def test_analytic_single_class_baseflow_recession(scripts):
    _, _ = scripts
    from _topmodel_core import initial_state, step
    p = {"qs0": 0.1, "lnTe": 0.0, "m": 1.0, "Sr0": 0.1, "Srmax": 0.1,
         "td": 1.0, "vch": 1.0, "vr": 1.0, "infiltration_excess": False}
    ti = np.array([0.0])
    weights = np.array([1.0])
    kernel = np.array([1.0])
    state = initial_state(ti, weights, p, 1, kernel)
    assert state["mean_deficit_m"] == pytest.approx(np.log(10))
    first = step(state, 0, 0, p, ti, weights, 1, kernel)
    assert first["baseflow_m"] == pytest.approx(0.1)
    second = step(state, 0, 0, p, ti, weights, 1, kernel)
    assert second["baseflow_m"] == pytest.approx(0.1 * np.exp(-0.1))
    assert second["outlet_m"] == pytest.approx(second["generated_m"])


def test_terrain_hash_tampering_rejected(tmp_path: Path, scripts):
    from argparse import Namespace
    terrain, _ = scripts
    stream, build, qc = terrain_fixture(tmp_path, terrain)
    # QC cites the original build-result hash; changing it must fail before calculation.
    build.write_text(build.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(terrain.QCError):
        terrain.run(Namespace(stream_result=stream, topology_result=build, topology_qc_result=qc,
                              classes=2, distance_bins=2, minimum_slope=0.01,
                              output_dir=tmp_path / "terrain", overwrite=False))


def test_spatial_cli_writes_error_result_on_qc_failure(tmp_path: Path, scripts):
    terrain, _ = scripts
    stream, build, qc = terrain_fixture(tmp_path, terrain)
    qc_doc = json.loads(qc.read_text(encoding="utf-8"))
    qc_doc["checks"] = [{"status": "FAIL"}]
    qc.write_text(json.dumps(qc_doc), encoding="utf-8")
    output = tmp_path / "failed"
    done = subprocess.run([sys.executable, str(Path(terrain.__file__)), "--stream-result", str(stream),
                           "--topology-result", str(build), "--topology-qc-result", str(qc),
                           "--classes", "2", "--distance-bins", "2", "--minimum-slope", "0.01",
                           "--output-dir", str(output)], capture_output=True, text=True, check=False)
    assert done.returncode == 2
    assert json.loads((output / "result.json").read_text(encoding="utf-8"))["status"] == "error"


def test_forcing_time_and_missing_qc(tmp_path: Path, scripts):
    _, model = scripts
    path = tmp_path / "forcing.csv"
    pd.DataFrame({"time": ["2020-01-01T01:00:00+08:00", "2020-01-01T01:00:00+08:00"],
                  "P": [1, 2], "PET": [0, 0]}).to_csv(path, index=False)
    with pytest.raises(model.QCError):
        model.forcing_table(path, config())
    cfg = config()
    cfg["forcing_unit"] = "mm/h"
    cfg_path = tmp_path / "bad_units.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(model.QCError):
        model.load_config(cfg_path)
    path.write_text("time,P,PET\n2020-01-01T01:00:00+08:00,,0\n", encoding="utf-8")
    with pytest.raises(model.QCError):
        model.forcing_table(path, config())
