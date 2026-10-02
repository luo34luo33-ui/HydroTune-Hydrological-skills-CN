from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from scipy.integrate import quad
from scipy.optimize._numdiff import approx_derivative
from scipy.stats import norm

EXAMPLES = Path(__file__).resolve().parents[1] / "post-processing/ensemble-discharge-with-bma/examples"


def _import(name):
    spec = importlib.util.spec_from_file_location("bma_test_" + name, EXAMPLES / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(EXAMPLES))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(EXAMPLES))
    return module


probability = _import("_bma_probability")
generator = _import("make_synthetic_example")


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _run(script, *args):
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True,
                          text=True, encoding="utf-8", env=environment, check=False)


def _train(data, output, mode="continuous", script=None, *extra):
    selector = ["--events", "flood-001", "flood-002"] if mode == "event" else [
        "--start", "2020-01-01T00:00:00+08:00", "--end", "2020-01-10T23:00:00+08:00"]
    return _run(script or EXAMPLES / "ensemble_discharge_bma.py", "--mode", "train", "--members", data / "members.json",
                "--observed-result", data / "observed-result.json", "--random-state", 2026,
                "--output-dir", output, *selector, *extra)


def _predict(data, fit_dir, output, mode="continuous", script=None, *extra):
    selector = ["--events", "flood-003"] if mode == "event" else [
        "--start", "2020-01-11T00:00:00+08:00", "--end", "2020-01-15T23:00:00+08:00"]
    return _run(script or EXAMPLES / "ensemble_discharge_bma.py", "--mode", "predict", "--members", data / "members.json",
                "--model-result", fit_dir / "result.json", "--output-dir", output, *selector, *extra)


@pytest.fixture
def continuous(tmp_path):
    data = tmp_path / "输入 数据（连续）"
    generator.generate(data)
    return data


@pytest.fixture
def trained(continuous, tmp_path):
    output = tmp_path / "训练 结果"
    result = _train(continuous, output)
    assert result.returncode == 0, result.stderr
    return continuous, output


def _rehash(data, member="HBV"):
    import hashlib
    path = data / (member + "-result.json")
    doc = _json(path)
    doc["artifacts"]["simulation_table"]["sha256"] = hashlib.sha256((data / (member + ".csv")).read_bytes()).hexdigest()
    _write(path, doc)


def test_censored_likelihood_and_analytic_gradient():
    y = np.array([0.0, 0.0, 0.2, 0.9])
    x = np.array([0.3, 0.7, 0.4, 0.8])
    theta = np.array([-0.2, 0.8, 0.15])
    mu = theta[0] + theta[1] * x
    actual = probability.component_loglik(y, x, theta)
    np.testing.assert_allclose(actual[:2], norm.logcdf(-mu[:2] / theta[2]))
    np.testing.assert_allclose(actual[2:], norm.logpdf(y[2:], mu[2:], theta[2]))
    r = np.array([0.1, 0.2, 0.4, 0.9])
    numeric = approx_derivative(lambda t: probability._objective(t, y, x, r)[0], theta).ravel()
    np.testing.assert_allclose(probability._objective(theta, y, x, r)[1], numeric, rtol=1e-6)
    extreme = probability.component_loglik(np.array([0.0]), np.array([100.0]), [0, 1, 0.1])
    assert np.isfinite(extreme).all()


def test_mixture_mean_cdf_and_quantiles_match_integrals():
    model = {"weights": [0.4, 0.6], "theta": [[-1, 1, 2], [1, 0.5, 1]], "scale_m3_s": 1}
    flows = np.array([0.0, 3.0])
    ps = [0.05, 0.5, 0.95]
    mean, zero, qs, mu, sigma, _ = probability.summarize(flows, model, ps)
    density = lambda q: float(np.dot(model["weights"], norm.pdf(q, mu, sigma)))
    np.testing.assert_allclose(mean, quad(lambda q: q * density(q), 0, np.inf)[0], rtol=1e-10)
    assert zero == pytest.approx(1 - quad(density, 0, np.inf)[0])
    assert probability.cdf(-0.01, mu, sigma, model["weights"]) == 0
    assert qs == sorted(qs) and qs[0] == 0
    for p, q in zip(ps, qs):
        if q > 0:
            assert probability.cdf(q, mu, sigma, model["weights"]) == pytest.approx(p, abs=1e-10)
        else:
            assert p <= zero
    # Identical components reduce to a single censored Gaussian.
    identical = {"weights": [0.2, 0.8], "theta": [[0, 1, 1], [0, 1, 1]], "scale_m3_s": 1e12}
    result = probability.summarize([1e12, 1e12], identical, [0.5, 0.999999])
    assert np.isfinite(result[0]) and result[2][0] == pytest.approx(1e12)
    assert result[2][1] == pytest.approx(1e12 * (1 + norm.ppf(0.999999)))


def test_known_mixture_fit_is_reproducible_and_monotone(continuous):
    obs = pd.read_csv(continuous / "observed.csv").discharge_m3_s.to_numpy()[3:240]
    x = np.column_stack([pd.read_csv(continuous / (m + ".csv")).Q.to_numpy()[3:240] for m in ("HBV", "Tank")])
    model, diag = probability.fit(obs, x, 2026)
    repeat, _ = probability.fit(obs, x, 2026)
    assert model == repeat
    assert model is not None
    assert sum(model["weights"]) == pytest.approx(1)
    assert model["weights"][0] == pytest.approx(0.65, abs=0.08)
    assert np.all(np.array(model["theta"])[:, 2] >= probability.SIGMA_FLOOR)
    for start in diag["starts"]:
        assert np.min(np.diff(start["history"])) >= -1e-9
    failed, diag = probability.fit(obs, x, 2026, max_iter=1, tol=1e-15)
    assert failed is None and all(not start["converged"] for start in diag["starts"])


def test_perfect_identical_members_reach_sigma_floor():
    observed = np.linspace(1, 100, 50)
    model, diagnostics = probability.fit(observed, np.column_stack([observed, observed]), 2026, n_starts=1)
    assert model is not None and diagnostics["starts"][0]["converged"]
    np.testing.assert_allclose(np.array(model["theta"])[:, 2], probability.SIGMA_FLOOR)
    assert sum(model["weights"]) == pytest.approx(1)
    mean, _, qs, *_ = probability.summarize([50, 50], model, [0.05, 0.5, 0.95])
    assert mean == pytest.approx(50) and qs[0] <= qs[1] <= qs[2]


@pytest.mark.parametrize("mode", ["continuous", "event"])
def test_train_predict_roundtrip_without_observations(tmp_path, mode):
    data, fitted, output = [tmp_path / p for p in ("输入 中文", "训练 中文", "预测 中文")]
    generator.generate(data, mode)
    train = _train(data, fitted, mode)
    assert train.returncode == 0, train.stderr
    model = _json(fitted / "bma_model.json")
    (data / "observed.csv").unlink()
    (data / "observed-result.json").unlink()
    predicted = _predict(data, fitted, output, mode)
    assert predicted.returncode == 0, predicted.stderr
    table = pd.read_csv(output / "ensemble_discharge.csv")
    components = pd.read_csv(output / "component_predictions.csv")
    assert len(table) == (117 if mode == "event" else 120)
    assert len(components) == 2 * len(table)
    assert (table.ensemble_mean_m3_s >= 0).all()
    assert table.probability_zero.between(0, 1).all()
    assert (table.p05_m3_s <= table.p50_m3_s).all() and (table.p50_m3_s <= table.p95_m3_s).all()
    first = components.iloc[:2].simulated_m3_s.to_numpy()
    direct = probability.summarize(first, model, [0.05, 0.5, 0.95])
    assert table.iloc[0].ensemble_mean_m3_s == pytest.approx(direct[0])
    np.testing.assert_allclose(table.iloc[0][["p05_m3_s", "p50_m3_s", "p95_m3_s"]].astype(float), direct[2])
    assert _json(output / "result.json")["parameters"]["mode"] == "predict"
    if mode == "event":
        assert set(table.event_id) == {"flood-003"}
        assert model["training_events"] == ["flood-001", "flood-002"]


@pytest.mark.parametrize("problem", ["hash", "gap", "duplicate_time", "missing", "negative", "outlet",
                                      "duplicate_id", "unit", "source_step", "xaj", "constant_observed",
                                      "no_warmup", "no_timezone", "axis_coverage", "observation_gap"])
def test_bad_training_inputs_stop_without_model(continuous, tmp_path, problem):
    manifest = _json(continuous / "members.json")
    if problem in {"hash", "gap", "duplicate_time", "missing", "negative", "no_warmup", "no_timezone", "axis_coverage"}:
        path = continuous / "HBV.csv"
        frame = pd.read_csv(path)
        if problem == "gap":
            frame = frame.drop(index=20)
        elif problem == "duplicate_time":
            frame.loc[20, "time"] = frame.loc[19, "time"]
        elif problem == "no_warmup":
            frame = frame.drop(columns="is_warmup")
        elif problem == "no_timezone":
            frame["time"] = frame.time.str.replace("+08:00", "", regex=False)
        elif problem == "axis_coverage":
            frame = frame.iloc[1:]
        else:
            frame.loc[20, "Q"] = {"hash": 123, "missing": np.nan, "negative": -1}[problem]
        frame.to_csv(path, index=False)
        if problem != "hash":
            _rehash(continuous)
    elif problem in {"outlet", "duplicate_id", "unit"}:
        if problem == "outlet":
            manifest["members"][1]["outlet_id"] = "other-outlet"
        elif problem == "duplicate_id":
            manifest["members"][1]["model_id"] = "HBV"
        else:
            manifest["unit"] = "mm/hour"
        _write(continuous / "members.json", manifest)
    elif problem in {"source_step", "xaj"}:
        path = continuous / "HBV-result.json"
        source = _json(path)
        if problem == "source_step":
            source["parameters"]["timestep_hours"] = 2
        else:
            source["skill"] = "hydrological-modeling/run-lumped-xaj-model"
        _write(path, source)
    else:
        import hashlib
        path = continuous / "observed.csv"
        frame = pd.read_csv(path)
        if problem == "observation_gap":
            frame = frame.drop(index=20)
        else:
            frame["discharge_m3_s"] = 0
        frame.to_csv(path, index=False)
        source = _json(continuous / "observed-result.json")
        source["artifacts"]["discharge_timeseries"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        _write(continuous / "observed-result.json", source)
    output = tmp_path / "failed"
    result = _train(continuous, output)
    assert result.returncode == 2, result.stderr
    assert _json(output / "result.json")["status"] == "error"
    assert not (output / "bma_model.json").exists()


def test_nonconvergence_preserves_diagnostics(continuous, tmp_path):
    output = tmp_path / "not-converged"
    result = _train(continuous, output, "continuous", None, "--max-iter", "1", "--tol", "1e-15")
    assert result.returncode == 2
    doc = _json(output / "result.json")
    assert "fit_diagnostics" in doc["artifacts"] and "qc" in doc["artifacts"]
    assert not (output / "bma_model.json").exists()


def test_warmup_union_and_duplicate_member_warning(continuous, tmp_path):
    hbv = pd.read_csv(continuous / "HBV.csv")
    tank = hbv.copy()
    tank["is_warmup"] = np.arange(len(tank)) < 5
    tank.to_csv(continuous / "Tank.csv", index=False)
    _rehash(continuous, "Tank")
    output = tmp_path / "warmup-union"
    trained = _train(continuous, output)
    assert trained.returncode == 0, trained.stderr
    qc = _json(output / "alignment_qc.json")
    assert qc["warmup_union_rows"] == 5 and qc["rows"] == 235
    assert any("duplicate member" in w for w in _json(output / "result.json")["warnings"])


def test_event_boundaries_and_training_event_reuse(tmp_path):
    data, fitted = tmp_path / "events", tmp_path / "fitted"
    generator.generate(data, "event")
    assert _train(data, fitted, "event").returncode == 0
    script = EXAMPLES / "ensemble_discharge_bma.py"
    reused = _run(script, "--mode", "predict", "--members", data / "members.json", "--model-result", fitted / "result.json",
                  "--events", "flood-002", "flood-003", "--output-dir", tmp_path / "reused")
    assert reused.returncode == 2
    sliced = _predict(data, fitted, tmp_path / "sliced", "event", None, "--start", "2020-01-11T00:00:00+08:00")
    assert sliced.returncode == 2 and "forbids" in sliced.stderr
    missing = _predict(data, fitted, tmp_path / "missing", "event", None, "--events", "unknown-event")
    assert missing.returncode == 2


def test_prediction_reorders_members_and_custom_quantiles(trained, tmp_path):
    data, fitted = trained
    first_output = tmp_path / "original-order"
    assert _predict(data, fitted, first_output).returncode == 0
    manifest = _json(data / "members.json")
    manifest["members"].reverse()
    _write(data / "members.json", manifest)
    second_output = tmp_path / "reversed-order"
    result = _predict(data, fitted, second_output)
    assert result.returncode == 0, result.stderr
    pd.testing.assert_frame_equal(pd.read_csv(first_output / "ensemble_discharge.csv"), pd.read_csv(second_output / "ensemble_discharge.csv"))
    custom = tmp_path / "custom-quantiles"
    result = _predict(data, fitted, custom, "continuous", None, "--quantiles", "0.1", "0.9")
    assert result.returncode == 0, result.stderr
    columns = pd.read_csv(custom / "ensemble_discharge.csv").columns
    assert "p10_m3_s" in columns and "p90_m3_s" in columns and "p50_m3_s" not in columns


def test_output_protection_and_invalid_quantiles(trained, tmp_path):
    data, fitted = trained
    before = (fitted / "bma_model.json").read_bytes()
    result = _predict(data, fitted, fitted, "continuous", None, "--overwrite")
    assert result.returncode == 1 and (fitted / "bma_model.json").read_bytes() == before
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "note.txt").write_text("preserve", encoding="utf-8")
    result = _predict(data, fitted, nonempty)
    assert result.returncode == 1 and (nonempty / "note.txt").read_text() == "preserve"
    invalid = tmp_path / "bad-quantiles"
    result = _predict(data, fitted, invalid, "continuous", None, "--quantiles", "0.9", "0.1")
    assert result.returncode == 1 and not (invalid / "ensemble_discharge.csv").exists()


@pytest.mark.parametrize("problem", ["overlap", "members", "model_hash", "timezone", "observation_argument"])
def test_prediction_rejects_leakage_and_changed_model(trained, tmp_path, problem):
    data, fitted = trained
    manifest = _json(data / "members.json")
    extra = []
    if problem == "overlap":
        extra = ["--start", "2020-01-09T00:00:00+08:00"]
    elif problem == "members":
        manifest["members"][0]["model_id"] = "new-model"
        _write(data / "members.json", manifest)
    elif problem == "model_hash":
        with (fitted / "bma_model.json").open("a", encoding="utf-8") as stream:
            stream.write(" ")
    elif problem == "timezone":
        manifest["timezone"] = "UTC"
        _write(data / "members.json", manifest)
    else:
        extra = ["--observed-result", data / "observed-result.json"]
    output = tmp_path / "bad-prediction"
    result = _predict(data, fitted, output, "continuous", None, *extra)
    assert result.returncode == (1 if problem == "observation_argument" else 2), result.stderr
    assert not (output / "ensemble_discharge.csv").exists()


def test_install_and_execute_self_contained_skill(repo_root, tmp_path, utf8_env):
    target = tmp_path / "安装 中文（含 空格）"
    engine = _run(repo_root / "scripts/install_skills.py", "--repo-root", repo_root,
                  "--tool-name", "Test", "--default-target", target, "--project-subdir", ".agents/skills",
                  "--categories", "post-processing")
    assert engine.returncode == 0, engine.stderr
    skill = target / "hydro-post-processing-ensemble-discharge-with-bma"
    assert "(references/data-contract.yaml)" in (skill / "SKILL.md").read_text(encoding="utf-8")
    scripts = skill / "scripts"
    data, fitted, output = [tmp_path / p for p in ("独立 输入", "独立 训练", "独立 预测")]
    generated = _run(scripts / "make_synthetic_example.py", "--output-dir", data)
    assert generated.returncode == 0, generated.stderr
    train = _train(data, fitted, "continuous", scripts / "ensemble_discharge_bma.py")
    assert train.returncode == 0, train.stderr
    prediction = _predict(data, fitted, output, "continuous", scripts / "ensemble_discharge_bma.py")
    assert prediction.returncode == 0, prediction.stderr
    assert (output / "ensemble_discharge.csv").is_file()
