from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path("hydrological-modeling/run-lumped-gr4j-model/examples/run_lumped_gr4j.py")
PARAMS = Path("hydrological-modeling/run-lumped-gr4j-model/examples/source_equivalent_params.json")


def _run(repo: Path, *arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy() if env is None else dict(env)
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(repo / SCRIPT), *map(str, arguments)],
        cwd=repo,
        text=True,
        capture_output=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


def _forcing(rows: int = 240, pet: float = 0.8) -> pd.DataFrame:
    precipitation = np.zeros(rows, dtype=float)
    precipitation[24:48] = 9.0
    precipitation[120:135] = 14.0
    return pd.DataFrame({
        "time": pd.date_range("2020-06-01", periods=rows, freq="h"),
        "P": precipitation,
        "PET": np.full(rows, pet),
    })


def _result(output: Path) -> dict:
    return json.loads((output / "result.json").read_text(encoding="utf-8"))


def _reference_gr4j(
    frame: pd.DataFrame, params: dict, area_km2: float, timestep_hours: float,
    initial_states: dict[str, float] | None = None,
) -> dict[str, np.ndarray]:
    """Independent row-by-row reference implementation of the GR4J source formulas."""
    from math import ceil, tanh

    steps = len(frame)
    x1, x2, x3, x4 = params["X1"], params["X2"], params["X3"], params["X4"]
    precipitation = frame["P"].to_numpy(dtype=float)
    pet = frame["PET"].to_numpy(dtype=float)
    initial = initial_states or {}

    n_uh1 = int(ceil(x4))
    n_uh2 = int(ceil(2.0 * x4))

    def s1(t: float) -> float:
        if t <= 0:
            return 0.0
        if t < x4:
            return (t / x4) ** 2.5
        return 1.0

    def s2(t: float) -> float:
        if t <= 0:
            return 0.0
        if t < x4:
            return 0.5 * (t / x4) ** 2.5
        if t < 2 * x4:
            return 1.0 - 0.5 * (2 - t / x4) ** 2.5
        return 1.0

    ord1 = [s1(t) - s1(t - 1) for t in range(1, n_uh1 + 1)]
    ord2 = [s2(t) - s2(t - 1) for t in range(1, n_uh2 + 1)]
    uh1 = [0.0] * n_uh1
    uh2 = [0.0] * n_uh2

    production = float(initial.get("production_store", 0.0))
    routing = float(initial.get("routing_store", 0.0))

    q_mm = np.zeros(steps)
    ea = np.zeros(steps)
    perc_out = np.zeros(steps)
    gw_out = np.zeros(steps)
    qr_out = np.zeros(steps)
    qd_out = np.zeros(steps)
    s_out = np.zeros(steps)
    r_out = np.zeros(steps)

    for t in range(steps):
        p = float(precipitation[t])
        e = float(pet[t])
        if p > e:
            net_evap = 0.0
            scaled = (p - e) / x1
            if scaled > 13:
                scaled = 13.0
            tanh_scaled = tanh(scaled)
            reservoir_production = (x1 * (1 - (production / x1) ** 2) * tanh_scaled) / (
                1 + production / x1 * tanh_scaled
            )
            routing_pattern = p - e - reservoir_production
        else:
            scaled = (e - p) / x1
            if scaled > 13:
                scaled = 13.0
            tanh_scaled = tanh(scaled)
            ps_div_x1 = (2 - production / x1) * tanh_scaled
            net_evap = production * ps_div_x1 / (1 + (1 - production / x1) * tanh_scaled)
            reservoir_production = 0.0
            routing_pattern = 0.0
        production = production - net_evap + reservoir_production
        percolation = production / (1 + (production / 2.25 / x1) ** 4) ** 0.25
        routing_pattern = routing_pattern + (production - percolation)
        production = percolation

        for i in range(0, len(uh1) - 1):
            uh1[i] = uh1[i + 1] + ord1[i] * routing_pattern
        uh1[-1] = ord1[-1] * routing_pattern
        for j in range(0, len(uh2) - 1):
            uh2[j] = uh2[j + 1] + ord2[j] * routing_pattern
        uh2[-1] = ord2[-1] * routing_pattern

        groundwater_exchange = x2 * (routing / x3) ** 3.5
        routing = max(0.0, routing + uh1[0] * 0.9 + groundwater_exchange)
        r2 = routing / (1 + (routing / x3) ** 4) ** 0.25
        qr = routing - r2
        qd = max(0.0, uh2[0] * 0.1 + groundwater_exchange)
        routing = r2

        q_mm[t] = qr + qd
        ea[t] = net_evap
        perc_out[t] = percolation
        gw_out[t] = groundwater_exchange
        qr_out[t] = qr
        qd_out[t] = qd
        s_out[t] = production
        r_out[t] = routing

    unit_conv = (area_km2 * 1000.0) / (timestep_hours * 3600.0)
    return {
        "Q_MM": q_mm,
        "Q": q_mm * unit_conv,
        "EA": ea,
        "PERC": perc_out,
        "GW": gw_out,
        "QR": qr_out,
        "QD": qd_out,
        "PROD_STORE": s_out,
        "ROUT_STORE": r_out,
    }


def test_gr4j_example_has_help(repo_root: Path) -> None:
    result = _run(repo_root, "--help")
    assert result.returncode == 0, result.stderr
    assert "--output-dir" in result.stdout


def test_gr4j_continuous_run_dual_calibre_and_bounds(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "GR4J 管线（测试）"
    root.mkdir(parents=True)
    forcing = root / "forcing（合成）.csv"
    _forcing().to_csv(forcing, index=False, encoding="utf-8-sig")
    output = root / "01 GR4J（结果）"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "584", "--timestep-hours", "1", "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    document = _result(output)
    assert document["skill"] == "hydrological-modeling/run-lumped-gr4j-model"
    assert document["status"] in {"success", "warning"}

    table = pd.read_csv(output / "lumped_gr4j_runoff.csv")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    assert len(table) == 240
    assert table["Q"].min() >= 0.0
    unit_conv = (584.0 * 1000.0) / (1.0 * 3600.0)
    np.testing.assert_allclose(
        table["Q"].to_numpy(dtype=float),
        table["Q_MM"].to_numpy(dtype=float) * unit_conv,
        rtol=1e-12,
        atol=1e-12,
    )
    assert table["PROD_STORE"].max() <= parameters["X1"] + 1e-9
    assert table["ROUT_STORE"].min() >= 0.0
    assert all(item["status"] != "FAIL" for item in document["checks"])


def test_gr4j_matches_source_formula_reference(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "GR4J 等价（测试）"
    root.mkdir(parents=True)
    frame = _forcing(150)
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "584", "--timestep-hours", "1", "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    produced = pd.read_csv(output / "lumped_gr4j_runoff.csv")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    reference = _reference_gr4j(frame, parameters, 584.0, 1.0)
    for column in ("Q_MM", "Q", "EA", "PERC", "GW", "QR", "QD", "PROD_STORE", "ROUT_STORE"):
        np.testing.assert_allclose(
            produced[column].to_numpy(dtype=float),
            reference[column],
            rtol=1e-9,
            atol=1e-12,
            err_msg=f"列 {column} 与源码公式参考实现不一致",
        )


def test_gr4j_negative_x2_means_external_inflow(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "GR4J 汇入（测试）"
    root.mkdir(parents=True)
    forcing = root / "forcing.csv"
    _forcing(96, pet=2.5).to_csv(forcing, index=False, encoding="utf-8-sig")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    parameters["X2"] = -3.0
    params_file = root / "params（负 X2）.json"
    params_file.write_text(json.dumps(parameters, ensure_ascii=False), encoding="utf-8")
    output = root / "out"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", params_file,
        "--area-km2", "584", "--timestep-hours", "1", "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    table = pd.read_csv(output / "lumped_gr4j_runoff.csv")
    # a negative exchange adds water on dry days, so some Q must exist despite P=0
    assert table["Q"].max() > 0.0
    assert np.any(table["GW"].to_numpy(dtype=float) < 0.0)
    document = _result(output)
    assert all(item["status"] != "FAIL" for item in document["checks"])


def test_gr4j_initial_state_seeds_simulation_start(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "GR4J 初值（测试）"
    root.mkdir(parents=True)
    frame = _forcing(96)
    frame["P"] = 0.0
    frame["PET"] = 0.0
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    initial = root / "initial（显式）.json"
    initial.write_text(json.dumps({"production_store": 120.0, "routing_store": 40.0}), encoding="utf-8")
    output = root / "out"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "584", "--timestep-hours", "1", "--mode", "continuous",
        "--initial-state", initial, "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    table = pd.read_csv(output / "lumped_gr4j_runoff.csv")
    # zero forcing with positive routing store: QR drains it over time from step 0
    assert table["Q"].iloc[0] > 0.0
    document = _result(output)
    assert document["parameters"]["initial_states"] == {"production_store": 120.0, "routing_store": 40.0}


@pytest.mark.parametrize(
    "mutation,expected",
    [("timestep", 2), ("missing", 2), ("params", 1)],
)
def test_gr4j_rejects_invalid_inputs_with_expected_exit_codes(
    repo_root: Path, tmp_path: Path, mutation: str, expected: int
) -> None:
    root = tmp_path / f"GR4J 负向（{mutation}）测试"
    root.mkdir(parents=True)
    frame = _forcing(48)
    declared = "1"
    if mutation == "timestep":
        declared = "6"
    elif mutation == "missing":
        frame.loc[10, "P"] = np.nan
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    params_argument = str(repo_root / PARAMS)
    if mutation == "params":
        bad = root / "bad.json"
        bad.write_text(json.dumps({"X1": 350.0, "X2": 0.0}), encoding="utf-8")
        params_argument = str(bad)
    output = root / "out"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", params_argument,
        "--area-km2", "584", "--timestep-hours", declared, "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == expected, completed.stdout
    document = _result(output)
    assert document["status"] == "error"
    if expected == 2:
        assert not (output / "lumped_gr4j_runoff.csv").exists()
