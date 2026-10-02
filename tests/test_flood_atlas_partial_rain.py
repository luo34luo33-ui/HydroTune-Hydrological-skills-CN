from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from test_timeseries_examples import SCRIPTS, _load_module, _pipeline, _run


@pytest.fixture
def atlas(repo_root):
    module = _load_module(repo_root / SCRIPTS["atlas"], "partial_rain_atlas_test")
    common = sys.modules["_event_atlas_common"]
    return module, sys.modules["_flood_plot"], common, common.load_style(repo_root / SCRIPTS["atlas"])


def test_gaps_partial_coverage_and_zero_kept_as_distinct_values(atlas, tmp_path):
    module, _, _, _ = atlas
    times = pd.date_range("2020-01-01", periods=6, freq="h", tz="UTC")
    series = pd.DataFrame({"time": times})
    path, meta = tmp_path / "rain.csv", tmp_path / "meta.json"
    pd.DataFrame({"time": times[[1, 2, 4]], "P_mm": [0, np.nan, 3]}).to_csv(path, index=False)
    meta.write_text(json.dumps({"spatial_scope": "basin_mean", "unit": "mm/step", "timezone": "UTC",
                                "timestamp_semantics": "interval_end", "timestep_seconds": 3600}), encoding="utf-8")
    args = SimpleNamespace(rainfall=path, rainfall_metadata=meta)
    rain, step, _ = module.load_rainfall(args, series, series.iloc[:1], {"parameters": {"timestep_seconds": 3600}})
    assert rain.time.tolist() == times.tolist() and step == 3600
    assert rain.P_mm.isna().tolist() == [True, False, True, True, False, True]
    assert rain.P_mm.dropna().tolist() == [0, 3]
    pd.DataFrame(columns=["time", "P_mm"]).to_csv(path, index=False)
    rain, _, _ = module.load_rainfall(args, series, series.iloc[:1], {})
    assert rain.P_mm.isna().all()


@pytest.mark.parametrize("values,overview", [([np.nan, 0, 3, np.nan], False), ([np.nan] * 3600, True),
                                           ([np.nan, np.nan, 2, np.nan] + [1] * 3596, True)])
def test_only_observed_bars_and_no_zero_bins_or_axis_for_all_missing(atlas, values, overview):
    module, plot, common, style = atlas
    rain = pd.DataFrame({"time": pd.date_range("2020-01-01", periods=len(values), freq="h", tz="UTC"), "P_mm": values})
    figure, axis = common.plt.subplots()
    try:
        handle, layers = plot._rainfall(axis, rain, 3600, style, module.TEXT["en"], overview=overview)
        if not rain.P_mm.notna().any():
            assert handle is None and not layers and len(figure.axes) == 1
        else:
            heights = [bar.get_height() for bar in figure.axes[1].patches]
            assert np.isfinite(heights).all()
            if overview:
                assert len(heights) == 1799 and heights[0] == 2  # Missing bin omitted; partial bin observed subtotal.
                assert "partial bins show observed subtotal" in layers[0]["display_transform"]
            else:
                assert heights == [0, 3]
    finally:
        common.plt.close(figure)


@pytest.mark.parametrize("mode", ["partial", "empty"])
def test_partial_or_absent_coverage_renders_real_atlas_without_stopping(repo_root, tmp_path, mode):
    root = tmp_path / "pipeline"
    _, _, events = _pipeline(repo_root, root)
    rain_path = root / "流域 雨量.csv"
    rain = pd.read_csv(rain_path)
    rain = rain.iloc[50:55].copy() if mode == "partial" else rain.iloc[:0]
    if mode == "partial":
        rain.iloc[2, rain.columns.get_loc("P_mm")] = np.nan
    rain.to_csv(rain_path, index=False)
    output = tmp_path / "atlas"
    result = _run(repo_root, "atlas", "--extract-result", events / "result.json", "--rainfall", rain_path,
                  "--rainfall-metadata", root / "雨量 元数据.json", "--language", "en", "--output-dir", output)
    assert result.returncode == 0, result.stderr
    document = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert document["status"] == "warning" and any("only observed" in warning for warning in document["warnings"])
    assert len(list(output.glob("*.png"))) == 3
    overview = json.loads((output / "flood-event-overview.figure.json").read_text(encoding="utf-8"))
    rain_layers = [layer for layer in overview["layers"] if layer["id"] == "basin-rainfall"]
    assert bool(rain_layers) == (mode == "partial")
