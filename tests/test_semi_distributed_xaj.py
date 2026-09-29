from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "hydrological-modeling/run-semi-distributed-xaj-model/examples/run_semi_distributed_xaj.py"
EXAMPLES = SCRIPT.parent
SOURCE = Path("D:/微信文件/xwechat_files/wxid_5d9pdzbua5n822_5e48/msg/file/2026-09/model_hhu.py")


def _load_runner():
    sys.path.insert(0, str(EXAMPLES))
    spec = importlib.util.spec_from_file_location("run_semi_distributed_xaj_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = _load_runner()


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _config(reaches: list[int], start: str, end: str) -> dict:
    return {
        "schema_version": "1.0",
        "time": {"step_seconds": 3600, "timezone": "Asia/Shanghai", "timestamp_meaning": "interval_end"},
        "xaj": {"KC": 0.8, "B": 0.3, "C": 0.15, "IMP": 0.02, "WM": 120,
                "WUM": 20, "WLM": 60, "SM": 40, "EX": 1.2, "KG": 0.2,
                "KI": 0.3, "CG": 0.9, "CI": 0.8, "CS": 0.5, "KE": 1, "XE": 0.2},
        "routing": {"dp_by_reach": {str(reach): 1 for reach in reaches}, "lag_steps": 0},
        "initial": {"soil_fraction": 0.2, "s_mm": 0, "fr": 0, "qi_m3s": 0,
                    "qg_m3s": 0, "qs_cs_m3s": 0, "qi_cs_m3s": 0, "qg_cs_m3s": 0},
        "warmup_steps": 1, "simulation": {"start": start, "end": end},
    }


def _fixture(tmp_path: Path) -> dict[str, Path]:
    subs = pd.DataFrame({"sub_id": [1, 2, 3], "area_km2": [2.0, 3.0, 5.0],
                         "outlet_reach_ids": ["1", "2", "4"]})
    reaches = pd.DataFrame({"reach_id": [1, 2, 3, 4], "sub_id": [1, 2, 3, 3],
                            "downstream_reach_id": [3, 3, 4, 0]})
    paths = {}
    for key, frame, filename in (("subbasins_table", subs, "subbasins.csv"),
                                 ("reaches_table", reaches, "reaches.csv"),
                                 ("topology_table", reaches, "topology.csv")):
        path = tmp_path / filename
        frame.to_csv(path, index=False)
        paths[key] = path
    build = tmp_path / "build_result.json"
    _write_json(build, {"schema_version": "1.0", "skill": "spatial-analysis/build-hydrological-topology",
                        "status": "success", "artifacts": {key: RUNNER.reference(path, tmp_path)
                                                           for key, path in paths.items()}})
    qc = tmp_path / "qc_result.json"
    _write_json(qc, {"schema_version": "1.0", "skill": "spatial-analysis/validate-hydrological-topology",
                     "status": "success", "checks": [{"check": "topology", "status": "PASS"}],
                     "warnings": [], "parameters": {"expected_outlets": 1},
                     "inputs": {"build_result": RUNNER.reference(build, tmp_path)}})
    times = pd.date_range("2020-01-01 01:00:00+08:00", periods=4, freq="h")
    forcing = tmp_path / "forcing.csv"
    pd.DataFrame([{"time": stamp.isoformat(), "sub_id": sub, "P_mm": (8.0 if i == 1 else 0.0),
                   "E0_mm": 0.1} for i, stamp in enumerate(times) for sub in (1, 2, 3)]).to_csv(forcing, index=False)
    params = tmp_path / "params.json"
    _write_json(params, _config([1, 2, 3, 4], times[1].isoformat(), times[-1].isoformat()))
    return {"build": build, "qc": qc, "forcing": forcing, "params": params,
            "time": times[1].isoformat(), "root": tmp_path}


def _call(data: dict, output: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--topology-result", str(data["build"]),
                           "--topology-qc-result", str(data["qc"]), "--forcing", str(data["forcing"]),
                           "--params", str(data["params"]), "--output-dir", str(output), *extra],
                          capture_output=True, text=True, encoding="utf-8", check=False)


def test_branching_network_boundary_and_warmup(tmp_path: Path) -> None:
    folder = tmp_path / "流域 (合成)"
    folder.mkdir()
    data = _fixture(folder)
    baseline = folder / "baseline"
    assert _call(data, baseline).returncode == 0
    b = pd.DataFrame({"time": [data["time"]], "reach_id": [3], "Q_m3s": [10.0]})
    boundary = folder / "boundary.csv"
    b.to_csv(boundary, index=False)
    output = folder / "with boundary"
    result = _call(data, output, "--boundary-inflow", str(boundary))
    assert result.returncode == 0, result.stderr
    sub = pd.read_csv(output / "subbasin_process.csv")
    reach = pd.read_csv(output / "reach_process.csv")
    outlet = pd.read_csv(output / "outlet_flow.csv")
    original = pd.read_csv(baseline / "outlet_flow.csv")
    assert len(sub) == 12 and len(reach) == 16 and len(outlet) == 4
    assert sub.groupby("time").size().eq(3).all()
    assert outlet["is_warmup"].tolist() == [True, False, False, False]
    at = reach[(reach.time == data["time"]) & (reach.reach_id == 3)].iloc[0]
    assert at.boundary_Q_m3s == 10.0
    assert outlet.Q_m3s.iloc[1] > original.Q_m3s.iloc[1]
    assert np.isfinite(sub.select_dtypes(include="number")).all().all()
    assert json.loads((output / "result.json").read_text(encoding="utf-8"))["status"] == "success"


@pytest.mark.parametrize("change", ["missing_forcing", "duplicate_forcing", "bad_qc", "bad_hash",
                                    "multiple_sub_outlets", "bad_dp", "bad_timestep", "nonfinite"])
def test_rejects_invalid_inputs(tmp_path: Path, change: str) -> None:
    data = _fixture(tmp_path)
    if change in ("missing_forcing", "duplicate_forcing", "nonfinite"):
        frame = pd.read_csv(data["forcing"])
        if change == "missing_forcing":
            frame = frame.iloc[:-1]
        elif change == "duplicate_forcing":
            frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
        else:
            frame.loc[0, "P_mm"] = float("inf")
        frame.to_csv(data["forcing"], index=False)
    elif change == "bad_qc":
        qc = json.loads(data["qc"].read_text(encoding="utf-8"))
        qc["checks"][0]["status"] = "FAIL"
        _write_json(data["qc"], qc)
    elif change == "bad_hash":
        with (tmp_path / "subbasins.csv").open("a", encoding="utf-8") as stream:
            stream.write("\n")
    elif change == "multiple_sub_outlets":
        sub = pd.read_csv(tmp_path / "subbasins.csv")
        sub["outlet_reach_ids"] = sub["outlet_reach_ids"].astype(str)
        sub.loc[2, "outlet_reach_ids"] = "3;4"
        sub.to_csv(tmp_path / "subbasins.csv", index=False)
        build = json.loads(data["build"].read_text(encoding="utf-8"))
        build["artifacts"]["subbasins_table"] = RUNNER.reference(tmp_path / "subbasins.csv", tmp_path)
        _write_json(data["build"], build)
        qc = json.loads(data["qc"].read_text(encoding="utf-8"))
        qc["inputs"]["build_result"] = RUNNER.reference(data["build"], tmp_path)
        _write_json(data["qc"], qc)
    else:
        cfg = json.loads(data["params"].read_text(encoding="utf-8"))
        if change == "bad_dp":
            cfg["routing"]["dp_by_reach"]["2"] = -1
        else:
            cfg["time"]["step_seconds"] = 7200
        _write_json(data["params"], cfg)
    result = _call(data, tmp_path / "output")
    assert result.returncode != 0
    assert json.loads((tmp_path / "output/result.json").read_text(encoding="utf-8"))["status"] == "error"


@pytest.mark.skipif(not SOURCE.is_file(), reason="external read-only HHU source unavailable")
def test_one_zone_equations_match_read_only_source() -> None:
    before = RUNNER.sha256(SOURCE)
    fake_numba = types.ModuleType("numba")
    fake_numba.njit = lambda function: function
    previous = sys.modules.get("numba")
    sys.modules["numba"] = fake_numba
    try:
        spec = importlib.util.spec_from_file_location("hhu_read_only_reference", SOURCE)
        hhu = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hhu)
    finally:
        if previous is None:
            del sys.modules["numba"]
        else:
            sys.modules["numba"] = previous
    original_areas, original_dp = hhu.AREAS.copy(), hhu.DP.copy()
    hhu.AREAS = np.array([2.0])
    hhu.DP = np.array([2], dtype=np.int64)
    cfg = _config([1], "2020-01-01T02:00:00+08:00", "2020-01-01T04:00:00+08:00")
    p = cfg["xaj"]
    cfg["routing"]["dp_by_reach"]["1"] = 2
    precip = np.array([0.0, 8.0, 3.0, 0.0])
    evaporation = np.full(4, 0.1)
    boundary_values = np.array([0.0, 5.0, 0.0, 0.0])
    expected = hhu.run_model(precip.reshape(1, -1), evaporation,
                             boundary_values.reshape(-1, 1), p, dt=3600.0)
    network = {"areas": {1: 2.0}, "sub_ids": [1], "reach_ids": [1], "incoming": {1: []},
               "order": [1], "injection": {1: 1}, "outlet": 1}
    adjusted, dp, coeff = RUNNER.validate_config(cfg, network)
    times = list(pd.date_range("2020-01-01 01:00:00+08:00", periods=4, freq="h"))
    forcing = {stamp: {1: (float(precip[i]), 0.1)} for i, stamp in enumerate(times)}
    boundary = {stamp: {1: float(boundary_values[i])} for i, stamp in enumerate(times)}
    _, _, outlet, _ = RUNNER.run_model(cfg, network, times, forcing, boundary,
                                        adjusted, dp, coeff)
    np.testing.assert_allclose(outlet.Q_m3s, expected, rtol=1e-10, atol=1e-10)

    # Nine independently routed source zones: compare the original outlet sum
    # against the migrated local and per-zone routing kernels, not the DAG outlet.
    hhu.AREAS, hhu.DP = original_areas, original_dp
    nine_p = np.array([[0.0, 4.0 + zone, 2.0, 0.0] for zone in range(9)])
    nine_boundary = np.zeros((4, 9))
    nine_boundary[1, 7] = 5.0
    source_sum = hhu.run_model(nine_p, evaporation, nine_boundary, p, dt=3600.0)
    migrated_sum = np.zeros(4)
    for zone in range(9):
        state = RUNNER.initial_state(adjusted, cfg["initial"])
        stages = [[(0.0, 0.0)] * int(original_dp[zone]) for _ in range(3)]
        for step in range(4):
            state, row = RUNNER.local_step(float(nine_p[zone, step]), float(evaporation[step]),
                                           float(original_areas[zone]), 3600.0, adjusted, state)
            components = [row["local_QS_m3s"] + nine_boundary[step, zone],
                          row["local_QI_m3s"], row["local_QG_m3s"]]
            for component in range(3):
                flow, stages[component] = RUNNER.muskingum_step(
                    components[component], stages[component], coeff)
                migrated_sum[step] += flow
    np.testing.assert_allclose(migrated_sum, source_sum, rtol=1e-10, atol=1e-10)
    assert RUNNER.sha256(SOURCE) == before
