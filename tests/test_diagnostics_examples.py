from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import hashlib

import numpy as np
import pandas as pd
import pytest


SCRIPTS = {
    "event": Path("evaluation-diagnostics/compute-event-flood-metrics/examples/compute_event_metrics.py"),
    "series": Path("evaluation-diagnostics/compute-continuous-series-metrics/examples/compute_series_metrics.py"),
    "aggregate": Path("evaluation-diagnostics/aggregate-event-metrics/examples/aggregate_event_metrics.py"),
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


def _event_frame(rows: int = 120, lag: int = 2, scale: float = 0.9) -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    observed = 30.0 + 180.0 * np.exp(-0.5 * ((index - 45.0) / 11.0) ** 2)
    simulated = np.clip(np.roll(observed, lag) * scale, 0.0, None)
    return pd.DataFrame({
        "time": pd.date_range("2020-07-01", periods=rows, freq="h"),
        "Q_obs": observed,
        "XAJ_output": simulated,
    })


def _result(output: Path) -> dict:
    return json.loads((output / "result.json").read_text(encoding="utf-8"))


def test_metrics_exclude_explicit_warmup(repo_root, tmp_path):
    frame = _event_frame(12)
    frame['scored'] = [False, False] + [True] * 10
    frame['is_warmup'] = [True, True] + [False] * 10
    frame.loc[:1, 'XAJ_output'] = 99999
    path = tmp_path / 'scoring.csv'; frame.to_csv(path, index=False)
    out = tmp_path / 'scoring-metrics'
    r = _run(repo_root, 'series', '--series', path, '--simulated-column', 'XAJ_output', '--timestep-hours', 1, '--output-dir', out)
    assert r.returncode == 0, r.stderr
    metrics = pd.read_csv(out/'series_metrics.csv').set_index('metric')['value']
    a = frame.iloc[2:]['Q_obs'].to_numpy(); b = frame.iloc[2:]['XAJ_output'].to_numpy()
    expected = 1 - np.sum((a-b)**2) / np.sum((a-a.mean())**2)
    assert float(metrics['nse']) == pytest.approx(expected)
    assert _result(out)['parameters']['scoring_policy'] == 'exclude_unscored_and_warmup'


@pytest.mark.parametrize("key", sorted(SCRIPTS))
def test_diagnostics_examples_have_help(repo_root: Path, key: str) -> None:
    result = _run(repo_root, key, "--help")
    assert result.returncode == 0, result.stderr
    assert "--output-dir" in result.stdout


def test_event_metrics_and_aggregation_roundtrip_on_unicode_paths(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "诊断 管线（测试）"
    events = root / "events"
    events.mkdir(parents=True)
    _event_frame(120, lag=2).to_csv(events / "flood 20100811（合成）.csv", index=False, encoding="utf-8-sig")
    _event_frame(120, lag=6).to_csv(events / "flood 20110729.csv", index=False, encoding="utf-8-sig")

    thresholds = root / "阈值（显式）.json"
    thresholds.write_text(
        json.dumps({"relative_volume_error": 0.2, "relative_peak_error": 0.2, "peak_time_error_hours": 3.0}),
        encoding="utf-8",
    )

    event_dir = root / "01 场次 指标（结果）"
    completed = _run(
        repo_root, "event", "--events-dir", events,
        "--observed-column", "Q_obs", "--simulated-column", "XAJ_output",
        "--timestep-hours", "1", "--thresholds", thresholds, "--output-dir", event_dir,
    )
    assert completed.returncode == 0, completed.stderr
    event_document = _result(event_dir)
    assert event_document["skill"] == "evaluation-diagnostics/compute-event-flood-metrics"
    assert event_document["parameters"]["volume_unit"] == "10k m3"
    input_refs = [v for v in event_document['inputs'].values() if isinstance(v, dict)]
    assert len(input_refs) == 2
    for reference in input_refs:
        path = (event_dir / reference['path']).resolve()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == reference['sha256']

    metrics = pd.read_csv(event_dir / "event_metrics.csv")
    assert len(metrics) == 2
    assert "flood 20100811（合成）" in set(metrics["event_id"])
    assert metrics["nse"].between(0.0, 1.0).all()
    np.testing.assert_allclose(metrics["nse"], metrics["r2"], rtol=1e-9, atol=1e-9)
    assert metrics.loc[1, "peak_time_error_hours"] > metrics.loc[0, "peak_time_error_hours"]
    assert set(metrics["qualified_peak_time"]) == {"是", "否"}

    aggregate_dir = root / "02 汇总（结果）"
    aggregate_thresholds = root / "汇总 阈值（显式）.json"
    aggregate_thresholds.write_text(json.dumps({"qualified_peak_time": 3.0}), encoding="utf-8")
    aggregated = _run(
        repo_root, "aggregate", "--metrics-table", event_dir / "event_metrics.csv",
        "--value-columns", "nse,peak_time_error_hours",
        "--pass-columns", "qualified_peak_time",
        "--pass-thresholds", aggregate_thresholds, "--output-dir", aggregate_dir,
    )
    assert aggregated.returncode == 0, aggregated.stderr
    summary = pd.read_csv(aggregate_dir / "aggregate_metrics.csv")
    assert len(summary) == 1
    assert summary.loc[0, "events"] == 2
    assert summary.loc[0, "nse_count"] == 2
    assert summary.loc[0, "qualified_peak_time_qualified_rate"] == pytest.approx(0.5)


def test_series_metrics_are_exact_for_identical_series(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "连续 序列（测试）"
    root.mkdir(parents=True)
    frame = _event_frame(96)
    frame["XAJ_output"] = frame["Q_obs"]
    series = root / "序列（合成）.csv"
    frame.to_csv(series, index=False, encoding="utf-8-sig")
    output = root / "序列 指标（结果）"
    completed = _run(
        repo_root, "series", "--series", series,
        "--observed-column", "Q_obs", "--simulated-column", "XAJ_output",
        "--timestep-hours", "1", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    table = pd.read_csv(output / "series_metrics.csv")
    values = dict(zip(table["metric"], table["value"]))
    assert float(values["nse"]) == pytest.approx(1.0)
    assert float(values["rmse"]) == pytest.approx(0.0)
    assert float(values["mae"]) == pytest.approx(0.0)
    assert float(values["volume_bias"]) == pytest.approx(0.0)
    assert set(table["status"]) == {"ok"}
    assert _result(output)["skill"] == "evaluation-diagnostics/compute-continuous-series-metrics"


@pytest.mark.parametrize("key", ["event", "series"])
def test_diagnostics_reject_inconsistent_timestep_with_exit_two(
    repo_root: Path, tmp_path: Path, key: str
) -> None:
    root = tmp_path / f"步长 校验（{key}）测试"
    root.mkdir(parents=True)
    frame = _event_frame(48)
    source = root / "table.csv"
    frame.to_csv(source, index=False, encoding="utf-8-sig")
    output = root / "out"
    if key == "event":
        arguments = ["--events", source, "--observed-column", "Q_obs", "--simulated-column", "XAJ_output"]
    else:
        arguments = ["--series", source, "--observed-column", "Q_obs", "--simulated-column", "XAJ_output"]
    completed = _run(
        repo_root, key, *arguments, "--timestep-hours", "2", "--output-dir", output,
    )
    assert completed.returncode == 2, completed.stdout
    document = _result(output)
    assert document["status"] == "error"
    assert any(item["name"].endswith("timestep_consistency") for item in document["checks"])


def test_event_metrics_require_explicit_thresholds_for_qualification(
    repo_root: Path, tmp_path: Path
) -> None:
    root = tmp_path / "阈值 缺失（测试）"
    events = root / "events"
    events.mkdir(parents=True)
    _event_frame(96).to_csv(events / "flood 20200109.csv", index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "event", "--events-dir", events,
        "--observed-column", "Q_obs", "--simulated-column", "XAJ_output",
        "--timestep-hours", "1", "--output-dir", output,
    )
    assert completed.returncode == 0, completed.stderr
    metrics = pd.read_csv(output / "event_metrics.csv")
    assert set(metrics["qualified_volume"]) == {"unavailable"}
    document = _result(output)
    assert any(item["name"] == "thresholds_provided" and item["status"] == "WARN" for item in document["checks"])
    assert any("未提供 --thresholds" in warning for warning in document["warnings"])

    aggregate_dir = root / "aggregate"
    aggregated = _run(
        repo_root, "aggregate", "--metrics-table", output / "event_metrics.csv",
        "--value-columns", "nse", "--pass-columns", "qualified_volume", "--output-dir", aggregate_dir,
    )
    assert aggregated.returncode == 0, aggregated.stderr
    summary = pd.read_csv(aggregate_dir / "aggregate_metrics.csv")
    assert str(summary.loc[0, "qualified_volume_qualified_rate"]) == "unavailable"


def test_event_metrics_reject_empty_series_with_exit_two(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "空 序列（测试）"
    events = root / "events"
    events.mkdir(parents=True)
    _event_frame(24).iloc[:0].to_csv(events / "empty.csv", index=False, encoding="utf-8-sig")
    output = root / "out"
    completed = _run(
        repo_root, "event", "--events-dir", events,
        "--observed-column", "Q_obs", "--simulated-column", "XAJ_output",
        "--timestep-hours", "1", "--output-dir", output,
    )
    assert completed.returncode == 2, completed.stdout
    assert _result(output)["status"] == "error"
