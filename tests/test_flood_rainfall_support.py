from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from jsonschema import Draft202012Validator
import numpy as np
import pandas as pd
import pytest
import yaml

from test_timeseries_examples import SCRIPTS, _load_module, _pipeline, _run, _write_config


@pytest.fixture
def extractor(repo_root, tmp_path):
    module = _load_module(repo_root / SCRIPTS["extract"], "rainfall_extract_test")
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    cfg, _ = module.load_config(config_path)
    return module, replace(cfg, rainfall_lookback_hours=2, min_observed_rainfall_mm=5)


def sample_flow():
    return pd.DataFrame({
        "time": pd.date_range("2020-01-01", periods=10, freq="h", tz="UTC"),
        "discharge_m3_s": [1, 1, 1, 2, 10, 4, 1, 1, 1, 1],
        "baseflow_m3_s": np.ones(10),
        "quickflow_m3_s": [0, 0, 0, 1, 9, 3, 0, 0, 0, 0],
    })


EVENT = {"start_idx": 3, "peak_idx": 4, "end_idx": 6}


@pytest.mark.parametrize("rain,kept,reason,valid,total", [
    ([0, 0, 3, 0, 2, 0, 0, 0, 0, 0], True, "rainfall_supported", 4, 5),
    ([0, np.nan, 3, np.nan, 2, 0, 0, 0, 0, 0], True, "rainfall_supported", 2, 5),
    ([np.nan] * 10, False, "rainfall_all_missing", 0, np.nan),
    ([0] * 10, False, "rainfall_all_zero", 4, 0),
    ([0, 0, 2, 0, 2, 0, 0, 0, 0, 0], False, "observed_rainfall_below_threshold", 4, 4),
    ([0, np.nan, 2, 0, 0, 0, 0, 0, 0, 0], False, "observed_rainfall_below_threshold", 3, 2),
    ([0, 0, 0, 0, 0, 100, 100, 0, 0, 0], False, "rainfall_all_zero", 4, 0),
])
def test_screening_rules_and_no_post_peak_support(extractor, rain, kept, reason, valid, total):
    module, cfg = extractor
    flow = sample_flow()
    rainfall = pd.Series(rain, index=flow.time)
    summary, process, annotated, audit = module.event_products(flow, [EVENT], 3600, cfg, rainfall)
    row = audit.iloc[0]
    assert bool(row.retained) == kept
    assert row.reason == reason and row.valid_steps == valid
    assert row.expected_steps == 4 and row.missing_steps == 4 - valid
    assert bool(row.has_missing_rainfall) == (valid < 4)
    assert pd.isna(row.observed_rainfall_mm) if pd.isna(total) else row.observed_rainfall_mm == total
    assert len(summary) == int(kept)
    assert process.empty == (not kept)
    assert annotated.event_id.notna().sum() == (4 if kept else 0)
    assert pd.notna(row.event_id) == kept
    assert all(check["status"] == "PASS" for check in module.qc_events(summary, process, annotated, cfg))


def test_window_endpoints_fractional_lookback_and_unclipped_antecedent(extractor):
    module, cfg = extractor
    flow = sample_flow()
    # Exact endpoints are included, samples just outside are excluded.
    rain = pd.Series([100, 2, 0, 0, 3, 100, 0, 0, 0, 0], index=flow.time)
    evidence = module.rainfall_evidence(flow, EVENT, rain, 3600, cfg)
    assert evidence["observed_rainfall_mm"] == 5 and evidence["retained"]
    evidence = module.rainfall_evidence(flow, EVENT, rain, 3600, replace(cfg, rainfall_lookback_hours=1.5))
    assert evidence["expected_steps"] == 3 and evidence["observed_rainfall_mm"] == 3
    evidence = module.rainfall_evidence(flow, EVENT, rain, 3600, replace(cfg, rainfall_lookback_hours=5))
    assert evidence["expected_steps"] == 7 and evidence["missing_steps"] == 2
    evidence = module.rainfall_evidence(flow, EVENT, rain, 3600, replace(cfg, rainfall_lookback_hours=0))
    assert evidence["expected_steps"] == 2 and evidence["observed_rainfall_mm"] == 3


def write_rain(tmp_path, frame, **overrides):
    path, metadata_path = tmp_path / "rain.csv", tmp_path / "metadata.json"
    frame.to_csv(path, index=False)
    metadata = {"spatial_scope": "basin_mean", "unit": "mm/step", "timezone": "UTC",
                "timestep_seconds": 3600, "timestamp_semantics": "interval_end", **overrides}
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    return path, metadata_path


def test_rainfall_timezone_start_semantics_gaps_and_empty(extractor, tmp_path):
    module, cfg = extractor
    flow = sample_flow()
    times = (flow.time - pd.Timedelta(hours=1)).dt.tz_convert("Asia/Shanghai")
    path, meta = write_rain(tmp_path, pd.DataFrame({"time": times, "P_mm": np.arange(10)}).drop(index=2),
                            timezone="Asia/Shanghai", timestamp_semantics="interval_start")
    rain = module.load_rainfall(path, meta, flow.time, 3600)
    assert rain.loc[flow.time[1]] == 1 and flow.time[2] not in rain.index
    assert rain.loc[flow.time[9]] == 9
    path, meta = write_rain(tmp_path, pd.DataFrame(columns=["time", "P_mm"]))
    rain = module.load_rainfall(path, meta, flow.time, 3600)
    evidence = module.rainfall_evidence(flow, EVENT, rain, 3600, cfg)
    assert evidence["reason"] == "rainfall_all_missing" and evidence["missing_steps"] == 4


@pytest.mark.parametrize("problem", ["negative", "infinite", "nonnumeric", "duplicate", "invalid_time", "naive",
                                     "timezone", "invalid_zone", "step", "grid", "unit", "scope", "semantics"])
def test_bad_rainfall_inputs_fail(extractor, tmp_path, problem):
    module, _ = extractor
    flow = sample_flow()
    frame = pd.DataFrame({"time": flow.time.astype(str), "P_mm": np.ones(10)}).astype({"P_mm": object})
    overrides = {}
    if problem in {"negative", "infinite", "nonnumeric"}:
        frame.loc[1, "P_mm"] = {"negative": -1, "infinite": np.inf, "nonnumeric": "bad"}[problem]
    elif problem in {"duplicate", "invalid_time", "naive", "grid"}:
        frame.loc[1, "time"] = {"duplicate": frame.loc[0, "time"], "invalid_time": "bad", "naive": "2020-01-01T01:00:00",
                                 "grid": "2020-01-01T01:30:00+00:00"}[problem]
    else:
        overrides = {"timezone": {"timezone": "Asia/Shanghai"}, "invalid_zone": {"timezone": "invalid/zone"},
                     "step": {"timestep_seconds": 7200}, "unit": {"unit": "mm/day"},
                     "scope": {"spatial_scope": "station"}, "semantics": {"timestamp_semantics": "instant"}}[problem]
    path, meta = write_rain(tmp_path, frame, **overrides)
    with pytest.raises(module.ContractError):
        module.load_rainfall(path, meta, flow.time, 3600)


@pytest.mark.parametrize("problem", ["old_version", "missing_section", "zero", "negative_lookback", "nan", "bool", "unknown"])
def test_new_config_schema_and_parser_agree(extractor, repo_root, tmp_path, problem):
    module, _ = extractor
    path = tmp_path / "config.yaml"
    _write_config(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if problem == "old_version":
        raw["schema_version"] = "1.0"
    elif problem == "missing_section":
        del raw["rainfall_support"]
    else:
        key, value = {"zero": ("min_observed_rainfall_mm", 0), "negative_lookback": ("lookback_hours", -1),
                      "nan": ("min_observed_rainfall_mm", np.nan), "bool": ("lookback_hours", True),
                      "unknown": ("unknown", 1)}[problem]
        raw["rainfall_support"][key] = value
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(module.ContractError, match="迁移" if problem == "old_version" else None):
        module.load_config(path)
    if problem != "nan":  # JSON schema applies to JSON numbers; runtime additionally rejects YAML NaN.
        schema = json.loads((repo_root / "resources/schemas/flood-event-config.schema.json").read_text())
        assert list(Draft202012Validator(schema).iter_errors(raw))


@pytest.mark.integration
def test_partial_gaps_rejected_events_renumber_and_zero_outputs(repo_root, tmp_path):
    root = tmp_path / "pipeline"
    _, baseflow_dir, original = _pipeline(repo_root, root)
    original_summary = pd.read_csv(original / "event_summary.csv")
    assert len(original_summary) == 2
    rain_path, meta = root / "流域 雨量.csv", root / "雨量 元数据.json"
    rain = pd.read_csv(rain_path)
    rain["P_mm"] = 0.0
    second_peak = int(original_summary.iloc[1].peak_idx)
    rain_peak = int(rain.index[pd.to_datetime(rain.time, utc=True) == pd.Timestamp(original_summary.iloc[1].peak_time)][0])
    rain.loc[rain_peak, "P_mm"] = 5.0
    rain.loc[rain_peak - 1, "P_mm"] = np.nan
    rain = rain.drop(index=rain_peak - 2)
    rain.to_csv(rain_path, index=False)
    def extract(output):
        return _run(repo_root, "extract", "--baseflow-result", baseflow_dir / "result.json",
                    "--config", root / "事件 参数（显式）.yaml", "--rainfall", rain_path,
                    "--rainfall-metadata", meta, "--output-dir", output)
    output = root / "screened"
    result = extract(output)
    assert result.returncode == 0, result.stderr
    document = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert document["status"] == "warning" and document["parameters"]["partial_rainfall_event_count"] == 1
    assert document["parameters"]["rainfall_rejected_event_count"] == 1
    audit = pd.read_csv(output / "rainfall_screening.csv")
    assert audit.retained.tolist() == [False, True]
    assert pd.isna(audit.iloc[0].event_id) and audit.iloc[1].event_id == 1
    assert audit.iloc[1].missing_steps == 2
    summary = pd.read_csv(output / "event_summary.csv")
    assert summary.event_id.tolist() == [1] and summary.iloc[0].peak_idx == second_peak
    assert len(list((output / "event_files").glob("*.csv"))) == 1
    series = pd.read_csv(output / "annotated_series.csv")
    first = original_summary.iloc[0]
    assert series.loc[int(first.start_idx):int(first.end_idx), "event_id"].isna().all()
    for name, path in [("rainfall", rain_path), ("rainfall_metadata", meta)]:
        assert document["inputs"][name]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    schema = json.loads((repo_root / "resources/schemas/data-processing-run-result.schema.json").read_text())
    Draft202012Validator(schema).validate(document)
    atlas = _run(repo_root, "atlas", "--extract-result", output / "result.json", "--language", "en",
                 "--output-dir", root / "atlas")
    assert atlas.returncode == 0, atlas.stderr
    rain["P_mm"] = 0
    rain.to_csv(rain_path, index=False)
    empty_output = root / "empty"
    result = extract(empty_output)
    assert result.returncode == 0, result.stderr
    empty = json.loads((empty_output / "result.json").read_text(encoding="utf-8"))
    assert empty["status"] == "warning" and empty["parameters"]["final_event_count"] == 0
    assert pd.read_csv(empty_output / "event_summary.csv").empty
    assert pd.read_csv(empty_output / "event_process.csv").empty
    assert pd.read_csv(empty_output / "annotated_series.csv").event_id.isna().all()
    assert not list((empty_output / "event_files").glob("*.csv"))
    assert len(pd.read_csv(empty_output / "rainfall_screening.csv")) == 2


def test_packaged_extractor_runs_without_repository(repo_root, tmp_path, utf8_env):
    from test_installer import _run_engine
    target = tmp_path / "installed"
    result = _run_engine(repo_root, target, "--categories", "data-processing", environment=utf8_env)
    assert result.returncode == 0, result.stderr
    root = tmp_path / "pipeline"
    _, baseflow, _ = _pipeline(repo_root, root)
    skill = target / "hydro-data-processing-extract-flood-events"
    script = skill / "scripts/extract_flood_events.py"
    result = subprocess.run([sys.executable, str(script), "--baseflow-result", str(baseflow / "result.json"),
                             "--config", str(root / "事件 参数（显式）.yaml"), "--rainfall", str(root / "流域 雨量.csv"),
                             "--rainfall-metadata", str(root / "雨量 元数据.json"), "--output-dir", str(tmp_path / "out")],
                            cwd=tmp_path, env=utf8_env, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert len(pd.read_csv(tmp_path / "out/rainfall_screening.csv")) == 2


def test_example_config_matches_schema_and_loader(extractor, repo_root):
    module, _ = extractor
    path = repo_root / "data-processing/extract-flood-events/examples/flood_event_config.source-equivalent.yaml"
    _, raw = module.load_config(path)
    schema = json.loads((repo_root / "resources/schemas/flood-event-config.schema.json").read_text())
    Draft202012Validator(schema).validate(raw)


@pytest.mark.parametrize("table_format", ["csv", "parquet", "xlsx"])
def test_rainfall_table_formats_preserve_missing(extractor, tmp_path, table_format):
    module, _ = extractor
    flow = sample_flow()
    frame = pd.DataFrame({"time": flow.time.astype(str), "P_mm": [0, np.nan, 3, 0, 2, 0, 0, 0, 0, 0]})
    _, meta = write_rain(tmp_path, frame)
    path = tmp_path / f"rain.{table_format}"
    module.write_table(frame, path, table_format)
    rain = module.load_rainfall(path, meta, flow.time, 3600)
    assert pd.isna(rain.loc[flow.time[1]]) and rain.loc[flow.time[2]] == 3


def test_no_candidates_and_scale_filter_precede_rainfall_audit(extractor):
    module, cfg = extractor
    flow = sample_flow()
    rain = pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    for events, settings in [([], cfg), ([EVENT], replace(cfg, min_peak_flow_m3_s=100))]:
        summary, process, annotated, audit = module.event_products(flow, events, 3600, settings, rain)
        assert summary.empty and process.empty and audit.empty
        assert audit.columns.tolist() == module.SCREENING_COLUMNS
        assert annotated.event_id.isna().all()


def test_non_object_metadata_and_rainfall_sum_overflow(extractor, tmp_path):
    module, cfg = extractor
    flow = sample_flow()
    path, meta = write_rain(tmp_path, pd.DataFrame({"time": flow.time.astype(str), "P_mm": np.ones(10)}))
    meta.write_text("[]", encoding="utf-8")
    with pytest.raises(module.ContractError, match="JSON"):
        module.load_rainfall(path, meta, flow.time, 3600)
    with np.errstate(over="ignore"), pytest.raises(module.ContractError, match="溢出"):
        module.rainfall_evidence(flow, EVENT, pd.Series([1e308] * 10, index=flow.time), 3600, cfg)
