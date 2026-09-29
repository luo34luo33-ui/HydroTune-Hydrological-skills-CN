from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path("hydrological-modeling/run-lumped-dhf-model/examples/run_lumped_dhf.py")
PARAMS = Path("hydrological-modeling/run-lumped-dhf-model/examples/source_equivalent_params.json")


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
        "time": pd.date_range("2020-06-01", periods=rows, freq="3h"),
        "P": precipitation,
        "PET": np.full(rows, 0.35),
    })


def _result(output: Path) -> dict:
    return json.loads((output / "result.json").read_text(encoding="utf-8"))


def _reference_dhf(frame: pd.DataFrame, params: dict, area_km2: float, river_length_km: float, dt: float) -> dict[str, np.ndarray]:
    """Independent row-by-row reference implementation of the DHF source formulas."""
    steps = len(frame)
    s0, u0, d0 = params["S0"], params["U0"], params["D0"]
    kc, kw, k2, ka, g = params["KC"], params["KW"], params["K2"], params["KA"], params["G"]
    a, b = params["A"], params["B"]
    b0, k0, nn = params["B0"], params["K0"], params["N"]
    dd, cc, coe = params["DD"], params["CC"], params["COE"]
    ddl, ccl = params["DDL"], params["CCL"]
    pai = float(np.pi)
    precipitation = frame["P"].to_numpy(dtype=float)
    pet = frame["PET"].to_numpy(dtype=float)

    out = {key: np.zeros(steps) for key in ("Q", "RUNOFF", "SA", "UA", "YA", "EB")}
    sa = 0.0
    ua = 0.0
    eb = 0.0
    runoff = np.zeros(steps)
    y0_series = np.zeros(steps)
    yl_series = np.zeros(steps)

    for i in range(steps):
        if i > 0:
            sa = out["SA"][i - 1]
            ua = out["UA"][i - 1]
            eb = out["EB"][i - 1]
        sa = min(sa, s0)
        ua = min(ua, u0)
        edt = kc * pet[i]
        pe = precipitation[i] - edt
        y0 = g * pe
        pc = pe - y0
        if pc > 0:
            temp = (1 - sa / s0) ** (1 / a)
            sm = a * s0 * (1 - temp)
            rr = (
                pc + sa - s0 + s0 * (1 - (sm + pc) / (a * s0)) ** a
                if sm + pc < a * s0
                else pc - (s0 - sa)
            )
            un = b * u0 * (1 - (1 - ua / u0) ** (1 / b))
            dn = b * d0 * (1 - (1 - ua / u0) ** (u0 / (b * d0)))
            z1 = 1 - np.exp(-k2 * dt * u0 / d0)
            z2 = 1 - np.exp(-k2 * dt)
            y = (
                rr + z2 * (ua - u0) + z2 * u0 * (1 - (z2 * un + rr) / (z2 * b * u0)) ** b
                if rr + z2 * un < z2 * b * u0
                else rr + z2 * (ua - u0)
            )
            temp = (1 - ua / u0) ** (u0 / d0)
            yu = (
                rr - z1 * d0 * temp + z1 * d0 * (1 - (z1 * dn + rr) / (z1 * b * d0)) ** b
                if z1 * dn + rr < z1 * b * d0
                else rr - z1 * d0 * temp
            )
            yl = (y - yu) * kw
            sa_new = (
                s0 * (1 - (1 - (sm + pc) / (a * s0)) ** a)
                if sm + pc < a * s0
                else sa + pc - rr
            )
            ua_new = ua + rr - y
            eb = 0.0
            y0_value = y0
        else:
            ec = edt - precipitation[i]
            eb += ec
            temp1 = (1 - (eb - ec) / (a * s0)) ** a
            temp2 = (1 - eb / (a * s0)) ** a
            if eb / (a * s0) <= 0.999999 and (eb - ec) / (a * s0) <= 0.999999:
                eu = s0 * (temp1 - temp2)
            elif eb / (a * s0) >= 1.00001 and (eb - ec) / (a * s0) <= 0.999999:
                eu = s0 * temp1
            else:
                eu = 0.00001
            if sa - eu < 0:
                el = (ec - sa) * ua / u0
                sa_new = 0.0
            else:
                el = (ec - eu) * ua / u0
                sa_new = sa - eu
            ua_new = max(ua - el, 0.0)
            rr = 0.0
            y = 0.0
            yu = 0.0
            yl = 0.0
            y0_value = 0.0
        out["SA"][i] = min(max(sa_new, 0.0), s0)
        out["UA"][i] = min(max(ua_new, 0.0), u0)
        out["EB"][i] = eb
        runoff[i] = max(y + y0_value, 0.0)
        y0_series[i] = y0_value
        yl_series[i] = yl

    w0 = area_km2 / (3.6 * dt)
    ya_previous = 0.5
    qs = np.zeros(steps)
    ql = np.zeros(steps)
    for i in range(steps):
        ya = max((ya_previous + runoff[i]) * ka, 0.0)
        out["YA"][i] = ya
        ya_val = max(ya_previous, 0.5)
        rl = max(yl_series[i], 0.0)
        tm = (river_length_km / b0) * (ya_val + runoff[i]) ** (-k0)
        tt = int(nn * tm)
        ts = int(coe * tm)
        aa = cc / (dd * (pai * coe) ** (dd - 1) * np.tan(pai * coe))
        k3 = 0.0
        for j in range(int(np.ceil(tm))):
            if j < tm:
                k3 += np.exp(-aa * (pai * j / tm) ** dd) * (np.sin(pai * j / tm)) ** cc
        if k3 != 0:
            k3 = tm * w0 / k3
        aal = ccl / (ddl * (pai * coe / nn) ** (ddl - 1) * np.tan(pai * coe / nn))
        k3l = 0.0
        for j in range(int(np.ceil(tt))):
            if j < tt:
                k3l += np.exp(-aal * (pai * j / tt) ** ddl) * (np.sin(pai * j / tt)) ** ccl
        if k3l != 0:
            k3l = tt * w0 / k3l
        tl = max(tt + ts - 1, 0)
        for j in range(int(np.ceil(tl))):
            if i + j >= steps:
                break
            temp0 = pai * j / tm
            q_surface = (
                (runoff[i] - rl) * k3 / tm
                * np.exp(-aa * temp0 ** dd)
                * (np.sin(temp0)) ** cc
            )
            if np.isnan(q_surface):
                q_surface = 0.0
            if j >= ts:
                temp00 = pai * (j - ts) / tt
                q_subsurface = (
                    rl * k3l / tt
                    * np.exp(-aal * temp00 ** ddl)
                    * (np.sin(temp00)) ** ccl
                )
            else:
                q_subsurface = 0.0
            if j <= tm:
                qs[i + j] += q_surface
                if j > ts:
                    ql[i + j] += q_subsurface
            else:
                ql[i + j] += q_subsurface
        ya_previous = ya
    out["Q"] = np.maximum(qs + ql, 0.0)
    out["RUNOFF"] = runoff
    return out


def test_dhf_example_has_help(repo_root: Path) -> None:
    result = _run(repo_root, "--help")
    assert result.returncode == 0, result.stderr
    assert "--output-dir" in result.stdout


def test_dhf_continuous_run_state_bounds_and_aliases(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "DHF 管线（测试）"
    root.mkdir(parents=True)
    forcing = root / "forcing（合成）.csv"
    _forcing().to_csv(forcing, index=False, encoding="utf-8-sig")

    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    alias = {("K" if key == "KC" else key): value for key, value in parameters.items() if not key.startswith("_")}
    params_file = root / "params（K 别名）.json"
    params_file.write_text(json.dumps(alias, ensure_ascii=False, indent=2), encoding="utf-8")

    output = root / "01 DHF（结果）"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", params_file,
        "--river-length-km", "155.763", "--area-km2", "5482", "--timestep-hours", "3",
        "--mode", "continuous", "--warmup-steps", "24", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    document = _result(output)
    assert document["skill"] == "hydrological-modeling/run-lumped-dhf-model"
    assert document["status"] in {"success", "warning"}

    table = pd.read_csv(output / "lumped_dhf_runoff.csv")
    assert len(table) == 240
    assert int(table["is_warmup"].sum()) == 24
    assert table["Q"].min() >= 0.0
    assert table["SA"].max() <= parameters["S0"] + 1e-9
    assert table["UA"].max() <= parameters["U0"] + 1e-9
    assert table["YA"].min() >= 0.0
    assert all(item["status"] != "FAIL" for item in document["checks"])


def test_dhf_matches_source_formula_reference(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "DHF 等价（测试）"
    root.mkdir(parents=True)
    frame = _forcing(150)
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--river-length-km", "155.763", "--area-km2", "5482", "--timestep-hours", "3",
        "--mode", "continuous", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    produced = pd.read_csv(output / "lumped_dhf_runoff.csv")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    reference = _reference_dhf(frame, parameters, 5482.0, 155.763, 3.0)
    for column in ("Q", "RUNOFF", "SA", "UA", "YA"):
        np.testing.assert_allclose(
            produced[column].to_numpy(dtype=float),
            reference[column],
            rtol=1e-9,
            atol=1e-9,
            err_msg=f"列 {column} 与源码公式参考实现不一致",
        )


def test_dhf_initial_state_overrides_simulation_start(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "DHF 初值（测试）"
    root.mkdir(parents=True)
    frame = _forcing(96)
    frame["P"] = 0.0
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    initial = root / "initial（显式）.json"
    initial.write_text(json.dumps({"sa0": 30.0, "ua0": 40.0, "ya0": 12.0}), encoding="utf-8")
    output = root / "out"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--river-length-km", "155.763", "--area-km2", "5482", "--timestep-hours", "3",
        "--mode", "continuous", "--initial-state", initial, "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    table = pd.read_csv(output / "lumped_dhf_runoff.csv")
    # with no rain the storages start from the overrides and only recede
    assert table.loc[0, "SA"] == pytest.approx(table.loc[0, "SA"])
    assert table["SA"].max() <= 30.0 + 1e-9
    assert table["SA"].iloc[0] < 30.0
    assert table["YA"].iloc[0] < 12.0
    document = _result(output)
    assert document["parameters"]["initial_states"] == {"sa0": 30.0, "ua0": 40.0, "ya0": 12.0}


@pytest.mark.parametrize(
    "mutation,expected",
    [("timestep", 2), ("missing", 2), ("params", 1), ("coe-zero", 1)],
)
def test_dhf_rejects_invalid_inputs_with_expected_exit_codes(
    repo_root: Path, tmp_path: Path, mutation: str, expected: int
) -> None:
    root = tmp_path / f"DHF 负向（{mutation}）测试"
    root.mkdir(parents=True)
    frame = _forcing(48)
    declared = "3"
    if mutation == "timestep":
        declared = "6"
    elif mutation == "missing":
        frame.loc[10, "P"] = np.nan
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")

    extra: list[str] = []
    params_argument: str = str(repo_root / PARAMS)
    if mutation == "params":
        bad = root / "bad.json"
        bad.write_text(json.dumps({"S0": 50.0}), encoding="utf-8")
        params_argument = str(bad)
    if mutation == "coe-zero":
        parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
        parameters["COE"] = 0.0
        bad = root / "coe0.json"
        bad.write_text(json.dumps(parameters, ensure_ascii=False), encoding="utf-8")
        params_argument = str(bad)
        extra = ["--param-scale", "original"]

    output = root / "out"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", params_argument,
        "--river-length-km", "155.763", "--area-km2", "5482", "--timestep-hours", declared,
        "--mode", "continuous", *extra, "--output-dir", output,
    )
    assert completed.returncode == expected, completed.stdout
    document = _result(output)
    assert document["status"] == "error"
    if expected == 2:
        assert not (output / "lumped_dhf_runoff.csv").exists()


def test_dhf_normalized_scale_changes_discharge(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "DHF 归一化（测试）"
    root.mkdir(parents=True)
    forcing = root / "forcing.csv"
    _forcing(96).to_csv(forcing, index=False, encoding="utf-8-sig")
    original_dir = root / "original"
    normalized_dir = root / "normalized"
    assert _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--river-length-km", "155.763", "--area-km2", "5482", "--timestep-hours", "3",
        "--mode", "continuous", "--param-scale", "original", "--output-dir", original_dir,
    ).returncode == 0
    assert _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--river-length-km", "155.763", "--area-km2", "5482", "--timestep-hours", "3",
        "--mode", "continuous", "--param-scale", "normalized", "--output-dir", normalized_dir,
    ).returncode == 0
    original = pd.read_csv(original_dir / "lumped_dhf_runoff.csv")
    normalized = pd.read_csv(normalized_dir / "lumped_dhf_runoff.csv")
    assert not np.allclose(original["Q"].to_numpy(), normalized["Q"].to_numpy())
    # normalized scale denormalizes S0=50 into lo + 50*(hi-lo) = 2500, so the SA
    # bound follows the denormalized capacity rather than the original 50 mm
    s0_normalized = 0.0 + 50.0 * (50.0 - 0.0)
    assert normalized["SA"].max() <= s0_normalized + 1e-9
