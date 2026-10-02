from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")

SCRIPTS = {
    "ml": Path("post-processing/correct-residual-with-ml/examples/correct_residual_ml.py"),
}


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


def _training_frame(rows: int = 240) -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    base = 40.0 + 160.0 * np.exp(-0.5 * ((index - 90.0) / 16.0) ** 2)
    bias = 0.12 * base + 6.0 * np.sin(index / 7.0)
    observed = base + bias
    return pd.DataFrame({
        "time": pd.date_range("2020-06-01", periods=rows, freq="h"),
        "Q_total": base,
        "Q_obs": observed,
        "P": np.where((index >= 60) & (index < 110), 6.0, 0.0),
        "Qt": base * 0.85,
    })


def _result(output: Path) -> dict:
    return json.loads((output / "result.json").read_text(encoding="utf-8"))


def test_ml_example_has_help(repo_root: Path) -> None:
    result = _run(repo_root, "ml", "--help")
    assert result.returncode == 0, result.stderr
    assert "--output-dir" in result.stdout


@pytest.mark.parametrize("backend", ["random-forest", "ridge"])
def test_residual_correction_train_then_predict_roundtrip(
    repo_root: Path, tmp_path: Path, backend: str
) -> None:
    root = tmp_path / f"残差 校正（{backend}）测试"
    root.mkdir(parents=True)
    table = root / "训练 序列（合成）.csv"
    _training_frame().to_csv(table, index=False, encoding="utf-8-sig")
    hyperparameters = root / "超参（显式）.json"
    chosen = {} if backend == "ridge" else {"n_estimators": 40, "max_depth": 3}
    hyperparameters.write_text(json.dumps(chosen), encoding="utf-8")

    train_dir = root / "01 训练（结果）"
    trained = _run(
        repo_root, "ml", "--mode", "train", "--table", table,
        "--base-column", "Q_total", "--observed-column", "Q_obs", "--feature-columns", "P,Qt",
        "--lag-spec", "residual:1,2,3,4,5", "--lag-spec", "P:0,1", "--backend", backend,
        "--hyperparameters", hyperparameters, "--random-state", "2025",
        "--split", "chronological", "--output-dir", train_dir,
    )
    assert trained.returncode == 0, trained.stderr
    train_document = _result(train_dir)
    assert train_document["skill"] == "post-processing/correct-residual-with-ml"
    assert train_document["parameters"]["backend"] == backend

    card = json.loads((train_dir / "model_card.json").read_text(encoding="utf-8"))
    assert card["residual_definition"] == "observed - base"
    assert card["random_state"] == 2025
    assert card["feature_columns"] == ["P", "Qt"]
    assert card["lag_spec"] == [
        {"column": "residual", "steps": [1, 2, 3, 4, 5]},
        {"column": "P", "steps": [0, 1]},
    ]
    assert train_document["parameters"]["lag_spec"] == card["lag_spec"]
    assert card["train_rows"] > 0

    produced = pd.read_csv(train_dir / "corrected_series.csv")
    np.testing.assert_allclose(
        produced["residual"].to_numpy(dtype=float),
        (produced["Q_obs"] - produced["Q_total"]).to_numpy(dtype=float),
        rtol=1e-9,
        atol=1e-9,
    )
    np.testing.assert_allclose(
        produced["corrected"].to_numpy(dtype=float),
        (produced["Q_total"] + produced["predicted_residual"]).to_numpy(dtype=float),
        rtol=1e-9,
        atol=1e-9,
    )
    original = _training_frame()
    original_residual = original["Q_obs"] - original["Q_total"]
    assert len(produced) == len(original) - 5
    for step in range(1, 6):
        np.testing.assert_allclose(
            produced[f"residual_lag_{step}"], original_residual.shift(step).iloc[5:],
        )
    np.testing.assert_allclose(produced["P_lag_0"], original["P"].iloc[5:])
    np.testing.assert_allclose(produced["P_lag_1"], original["P"].shift(1).iloc[5:])

    predict_dir = root / "02 预测（结果）"
    history_table = root / "history.csv"
    history = original.drop(columns="Q_obs")
    history["residual"] = original_residual
    history.to_csv(history_table, index=False)
    predicted = _run(
        repo_root, "ml", "--mode", "predict", "--table", history_table,
        "--base-column", "Q_total", "--model-in", train_dir / "model.joblib",
        "--output-dir", predict_dir,
    )
    assert predicted.returncode == 0, predicted.stderr
    applied = pd.read_csv(predict_dir / "corrected_series.csv")
    np.testing.assert_allclose(applied["predicted_residual"], produced["predicted_residual"])
    np.testing.assert_allclose(
        applied["corrected"].to_numpy(dtype=float),
        (applied["Q_total"] + applied["predicted_residual"]).to_numpy(dtype=float),
        rtol=1e-9,
        atol=1e-9,
    )
    assert _result(predict_dir)["skill"] == "post-processing/correct-residual-with-ml"


def test_residual_correction_rejects_missing_random_state(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "残差 负向（种子）测试"
    root.mkdir(parents=True)
    table = root / "table.csv"
    _training_frame(120).to_csv(table, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "ml", "--mode", "train", "--table", table,
        "--base-column", "Q_total", "--observed-column", "Q_obs", "--feature-columns", "P,Qt",
        "--lag-spec", "residual:1,2,3,4,5",
        "--backend", "ridge", "--hyperparameters", "{}", "--output-dir", output,
    )
    assert completed.returncode == 1, completed.stdout
    assert _result(output)["status"] == "error"


@pytest.mark.parametrize(
    "case,arguments",
    [
        ("future-lag", ["--lag-spec", "residual:-1"]),
        ("missing-feature", ["--feature-columns", "missing_column", "--lag-spec", "residual:1,2,3,4,5"]),
    ],
)
def test_residual_correction_rejects_future_information_and_missing_features(
    repo_root: Path, tmp_path: Path, case: str, arguments: list[str]
) -> None:
    root = tmp_path / f"残差 负向（{case}）测试"
    root.mkdir(parents=True)
    table = root / "table.csv"
    _training_frame(120).to_csv(table, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "ml", "--mode", "train", "--table", table,
        "--base-column", "Q_total", "--observed-column", "Q_obs",
        "--backend", "ridge", "--hyperparameters", "{}", "--random-state", "3",
        *arguments, "--output-dir", output,
    )
    assert completed.returncode == 1, completed.stdout
    assert _result(output)["status"] == "error"


def test_residual_correction_reports_lag_rows_dropped(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "残差 滞后（测试）"
    root.mkdir(parents=True)
    table = root / "table.csv"
    _training_frame(120).to_csv(table, index=False, encoding="utf-8-sig")
    output = root / "滞后 结果（测试）"
    completed = _run(
        repo_root, "ml", "--mode", "train", "--table", table,
        "--base-column", "Q_total", "--observed-column", "Q_obs", "--feature-columns", "P,Qt",
        "--lag-spec", "residual:1,2,3,4,5", "--backend", "ridge",
        "--hyperparameters", "{}", "--random-state", "11", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    document = _result(output)
    assert any(item["name"] == "lag_rows_dropped" and item["status"] == "WARN" for item in document["checks"])
    assert any("滞后特征不完整而丢弃 5 行" in warning for warning in document["warnings"])
    assert len(pd.read_csv(output / "corrected_series.csv")) == 115


def test_residual_correction_xgboost_backend_when_available(repo_root: Path, tmp_path: Path) -> None:
    pytest.importorskip("xgboost")
    root = tmp_path / "残差 XGB（测试）"
    root.mkdir(parents=True)
    table = root / "table.csv"
    _training_frame(120).to_csv(table, index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "ml", "--mode", "train", "--table", table,
        "--base-column", "Q_total", "--observed-column", "Q_obs", "--feature-columns", "P,Qt",
        "--lag-spec", "residual:1,2,3,4,5",
        "--backend", "xgboost",
        "--hyperparameters", repo_root / "post-processing/correct-residual-with-ml/examples/source_equivalent_hyperparameters.json",
        "--random-state", "7", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    card = json.loads((output / "model_card.json").read_text(encoding="utf-8"))
    assert card["backend"] == "xgboost"
    assert card["hyperparameters"]["n_estimators"] == 500
    assert "_note" not in card["hyperparameters"]


@pytest.mark.parametrize("missing_step", [None, 1, 2, 3, 4, 5])
def test_residual_correction_requires_all_five_lags(repo_root: Path, tmp_path: Path, missing_step: int | None) -> None:
    table = tmp_path / "table.csv"
    _training_frame(120).to_csv(table, index=False)
    output = tmp_path / "out"
    arguments = [] if missing_step is None else [
        "--lag-spec", "residual:" + ",".join(str(step) for step in range(1, 6) if step != missing_step),
    ]
    completed = _run(
        repo_root, "ml", "--mode", "train", "--table", table,
        "--feature-columns", "P,Qt", "--backend", "ridge", "--hyperparameters", "{}",
        "--random-state", "3", *arguments, "--output-dir", output,
    )
    assert completed.returncode == 1
    expected = [1, 2, 3, 4, 5] if missing_step is None else [missing_step]
    assert f"缺少残差滞后步数: {expected}" in completed.stderr
    assert not (output / "model.joblib").exists()
    assert not (output / "corrected_series.csv").exists()


@pytest.mark.parametrize("features,lag", [
    ("P,Qt,residual", "residual:1,2,3,4,5"),
    ("P,Qt", "residual:0,1,2,3,4,5"),
])
def test_residual_correction_rejects_current_residual(repo_root: Path, tmp_path: Path, features: str, lag: str) -> None:
    table = tmp_path / "table.csv"
    _training_frame(120).to_csv(table, index=False)
    completed = _run(
        repo_root, "ml", "--mode", "train", "--table", table,
        "--feature-columns", features, "--lag-spec", lag,
        "--backend", "ridge", "--hyperparameters", "{}", "--random-state", "3",
        "--output-dir", tmp_path / "out",
    )
    assert completed.returncode == 1
    assert "当前" in completed.stderr


@pytest.mark.parametrize("case", ["old-card", "current-feature", "current-lag", "missing-history"])
def test_residual_prediction_rejects_invalid_card_or_missing_history(repo_root: Path, tmp_path: Path, case: str) -> None:
    table = tmp_path / "table.csv"
    frame = _training_frame(120)
    frame.to_csv(table, index=False)
    train_dir = tmp_path / "train"
    trained = _run(
        repo_root, "ml", "--mode", "train", "--table", table,
        "--feature-columns", "P,Qt", "--lag-spec", "residual:1,2,3,4,5",
        "--backend", "ridge", "--hyperparameters", "{}", "--random-state", "3",
        "--output-dir", train_dir,
    )
    assert trained.returncode == 0, trained.stderr
    card_path = train_dir / "model_card.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    if case == "old-card":
        card["lag_spec"][0]["steps"] = [1, 2, 3]
    elif case == "current-feature":
        card["feature_columns"].append("residual")
    elif case == "current-lag":
        card["lag_spec"][0]["steps"].append(0)
    else:
        frame.drop(columns="Q_obs").to_csv(table, index=False)
    card_path.write_text(json.dumps(card), encoding="utf-8")
    output = tmp_path / "predict"
    predicted = _run(
        repo_root, "ml", "--mode", "predict", "--table", table,
        "--model-in", train_dir / "model.joblib", "--output-dir", output,
    )
    assert predicted.returncode == 1
    assert ("历史残差来源" if case == "missing-history" else "重新训练") in predicted.stderr
    assert not (output / "corrected_series.csv").exists()
