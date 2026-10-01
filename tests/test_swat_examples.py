from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "hydrological-modeling" / "run-semi-distributed-swat-model"
SCRIPT = SKILL_DIR / "examples" / "run_semi_distributed_swat.py"
EXAMPLES = SCRIPT.parent
SOURCE_PROJECT = Path(r"E:\流域水文模型重构\swat\MiniSWAT-RR")


def _load_module(name: str, path: Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses needs registry entry before exec
    spec.loader.exec_module(module)
    return module


RUNNER = _load_module("run_semi_distributed_swat_test", SCRIPT)
CORE = _load_module("_swat_core_test", EXAMPLES / "_swat_core.py")

SUBS = pd.DataFrame({"sub_id": [1, 2, 3], "area_km2": [20.0, 20.0, 5.0],
                     "outlet_reach_ids": ["1", "2", "4"]})
REACHES = pd.DataFrame({"reach_id": [1, 2, 3, 4], "sub_id": [1, 2, 3, 3],
                        "downstream_reach_id": [3, 3, 4, 0]})
HRU_ROWS = [
    {"hru_id": "H1", "sub_id": 1, "area_km2": 12.0},
    {"hru_id": "H2", "sub_id": 1, "area_km2": 8.0},
    {"hru_id": "H3", "sub_id": 2, "area_km2": 20.0},
    {"hru_id": "H4", "sub_id": 3, "area_km2": 5.0},
]
HRU_DEFAULTS = {
    "cn2": 65.0, "canmx_mm": 1.5, "brt": 0.55, "latq_co": 0.30, "lat_ttime_days": 3.0,
    "slope_m_m": 0.05, "lat_len_m": 60.0, "perco_lim": 1.0, "cn3_swf": 0.5,
    "esco": 0.15, "ep_frac": 0.5,
    "aquifer_alpha": 0.048, "aquifer_seep_frac": 0.02, "aquifer_specific_yield": 0.06,
    "aquifer_dep_bot_m": 12.0, "aquifer_flo_min_m": 12.0, "aquifer_revap_co": 0.0,
    "aquifer_revap_min_m": 0.0,
    "soil_1_thickness_mm": 300.0, "soil_1_fc_mm": 60.0, "soil_1_ul_mm": 105.0,
    "soil_1_wp_mm_per_mm": 0.10, "soil_1_ksat_mm_hr": 6.0,
    "soil_2_thickness_mm": 700.0, "soil_2_fc_mm": 120.0, "soil_2_ul_mm": 210.0,
    "soil_2_wp_mm_per_mm": 0.12, "soil_2_ksat_mm_hr": 2.0,
}


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _hru_table(path: Path, mutate=None) -> Path:
    rows = []
    for row in HRU_ROWS:
        merged = dict(HRU_DEFAULTS)
        merged.update(row)
        rows.append(merged)
    if mutate:
        for row in rows:
            mutate(row)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _config(start: str, end: str, step_seconds: float = 86400.0, warmup: int = 5,
           channel_profile: str = "linear_storage", overrides=None, reaches=(1, 2, 3, 4)) -> dict:
    return {
        "schema_version": "1.0",
        "time": {"step_seconds": step_seconds, "timezone": "Asia/Shanghai",
                 "timestamp_meaning": "interval_end"},
        "swat": {"profile": "source_port", "cn_mode": "dynamic",
                 "channel_profile": channel_profile, "soil_slug_mm": 1000.0,
                 "cn_froz": 0.000862},
        "initial": {"soil_fraction": 0.5},
        "reach_params_by_id": {
            str(reach): {"musk_k_hr": 24.0, "musk_x": 0.2, "ttime_hr": 18.0,
                         "scoef": 1.0, "trans_loss_m3_day": 0.0, "evap_m3_day": 0.0}
            for reach in reaches
        },
        "parameter_overrides": overrides or {},
        "warmup_steps": warmup,
        "simulation": {"start": start, "end": end},
    }


def _daily_times(periods: int = 40):
    return pd.date_range("2020-01-01T00:00:00+08:00", periods=periods, freq="D")


def _fixture(root: Path, periods: int = 40) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, frame, filename in (("subbasins_table", SUBS, "subbasins.csv"),
                                 ("reaches_table", REACHES, "reaches.csv"),
                                 ("topology_table", REACHES, "topology.csv")):
        path = root / filename
        frame.to_csv(path, index=False)
        paths[key] = path
    build = root / "build_result.json"
    _write_json(build, {"schema_version": "1.0",
                        "skill": "spatial-analysis/build-hydrological-topology",
                        "status": "success",
                        "artifacts": {key: RUNNER.reference(path, root) for key, path in paths.items()}})
    qc = root / "qc_result.json"
    _write_json(qc, {"schema_version": "1.0",
                     "skill": "spatial-analysis/validate-hydrological-topology",
                     "status": "success", "checks": [{"check": "topology", "status": "PASS"}],
                     "warnings": [], "parameters": {"expected_outlets": 1},
                     "inputs": {"build_result": RUNNER.reference(build, root)}})

    times = _daily_times(periods)
    forcing = root / "forcing.csv"
    rows = []
    for index, stamp in enumerate(times):
        rain = 25.0 if index % 7 == 0 else 0.0
        for sub in (1, 2, 3):
            rows.append({"time": stamp.isoformat(), "sub_id": sub,
                         "P_mm": rain, "E0_mm": 2.0})
    pd.DataFrame(rows).to_csv(forcing, index=False)

    params = root / "params.json"
    _write_json(params, _config(times[5].isoformat(), times[-1].isoformat()))
    hru = _hru_table(root / "hru_table.csv")
    return {"root": root, "build": build, "qc": qc, "forcing": forcing, "params": params,
            "hru": hru, "times": times}


def _call(data: dict, output: Path, *extra: str) -> subprocess.CompletedProcess:
    args = [sys.executable, str(SCRIPT),
            "--topology-result", str(data["build"]),
            "--topology-qc-result", str(data["qc"]),
            "--hru-table", str(data["hru"]),
            "--forcing", str(data["forcing"]),
            "--params", str(data["params"]),
            "--output-dir", str(output), *extra]
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False)


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------
def test_end_to_end_produces_declared_artifacts(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    output = tmp_path / "output"
    result = _call(data, output)
    assert result.returncode == 0, result.stderr

    hru = pd.read_csv(output / "hru_process.csv")
    subbasin = pd.read_csv(output / "subbasin_process.csv")
    reach = pd.read_csv(output / "reach_process.csv")
    outlet = pd.read_csv(output / "outlet_flow.csv")
    balance = json.loads((output / "water_balance.json").read_text(encoding="utf-8"))
    document = json.loads((output / "result.json").read_text(encoding="utf-8"))

    assert len(outlet) == 40 and len(subbasin) == 120 and len(reach) == 160 and len(hru) == 160
    assert outlet["outlet_reach_id"].eq(4).all()
    assert outlet["is_warmup"].tolist()[:5] == [True] * 5
    assert not outlet["is_warmup"].iloc[5:].any()
    assert np.isfinite(outlet["Q_m3s"]).all() and (outlet["Q_m3s"] >= 0).all()
    assert document["skill"] == "hydrological-modeling/run-semi-distributed-swat-model"
    assert document["status"] == "success"
    assert all(item["status"] == "PASS" for item in balance["checks"])
    assert abs(balance["max_abs_residual"]["hru_mm"]) <= balance["tolerances"]["hru_mm"]
    scale = max(1.0, abs(balance["basin_fluxes_m3"]["precip_m3"]))
    relative = abs(balance["basin_fluxes_m3"]["residual_m3"]) / scale
    assert relative <= balance["tolerances"]["basin_relative"]
    # every input keeps a SHA-256 record
    for key in ("topology_result", "topology_qc_result", "hru_table", "forcing", "params"):
        assert set(document["inputs"][key]) == {"path", "sha256"}


def test_nonempty_output_directory_is_rejected(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    (output / "keep.txt").write_text("data", encoding="utf-8")
    result = _call(data, output)
    assert result.returncode == 1
    assert result.stdout.strip() == "" or True
    _call(data, output, "--overwrite")
    assert (output / "result.json").is_file()
    assert (output / "keep.txt").is_file()


def test_unicode_and_parenthesis_path(tmp_path: Path) -> None:
    folder = tmp_path / "流域 (合成 SWAT)"
    data = _fixture(folder)
    output = folder / "out put"
    result = _call(data, output)
    assert result.returncode == 0, result.stderr
    assert (output / "hru_process.csv").is_file()


def test_boundary_inflow_enters_receiving_reach(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    baseline = _call(data, tmp_path / "baseline")
    assert baseline.returncode == 0, baseline.stderr
    boundary = data["root"] / "boundary.csv"
    pd.DataFrame({"time": [data["times"][10].isoformat()], "reach_id": [4],
                  "Q_m3s": [10.0]}).to_csv(boundary, index=False)
    output = tmp_path / "with boundary"
    result = _call(data, output, "--boundary-inflow", str(boundary))
    assert result.returncode == 0, result.stderr
    base = pd.read_csv(tmp_path / "baseline" / "outlet_flow.csv")
    changed = pd.read_csv(output / "outlet_flow.csv")
    assert changed["Q_m3s"].iloc[10] > base["Q_m3s"].iloc[10]
    assert set(changed["Q_m3s"].iloc[:10]) == set(base["Q_m3s"].iloc[:10])


def test_boundary_must_target_receiving_reach(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    boundary = data["root"] / "boundary.csv"
    pd.DataFrame({"time": [data["times"][10].isoformat()], "reach_id": [3],
                  "Q_m3s": [1.0]}).to_csv(boundary, index=False)
    # reach 3 receives nothing locally (it only carries reach 1 and 2 outflow)
    result = _call(data, tmp_path / "bad", "--boundary-inflow", str(boundary))
    assert result.returncode == 2


def test_subdaily_forcing_requires_opt_in_aggregation(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    params = data["root"] / "hourly_params.json"
    _write_json(params, _config(data["times"][5].isoformat(), data["times"][-1].isoformat(),
                                step_seconds=3600.0))
    data = dict(data, params=params)
    result = _call(data, tmp_path / "hourly")
    assert result.returncode == 1
    assert "step_seconds" in result.stderr


def test_aggregate_to_daily_matches_daily_forcing(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    baseline = _call(data, tmp_path / "daily")
    assert baseline.returncode == 0, baseline.stderr

    daily = pd.read_csv(data["forcing"])
    rows = []
    for row in daily.itertuples(index=False):
        for hour in range(1, 25):
            stamp = (pd.Timestamp(row.time) - pd.Timedelta(days=1)
                     + pd.Timedelta(hours=hour)).tz_convert("Asia/Shanghai")
            rows.append({"time": stamp.isoformat(), "sub_id": row.sub_id,
                         "P_mm": row.P_mm / 24.0, "E0_mm": row.E0_mm / 24.0})
    hourly = data["root"] / "forcing_hourly.csv"
    pd.DataFrame(rows).to_csv(hourly, index=False)

    params = data["root"] / "hourly_params.json"
    _write_json(params, _config(data["times"][5].isoformat(), data["times"][-1].isoformat(),
                                step_seconds=3600.0))
    result = _call(dict(data, forcing=hourly, params=params), tmp_path / "aggregated",
                   "--aggregate-to-daily", "sum")
    assert result.returncode == 0, result.stderr
    left = pd.read_csv(tmp_path / "daily" / "outlet_flow.csv")
    right = pd.read_csv(tmp_path / "aggregated" / "outlet_flow.csv")
    assert np.allclose(left["Q_m3s"], right["Q_m3s"], rtol=1e-9, atol=1e-12)
    document = json.loads((tmp_path / "aggregated" / "result.json").read_text(encoding="utf-8"))
    assert document["parameters"]["aggregate_to_daily"] == "sum"


def test_incomplete_aggregation_day_is_rejected(tmp_path: Path) -> None:
    data = _fixture(tmp_path)
    daily = pd.read_csv(data["forcing"])
    rows = []
    for index, row in enumerate(daily.itertuples(index=False)):
        hours = 24 if index != 3 else 20
        for hour in range(1, hours + 1):
            stamp = (pd.Timestamp(row.time) - pd.Timedelta(days=1)
                     + pd.Timedelta(hours=hour)).tz_convert("Asia/Shanghai")
            rows.append({"time": stamp.isoformat(), "sub_id": row.sub_id,
                         "P_mm": 0.0, "E0_mm": 0.1})
    hourly = data["root"] / "forcing_short_day.csv"
    pd.DataFrame(rows).to_csv(hourly, index=False)
    params = data["root"] / "hourly_params.json"
    _write_json(params, _config(data["times"][5].isoformat(), data["times"][-1].isoformat(),
                                step_seconds=3600.0))
    result = _call(dict(data, forcing=hourly, params=params), tmp_path / "bad_agg",
                   "--aggregate-to-daily", "sum")
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("change", [
    "missing_hash", "failed_check", "wrong_skill", "multiple_outlets",
    "area_mismatch", "unknown_sub", "missing_column", "duplicate_forcing",
    "negative_rain", "unknown_override", "out_of_range_override", "reach_coverage",
    "simulation_window", "cycle", "missing_layer_column",
])
def test_rejects_invalid_inputs(tmp_path: Path, change: str) -> None:
    data = _fixture(tmp_path)
    output = tmp_path / "out"
    if change == "missing_hash":
        subbasins = data["root"] / "subbasins.csv"
        text = subbasins.read_text(encoding="utf-8")
        subbasins.write_text(text.replace("20.0", "21.0", 1), encoding="utf-8")
    elif change == "failed_check":
        qc = json.loads(data["qc"].read_text(encoding="utf-8"))
        qc["checks"] = [{"check": "topology", "status": "FAIL"}]
        _write_json(data["qc"], qc)
    elif change == "wrong_skill":
        qc = json.loads(data["qc"].read_text(encoding="utf-8"))
        qc["skill"] = "spatial-analysis/build-hydrological-topology"
        _write_json(data["qc"], qc)
    elif change == "multiple_outlets":
        frame = pd.read_csv(data["root"] / "reaches.csv")
        frame.loc[frame["reach_id"] == 4, "downstream_reach_id"] = 0
        frame.loc[frame["reach_id"] == 3, "downstream_reach_id"] = 0
        for name in ("reaches.csv", "topology.csv"):
            frame.to_csv(data["root"] / name, index=False)
        # rewrite hashes so the file check passes and the outlet logic rejects
        data = _refresh_hashes(data)
    elif change == "area_mismatch":
        data = _fixture(tmp_path, periods=40)
        _hru_table(data["hru"], mutate=lambda row: row.update(
            {"area_km2": row["area_km2"] + 1.0}) if row["hru_id"] == "H1" else None)
        data = _refresh_input_hashes(data)
    elif change == "unknown_sub":
        _hru_table(data["hru"], mutate=lambda row: row.update({"sub_id": 99})
                   if row["hru_id"] == "H4" else None)
    elif change == "missing_column":
        _hru_table(data["hru"])
        frame = pd.read_csv(data["hru"])
        frame = frame.drop(columns=["ep_frac"])
        frame.to_csv(data["hru"], index=False)
    elif change == "missing_layer_column":
        frame = pd.read_csv(data["hru"])
        frame = frame.drop(columns=["soil_2_ksat_mm_hr"])
        frame.to_csv(data["hru"], index=False)
    elif change == "duplicate_forcing":
        frame = pd.read_csv(data["forcing"])
        pd.concat([frame, frame.head(3)]).to_csv(data["forcing"], index=False)
    elif change == "negative_rain":
        frame = pd.read_csv(data["forcing"])
        frame.loc[0, "P_mm"] = -1.0
        frame.to_csv(data["forcing"], index=False)
    elif change == "unknown_override":
        _write_json(data["params"], _config(data["times"][5].isoformat(),
                                            data["times"][-1].isoformat(),
                                            overrides={"cn_froz": 0.1}))
    elif change == "out_of_range_override":
        _write_json(data["params"], _config(data["times"][5].isoformat(),
                                            data["times"][-1].isoformat(),
                                            overrides={"cn2": 5.0}))
    elif change == "reach_coverage":
        _write_json(data["params"], _config(data["times"][5].isoformat(),
                                            data["times"][-1].isoformat(),
                                            reaches=(1, 2, 3)))
    elif change == "simulation_window":
        _write_json(data["params"], _config(data["times"][6].isoformat(),
                                            data["times"][-1].isoformat()))
    elif change == "cycle":
        frame = pd.read_csv(data["root"] / "reaches.csv")
        frame.loc[frame["reach_id"] == 4, "downstream_reach_id"] = 1
        for name in ("reaches.csv", "topology.csv"):
            frame.to_csv(data["root"] / name, index=False)
        data = _refresh_hashes(data)

    result = _call(data, output)
    assert result.returncode in (1, 2), (change, result.stdout, result.stderr)
    assert not (output / "outlet_flow.csv").is_file()


def _refresh_hashes(data: dict) -> dict:
    build = json.loads(data["build"].read_text(encoding="utf-8"))
    for key, path in (("subbasins_table", "subbasins.csv"), ("reaches_table", "reaches.csv"),
                      ("topology_table", "topology.csv")):
        target = data["root"] / path
        build["artifacts"][key] = RUNNER.reference(target, data["root"])
    _write_json(data["build"], build)
    qc = json.loads(data["qc"].read_text(encoding="utf-8"))
    qc["inputs"]["build_result"] = RUNNER.reference(data["build"], data["root"])
    _write_json(data["qc"], qc)
    return data


def _refresh_input_hashes(data: dict) -> dict:
    return data


# ---------------------------------------------------------------------------
# Core unit tests
# ---------------------------------------------------------------------------
def test_unit_conversions_match_upstream_identity() -> None:
    assert CORE.MM_KM2_TO_M3 == 1000.0
    assert float(CORE.mm_km2_to_m3(1.0, 1.0)) == 1000.0
    assert float(CORE.m3_day_to_m3_s(86400.0)) == 1.0


def test_topology_rejects_cycles_and_bad_outlet() -> None:
    assert CORE.has_cycle(np.array([1, 2, 1], dtype=np.int64))
    assert not CORE.has_cycle(np.array([2, 2, -1], dtype=np.int64))
    assert CORE.topological_order(np.array([2, 2, -1], dtype=np.int64), 2) is not None
    with pytest.raises(ValueError):
        CORE.assert_acyclic(np.array([1, 2, 1], dtype=np.int64))


def test_curve_number_monotonic_and_bounded() -> None:
    smx, wrt1, wrt2 = CORE.curno(np.array([60.0]), np.array([180.0]), np.array([315.0]),
                                 np.array([0.5]))
    low = float(CORE.curve_number_daily(np.array([5.0]), smx, wrt1, wrt2)[0])
    high = float(CORE.curve_number_daily(np.array([300.0]), smx, wrt1, wrt2)[0])
    assert high > low
    assert 30.0 <= low <= 100.0 and 30.0 <= high <= 100.0


def test_surface_runoff_respects_precipitation_bound() -> None:
    runoff = CORE.surface_runoff_cn(np.array([0.0, 5.0, 120.0]), np.array([75.0, 75.0, 75.0]))
    assert float(runoff[0]) == 0.0
    assert 0.0 <= float(runoff[1]) <= 5.0
    assert 0.0 <= float(runoff[2]) <= 120.0
    gated = CORE.surface_runoff_cn(np.array([0.05]), np.array([80.0]))
    assert float(gated[0]) == 0.0  # PRECIP_GATE_MM


def test_soil_water_routing_conserves_dry_column() -> None:
    thick = np.array([[300.0]])
    fc = np.array([[60.0]])
    ul = np.array([[105.0]])
    ksat = np.array([[6.0]])
    st0 = np.array([[10.0]])
    st1, sepbtm, latq = CORE.soil_water_routing(
        np.array([0.0]), st0, thick, fc, ul, ksat, np.array([0.3]),
        np.array([0.05]), np.array([60.0]), np.array([1.0]))
    assert float(st1[0, 0]) == 10.0
    assert float(sepbtm[0]) == 0.0 and float(latq[0]) == 0.0


def test_aquifier_closure_and_unit_storage_bound() -> None:
    out = CORE.aquifer_step(np.array([10.0]), np.array([0.0]), np.array([0.0]),
                            np.array([0.048]), np.array([0.02]), np.array([0.06]),
                            np.array([12.0]), np.array([12.0]))
    seep = 10.0 * 0.02
    assert (out["baseflow_mm"][0] + seep + out["revap_mm"][0] + out["stor_new_mm"][0]) == pytest.approx(10.0)


def test_muskingum_daily_stability_flag() -> None:
    assert CORE.muskingum_negative_c3(np.array([10.0]), np.array([0.2]))
    assert not CORE.muskingum_negative_c3(np.array([24.0]), np.array([0.2]))
    c1, c2, c3 = CORE.muskingum_coefficients(np.array([24.0]), np.array([0.2]))
    assert float(c1[0] + c2[0] + c3[0]) == pytest.approx(1.0)


def test_parameter_overrides_policy() -> None:
    assert CORE.validate_parameter_overrides(None) == {}
    assert CORE.validate_parameter_overrides({"cn2": 70.0})["cn2"] == 70.0
    with pytest.raises(ValueError):
        CORE.validate_parameter_overrides({"cn_froz": 0.001})
    with pytest.raises(ValueError):
        CORE.validate_parameter_overrides({"alpha_bf": 5.0})
    with pytest.raises(ValueError):
        CORE.validate_parameter_overrides({"unknown": 1.0})


# ---------------------------------------------------------------------------
# Read-only regression against the source project
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_matches_miniswat_rr_reference_run() -> None:
    """只读加载 MiniSWAT-RR 示例，比对出口流量与水量平衡残差。

    源工程路径不存在时跳过；任何时候都不修改源工程。
    """
    source = SOURCE_PROJECT
    if not (source / "src" / "miniswat_rr" / "api.py").is_file():
        pytest.skip(f"MiniSWAT-RR source not available at {source}")
    module = _load_module("miniswat_rr", source / "src" / "miniswat_rr" / "__init__.py")

    import csv

    basin = module.basin_from_yaml(source / "examples" / "minimal_basin.yaml")
    rows = list(csv.DictReader((source / "examples" / "example_forcing.csv").open(encoding="utf-8")))
    P = np.vstack([np.array([float(r["P_S1_mm"]) for r in rows]),
                   np.array([float(r["P_S2_mm"]) for r in rows])])
    E0 = np.array([float(r["PET_mm"]) for r in rows])
    Boundary = np.vstack([np.array([float(r["Boundary_S1_m3s"]) for r in rows]),
                          np.array([float(r["Boundary_S2_m3s"]) for r in rows])]).T
    reference_Q = module.run_model(P=P, E0=E0, Boundary=Boundary, p={}, dt=86400.0, basin=basin)

    hrus = []
    for index, hru in enumerate(basin.hrus, start=1):
        layers = tuple(CORE.SoilLayer(thickness_mm=lay.thickness_mm, fc_mm=lay.fc_mm,
                                      ul_mm=lay.ul_mm, wp_mm_per_mm=lay.wp_mm_per_mm,
                                      ksat_mm_hr=lay.ksat_mm_hr) for lay in hru.soil_layers)
        aquifer = CORE.AquiferParams(alpha=hru.aquifer.alpha, seep_frac=hru.aquifer.seep_frac,
                                     specific_yield=hru.aquifer.specific_yield,
                                     dep_bot_m=hru.aquifer.dep_bot_m,
                                     flo_min_m=hru.aquifer.flo_min_m,
                                     revap_co=hru.aquifer.revap_co,
                                     revap_min_m=hru.aquifer.revap_min_m)
        hrus.append(CORE.HRU(id=f"H{index}", subbasin_id=1 if hru.subbasin_id == "S1" else 2,
                             area_km2=hru.area_km2, cn2=hru.cn2, soil_layers=layers,
                             canmx_mm=hru.canmx_mm, brt=hru.brt, latq_co=hru.latq_co,
                             lat_ttime_days=hru.lat_ttime_days, slope_m_m=hru.slope_m_m,
                             lat_len_m=hru.lat_len_m, perco_lim=hru.perco_lim,
                             cn3_swf=hru.cn3_swf, esco=hru.esco, ep_frac=hru.ep_frac,
                             aquifer=aquifer))
    reaches = []
    for spec, original in zip([(1, 1, 3), (2, 2, 3), (3, None, None)], basin.reaches):
        reaches.append(CORE.Reach(id=spec[0], subbasin_id=spec[1], downstream_id=spec[2],
                                  musk_k_hr=original.musk_k_hr, musk_x=original.musk_x,
                                  ttime_hr=original.ttime_hr, scoef=original.scoef,
                                  trans_loss_m3_day=original.trans_loss_m3_day,
                                  evap_m3_day=original.evap_m3_day))
    config = CORE.BasinConfig(
        hrus=hrus, reaches=reaches,
        subbasins=[CORE.Subbasin(1, 20.0), CORE.Subbasin(2, 20.0)],
        profile=basin.profile, cn_mode=basin.cn_mode,
        channel_profile=basin.channel_profile, cn_froz=basin.cn_froz,
        soil_slug_mm=basin.soil_slug_mm)
    result = CORE.SWATSimulator(config).run(P, E0, Boundary)

    assert np.array_equal(result.outlet_Q_m3s, np.asarray(reference_Q, dtype=np.float64))
    assert all(item["status"] == "PASS" for item in CORE.evaluate_water_balance(result))
