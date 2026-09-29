from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path("hydrological-modeling/run-lumped-tank-model/examples/run_lumped_tank.py")
PARAMS = Path("hydrological-modeling/run-lumped-tank-model/examples/source_equivalent_params.json")


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


def _forcing(rows: int = 240, evap: float = 0.35) -> pd.DataFrame:
    precipitation = np.zeros(rows, dtype=float)
    precipitation[24:48] = 9.0
    precipitation[120:135] = 14.0
    return pd.DataFrame({
        "time": pd.date_range("2020-06-01", periods=rows, freq="h"),
        "P": precipitation,
        "E0": np.full(rows, evap),
    })


def _result(output: Path) -> dict:
    return json.loads((output / "result.json").read_text(encoding="utf-8"))


def _reference_tank(frame: pd.DataFrame, params: dict, area_km2: float, timestep_hours: float) -> dict[str, np.ndarray]:
    """Independent row-by-row reference implementation of the Tank source formulas."""
    steps = len(frame)
    precipitation = frame["P"].to_numpy(dtype=float)
    evaporation = frame["E0"].to_numpy(dtype=float)
    net = precipitation - evaporation

    storage = np.zeros((steps, 4))
    qs0_lo = np.zeros(steps)
    qs0_uo = np.zeros(steps)
    qs1 = np.zeros(steps)
    qs2 = np.zeros(steps)
    qs3 = np.zeros(steps)
    qb0 = np.zeros(steps)
    qb1 = np.zeros(steps)
    qb2 = np.zeros(steps)

    storage[0, 0] = max(params["t0_is"], 0.0)
    storage[0, 1] = max(params["t1_is"], 0.0)
    storage[0, 2] = max(params["t2_is"], 0.0)
    storage[0, 3] = max(params["t3_is"], 0.0)

    for t in range(steps):
        qs0_lo[t] = params["t0_soc_lo"] * max(storage[t, 0] - params["t0_soh_lo"], 0.0)
        qs0_uo[t] = params["t0_soc_uo"] * max(storage[t, 0] - params["t0_soh_uo"], 0.0)
        side0 = qs0_lo[t] + qs0_uo[t]
        qs1[t] = params["t1_soc"] * max(storage[t, 1] - params["t1_soh"], 0.0)
        qs2[t] = params["t2_soc"] * max(storage[t, 2] - params["t2_soh"], 0.0)
        qs3[t] = params["t3_soc"] * storage[t, 3]
        qb0[t] = params["t0_boc"] * storage[t, 0]
        qb1[t] = params["t1_boc"] * storage[t, 1]
        qb2[t] = params["t2_boc"] * storage[t, 2]
        if t < steps - 1:
            storage[t + 1, 0] = max(storage[t, 0] + net[t + 1] - (side0 + qb0[t]), 0.0)
            storage[t + 1, 1] = max(storage[t, 1] + qb0[t] - (qs1[t] + qb1[t]), 0.0)
            storage[t + 1, 2] = max(storage[t, 2] + qb1[t] - (qs2[t] + qb2[t]), 0.0)
            storage[t + 1, 3] = max(storage[t, 3] + qb2[t] - qs3[t], 0.0)

    unit_conv = (area_km2 * 1000.0) / (timestep_hours * 3600.0)
    return {
        "S0": storage[:, 0],
        "S1": storage[:, 1],
        "S2": storage[:, 2],
        "S3": storage[:, 3],
        "QS0_LO": qs0_lo,
        "QS0_UO": qs0_uo,
        "QS1": qs1,
        "QS2": qs2,
        "QS3": qs3,
        "QB0": qb0,
        "QB1": qb1,
        "QB2": qb2,
        "Q": np.maximum(unit_conv * (qs0_lo + qs0_uo + qs1 + qs2 + qs3), 0.0),
    }


def test_tank_example_has_help(repo_root: Path) -> None:
    result = _run(repo_root, "--help")
    assert result.returncode == 0, result.stderr
    assert "--output-dir" in result.stdout


def test_tank_continuous_run_state_bounds(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "Tank 管线（测试）"
    root.mkdir(parents=True)
    forcing = root / "forcing（合成）.csv"
    _forcing().to_csv(forcing, index=False, encoding="utf-8-sig")
    output = root / "01 Tank（结果）"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "584", "--timestep-hours", "1", "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    document = _result(output)
    assert document["skill"] == "hydrological-modeling/run-lumped-tank-model"
    assert document["status"] in {"success", "warning"}

    table = pd.read_csv(output / "lumped_tank_runoff.csv")
    assert len(table) == 240
    for column in ("S0", "S1", "S2", "S3"):
        assert table[column].min() >= 0.0
    assert table["Q"].min() >= 0.0
    assert all(item["status"] != "FAIL" for item in document["checks"])


def test_tank_matches_source_formula_reference(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "Tank 等价（测试）"
    root.mkdir(parents=True)
    frame = _forcing(150, evap=2.0)
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "584", "--timestep-hours", "1", "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    produced = pd.read_csv(output / "lumped_tank_runoff.csv")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    reference = _reference_tank(frame, parameters, 584.0, 1.0)
    for column in ("S0", "S1", "S2", "S3", "QS0_LO", "QS0_UO", "QS1", "QS2", "QS3", "QB0", "QB1", "QB2", "Q"):
        np.testing.assert_allclose(
            produced[column].to_numpy(dtype=float),
            reference[column],
            rtol=1e-9,
            atol=1e-9,
            err_msg=f"列 {column} 与源码公式参考实现不一致",
        )


def test_tank_rejects_inverted_outlet_heights(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "Tank 孔高（测试）"
    root.mkdir(parents=True)
    forcing = root / "forcing.csv"
    _forcing(48).to_csv(forcing, index=False, encoding="utf-8-sig")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    parameters["t0_soh_uo"] = 20.0
    parameters["t0_soh_lo"] = 30.0
    bad = root / "bad.json"
    bad.write_text(json.dumps(parameters, ensure_ascii=False), encoding="utf-8")
    output = root / "out"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", bad,
        "--area-km2", "584", "--timestep-hours", "1", "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == 1, completed.stdout
    assert _result(output)["status"] == "error"


@pytest.mark.parametrize(
    "mutation,expected",
    [("timestep", 2), ("missing", 2), ("params", 1)],
)
def test_tank_rejects_invalid_inputs_with_expected_exit_codes(
    repo_root: Path, tmp_path: Path, mutation: str, expected: int
) -> None:
    root = tmp_path / f"Tank 负向（{mutation}）测试"
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
        bad.write_text(json.dumps({"t0_is": 10.0}), encoding="utf-8")
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
        assert not (output / "lumped_tank_runoff.csv").exists()
