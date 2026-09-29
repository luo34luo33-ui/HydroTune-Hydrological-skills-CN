from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path("hydrological-modeling/run-lumped-hbv-model/examples/run_lumped_hbv.py")
PARAMS = Path("hydrological-modeling/run-lumped-hbv-model/examples/source_equivalent_params.json")


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


def _forcing(rows: int = 240) -> pd.DataFrame:
    precipitation = np.zeros(rows, dtype=float)
    precipitation[24:48] = 9.0
    precipitation[120:135] = 14.0
    return pd.DataFrame({
        "time": pd.date_range("2020-06-01", periods=rows, freq="h"),
        "P": precipitation,
        "E0": np.full(rows, 0.35),
    })


def _result(output: Path) -> dict:
    return json.loads((output / "result.json").read_text(encoding="utf-8"))


def _reference_hbv(frame: pd.DataFrame, params: dict, area_km2: float, timestep_hours: float) -> dict[str, np.ndarray]:
    """Independent row-by-row reference implementation of the HBV source formulas."""
    steps = len(frame)
    fc, beta, c = params["fc"], params["beta"], params["c"]
    k0, threshold, k1, k2, kp, lp = params["k0"], params["l"], params["k1"], params["k2"], params["kp"], params["lp"]
    pwp = lp * fc
    precipitation = frame["P"].to_numpy(dtype=float)
    evaporation = frame["E0"].to_numpy(dtype=float)

    sm = np.zeros(steps)
    suz = np.zeros(steps)
    slz = np.zeros(steps)
    runoff = np.zeros(steps)
    q0 = np.zeros(steps)
    q1 = np.zeros(steps)
    q2 = np.zeros(steps)

    for t in range(steps):
        prev_sm = sm[t - 1] if t > 0 else 0.0
        prev_suz = suz[t - 1] if t > 0 else 0.0
        prev_slz = slz[t - 1] if t > 0 else 0.0
        pet = evaporation[t] * c
        if prev_sm > pwp:
            ae = pet
        else:
            ae = pet * max(prev_sm, 0.0) / max(pwp, 1e-10)
        ae = max(ae, 0.0)
        ratio = min(max(prev_sm, 0.0) / max(fc, 1e-10), 1.0)
        effective = precipitation[t] * (ratio ** beta)
        sm[t] = max(prev_sm + precipitation[t] - ae - effective, 0.0)
        recharge = max(effective * (1 - ((fc - sm[t]) / fc) ** 2), 0.0)
        q0[t] = k0 * max(sm[t] - threshold, 0.0)
        q1[t] = k1 * max(prev_suz, 0.0) if t > 0 else 0.0
        q2[t] = k2 * max(prev_slz, 0.0) if t > 0 else 0.0
        exchange = kp * (prev_slz - prev_suz) if t > 0 else 0.0
        if t > 0:
            suz[t] = max(prev_suz + recharge - q1[t] + exchange, 0.0)
            slz[t] = max(prev_slz - q2[t] - exchange, 0.0)
        else:
            suz[t] = max(recharge, 0.0)
            slz[t] = 0.0
        runoff[t] = q0[t] + q1[t] + q2[t]

    unit_conv = (area_km2 * 1000.0) / (timestep_hours * 3600.0)
    return {
        "Q": np.maximum(unit_conv * runoff, 0.0),
        "SM": sm,
        "SUZ": suz,
        "SLZ": slz,
        "Q0": q0,
        "Q1": q1,
        "Q2": q2,
    }


def test_hbv_example_has_help(repo_root: Path) -> None:
    result = _run(repo_root, "--help")
    assert result.returncode == 0, result.stderr
    assert "--output-dir" in result.stdout


def test_hbv_continuous_run_state_bounds(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "HBV 管线（测试）"
    root.mkdir(parents=True)
    forcing = root / "forcing（合成）.csv"
    _forcing().to_csv(forcing, index=False, encoding="utf-8-sig")
    output = root / "01 HBV（结果）"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "584", "--timestep-hours", "1", "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    document = _result(output)
    assert document["skill"] == "hydrological-modeling/run-lumped-hbv-model"
    assert document["status"] in {"success", "warning"}

    table = pd.read_csv(output / "lumped_hbv_runoff.csv")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    assert len(table) == 240
    assert table["Q"].min() >= 0.0
    assert table["SM"].max() <= parameters["fc"] + 1e-9
    assert table["SUZ"].min() >= 0.0 and table["SLZ"].min() >= 0.0
    assert all(item["status"] != "FAIL" for item in document["checks"])


def test_hbv_matches_source_formula_reference(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "HBV 等价（测试）"
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
    produced = pd.read_csv(output / "lumped_hbv_runoff.csv")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    reference = _reference_hbv(frame, parameters, 584.0, 1.0)
    for column in ("Q", "SM", "SUZ", "SLZ", "Q0", "Q1", "Q2"):
        np.testing.assert_allclose(
            produced[column].to_numpy(dtype=float),
            reference[column],
            rtol=1e-9,
            atol=1e-9,
            err_msg=f"列 {column} 与源码公式参考实现不一致",
        )


def test_hbv_timestep_changes_discharge_scale(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "HBV 步长（测试）"
    root.mkdir(parents=True)
    # the daily run needs an hourly-consistent axis, so give it a 24h-spaced forcing
    daily_frame = _forcing(96)
    daily_frame["time"] = pd.date_range("2020-06-01", periods=96, freq="24h")
    forcing = root / "forcing.csv"
    daily_frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    hourly_forcing = root / "forcing_h.csv"
    _forcing(96).to_csv(hourly_forcing, index=False, encoding="utf-8-sig")
    daily_dir = root / "daily"
    hourly_dir = root / "hourly"
    assert _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "584", "--timestep-hours", "24", "--mode", "continuous",
        "--output-dir", daily_dir,
    ).returncode == 0
    assert _run(
        repo_root, "--forcing", hourly_forcing, "--params", repo_root / PARAMS,
        "--area-km2", "584", "--timestep-hours", "1", "--mode", "continuous",
        "--output-dir", hourly_dir,
    ).returncode == 0
    daily = pd.read_csv(daily_dir / "lumped_hbv_runoff.csv")
    hourly = pd.read_csv(hourly_dir / "lumped_hbv_runoff.csv")
    # the source hard-codes 86400 seconds; exposing the step must move the scale
    # (hourly conversion is 24x the daily one, so hourly m3/s is larger)
    assert not np.allclose(daily["Q"].to_numpy(), hourly["Q"].to_numpy())
    assert daily["Q"].max() < hourly["Q"].max()


@pytest.mark.parametrize(
    "mutation,expected",
    [("timestep", 2), ("missing", 2), ("params", 1)],
)
def test_hbv_rejects_invalid_inputs_with_expected_exit_codes(
    repo_root: Path, tmp_path: Path, mutation: str, expected: int
) -> None:
    root = tmp_path / f"HBV 负向（{mutation}）测试"
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
        bad.write_text(json.dumps({"fc": 150.0}), encoding="utf-8")
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
        assert not (output / "lumped_hbv_runoff.csv").exists()
