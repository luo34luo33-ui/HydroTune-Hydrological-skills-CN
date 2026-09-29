from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest


SCRIPTS = {
    "xaj": Path("hydrological-modeling/run-lumped-xaj-model/examples/run_lumped_xaj.py"),
    "route": Path("hydrological-modeling/route-muskingum-channel/examples/route_muskingum.py"),
}
PARAMS = Path("hydrological-modeling/run-lumped-xaj-model/examples/source_equivalent_params.json")
ROUTE_SPEC = Path("hydrological-modeling/route-muskingum-channel/examples/source_equivalent_route_spec.json")

EPS = 1e-12


def _run(repo: Path, key: str, *arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy() if env is None else dict(env)
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(repo / SCRIPTS[key]), *map(str, arguments)],
        cwd=repo,
        text=True,
        capture_output=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


def _forcing(rows: int = 240) -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    precipitation = np.zeros(rows, dtype=float)
    precipitation[24:48] = 8.0
    precipitation[120:140] = 12.0
    return pd.DataFrame({
        "time": pd.date_range("2020-06-01", periods=rows, freq="h"),
        "P": precipitation,
        "E0": np.full(rows, 0.12),
        "Qres_in": np.full(rows, 50.0),
    })


def _result(output: Path) -> dict:
    return json.loads((output / "result.json").read_text(encoding="utf-8"))


def _reference_xaj(
    frame: pd.DataFrame,
    params: dict,
    area_km2: float,
    timestep_hours: float,
) -> pd.DataFrame:
    """Pandas row-by-row reference implementation that mirrors the source notebook formulas."""
    def clip01(value: float) -> float:
        return float(np.clip(value, 0.0, 1.0 - EPS))

    def pos(value: float) -> float:
        return float(max(value, EPS))

    df = frame.copy()
    steps = len(df)
    for column in ["WU", "WL", "WD", "EP", "EU", "EL", "ED", "E", "PE", "W", "R", "FR",
                   "S1", "RS", "RI", "RG", "QS", "QI", "QG", "QT", "Qt", "R_im"]:
        df[column] = 0.0
    df["P_perv"] = (1.0 - params["IM"]) * df["P"]
    df["P_im"] = params["IM"] * df["P"]
    unit = area_km2 / (3.6 * timestep_hours)
    wdm = max(0.0, params["WM"] - params["WUM"] - params["WLM"])
    wdm_init = min(params["WDM_init"], wdm)
    wmm = float(params["WM"])
    smm = float(params["SM"])
    sm = float(params["SM"])

    for i in range(steps):
        if i == 0:
            df.loc[i, "WU"] = params["WUM_init"]
            df.loc[i, "WL"] = params["WLM_init"]
            df.loc[i, "WD"] = wdm_init
            prev_wl = 0.0
            prev_wd = 0.0
        else:
            infiltration = df["PE"][i - 1] - df["R"][i - 1]
            df.loc[i, "WU"] = df["WU"][i - 1] + infiltration
            df.loc[i, "WL"] = df["WL"][i - 1]
            df.loc[i, "WD"] = df["WD"][i - 1]
            prev_wl = df["WL"][i - 1]
            prev_wd = df["WD"][i - 1]
        if df["WU"][i] < 0:
            df.loc[i, "WL"] = prev_wl + df["WU"][i]
            df.loc[i, "WU"] = 0
            df.loc[i, "WD"] = prev_wd
            if df["WL"][i] < 0:
                df.loc[i, "WD"] = prev_wd + df["WL"][i]
                df.loc[i, "WL"] = 0
                df.loc[i, "WU"] = 0
                if df["WD"][i] < 0:
                    df.loc[i, "WD"] = 0
                    df.loc[i, "WL"] = 0
                    df.loc[i, "WU"] = 0
        if df["WU"][i] > params["WUM"]:
            df.loc[i, "WL"] = df["WU"][i] - params["WUM"] + prev_wl
            df.loc[i, "WU"] = params["WUM"]
            df.loc[i, "WD"] = prev_wd
            if df["WL"][i] > params["WLM"]:
                df.loc[i, "WD"] = df["WL"][i] - params["WLM"] + prev_wd
                df.loc[i, "WU"] = params["WUM"]
                df.loc[i, "WL"] = params["WLM"]
                if df["WD"][i] > wdm:
                    df.loc[i, "WD"] = wdm
                    df.loc[i, "WU"] = params["WUM"]
                    df.loc[i, "WL"] = params["WLM"]

        df.loc[i, "EP"] = df["E0"][i] * params["K"]
        pp = df["P_perv"][i]
        unassigned = 0.0
        if df["WU"][i] + pp >= df["EP"][i]:
            df.loc[i, "EU"] = df["EP"][i]
            df.loc[i, "EL"] = 0
            df.loc[i, "ED"] = 0
        elif df["WL"][i] >= params["C"] * params["WLM"]:
            df.loc[i, "EU"] = df["WU"][i] + pp
            df.loc[i, "EL"] = (df["EP"][i] - df["EU"][i]) * (df["WL"][i] / pos(params["WLM"]))
            df.loc[i, "ED"] = 0
        elif params["C"] * (df["EP"][i] - unassigned) <= df["WL"][i] and df["WL"][i] < params["C"] * params["WLM"]:
            df.loc[i, "EU"] = df["WU"][i] + pp
            df.loc[i, "EL"] = params["C"] * (df["EP"][i] - df["EU"][i])
            df.loc[i, "ED"] = 0
        elif df["WL"][i] < params["C"] * (df["EP"][i] - unassigned):
            df.loc[i, "EU"] = df["WU"][i] + pp
            df.loc[i, "EL"] = df["WL"][i]
            df.loc[i, "ED"] = params["C"] * (df["EP"][i] - df["EU"][i]) - df["EL"][i]
        df.loc[i, "E"] = df["EU"][i] + df["EL"][i] + df["ED"][i]
        df.loc[i, "PE"] = pp - df["E"][i]
        df.loc[i, "W"] = df["WU"][i] + df["WL"][i] + df["WD"][i]

        if df["PE"][i] > 0:
            remaining = clip01(1.0 - df["W"][i] / pos(params["WM"]))
            a = wmm * (1.0 - pow(remaining, 1.0 / (1.0 + params["B"])))
            if a + df["PE"][i] <= wmm:
                inner = clip01(1.0 - (df["PE"][i] + a) / pos(wmm))
                df.loc[i, "R"] = df["PE"][i] + df["W"][i] - params["WM"] + params["WM"] * pow(inner, params["B"] + 1.0)
            else:
                df.loc[i, "R"] = df["PE"][i] - (params["WM"] - df["W"][i])
        else:
            df.loc[i, "R"] = 0.0

        if df["R"][i] > 0:
            df.loc[i, "FR"] = min(1.0, max(df["R"][i] / pos(df["PE"][i]), 1e-9))
        else:
            df.loc[i, "FR"] = params["FR1"] if i == 0 else df["FR"][i - 1]

        if i == 0:
            df.loc[i, "S1"] = params["S1"]
        fr_i = max(float(df["FR"][i]), 1e-9)
        fr_previous = max(float(df["FR"][i - 1]) if i > 0 else params["FR1"], 1e-9)
        ratio_source = params["FR1"] if i == 0 else fr_previous
        free_storage = df["S1"][i] * ratio_source / pos(fr_i)

        if df["PE"][i] > 0:
            ratio = clip01(free_storage / pos(smm))
            au = smm * (1.0 - pow(1.0 - ratio, 1.0 / (1.0 + params["EX"])))
            if df["PE"][i] + au < smm:
                base = df["PE"][i] + free_storage - sm
                inner = clip01(1.0 - (df["PE"][i] + au) / pos(smm))
                rs_raw = fr_i * (base + sm * pow(inner, 1.0 + params["EX"]))
            else:
                rs_raw = fr_i * (df["PE"][i] + free_storage - sm)
            r_total = float(df["R"][i]) if np.isfinite(df["R"][i]) else 0.0
            df.loc[i, "RS"] = float(np.clip(rs_raw, 0.0, r_total))
            s_value = free_storage + (df["R"][i] - df["RS"][i]) / pos(fr_i)
            df.loc[i, "RI"] = params["KI"] * s_value * fr_i
            df.loc[i, "RG"] = params["KG"] * s_value * fr_i
            if i < steps - 1:
                df.loc[i + 1, "S1"] = s_value * (1.0 - params["KI"] - params["KG"])
        else:
            s_value = free_storage
            if i < steps - 1:
                df.loc[i + 1, "S1"] = s_value * (1.0 - params["KG"] - params["KI"])
            df.loc[i, "RS"] = 0.0
            df.loc[i, "RG"] = params["KG"] * s_value * fr_i
            df.loc[i, "RI"] = params["KI"] * s_value * fr_i

        df.loc[i, "R_im"] = df["P_im"][i]
        df.loc[i, "QS"] = max(0.0, float((df["RS"][i] + df["R_im"][i]) * unit))
        if i == 0:
            df.loc[i, "QI"] = params["Q"] / 3.0
            df.loc[i, "QG"] = params["Q"] / 3.0
        else:
            df.loc[i, "QI"] = params["CI"] * df["QI"][i - 1] + (1 - params["CI"]) * df["RI"][i] * unit
            df.loc[i, "QG"] = params["CG"] * df["QG"][i - 1] + (1 - params["CG"]) * df["RG"][i] * unit
        df.loc[i, "QT"] = df["QS"][i] + df["QI"][i] + df["QG"][i]
        if 0 <= i <= int(params["L"]):
            df.loc[i, "Qt"] = params["Q"]
        else:
            df.loc[i, "Qt"] = params["CS"] * df["Qt"][i - 1] + (1 - params["CS"]) * df["QT"][i - int(params["L"])]
    return df


@pytest.mark.parametrize("key", sorted(SCRIPTS))
def test_modeling_examples_have_help(repo_root: Path, key: str) -> None:
    result = _run(repo_root, key, "--help")
    assert result.returncode == 0, result.stderr
    assert "--output-dir" in result.stdout


def test_lumped_xaj_continuous_run_artifacts_and_state_bounds(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "建模 管线（测试）"
    root.mkdir(parents=True)
    forcing = root / "forcing（合成）.csv"
    _forcing().to_csv(forcing, index=False, encoding="utf-8-sig")
    output = root / "01 新安江（结果）"
    completed = _run(
        repo_root, "xaj", "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "640", "--timestep-hours", "1", "--mode", "continuous",
        "--warmup-steps", "12", "--carry-columns", "Qres_in", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    document = _result(output)
    assert document["skill"] == "hydrological-modeling/run-lumped-xaj-model"
    assert document["status"] in {"success", "warning"}
    assert (output / document["artifacts"]["simulation_table"]["path"]).is_file()

    table = pd.read_csv(output / "lumped_xaj_runoff.csv")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    assert len(table) == 240
    assert int(table["is_warmup"].sum()) == 12
    assert table["Qt"].min() >= 0.0
    assert table["W"].max() <= parameters["WM"] + 1e-9
    assert table["WU"].max() <= parameters["WUM"] + 1e-9
    assert table["Qres_in"].round(6).eq(50.0).all()
    assert all(item["status"] != "FAIL" for item in document["checks"])


def test_lumped_xaj_event_mode_keeps_unicode_event_names(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "场次 建模（测试）"
    events = root / "events"
    events.mkdir(parents=True)
    _forcing(120).to_csv(events / "flood 20100811（合成）.csv", index=False, encoding="utf-8-sig")
    _forcing(120).to_csv(events / "flood 20110729.csv", index=False, encoding="utf-8-sig")
    output = root / "场次 结果（测试）"
    completed = _run(
        repo_root, "xaj", "--events-dir", events, "--params", repo_root / PARAMS,
        "--area-km2", "640", "--timestep-hours", "1", "--mode", "event",
        "--warmup-steps", "6", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    index = pd.read_csv(output / "event_index.csv")
    assert len(index) == 2
    assert "flood 20100811（合成）" in set(index["event_id"])
    table = pd.read_csv(output / "lumped_xaj_runoff.csv")
    assert len(table) == 240
    assert set(table["event_id"]) == {"flood 20100811（合成）", "flood 20110729"}


def test_lumped_xaj_matches_source_formula_reference(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "等价 对比（测试）"
    root.mkdir(parents=True)
    frame = _forcing(180)
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "xaj", "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "640", "--timestep-hours", "1", "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    produced = pd.read_csv(output / "lumped_xaj_runoff.csv")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    reference = _reference_xaj(frame, parameters, 640.0, 1.0)
    for column in ["WU", "WL", "WD", "E", "PE", "W", "R", "FR", "RS", "RI", "RG",
                   "QS", "QI", "QG", "QT", "Qt"]:
        np.testing.assert_allclose(
            produced[column].to_numpy(dtype=float),
            reference[column].to_numpy(dtype=float),
            rtol=1e-9,
            atol=1e-9,
            err_msg=f"列 {column} 与源码公式参考实现不一致",
        )


def test_lumped_xaj_rejects_invalid_parameters_with_exit_one(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "参数 校验（测试）"
    root.mkdir(parents=True)
    frame = _forcing(24)
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    parameters = json.loads((repo_root / PARAMS).read_text(encoding="utf-8"))
    parameters["WUM"] = 100.0
    parameters["WLM"] = 90.0
    bad = root / "bad params.json"
    bad.write_text(json.dumps(parameters, ensure_ascii=False, indent=2), encoding="utf-8")
    completed = _run(
        repo_root, "xaj", "--forcing", forcing, "--params", bad,
        "--area-km2", "640", "--timestep-hours", "1", "--mode", "continuous",
        "--output-dir", root / "out",
    )
    assert completed.returncode == 1, completed.stdout
    assert _result(root / "out")["status"] == "error"


@pytest.mark.parametrize(
    "mutation",
    ["timestep", "missing"],
)
def test_lumped_xaj_scientific_qc_uses_exit_code_two(
    repo_root: Path, tmp_path: Path, mutation: str
) -> None:
    root = tmp_path / f"QC（{mutation}）测试"
    root.mkdir(parents=True)
    frame = _forcing(48)
    if mutation == "timestep":
        declared = "2"
    else:
        frame.loc[10, "P"] = np.nan
        declared = "1"
    forcing = root / "forcing.csv"
    frame.to_csv(forcing, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "xaj", "--forcing", forcing, "--params", repo_root / PARAMS,
        "--area-km2", "640", "--timestep-hours", declared, "--mode", "continuous",
        "--output-dir", output,
    )
    assert completed.returncode == 2, completed.stdout
    document = _result(output)
    assert document["status"] == "error"
    assert any(item["status"] == "FAIL" for item in document["checks"])
    assert not (output / "lumped_xaj_runoff.csv").exists()


def test_muskingum_constant_inflow_is_conserved_and_coefficients_sum_to_one(
    repo_root: Path, tmp_path: Path
) -> None:
    root = tmp_path / "演算 常量（测试）"
    root.mkdir(parents=True)
    inflow = root / "inflow.csv"
    pd.DataFrame({
        "time": pd.date_range("2020-06-01", periods=48, freq="h"),
        "Qt": np.full(48, 120.0),
        "Qres_in": np.full(48, 50.0),
    }).to_csv(inflow, index=False, encoding="utf-8-sig")
    output = root / "演算 结果（测试）"
    completed = _run(
        repo_root, "route", "--inflow", inflow, "--timestep-hours", "1",
        "--route-spec", repo_root / ROUTE_SPEC, "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    table = pd.read_csv(output / "routed_flow.csv")
    np.testing.assert_allclose(table["routed_upstream_reservoir"].to_numpy(), 50.0, rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(table["routed_local_channel"].to_numpy(), 120.0, rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(table["Q_total"].to_numpy(), 170.0, rtol=1e-9, atol=1e-9)
    coefficients = json.loads((output / "route_coefficients.json").read_text(encoding="utf-8"))
    for entry in coefficients.values():
        assert entry["c0"] + entry["c1"] + entry["c2"] == pytest.approx(1.0, abs=1e-9)
    document = _result(output)
    assert document["skill"] == "hydrological-modeling/route-muskingum-channel"
    assert any(
        item["status"] == "WARN" and item["name"].startswith("local_channel:stability_range")
        for item in document["checks"]
    )


def test_muskingum_cascade_final_level_differs_from_first_level(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "演算 串联（测试）"
    root.mkdir(parents=True)
    steps = 72
    index = np.arange(steps, dtype=float)
    pulse = 200.0 * np.exp(-0.5 * ((index - 20.0) / 5.0) ** 2)
    inflow = root / "inflow（合成）.csv"
    pd.DataFrame({
        "time": pd.date_range("2020-06-01", periods=steps, freq="h"),
        "Qt": pulse,
    }).to_csv(inflow, index=False, encoding="utf-8-sig")

    spec = {"routes": [{"id": "channel", "column": "Qt", "layout": "cascade",
                        "k": 1.0, "x": 0.0, "reaches": 4, "output_level": "final"}]}
    final_spec = root / "final spec.json"
    final_spec.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    final_output = root / "final（结果）"
    final_run = _run(
        repo_root, "route", "--inflow", inflow, "--timestep-hours", "1",
        "--route-spec", final_spec, "--output-dir", final_output,
    )
    assert final_run.returncode == 0, final_run.stderr

    spec["routes"][0]["output_level"] = "first"
    first_spec = root / "first spec.json"
    first_spec.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    first_output = root / "first（结果）"
    first_run = _run(
        repo_root, "route", "--inflow", inflow, "--timestep-hours", "1",
        "--route-spec", first_spec, "--output-dir", first_output,
    )
    assert first_run.returncode == 0, first_run.stderr

    final_table = pd.read_csv(final_output / "routed_flow.csv")
    first_table = pd.read_csv(first_output / "routed_flow.csv")
    assert not np.allclose(
        final_table["routed_channel"].to_numpy(dtype=float),
        first_table["routed_channel"].to_numpy(dtype=float),
    )
    assert final_table["routed_channel"].max() <= first_table["routed_channel"].max() + 1e-9


def test_muskingum_rejects_irregular_time_axis_with_exit_two(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "演算 QC（测试）"
    root.mkdir(parents=True)
    frame = pd.DataFrame({
        "time": pd.date_range("2020-06-01", periods=24, freq="h"),
        "Qt": np.full(24, 80.0),
        "Qres_in": np.full(24, 50.0),
    }).drop(index=8).reset_index(drop=True)
    inflow = root / "inflow.csv"
    frame.to_csv(inflow, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "route", "--inflow", inflow, "--timestep-hours", "1",
        "--route-spec", repo_root / ROUTE_SPEC, "--output-dir", output,
    )
    assert completed.returncode == 2, completed.stdout
    document = _result(output)
    assert document["status"] == "error"
    assert not (output / "routed_flow.csv").exists()
