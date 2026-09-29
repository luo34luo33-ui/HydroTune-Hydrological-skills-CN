from __future__ import annotations

import json
import os
from math import floor
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest


SCRIPT = Path("hydrological-modeling/run-lumped-sacsma-model/examples/run_lumped_sacsma.py")
PARAMS = Path("hydrological-modeling/run-lumped-sacsma-model/examples/source_equivalent_params.json")

COLUMNS = ("Q_MM", "Q", "ET1", "ET2", "ET3", "ET4", "ET5", "TET",
           "SURF", "BASE", "UZTWC", "UZFWC", "LZTWC", "LZFSC", "LZFPC", "ADIMC")


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


def _forcing(rows: int = 180, pet: float = 3.2) -> pd.DataFrame:
    precipitation = np.zeros(rows, dtype=float)
    precipitation[20:45] = 18.0
    precipitation[100:118] = 25.0
    return pd.DataFrame({
        "time": pd.date_range("2020-06-01", periods=rows, freq="D"),
        "P": precipitation,
        "PET": np.full(rows, pet),
    })


def _result(output: Path) -> dict:
    return json.loads((output / "result.json").read_text(encoding="utf-8"))


def _reference_sacsma(
    frame: pd.DataFrame, params: dict, area_km2: float, timestep_hours: float,
    initial_states: dict[str, float] | None = None,
) -> dict[str, np.ndarray]:
    """Independent row-by-row reference implementation of the SAC-SMA source formulas."""
    steps = len(frame)
    uztwm, uzfwm = params["UZTWM"], params["UZFWM"]
    lztwm, lzfpm, lzfsm = params["LZTWM"], params["LZFPM"], params["LZFSM"]
    adimp, uzk = params["ADIMP"], params["UZK"]
    lzpk, lzsk = params["LZPK"], params["LZSK"]
    zperc, rexp = params["ZPERC"], params["REXP"]
    pctim, pfree = params["PCTIM"], params["PFREE"]
    riva, side, rserv = params["RIVA"], params["SIDE"], params["RSERV"]

    init = {"UZTWC": 0.0, "UZFWC": 0.0, "LZTWC": 500.0, "LZFSC": 500.0, "LZFPC": 500.0, "ADIMC": 0.0}
    if initial_states:
        init.update(initial_states)
    uztwc, uzfwc = init["UZTWC"], init["UZFWC"]
    lztwc, lzfsc, lzfpc, adimc = init["LZTWC"], init["LZFSC"], init["LZFPC"], init["ADIMC"]

    prcp = frame["P"].to_numpy(dtype=float)
    pet = frame["PET"].to_numpy(dtype=float)
    parea = 1 - adimp - pctim
    eps = 0.00001

    out = {name: np.zeros(steps) for name in COLUMNS}

    for i in range(steps):
        pr, edmnd = float(prcp[i]), float(pet[i])

        et1 = edmnd * uztwc / uztwm
        red = edmnd - et1
        uztwc = uztwc - et1
        et2 = 0.0
        if uztwc <= 0:
            et1 = et1 + uztwc
            uztwc = 0.0
            red = edmnd - et1
            if uzfwc < red:
                et2 = uzfwc
                uzfwc = 0.0
                red = red - et2
                uztwc = 0.0 if uztwc < eps else uztwc
                uzfwc = 0.0 if uzfwc < eps else uzfwc
            else:
                et2 = red
                uzfwc = uzfwc - et2
                red = 0.0
        elif (uztwc / uztwm) < (uzfwc / uzfwm):
            uzrat = (uztwc + uzfwc) / (uztwm + uzfwm)
            uztwc = uztwm * uzrat
            uzfwc = uzfwm * uzrat
            uztwc = 0.0 if uztwc < eps else uztwc
            uzfwc = 0.0 if uzfwc < eps else uzfwc

        et3 = red * lztwc / (uztwm + lztwm)
        lztwc = lztwc - et3
        if lztwc < 0:
            et3 = et3 + lztwc
            lztwc = 0.0

        saved = rserv * (lzfpm + lzfsm)
        ratlzt = lztwc / lztwm
        ratlz = (lztwc + lzfpc + lzfsc - saved) / (lztwm + lzfpm + lzfsm - saved)
        if ratlzt < ratlz:
            delta = (ratlz - ratlzt) * lztwm
            lztwc = lztwc + delta
            lzfsc = lzfsc - delta
            if lzfsc < 0:
                lzfpc = lzfpc + lzfsc
                lzfsc = 0.0
            lztwc = 0.0 if lztwc < eps else lztwc

        et5 = et1 + (red + et2) * (adimc - et1 - uztwc) / (uztwm + lztwm)
        adimc = adimc - et5
        if adimc < 0:
            et5 = et5 + adimc
            adimc = 0.0
        et5 = et5 * adimp

        twx = pr + uztwc - uztwm
        if twx < 0:
            uztwc = uztwc + pr
            twx = 0.0
        else:
            uztwc = uztwm
        adimc = adimc + pr - twx

        roimp = pr * pctim
        sbf = ssur = sif = sperc = sdro = 0.0

        ninc = int(floor(1.0 + 0.2 * (uzfwc + twx)))
        dinc = 1.0 / ninc
        pinc = twx / ninc
        duz = 1 - (1 - uzk) ** dinc
        dlzp = 1 - (1 - lzpk) ** dinc
        dlzs = 1 - (1 - lzsk) ** dinc

        for _ in range(ninc):
            ratio = (adimc - uztwc) / lztwm
            ratio = 0.0 if ratio < 0 else ratio
            addro = pinc * ratio ** 2

            bf_p = lzfpc * dlzp
            lzfpc = lzfpc - bf_p
            if lzfpc <= 0.0001:
                bf_p = bf_p + lzfpc
                lzfpc = 0.0
            sbf += bf_p

            bf_s = lzfsc * dlzs
            lzfsc = lzfsc - bf_s
            if lzfsc <= 0.0001:
                bf_s = bf_s + lzfsc
                lzfsc = 0.0
            sbf += bf_s

            if (pinc + uzfwc) <= 0.01:
                uzfwc = uzfwc + pinc
            else:
                percm = lzfpm * dlzp + lzfsm * dlzs
                perc = percm * uzfwc / uzfwm
                defr = 1.0 - (lztwc + lzfpc + lzfsc) / (lztwm + lzfpm + lzfsm)
                defr = 0.0 if defr < 0 else defr
                perc = perc * (1.0 + zperc * defr ** rexp)
                perc = uzfwc if perc >= uzfwc else perc
                uzfwc = uzfwc - perc
                check = lztwc + lzfpc + lzfsc + perc - lztwm - lzfpm - lzfsm
                if check > 0:
                    perc = perc - check
                    uzfwc = uzfwc + check
                sperc += perc
                delta = uzfwc * duz
                sif += delta
                uzfwc = uzfwc - delta

                perct = perc * (1.0 - pfree)
                if perct + lztwc <= lztwm:
                    lztwc = lztwc + perct
                    percf = 0.0
                else:
                    percf = lztwc + perct - lztwm
                    lztwc = lztwm
                percf = percf + perc * pfree

                if percf != 0:
                    hpl = lzfpm / (lzfpm + lzfsm)
                    ratlp = lzfpc / lzfpm
                    ratls = lzfsc / lzfsm
                    fracp = hpl * 2 * (1 - ratlp) / (2 - ratlp - ratls)
                    fracp = 1.0 if fracp > 1.0 else fracp
                    percp = percf * fracp
                    percs = percf - percp
                    lzfsc = lzfsc + percs
                    if lzfsc > lzfsm:
                        percs = percs - lzfsc + lzfsm
                        lzfsc = lzfsm
                    lzfpc = lzfpc + percf - percs
                    if lzfpc >= lzfpm:
                        excess = lzfpc - lzfpm
                        lztwc = lztwc + excess
                        lzfpc = lzfpm

                if pinc != 0:
                    if pinc + uzfwc <= uzfwm:
                        uzfwc = uzfwc + pinc
                    else:
                        sur = pinc + uzfwc - uzfwm
                        uzfwc = uzfwm
                        ssur += sur * parea
                        adsur = sur * (1.0 - addro / pinc)
                        ssur += adsur * adimp
                        adimc = adimc + pinc - addro - adsur
                        if adimc > uztwm + lztwm:
                            addro = addro + adimc - (uztwm + lztwm)
                            adimc = uztwm + lztwm
                        sdro += addro * adimp
                        adimc = 0.0 if adimc < eps else adimc

        eused0 = et1 + et2 + et3
        sif = sif * parea
        tbf = sbf * parea
        base = tbf / (1 + side)
        surf = roimp + sdro + ssur + sif
        et4 = (edmnd - eused0) * riva
        tet = eused0 * parea + et4 + et5

        if adimc < uztwc:
            adimc = uztwc
        tot_outflow = surf + base - et4
        if tot_outflow < 0:
            tot_outflow = 0.0
            surf = 0.0
            base = 0.0
        else:
            surf_remainder = surf - et4
            surf = max(0, surf_remainder)
            if surf_remainder < 0:
                base = base + surf_remainder
                base = max(base, 0)

        values = {
            "Q_MM": tot_outflow, "Q": tot_outflow * (area_km2 * 1000.0) / (timestep_hours * 3600.0),
            "ET1": et1, "ET2": et2, "ET3": et3, "ET4": et4, "ET5": et5, "TET": tet,
            "SURF": surf, "BASE": base,
            "UZTWC": uztwc, "UZFWC": uzfwc, "LZTWC": lztwc, "LZFSC": lzfsc, "LZFPC": lzfpc, "ADIMC": adimc,
        }
        for name, value in values.items():
            out[name][i] = value
    return out


def test_sacsma_example_has_help(repo_root: Path) -> None:
    result = _run(repo_root, "--help")
    assert result.returncode == 0, result.stderr
    assert "--output-dir" in result.stdout


def test_sacsma_continuous_run_dual_calibre_and_bounds(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "SAC-SMA 管线（测试）"
    root.mkdir(parents=True)
    forcing = root / "forcing（合成）.csv"
    _forcing().to_csv(forcing, index=False, encoding="utf-8-sig")
    output = root / "01 SAC-SMA（结果）"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "1200", "--timestep-hours", "24", "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    document = _result(output)
    assert document["skill"] == "hydrological-modeling/run-lumped-sacsma-model"
    assert document["status"] in {"success", "warning"}

    table = pd.read_csv(output / "lumped_sacsma_runoff.csv")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    assert len(table) == 180
    assert table["Q"].min() >= 0.0
    unit_conv = (1200.0 * 1000.0) / (24.0 * 3600.0)
    np.testing.assert_allclose(
        table["Q"].to_numpy(dtype=float),
        table["Q_MM"].to_numpy(dtype=float) * unit_conv,
        rtol=1e-12,
        atol=1e-12,
    )
    assert table["UZTWC"].max() <= parameters["UZTWM"] + 1e-9
    assert table["UZFWC"].max() <= parameters["UZFWM"] + 1e-9
    assert table["LZTWC"].min() >= 0.0
    assert all(item["status"] != "FAIL" for item in document["checks"])
    assert document["parameters"]["initial_states_source"] == "source_equivalent_default"


def test_sacsma_matches_source_formula_reference(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "SAC-SMA 等价（测试）"
    root.mkdir(parents=True)
    frame = _forcing(120)
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "1200", "--timestep-hours", "24", "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    produced = pd.read_csv(output / "lumped_sacsma_runoff.csv")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    reference = _reference_sacsma(frame, parameters, 1200.0, 24.0)
    for column in COLUMNS:
        np.testing.assert_allclose(
            produced[column].to_numpy(dtype=float),
            reference[column],
            rtol=1e-9,
            atol=1e-12,
            err_msg=f"列 {column} 与源码公式参考实现不一致",
        )


def test_sacsma_initial_state_seeds_simulation_start(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "SAC-SMA 初值（测试）"
    root.mkdir(parents=True)
    frame = _forcing(90)
    frame["P"] = 0.0
    frame["PET"] = 0.0
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    initial = root / "initial（显式）.json"
    initial.write_text(json.dumps({"UZFWC": 30.0, "LZFSC": 400.0, "LZFPC": 450.0}), encoding="utf-8")
    output = root / "out"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "1200", "--timestep-hours", "24", "--mode", "continuous",
        "--initial-state", initial, "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    table = pd.read_csv(output / "lumped_sacsma_runoff.csv")
    # zero forcing with positive free water storage: baseflow drains it from step 0
    assert table["Q"].iloc[0] > 0.0
    # initial states were consumed as seeds: step-0 state equals the explicit seed
    # adjusted by the first step's accounting, not the source default 500
    assert table["LZFSC"].iloc[0] != 500.0
    document = _result(output)
    assert document["parameters"]["initial_states_source"] == "explicit"
    assert document["parameters"]["initial_states"]["LZFSC"] == 400.0


def test_sacsma_non_daily_timestep_warns_but_runs(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "SAC-SMA 非日步长（测试）"
    root.mkdir(parents=True)
    n = 48
    frame = pd.DataFrame({
        "time": pd.date_range("2020-06-01", periods=n, freq="12h"),
        "P": np.where(np.arange(n) % 8 < 3, 9.0, 0.0),
        "PET": np.full(n, 1.6),
    })
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "1200", "--timestep-hours", "12", "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    document = _result(output)
    names = {item["name"].split(":")[-1]: item["status"] for item in document["checks"]}
    assert names["daily_timestep"] == "WARN"
    assert names["time_axis_regular"] == "PASS"
    assert any("非日步长" in warning for warning in document["warnings"])


@pytest.mark.parametrize(
    "mutation,expected",
    [("timestep", 2), ("missing", 2), ("params", 1), ("fractions", 1)],
)
def test_sacsma_rejects_invalid_inputs_with_expected_exit_codes(
    repo_root: Path, tmp_path: Path, mutation: str, expected: int
) -> None:
    root = tmp_path / f"SAC-SMA 负向（{mutation}）测试"
    root.mkdir(parents=True)
    frame = _forcing(48)
    declared = "24"
    if mutation == "timestep":
        declared = "12"
    elif mutation == "missing":
        frame.loc[10, "PET"] = np.nan
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    params_argument = str(repo_root / PARAMS)
    if mutation == "params":
        bad = root / "bad.json"
        bad.write_text(json.dumps({"UZTWM": 150.0, "UZFWM": 50.0}), encoding="utf-8")
        params_argument = str(bad)
    elif mutation == "fractions":
        bad = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
        bad["PCTIM"] = 0.95
        bad["ADIMP"] = 0.3  # adimp + pctim >= 1 -> parea <= 0
        bad_file = root / "bad_fractions.json"
        bad_file.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        params_argument = str(bad_file)
    output = root / "out"
    completed = _run(
        repo_root, "--forcing", forcing, "--params", params_argument,
        "--area-km2", "1200", "--timestep-hours", declared, "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == expected, completed.stdout
    document = _result(output)
    assert document["status"] == "error"
    if expected == 2:
        assert not (output / "lumped_sacsma_runoff.csv").exists()
