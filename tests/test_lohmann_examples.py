from __future__ import annotations

import json
import os
from math import sqrt
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from scipy.integrate import quad


SCRIPT = Path("hydrological-modeling/route-lohmann-channel/examples/route_lohmann.py")
PARAMS = Path("hydrological-modeling/route-lohmann-channel/examples/source_equivalent_params.json")

KE = 12
UH_DAY = 96
DT = 3600.0
TMAX = UH_DAY * 24
LE = 48 * 50


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


def _inflow(rows: int = 200) -> pd.DataFrame:
    direct = np.zeros(rows, dtype=float)
    direct[20:45] = 12.0
    direct[100:118] = 18.0
    base = np.full(rows, 4.0)
    base[45:100] = 6.0
    return pd.DataFrame({
        "time": pd.date_range("2020-06-01", periods=rows, freq="D"),
        "direct": direct,
        "base": base,
    })


def _result(output: Path) -> dict:
    return json.loads((output / "result.json").read_text(encoding="utf-8"))


def _reference_channel_uh(flowlen: float, velo: float, diff: float) -> np.ndarray:
    """Independent reference for the standard 96-day channel UH.

    grid[k] = H((k+1)*DT) is the impulse response at lag (k+1) hours, so the
    convolution weight for an integer-hour lag l is grid[l-1]; the delay-0
    weight is 0.
    """
    grid = np.zeros(LE)
    elapsed = 0.0
    for k in range(LE):
        elapsed += DT
        pot = ((velo * elapsed - flowlen) ** 2) / (4 * diff * elapsed)
        grid[k] = flowlen / (2 * elapsed * sqrt(np.pi * elapsed * diff)) * np.exp(-pot) if pot <= 69 else 0.0
    total = grid.sum()
    grid = grid / total if total > 0 else np.eye(1, LE)[0] * 0 + np.where(np.arange(LE) == 0, 1.0, 0.0)
    delays = np.concatenate([[0.0], grid[: LE - 1]])  # delays[j] = weight for lag j
    unit_day = np.zeros(TMAX)
    unit_day[0:24] = 1.0 / 24.0
    response = np.convolve(unit_day, delays)[:TMAX]
    uh = np.array([response[24 * i: 24 * i + 24].sum() for i in range(UH_DAY)])
    return uh


def _reference_route(frame: pd.DataFrame, params: dict, flowlen: float) -> dict[str, np.ndarray]:
    """Independent reference for the standard Lohmann routing."""
    uh_river = _reference_channel_uh(flowlen, params["VELO"], params["DIFF"])
    dist = stats.gamma(params["N"], scale=params["K"])
    hru = np.array([quad(dist.pdf, i, i + 1)[0] for i in range(KE)])
    uh_base_hru = np.zeros(KE)
    uh_base_hru[0] = 1.0

    length = KE + UH_DAY - 1
    uh_direct = np.zeros(length)
    uh_base = np.zeros(length)
    for k in range(KE):
        for u in range(UH_DAY):
            uh_direct[k + u] += hru[k] * uh_river[u]
            uh_base[k + u] += uh_base_hru[k] * uh_river[u]
    uh_direct /= uh_direct.sum()
    uh_base /= uh_base.sum()

    def convolve_zero(series: np.ndarray, uh: np.ndarray) -> np.ndarray:
        return np.convolve(series, uh)[: len(series)]

    direct = frame["direct"].to_numpy(dtype=float)
    base = frame["base"].to_numpy(dtype=float)
    return {
        "routed_direct": convolve_zero(direct, uh_direct),
        "routed_base": convolve_zero(base, uh_base),
        "uh_river": uh_river,
        "uh_direct": uh_direct,
        "uh_base": uh_base,
    }


def test_lohmann_example_has_help(repo_root: Path) -> None:
    result = _run(repo_root, "--help")
    assert result.returncode == 0, result.stderr
    assert "--output-dir" in result.stdout


def test_lohmann_run_dual_components_and_normalization(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "Lohmann 管线（测试）"
    root.mkdir(parents=True)
    inflow = root / "inflow（合成）.csv"
    _inflow().to_csv(inflow, index=False, encoding="utf-8-sig")
    output = root / "01 Lohmann（结果）"
    completed = _run(
        repo_root, "--inflow", inflow, "--route-params", repo_root / PARAMS,
        "--flowlen-m", "50000", "--timestep-hours", "24", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    document = _result(output)
    assert document["skill"] == "hydrological-modeling/route-lohmann-channel"
    assert document["status"] in {"success", "warning"}

    table = pd.read_csv(output / "routed_lohmann.csv")
    assert len(table) == 200
    np.testing.assert_allclose(
        table["Q_total"].to_numpy(dtype=float),
        table["routed_direct"].to_numpy(dtype=float) + table["routed_base"].to_numpy(dtype=float),
        rtol=1e-12,
        atol=1e-12,
    )
    uh_document = json.loads((output / "lohmann_unit_hydrographs.json").read_text(encoding="utf-8"))
    uh_river = np.array(uh_document["uh_river_days"])
    np.testing.assert_allclose(uh_river.sum(), 1.0, rtol=0, atol=1e-9)
    np.testing.assert_allclose(np.array(uh_document["uh_direct_steps"]).sum(), 1.0, rtol=0, atol=1e-9)
    np.testing.assert_allclose(np.array(uh_document["uh_base_steps"]).sum(), 1.0, rtol=0, atol=1e-9)
    assert uh_document["uh_river_days"].index(max(uh_document["uh_river_days"])) + 1 > 1  # delayed, not pulse
    assert all(item["status"] != "FAIL" for item in document["checks"])


def test_lohmann_matches_standard_reference(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "Lohmann 等价（测试）"
    root.mkdir(parents=True)
    frame = _inflow(120)
    inflow = root / "inflow.csv"
    frame.to_csv(inflow, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "--inflow", inflow, "--route-params", repo_root / PARAMS,
        "--flowlen-m", "50000", "--timestep-hours", "24", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    produced = pd.read_csv(output / "routed_lohmann.csv")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    reference = _reference_route(frame, parameters, 50000.0)
    for column in ("routed_direct", "routed_base"):
        np.testing.assert_allclose(
            produced[column].to_numpy(dtype=float),
            reference[column],
            rtol=1e-9,
            atol=1e-12,
            err_msg=f"列 {column} 与标准 Lohmann 参考实现不一致",
        )
    uh_document = json.loads((output / "lohmann_unit_hydrographs.json").read_text(encoding="utf-8"))
    np.testing.assert_allclose(
        np.array(uh_document["uh_river_days"]), reference["uh_river"], rtol=1e-9, atol=1e-12
    )


def test_lohmann_pulse_uh_passes_through(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "Lohmann 脉冲（测试）"
    root.mkdir(parents=True)
    frame = _inflow(80)
    inflow = root / "inflow.csv"
    frame.to_csv(inflow, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "--inflow", inflow, "--route-params", repo_root / PARAMS,
        "--flowlen-m", "0", "--timestep-hours", "24", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    produced = pd.read_csv(output / "routed_lohmann.csv")
    # flowlen=0 makes the CHANNEL UH a pulse; base (pulse HRU) passes through
    # exactly, while direct still passes the Gamma HRU UH (smoothed, mass kept)
    np.testing.assert_allclose(
        produced["routed_base"].to_numpy(dtype=float),
        frame["base"].to_numpy(dtype=float),
        rtol=1e-9,
        atol=1e-12,
    )
    direct_in = frame["direct"].to_numpy(dtype=float)
    direct_out = produced["routed_direct"].to_numpy(dtype=float)
    assert direct_out.max() <= direct_in.max() + 1e-9  # smoothing cannot exceed the pulse peak
    np.testing.assert_allclose(
        direct_out.sum(), direct_in.sum() * 0.99, rtol=0, atol=direct_in.sum() * 0.02
    )
    uh_document = _result(output)
    assert all(item["status"] != "FAIL" for item in uh_document["checks"])


def test_lohmann_mass_conservation_on_long_series(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "Lohmann 守恒（测试）"
    root.mkdir(parents=True)
    frame = _inflow(400)
    inflow = root / "inflow.csv"
    frame.to_csv(inflow, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "--inflow", inflow, "--route-params", repo_root / PARAMS,
        "--flowlen-m", "50000", "--timestep-hours", "24", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    produced = pd.read_csv(output / "routed_lohmann.csv")
    total_in = float(frame["direct"].sum() + frame["base"].sum())
    total_out = float(produced["Q_total"].sum())
    assert total_out / total_in > 0.95  # UH tail storage only
    document = _result(output)
    assert all(item["status"] != "FAIL" for item in document["checks"])


def test_lohmann_sacsma_output_direct_binding(repo_root: Path, tmp_path: Path) -> None:
    """run-lumped-sacsma-model 的 SURF/BASE 列可直接作入流（列名可配）。"""
    root = tmp_path / "Lohmann 对接（测试）"
    root.mkdir(parents=True)
    frame = _inflow(90).rename(columns={"direct": "SURF", "base": "BASE"})
    inflow = root / "sacsma_out.csv"
    frame.to_csv(inflow, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "--inflow", inflow, "--route-params", repo_root / PARAMS,
        "--direct-column", "SURF", "--base-column", "BASE",
        "--flowlen-m", "30000", "--timestep-hours", "24", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    document = _result(output)
    assert document["parameters"]["direct_column"] == "SURF"
    assert all(item["status"] != "FAIL" for item in document["checks"])


def test_lohmann_non_daily_timestep_warns_but_runs(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "Lohmann 非日步长（测试）"
    root.mkdir(parents=True)
    n = 48
    frame = pd.DataFrame({
        "time": pd.date_range("2020-06-01", periods=n, freq="12h"),
        "direct": np.where(np.arange(n) % 8 < 3, 6.0, 0.0),
        "base": np.full(n, 2.0),
    })
    inflow = root / "inflow.csv"
    frame.to_csv(inflow, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "--inflow", inflow, "--route-params", repo_root / PARAMS,
        "--flowlen-m", "50000", "--timestep-hours", "12", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    document = _result(output)
    names = {item["name"].split(":")[-1]: item["status"] for item in document["checks"]}
    assert names["daily_timestep"] == "WARN"
    assert any("非日步长" in warning for warning in document["warnings"])


@pytest.mark.parametrize(
    "mutation,expected",
    [("timestep", 2), ("missing", 2), ("params", 1), ("column", 1)],
)
def test_lohmann_rejects_invalid_inputs_with_expected_exit_codes(
    repo_root: Path, tmp_path: Path, mutation: str, expected: int
) -> None:
    root = tmp_path / f"Lohmann 负向（{mutation}）测试"
    root.mkdir(parents=True)
    frame = _inflow(48)
    declared = "24"
    if mutation == "timestep":
        declared = "12"
    elif mutation == "missing":
        frame.loc[10, "base"] = np.nan
    inflow = root / "inflow.csv"
    frame.to_csv(inflow, index=False, encoding="utf-8-sig")
    params_argument = str(repo_root / PARAMS)
    if mutation == "params":
        bad = root / "bad.json"
        bad.write_text(json.dumps({"N": 2.0, "K": 2.0}), encoding="utf-8")
        params_argument = str(bad)
    direct_column = "direct"
    if mutation == "column":
        frame = frame.rename(columns={"direct": "SURF"})
        frame.to_csv(inflow, index=False, encoding="utf-8-sig")
        direct_column = "direct"  # renamed away -> missing column
    output = root / "out"
    completed = _run(
        repo_root, "--inflow", inflow, "--route-params", params_argument,
        "--direct-column", direct_column,
        "--flowlen-m", "50000", "--timestep-hours", declared, "--output-dir", output,
    )
    assert completed.returncode == expected, completed.stdout
    document = _result(output)
    assert document["status"] == "error"
    if expected == 2:
        assert not (output / "routed_lohmann.csv").exists()
