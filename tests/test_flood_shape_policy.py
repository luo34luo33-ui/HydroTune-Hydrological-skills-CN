from __future__ import annotations

from dataclasses import replace
import json

from jsonschema import Draft202012Validator
import numpy as np
import pandas as pd
import pytest
import yaml

from test_flood_rainfall_support import extractor, sample_flow, EVENT
from test_timeseries_examples import _pipeline, _run, _write_config


def flow_frame(values):
    values = np.asarray(values, dtype=float)
    return pd.DataFrame({
        "time": pd.date_range("2020-01-01", periods=len(values), freq="h", tz="UTC"),
        "discharge_m3_s": values, "baseflow_m3_s": np.minimum(values, 100),
        "quickflow_m3_s": values - np.minimum(values, 100),
    })


def whole_event(flow):
    return {"start_idx": 0, "peak_idx": int(flow.discharge_m3_s.to_numpy().argmax()), "end_idx": len(flow) - 1}


@pytest.mark.parametrize("values", [
    [200, 441.8] + [412] * 329 + [200],  # Sudden rise, one-point overshoot, long line.
    [412] * 210 + [580, 583.8, 580] + [380, 390, 375, 385, 365, 375, 350, 360, 345, 370, 350],
])
@pytest.mark.parametrize("rain_kind", ["supported", "zero", "missing"])
def test_image_like_shapes_rejected_even_with_rain_or_keep_policies(extractor, values, rain_kind):
    module, cfg = extractor
    cfg = replace(cfg, no_rainfall_policy="keep", missing_rainfall_policy="keep")
    flow = flow_frame(values)
    rain_times = pd.date_range(flow.time.iloc[0] - pd.Timedelta(hours=2), periods=len(flow) + 2, freq="h")
    rainfall = pd.Series(5.0 if rain_kind == "supported" else 0.0 if rain_kind == "zero" else np.nan, index=rain_times)
    before = flow.copy(deep=True)
    summary, process, annotated, audit = module.event_products(flow, [whole_event(flow)], 3600, cfg, rainfall)
    assert audit.iloc[0].shape_rejected and audit.iloc[0].reason == "abrupt_jump_long_plateau"
    assert audit.iloc[0].rainfall_accepted
    assert bool(audit.iloc[0].rainfall_supported) == (rain_kind == "supported")
    assert audit.iloc[0].plateau_duration_hours >= 24 and audit.iloc[0].plateau_fraction >= 0.5
    assert summary.empty and process.empty and annotated.event_id.isna().all()
    assert pd.isna(audit.iloc[0].event_id)
    pd.testing.assert_frame_equal(flow, before)


@pytest.mark.parametrize("values", [
    np.r_[np.linspace(100, 400, 25), np.linspace(390, 100, 70)],
    np.r_[np.linspace(100, 400, 25), np.linspace(390, 200, 20), np.linspace(205, 450, 25), np.linspace(440, 100, 40)],
    np.r_[[100, 300, 500], np.linspace(490, 100, 90)],
    np.r_[np.linspace(100, 400, 10), np.full(20, 400), np.linspace(390, 100, 50)],
    np.r_[np.full(70, 400), np.linspace(400, 420, 40)],  # Long flat segment, no abrupt jump.
    np.r_[np.full(30, 400), [600], np.linspace(590, 100, 100)],  # Plateau is not dominant.
])
def test_normal_single_multiple_fast_short_flat_and_slow_shapes_survive(extractor, values):
    module, cfg = extractor
    flow = flow_frame(values)
    event = whole_event(flow)
    shape = module.shape_evidence(flow, event, 3600, cfg)
    assert not shape["shape_rejected"]
    summary, _, _, audit = module.event_products(flow, [event], 3600, cfg, pd.Series(5.0, index=flow.time))
    assert len(summary) == 1 and audit.iloc[0].retained


def test_inclusive_shape_thresholds_and_custom_overrides(extractor):
    module, cfg = extractor
    flow = flow_frame(np.r_[[100], np.full(25, 200), np.linspace(190, 100, 23)])
    event = whole_event(flow)
    cfg = replace(cfg, min_jump_fraction=0.5, plateau_range_fraction=0)
    shape = module.shape_evidence(flow, event, 3600, cfg)
    assert shape["plateau_duration_hours"] == 24 and shape["plateau_fraction"] == 0.5
    assert shape["max_jump_fraction"] == 0.5 and shape["shape_rejected"]
    for key, value in [("min_plateau_duration_hours", 24.001), ("min_plateau_fraction", 0.501), ("min_jump_fraction", 0.501)]:
        assert not module.shape_evidence(flow, event, 3600, replace(cfg, **{key: value}))["shape_rejected"]
    noisy = flow.copy()
    noisy.loc[14:25, "discharge_m3_s"] = 201
    at_tolerance = module.shape_evidence(noisy, event, 3600, replace(cfg, plateau_range_fraction=0.005))
    assert at_tolerance["plateau_range_m3_s"] == 1 and at_tolerance["shape_rejected"]
    assert not module.shape_evidence(noisy, event, 3600, replace(cfg, plateau_range_fraction=0.0049))["shape_rejected"]


def test_warmup_excluded_ties_earliest_and_zero_reference(extractor):
    module, cfg = extractor
    # Long jump/plateau only in warm-up must not invalidate the normal event.
    values = np.r_[[100, 400], np.full(100, 400), np.linspace(100, 500, 30), np.linspace(490, 100, 40)]
    flow = flow_frame(values)
    event = {"start_idx": 102, "peak_idx": 131, "end_idx": len(flow) - 1}
    assert not module.shape_evidence(flow, event, 3600, cfg)["shape_rejected"]
    tied = flow_frame([100] * 30 + [200] * 30)
    evidence = module.shape_evidence(tied, whole_event(tied), 3600, cfg)
    assert evidence["plateau_start_idx"] == 0 and evidence["plateau_end_idx"] == 29
    zeros = flow_frame([0] * 30)
    evidence = module.shape_evidence(zeros, whole_event(zeros), 3600, cfg)
    assert evidence["shape_reference_flow_m3_s"] == 1e-9
    assert evidence["max_jump_fraction"] == 0 and not evidence["shape_rejected"]


def test_jump_window_checks_two_steps_and_is_configurable(extractor):
    module, cfg = extractor
    flow = flow_frame([400] * 50 + [430, 460, 460])
    event = whole_event(flow)
    assert module.shape_evidence(flow, event, 3600, cfg)["shape_rejected"]
    evidence = module.shape_evidence(flow, event, 3600, replace(cfg, jump_window_steps=1))
    assert evidence["max_jump_change_m3_s"] == 30 and not evidence["shape_rejected"]


@pytest.mark.parametrize("no_policy,missing_policy", [("keep", "keep"), ("keep", "exclude"), ("exclude", "keep"), ("exclude", "exclude")])
@pytest.mark.parametrize("rain_kind", ["zero", "missing", "partial_zero", "little_rain", "partial_supported"])
def test_independent_rainfall_choices_and_partial_or_low_rain(extractor, no_policy, missing_policy, rain_kind):
    module, cfg = extractor
    cfg = replace(cfg, no_rainfall_policy=no_policy, missing_rainfall_policy=missing_policy)
    flow = sample_flow()
    values = np.zeros(10)
    if rain_kind == "missing":
        values[:] = np.nan
    elif rain_kind == "partial_zero":
        values[2] = np.nan
    elif rain_kind == "little_rain":
        values[2] = 1
    elif rain_kind == "partial_supported":
        values[2] = np.nan
        values[4] = 5
    summary, _, _, audit = module.event_products(flow, [EVENT], 3600, cfg, pd.Series(values, index=flow.time))
    expected = (rain_kind == "partial_supported" or rain_kind == "zero" and no_policy == "keep"
                or rain_kind == "missing" and missing_policy == "keep")
    assert len(summary) == int(expected) and bool(audit.iloc[0].retained) == expected
    assert bool(audit.iloc[0].rainfall_supported) == (rain_kind == "partial_supported")
    if expected and rain_kind != "partial_supported":
        assert audit.iloc[0].reason == ("no_rainfall_kept_by_policy" if rain_kind == "zero" else "missing_rainfall_kept_by_policy")
    if rain_kind in {"partial_zero", "little_rain"}:
        assert audit.iloc[0].reason == "observed_rainfall_below_threshold"


@pytest.mark.parametrize("problem", ["old_1.1", "missing_no", "missing_missing", "invalid_policy", "null_shape", "extra_shape",
                                     "duration_zero", "fraction_zero", "fraction_over", "negative_range", "range_one",
                                     "jump_zero", "step_zero", "step_fraction", "nan", "bool"])
def test_config_rejects_missing_choices_old_versions_and_invalid_shape(extractor, repo_root, tmp_path, problem):
    module, _ = extractor
    path = tmp_path / "config.yaml"
    _write_config(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["shape_filter"] = {}
    if problem == "old_1.1":
        raw["schema_version"] = "1.1"
    elif problem in {"missing_no", "missing_missing"}:
        del raw["rainfall_support"]["no_rainfall_policy" if problem == "missing_no" else "missing_rainfall_policy"]
    elif problem == "invalid_policy":
        raw["rainfall_support"]["no_rainfall_policy"] = "ask"
    elif problem == "null_shape":
        raw["shape_filter"] = None
    else:
        key, value = {"extra_shape": ("unknown", 1), "duration_zero": ("min_plateau_duration_hours", 0),
                      "fraction_zero": ("min_plateau_fraction", 0), "fraction_over": ("min_plateau_fraction", 1.1),
                      "negative_range": ("plateau_range_fraction", -0.1), "range_one": ("plateau_range_fraction", 1),
                      "jump_zero": ("min_jump_fraction", 0), "step_zero": ("jump_window_steps", 0),
                      "step_fraction": ("jump_window_steps", 1.5), "nan": ("min_jump_fraction", float("nan")),
                      "bool": ("jump_window_steps", True)}[problem]
        raw["shape_filter"][key] = value
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(module.ContractError):
        module.load_config(path)
    if problem != "nan":
        schema = json.loads((repo_root / "resources/schemas/flood-event-config.schema.json").read_text())
        assert list(Draft202012Validator(schema).iter_errors(raw))


def test_default_and_partial_shape_config_normalized(extractor, repo_root, tmp_path):
    module, _ = extractor
    path = tmp_path / "config.yaml"
    _write_config(path)
    cfg, normalized = module.load_config(path)
    assert normalized["shape_filter"] == module.SHAPE_DEFAULTS and cfg.jump_window_steps == 2
    normalized["shape_filter"] = {"min_plateau_duration_hours": 48}
    path.write_text(yaml.safe_dump(normalized), encoding="utf-8")
    cfg, normalized = module.load_config(path)
    assert cfg.min_plateau_duration_hours == 48 and cfg.plateau_range_fraction == 0.005
    schema = json.loads((repo_root / "resources/schemas/flood-event-config.schema.json").read_text())
    Draft202012Validator(schema).validate(normalized)


@pytest.mark.parametrize("table_format", ["csv", "parquet", "xlsx"])
def test_policy_keep_end_to_end_formats_warning_qc_and_zero(repo_root, tmp_path, table_format):
    root = tmp_path / "pipeline"
    _, baseflow, _ = _pipeline(repo_root, root)
    rain_path = root / "流域 雨量.csv"
    rain = pd.read_csv(rain_path)
    rain["P_mm"] = 0
    rain.to_csv(rain_path, index=False)
    config = root / "事件 参数（显式）.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["rainfall_support"]["no_rainfall_policy"] = "keep"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    def extract(output, overwrite=False):
        return _run(repo_root, "extract", "--baseflow-result", baseflow / "result.json", "--config", config,
                    "--rainfall", rain_path, "--rainfall-metadata", root / "雨量 元数据.json",
                    "--output-format", table_format, "--per-event-format", table_format,
                    "--output-dir", output, *(["--overwrite"] if overwrite else []))
    output = root / "policies"
    result = extract(output)
    assert result.returncode == 0, result.stderr
    doc = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert doc["status"] == "warning" and doc["parameters"]["policy_retained_event_count"] == 2
    assert doc["parameters"]["no_rainfall_policy"] == "keep"
    assert doc["parameters"]["shape_filter"]["jump_window_steps"] == 2
    assert set(pd.read_csv(output / "event_qc.csv").status) == {"PASS"}
    audit = pd.read_csv(output / "rainfall_screening.csv")
    assert audit.event_id.tolist() == [1, 2] and audit.reason.tolist() == ["no_rainfall_kept_by_policy"] * 2
    assert not audit.rainfall_supported.any() and audit.retained.all()
    if table_format == "csv":
        summary = pd.read_csv(output / "event_summary.csv")
    elif table_format == "parquet":
        summary = pd.read_parquet(output / "event_summary.parquet")
    else:
        summary = pd.read_excel(output / "flood_event_dataset.xlsx", sheet_name="event_summary")
    assert len(summary) == 2 and not summary.rainfall_supported.any()
    assert len(list((output / "event_files").glob(f"*.{table_format}"))) == 2
    exported = yaml.safe_load((output / "event_config.yaml").read_text(encoding="utf-8"))
    assert exported["shape_filter"]["min_plateau_fraction"] == 0.5
    # A header-only rainfall table applies the separate missing policy.
    rain.iloc[:0].to_csv(rain_path, index=False)
    result = extract(output, True)
    assert result.returncode == 0, result.stderr
    doc = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert doc["status"] == "warning" and doc["parameters"]["final_event_count"] == 0
    assert not list((output / "event_files").glob("*"))
    raw["rainfall_support"]["missing_rainfall_policy"] = "keep"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    result = extract(output, True)
    assert result.returncode == 0, result.stderr
    doc = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert doc["status"] == "warning" and doc["parameters"]["policy_retained_event_count"] == 2
    assert doc["parameters"]["partial_rainfall_event_count"] == 0
    assert pd.read_csv(output / "rainfall_screening.csv").reason.tolist() == ["missing_rainfall_kept_by_policy"] * 2
    assert set(pd.read_csv(output / "event_qc.csv").status) == {"PASS"}


def test_abrupt_plateau_removed_by_real_extraction_pipeline_and_ids_stable(repo_root, tmp_path):
    index = np.arange(700)
    values = 200.0 + 300 * np.exp(-0.5 * ((index - 550) / 8) ** 2)
    values[50] = 441.8
    values[51:350] = 412
    frame = pd.DataFrame({"time": pd.date_range("2020-01-01", periods=len(values), freq="h", tz="UTC"), "Q": values})
    source = tmp_path / "source.csv"
    frame.to_csv(source, index=False)
    prepared, separated, output = tmp_path / "prepared", tmp_path / "baseflow", tmp_path / "events"
    result = _run(repo_root, "prepare", "--input", source, "--time-column", "time", "--flow-column", "Q",
                  "--flow-unit", "m3/s", "--timezone", "UTC", "--output-dir", prepared)
    assert result.returncode == 0, result.stderr
    result = _run(repo_root, "baseflow", "--prepare-result", prepared / "result.json", "--bfi-max", "0.8",
                  "--alpha", "0.95", "--output-dir", separated)
    assert result.returncode == 0, result.stderr
    config = tmp_path / "config.yaml"
    _write_config(config)
    rain_path = tmp_path / "rain.csv"
    pd.DataFrame({"time": pd.date_range("2019-12-31", periods=724, freq="h", tz="UTC"), "P_mm": 5}).to_csv(rain_path, index=False)
    meta = tmp_path / "metadata.json"
    meta.write_text(json.dumps({"spatial_scope": "basin_mean", "unit": "mm/step", "timezone": "UTC",
                                "timestep_seconds": 3600, "timestamp_semantics": "interval_end"}), encoding="utf-8")
    result = _run(repo_root, "extract", "--baseflow-result", separated / "result.json", "--config", config,
                  "--rainfall", rain_path, "--rainfall-metadata", meta, "--output-dir", output)
    assert result.returncode == 0, result.stderr
    audit = pd.read_csv(output / "rainfall_screening.csv")
    assert audit.reason.tolist() == ["abrupt_jump_long_plateau", "rainfall_supported"]
    assert audit.retained.tolist() == [False, True] and audit.iloc[1].event_id == 1
    summary = pd.read_csv(output / "event_summary.csv")
    # Existing detection uses the quickflow peak, which can precede the total-flow peak.
    assert summary.event_id.tolist() == [1] and 540 <= summary.iloc[0].peak_idx <= 560
    assert summary.iloc[0].peak_idx == audit.iloc[1].peak_idx
    assert len(list((output / "event_files").glob("*.csv"))) == 1
    series = pd.read_csv(output / "annotated_series.csv")
    rejected = audit.iloc[0]
    assert series.loc[int(rejected.start_idx):int(rejected.end_idx), "event_id"].isna().all()
    np.testing.assert_allclose(series.discharge_m3_s, values)
    doc = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert doc["parameters"]["shape_rejected_event_count"] == 1
    assert doc["parameters"]["rainfall_rejected_event_count"] == 0
    assert doc["parameters"]["scale_filtered_event_count"] == 2 and doc["parameters"]["final_event_count"] == 1
    assert set(pd.read_csv(output / "event_qc.csv").status) == {"PASS"}
