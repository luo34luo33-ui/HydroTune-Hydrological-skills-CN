"""Deterministic end-to-end checks for the four forcing-chain skills."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


def call(skill: str, script: str, *args: object) -> subprocess.CompletedProcess:
    path = ROOT / skill / "examples" / script
    return subprocess.run([sys.executable, str(path), *map(str, args)], capture_output=True, text=True)


def save(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def ref(path: Path, base: Path | None = None) -> dict:
    return {"path": path.name if base else str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def check_schema(result_path: Path, schema_name: str):
    schema = json.loads((ROOT / "resources" / "schemas" / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(json.loads(result_path.read_text(encoding="utf-8")))


def test_station_forcing_and_reset(tmp_path):
    source = tmp_path / "rain.csv"
    pd.DataFrame({"t": ["2020-01-01 00:00", "2020-01-01 01:00", "2020-01-01 02:00"],
                  "id": ["A"] * 3, "v": [10, 12, 1], "reset": [0, 0, 1]}).to_csv(source, index=False)
    config = {"schema_version": "1.0", "variable": "P", "columns": {"time": "t", "source_id": "id", "value": "v", "reset": "reset"},
              "unit": "mm/step", "value_semantics": "running_cumulative", "timezone": "Asia/Shanghai",
              "source_timezone_mode": "local_naive", "timestamp_semantics": "interval_end", "timestep_seconds": 3600}
    cfg = save(tmp_path / "config.json", config)
    out = tmp_path / "out"
    result = call("data-processing/prepare-model-forcing-timeseries", "prepare_model_forcing_timeseries.py", "--source", source, "--config", cfg, "--output-dir", out)
    assert result.returncode == 0, result.stderr
    check_schema(out / "result.json", "data-processing-run-result.schema.json")
    assert pd.read_csv(out / "prepared_forcing.csv").depth_mm.tolist() == [2, 1]
    config["columns"].pop("reset")
    save(cfg, config)
    bad = call("data-processing/prepare-model-forcing-timeseries", "prepare_model_forcing_timeseries.py", "--source", source, "--config", cfg, "--output-dir", tmp_path / "bad")
    assert bad.returncode == 2


def test_daily_and_hourly_eto(tmp_path):
    stations = tmp_path / "stations.csv"
    pd.DataFrame({"source_id": ["A"], "latitude_deg": [50.8], "elevation_m": [100]}).to_csv(stations, index=False)
    for scale, step in (("daily", 86400), ("hourly", 3600)):
        source = tmp_path / f"{scale}.csv"
        times = ["2020-07-01T00:00:00+00:00", "2020-07-02T00:00:00+00:00"] if scale == "daily" else ["2020-07-01T01:00:00+00:00", "2020-07-01T02:00:00+00:00"]
        data = {"time": times, "source_id": ["A", "A"], "t_c": [20, 20], "rn": [10, 10] if scale == "daily" else [2, 2], "ea": [1.5, 1.5], "u2": [2, 2]}
        if scale == "daily":
            data.update(tmax=[25, 25], tmin=[15, 15])
        else:
            data["g"] = [0.2, 0.2]
        pd.DataFrame(data).to_csv(source, index=False)
        config = {"scale": scale, "timestep_seconds": step, "timezone": "UTC", "source_timezone_mode": "aware", "timestamp_semantics": "interval_end",
                  "columns": {"time": "time", "source_id": "source_id", "t_c": "t_c", "rn_mj_m2_step": "rn", "ea_kpa": "ea", "u2_m_s": "u2",
                              **({"tmax_c": "tmax", "tmin_c": "tmin"} if scale == "daily" else {"g_mj_m2_step": "g"})},
                  "units": {"t_c": "degC", "rn_mj_m2_step": "MJ/m2/step", "ea_kpa": "kPa", "u2_m_s": "m/s",
                            **({"tmax_c": "degC", "tmin_c": "degC"} if scale == "daily" else {"g_mj_m2_step": "MJ/m2/step"})},
                  "mapping": {"target": "PET", "factor": 1, "negative_policy": "error"}}
        cfg = save(tmp_path / f"{scale}.json", config)
        out = tmp_path / f"out-{scale}"
        run = call("data-processing/derive-potential-evapotranspiration", "derive_potential_evapotranspiration.py", "--source", source, "--stations", stations, "--config", cfg, "--output-dir", out)
        assert run.returncode == 0, run.stderr
        check_schema(out / "result.json", "data-processing-run-result.schema.json")
        eto = pd.read_csv(out / "eto.csv").ETo_mm.to_numpy()
        assert np.all(eto > 0) and np.allclose(eto, eto[0])
        assert eto[0] == pytest.approx(3.7943702286712195 if scale == "daily" else 0.469191585450837, abs=1e-6)
        assert np.allclose(pd.read_csv(out / "mapped_evap.csv").depth_mm, eto)
        config["units"]["u2_m_s"] = "km/h"
        save(cfg, config)
        bad = call("data-processing/derive-potential-evapotranspiration", "derive_potential_evapotranspiration.py", "--source", source, "--stations", stations, "--config", cfg, "--output-dir", tmp_path / f"bad-{scale}")
        assert bad.returncode == 2


def test_fao56_published_daily_and_hourly_examples(tmp_path):
    # FAO-56 Chapter 4, examples 18 and 19; published inputs are rounded.
    cases = [
        ("daily", 100, {"t_c": 16.9, "tmax_c": 21.5, "tmin_c": 12.3, "rn_mj_m2_step": 13.28, "ea_kpa": 1.409, "u2_m_s": 2.078}, 3.88),
        ("hourly-night", 8, {"t_c": 28, "rn_mj_m2_step": -0.100, "g_mj_m2_step": -0.050, "ea_kpa": 3.402, "u2_m_s": 1.9}, 0.00),
        ("hourly-day", 8, {"t_c": 38, "rn_mj_m2_step": 1.749, "g_mj_m2_step": 0.175, "ea_kpa": 3.445, "u2_m_s": 3.3}, 0.63),
    ]
    for name, elevation, met, expected in cases:
        directory = tmp_path / name
        directory.mkdir()
        daily = name == "daily"
        step = 86400 if daily else 3600
        stations = directory / "stations.csv"
        pd.DataFrame({"source_id": ["FAO"], "latitude_deg": [50.8 if daily else 16.22], "elevation_m": [elevation]}).to_csv(stations, index=False)
        times = ["2020-07-06T00:00:00+00:00", "2020-07-07T00:00:00+00:00"] if daily else ["2020-10-01T03:00:00+00:00", "2020-10-01T04:00:00+00:00"]
        source = directory / "met.csv"
        pd.DataFrame({"time": times, "source_id": ["FAO", "FAO"], **{key: [value, value] for key, value in met.items()}}).to_csv(source, index=False)
        units = {"t_c": "degC", "rn_mj_m2_step": "MJ/m2/step", "ea_kpa": "kPa", "u2_m_s": "m/s"}
        units.update({"tmax_c": "degC", "tmin_c": "degC"} if daily else {"g_mj_m2_step": "MJ/m2/step"})
        config = {"scale": "daily" if daily else "hourly", "timestep_seconds": step, "timezone": "UTC", "source_timezone_mode": "aware",
                  "timestamp_semantics": "interval_end", "columns": {key: key for key in ("time", "source_id", *met)}, "units": units}
        cfg = save(directory / "config.json", config)
        run = call("data-processing/derive-potential-evapotranspiration", "derive_potential_evapotranspiration.py",
                   "--source", source, "--stations", stations, "--config", cfg, "--output-dir", directory / "out")
        assert run.returncode == 0, run.stderr
        value = pd.read_csv(directory / "out" / "eto.csv").ETo_mm.iloc[0]
        assert value == pytest.approx(expected, abs=0.02)


def test_spatial_station_and_grid(tmp_path):
    grid = tmp_path / "subs.tif"
    with rasterio.open(grid, "w", driver="GTiff", height=1, width=2, count=1, dtype="int16", crs="EPSG:32649", transform=from_origin(0, 1, 1, 1), nodata=0) as ds:
        ds.write(np.array([[1, 2]], dtype="int16"), 1)
    table = tmp_path / "subs.csv"
    pd.DataFrame({"sub_id": [1, 2]}).to_csv(table, index=False)
    build = save(tmp_path / "build" / "result.json", {"skill": "spatial-analysis/build-hydrological-topology", "status": "success",
                                                   "artifacts": {"subbasins_clipped": ref(grid), "subbasins_table": ref(table)}})
    qc = save(tmp_path / "qc" / "result.json", {"skill": "spatial-analysis/validate-hydrological-topology", "status": "success",
                                                 "parameters": {"expected_outlets": 1}, "inputs": {"build_result": ref(build)},
                                                 "checks": [{"check": "expected_outlets", "status": "PASS"}]})
    # Two station cells coincide with the two synthetic unit polygons.
    stations = tmp_path / "coords.csv"
    pd.DataFrame({"source_id": ["A", "B"], "x": [0.5, 1.5], "y": [0.5, 0.5]}).to_csv(stations, index=False)
    results = {}
    for var, values in (("P", [1, 3]), ("PET", [0.2, 0.4])):
        directory = tmp_path / var
        directory.mkdir()
        frame = pd.DataFrame({"time": ["2020-01-01T01:00:00+00:00"] * 2 + ["2020-01-01T02:00:00+00:00"] * 2,
                              "source_id": ["A", "B", "A", "B"], "variable": [var] * 4, "depth_mm": values * 2})
        csv = directory / "prepared_forcing.csv"
        frame.to_csv(csv, index=False)
        results[var] = save(directory / "result.json", {"skill": "data-processing/prepare-model-forcing-timeseries", "status": "success", "artifacts": {"forcing": ref(csv, directory)}})
    config = {"min_coverage_fraction": 0.999, "layers": {v: {"kind": "station", "variable": v, "result": str(results[v]), "stations": str(stations), "station_crs": "EPSG:32649"} for v in ("P", "PET")}}
    config["layers"]["evap"] = config["layers"].pop("PET")
    cfg = save(tmp_path / "sources.json", config)
    out = tmp_path / "aggregate"
    run = call("spatial-analysis/aggregate-forcing-to-model-units", "aggregate_forcing_to_model_units.py", "--topology-result", build, "--topology-qc-result", qc, "--source-config", cfg, "--target", "basin", "--output-dir", out)
    assert run.returncode == 0, run.stderr
    check_schema(out / "result.json", "spatial-run-result.schema.json")
    assert np.allclose(pd.read_csv(out / "basin_forcing.csv").P, 2)
    e0_dir = tmp_path / "E0"
    e0_dir.mkdir()
    e0_csv = e0_dir / "prepared_forcing.csv"
    pd.read_csv(tmp_path / "PET" / "prepared_forcing.csv").assign(variable="E0").to_csv(e0_csv, index=False)
    e0_result = save(e0_dir / "result.json", {"skill": "data-processing/prepare-model-forcing-timeseries", "status": "success",
                                               "artifacts": {"forcing": ref(e0_csv, e0_dir)}})
    config["layers"]["evap"] = {"kind": "station", "variable": "E0", "result": str(e0_result),
                                  "stations": str(stations), "station_crs": "EPSG:32649"}
    save(cfg, config)
    subrun = call("spatial-analysis/aggregate-forcing-to-model-units", "aggregate_forcing_to_model_units.py", "--topology-result", build,
                  "--topology-qc-result", qc, "--source-config", cfg, "--target", "subbasin", "--output-dir", tmp_path / "sub-out")
    assert subrun.returncode == 0, subrun.stderr
    sub = pd.read_csv(tmp_path / "sub-out" / "subbasin_forcing.csv")
    assert set(sub.sub_id) == {1, 2} and set(sub.P_mm) == {1, 3}
    config["layers"]["evap"]["variable"] = "PET"
    config["layers"]["evap"]["result"] = str(results["PET"])
    save(cfg, config)
    with (tmp_path / "P" / "prepared_forcing.csv").open("a", encoding="utf-8") as stream:
        stream.write("\n")
    tampered = call("spatial-analysis/aggregate-forcing-to-model-units", "aggregate_forcing_to_model_units.py", "--topology-result", build,
                    "--topology-qc-result", qc, "--source-config", cfg, "--target", "basin", "--output-dir", tmp_path / "tampered")
    assert tampered.returncode == 2
    # A single-band GeoTIFF sequence is also supported.
    manifests = {}
    for var, value in (("P", 4), ("PET", 0.5)):
        rows = []
        for i in range(2):
            path = tmp_path / f"{var}-{i}.tif"
            with rasterio.open(path, "w", driver="GTiff", height=1, width=2, count=1, dtype="float32", crs="EPSG:32649", transform=from_origin(0, 1, 1, 1), nodata=-9999) as ds:
                ds.write(np.full((1, 2), value, dtype="float32"), 1)
            rows.append({"time": f"2020-01-01T0{i+1}:00:00+00:00", "path": path.name})
        manifest = tmp_path / f"{var}-manifest.csv"
        pd.DataFrame(rows).to_csv(manifest, index=False)
        manifests[var] = manifest
    config["layers"] = {"P": {"kind": "geotiff", "variable": "P", "manifest": str(manifests["P"]), "unit": "mm/step", "timestep_seconds": 3600, "timestamp_semantics": "interval_end"},
                        "evap": {"kind": "geotiff", "variable": "PET", "manifest": str(manifests["PET"]), "unit": "mm/step", "timestep_seconds": 3600, "timestamp_semantics": "interval_end"}}
    save(cfg, config)
    run = call("spatial-analysis/aggregate-forcing-to-model-units", "aggregate_forcing_to_model_units.py", "--topology-result", build, "--topology-qc-result", qc, "--source-config", cfg, "--target", "basin", "--output-dir", tmp_path / "grid-out")
    assert run.returncode == 0, run.stderr
    assert np.allclose(pd.read_csv(tmp_path / "grid-out" / "basin_forcing.csv").P, 4)
    with rasterio.open(tmp_path / "P-0.tif", "r+") as ds:
        changed = ds.read(1)
        changed[0, 0] = -9999
        ds.write(changed, 1)
    missing = call("spatial-analysis/aggregate-forcing-to-model-units", "aggregate_forcing_to_model_units.py", "--topology-result", build,
                   "--topology-qc-result", qc, "--source-config", cfg, "--target", "basin", "--output-dir", tmp_path / "missing-grid")
    assert missing.returncode == 2
    import xarray as xr
    for var, value in (("P", 5), ("PET", 0.6)):
        path = tmp_path / f"{var}.nc"
        xr.Dataset({"depth": (("time", "y", "x"), np.full((2, 2, 2), value))},
                   coords={"time": np.array(["2020-01-01T01:00", "2020-01-01T02:00"], dtype="datetime64[s]"),
                           "y": [0.5, -0.5], "x": [0.5, 1.5]}).to_netcdf(path)
        config["layers"]["P" if var == "P" else "evap"] = {"kind": "netcdf", "variable": var, "path": str(path), "data_variable": "depth",
                                                           "dimensions": ["time", "y", "x"], "grid_crs": "EPSG:32649", "time_zone": "UTC",
                                                           "unit": "mm/step", "timestep_seconds": 3600, "timestamp_semantics": "interval_end"}
    save(cfg, config)
    run = call("spatial-analysis/aggregate-forcing-to-model-units", "aggregate_forcing_to_model_units.py", "--topology-result", build, "--topology-qc-result", qc, "--source-config", cfg, "--target", "basin", "--output-dir", tmp_path / "netcdf-out")
    assert run.returncode == 0, run.stderr
    assert np.allclose(pd.read_csv(tmp_path / "netcdf-out" / "basin_forcing.csv").P, 5)
    # The QC record is bound to the exact topology result hash.
    qc_doc = json.loads(qc.read_text())
    qc_doc["inputs"]["build_result"]["sha256"] = "0" * 64
    save(qc, qc_doc)
    bad = call("spatial-analysis/aggregate-forcing-to-model-units", "aggregate_forcing_to_model_units.py", "--topology-result", build, "--topology-qc-result", qc, "--source-config", cfg, "--target", "basin", "--output-dir", tmp_path / "bad-qc")
    assert bad.returncode == 2


def test_align_outlet_and_reject_qt(tmp_path):
    obs_dir = tmp_path / "obs"
    obs_dir.mkdir()
    obs_csv = obs_dir / "discharge.csv"
    pd.DataFrame({"time": ["2020-01-01T01:00:00+00:00", "2020-01-01T02:00:00+00:00"], "discharge_m3_s": [1, 2]}).to_csv(obs_csv, index=False)
    obs = save(obs_dir / "result.json", {"skill": "data-processing/prepare-discharge-timeseries", "status": "success", "artifacts": {"discharge_timeseries": ref(obs_csv, obs_dir)}})
    sim_dir = tmp_path / "sim"
    sim_dir.mkdir()
    sim_csv = sim_dir / "simulation.csv"
    pd.DataFrame({"time": ["2020-01-01T01:00:00+00:00", "2020-01-01T02:00:00+00:00"], "Q": [1.1, 1.9], "Qt": [1.1, 1.9], "is_warmup": [True, False]}).to_csv(sim_csv, index=False)
    sim = save(sim_dir / "result.json", {"skill": "hydrological-modeling/run-lumped-hbv-model", "status": "success", "artifacts": {"simulation_table": ref(sim_csv, sim_dir)}})
    run = call("post-processing/align-observed-simulated-discharge", "align_observed_simulated_discharge.py", "--observed-result", obs, "--simulation-result", sim, "--output-dir", tmp_path / "aligned")
    assert run.returncode == 0, run.stderr
    assert len(pd.read_csv(tmp_path / "aligned" / "evaluation_discharge.csv")) == 1
    save(sim, {"skill": "hydrological-modeling/run-lumped-xaj-model", "status": "success", "artifacts": {"simulation_table": ref(sim_csv, sim_dir)}})
    bad = call("post-processing/align-observed-simulated-discharge", "align_observed_simulated_discharge.py", "--observed-result", obs, "--simulation-result", sim, "--output-dir", tmp_path / "bad")
    assert bad.returncode == 2
    route_csv = sim_dir / "routed.csv"
    pd.DataFrame({"time": ["2020-01-01T01:00:00+00:00", "2020-01-01T02:00:00+00:00"], "Q_total": [1, 2]}).to_csv(route_csv, index=False)
    route = save(sim_dir / "route.json", {"skill": "hydrological-modeling/route-muskingum-channel", "status": "success",
                                           "inputs": {"inflow": ref(sim_csv)}, "artifacts": {"routed_table": ref(route_csv)}})
    declaration = save(tmp_path / "outlet.json", {"location": "basin_outlet", "outlet_id": "O", "unit": "m3/s",
                                                   "input_unit": "m3/s", "inflow_sha256": ref(sim_csv)["sha256"], "column": "Q_total",
                                                   "upstream_xaj_result": str(sim)})
    routed = call("post-processing/align-observed-simulated-discharge", "align_observed_simulated_discharge.py", "--observed-result", obs, "--simulation-result", route, "--outlet-declaration", declaration, "--output-dir", tmp_path / "routed")
    assert routed.returncode == 0, routed.stderr
    declaration_doc = json.loads(declaration.read_text())
    declaration_doc["inflow_sha256"] = "0" * 64
    save(declaration, declaration_doc)
    invalid = call("post-processing/align-observed-simulated-discharge", "align_observed_simulated_discharge.py", "--observed-result", obs, "--simulation-result", route, "--outlet-declaration", declaration, "--output-dir", tmp_path / "bad-unit")
    assert invalid.returncode == 2


def test_installed_style_synthetic_examples(tmp_path):
    spatial = "spatial-analysis/aggregate-forcing-to-model-units"
    align = "post-processing/align-observed-simulated-discharge"
    demo = tmp_path / "spatial-demo"
    assert call(spatial, "make_synthetic_example.py", "--output-dir", demo).returncode == 0
    run = call(spatial, "aggregate_forcing_to_model_units.py", "--topology-result", demo / "build-result.json",
               "--topology-qc-result", demo / "qc-result.json", "--source-config", demo / "sources.json",
               "--target", "basin", "--output-dir", tmp_path / "spatial-out")
    assert run.returncode == 0, run.stderr

    pair = tmp_path / "pair-demo"
    assert call(align, "make_synthetic_example.py", "--output-dir", pair).returncode == 0
    run = call(align, "align_observed_simulated_discharge.py", "--observed-result", pair / "observed-result.json",
               "--simulation-result", pair / "simulation-result.json", "--output-dir", tmp_path / "pair-out")
    assert run.returncode == 0, run.stderr


def test_event_alignment_and_parquet_observations(tmp_path):
    obs_csv = tmp_path / "obs.parquet"
    pd.DataFrame({"time": ["2020-01-01T01:00:00+00:00", "2020-01-01T02:00:00+00:00"],
                  "discharge_m3_s": [1, 2]}).to_parquet(obs_csv, index=False)
    obs = save(tmp_path / "obs-result.json", {"skill": "data-processing/prepare-discharge-timeseries", "status": "success",
                                              "artifacts": {"discharge_timeseries": ref(obs_csv)}})
    manifest = {}
    for event_id in ("flood-a", "flood-b"):
        frame_path = tmp_path / f"{event_id}.csv"
        pd.DataFrame({"time": ["2020-01-01T01:00:00+00:00", "2020-01-01T02:00:00+00:00"],
                      "Q": [1.1, 1.9], "is_warmup": [True, False]}).to_csv(frame_path, index=False)
        result = save(tmp_path / f"{event_id}.json", {"skill": "hydrological-modeling/run-lumped-hbv-model", "status": "success",
                                                      "artifacts": {"simulation_table": ref(frame_path)}})
        manifest[event_id] = result.name
    manifest_path = save(tmp_path / "events.json", manifest)
    run = call("post-processing/align-observed-simulated-discharge", "align_observed_simulated_discharge.py",
               "--observed-result", obs, "--event-manifest", manifest_path, "--output-dir", tmp_path / "aligned-events")
    assert run.returncode == 0, run.stderr
    output = pd.read_csv(tmp_path / "aligned-events" / "evaluation_discharge.csv")
    assert len(output) == 2 and set(output.event_id) == set(manifest)


@pytest.mark.parametrize("model,key,column,warm", [
    ("run-lumped-hbv-model", "simulation_table", "Q", "is_warmup"),
    ("run-lumped-tank-model", "simulation_table", "Q", "is_warmup"),
    ("run-lumped-dhf-model", "simulation_table", "Q", "is_warmup"),
    ("run-lumped-gr4j-model", "simulation_table", "Q", "is_warmup"),
    ("run-lumped-sacsma-model", "simulation_table", "Q", "is_warmup"),
    ("run-lumped-topmodel", "process.csv", "outlet_m3_s", "warmup"),
    ("run-semi-distributed-xaj-model", "outlet_flow", "Q_m3s", "is_warmup"),
])
def test_all_direct_outlet_adapters(tmp_path, model, key, column, warm):
    obs_csv = tmp_path / "observed.csv"
    times = ["2020-01-01T01:00:00+00:00", "2020-01-01T02:00:00+00:00"]
    pd.DataFrame({"time": times, "discharge_m3_s": [1, 2]}).to_csv(obs_csv, index=False)
    obs = save(tmp_path / "observed.json", {"skill": "data-processing/prepare-discharge-timeseries", "status": "success",
                                            "artifacts": {"discharge_timeseries": ref(obs_csv)}})
    sim_csv = tmp_path / "sim.csv"
    frame = pd.DataFrame({"time": times, column: [1.1, 1.9], warm: [True, False]})
    if model == "run-semi-distributed-xaj-model":
        frame["outlet_reach_id"] = 10
    frame.to_csv(sim_csv, index=False)
    sim = save(tmp_path / "sim.json", {"skill": "hydrological-modeling/" + model, "status": "success",
                                       "artifacts": {key: ref(sim_csv)}})
    run = call("post-processing/align-observed-simulated-discharge", "align_observed_simulated_discharge.py",
               "--observed-result", obs, "--simulation-result", sim, "--output-dir", tmp_path / "aligned")
    assert run.returncode == 0, run.stderr
    assert len(pd.read_csv(tmp_path / "aligned" / "evaluation_discharge.csv")) == 1
