from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from jsonschema import Draft202012Validator
import numpy as np
import pandas as pd
from PIL import Image
import pytest
import yaml


SCRIPTS = {
    "prepare": Path("data-processing/prepare-discharge-timeseries/examples/prepare_discharge_timeseries.py"),
    "baseflow": Path("data-processing/separate-baseflow-eckhardt/examples/separate_baseflow_eckhardt.py"),
    "extract": Path("data-processing/extract-flood-events/examples/extract_flood_events.py"),
    "atlas": Path("visualization-reporting/visualize-flood-event-atlas/examples/render_flood_event_atlas.py"),
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


def _load_module(path: Path, name: str):
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def _source_frame(rows: int = 240) -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    discharge = 18.0 + 0.006 * index
    discharge += 170.0 * np.exp(-0.5 * ((index - 70.0) / 7.0) ** 2)
    discharge += 125.0 * np.exp(-0.5 * ((index - 175.0) / 10.0) ** 2)
    return pd.DataFrame({
        "when": pd.date_range("2020-01-01", periods=rows, freq="h"),
        "flow": discharge,
        "station_note": ["unconfirmed"] * rows,
    })


def _write_config(path: Path) -> None:
    document = {
        "schema_version": "1.0",
        "peak_detection": {"peak_quantile": 0.7, "prominence_factor": 0.05, "min_peak_distance_hours": 24},
        "boundaries": {"boundary_fraction": 0.05, "boundary_persistence_steps": 2, "max_search_days": 4},
        "merging": {"merge_gap_hours": 12, "valley_ratio_threshold": 0.8},
        "filters": {"min_event_duration_hours": 1, "min_peak_flow_m3_s": None, "min_event_volume_m3": None},
        "warmup": {"steps": 12},
    }
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def _pipeline(repo: Path, root: Path, output_format: str = "csv") -> tuple[Path, Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    source = root / "输入 时序（合成）.csv"
    _source_frame().to_csv(source, index=False, encoding="utf-8-sig")
    prepare_dir = root / "01 准备（结果）"
    prepared = _run(
        repo, "prepare", "--input", source, "--time-column", "when", "--flow-column", "flow",
        "--flow-unit", "m3/s", "--timezone", "UTC", "--output-format", output_format,
        "--output-dir", prepare_dir,
    )
    assert prepared.returncode == 0, prepared.stderr
    baseflow_dir = root / "02 基流（结果）"
    separated = _run(
        repo, "baseflow", "--prepare-result", prepare_dir / "result.json", "--bfi-max", "0.8",
        "--alpha", "0.95", "--output-format", output_format, "--output-dir", baseflow_dir,
    )
    assert separated.returncode == 0, separated.stderr
    config = root / "事件 参数（显式）.yaml"
    _write_config(config)
    event_dir = root / "03 事件（结果）"
    extracted = _run(
        repo, "extract", "--baseflow-result", baseflow_dir / "result.json", "--config", config,
        "--output-format", output_format, "--per-event-format", "csv", "--output-dir", event_dir,
    )
    assert extracted.returncode == 0, extracted.stderr
    return prepare_dir, baseflow_dir, event_dir


@pytest.mark.parametrize("key", sorted(SCRIPTS))
def test_all_timeseries_examples_have_help(repo_root: Path, key: str) -> None:
    result = _run(repo_root, key, "--help")
    assert result.returncode == 0, result.stderr
    assert "--output-dir" in result.stdout


@pytest.mark.parametrize("table_format", ["csv", "parquet", "xlsx"])
def test_prepare_reads_and_writes_each_supported_format(repo_root: Path, tmp_path: Path, table_format: str) -> None:
    frame = _source_frame(24)
    source = tmp_path / f"source.{table_format}"
    if table_format == "csv":
        frame.to_csv(source, index=False, encoding="utf-8-sig")
    elif table_format == "parquet":
        frame.to_parquet(source, index=False)
    else:
        frame.to_excel(source, index=False)
    output = tmp_path / f"out-{table_format}"
    result = _run(
        repo_root, "prepare", "--input", source, "--time-column", "when", "--flow-column", "flow",
        "--flow-unit", "L/s", "--timezone", "UTC", "--output-format", table_format,
        "--output-dir", output,
    )
    assert result.returncode == 0, result.stderr
    artifact = json.loads((output / "result.json").read_text(encoding="utf-8"))["artifacts"]["discharge_timeseries"]["path"]
    assert (output / artifact).is_file()
    metadata = json.loads((output / "series_metadata.json").read_text(encoding="utf-8"))
    assert metadata["additional_column_semantics"] == "unconfirmed"
    assert metadata["unit_scale"] == pytest.approx(0.001)


def test_prepare_enforces_missing_duplicate_negative_and_regular_time_rules(repo_root: Path, tmp_path: Path) -> None:
    base = _source_frame(12)
    cases = {
        "missing": base.assign(flow=lambda frame: frame["flow"].mask(frame.index == 5)),
        "duplicate": base.assign(when=lambda frame: frame["when"].mask(frame.index == 6, frame.loc[5, "when"])),
        "negative": base.assign(flow=lambda frame: frame["flow"].mask(frame.index == 5, -1.0)),
        "irregular": base.drop(index=5).reset_index(drop=True),
    }
    for name, frame in cases.items():
        source = tmp_path / f"{name}.csv"
        frame.to_csv(source, index=False)
        result = _run(
            repo_root, "prepare", "--input", source, "--time-column", "when", "--flow-column", "flow",
            "--flow-unit", "m3/s", "--timezone", "UTC", "--output-dir", tmp_path / f"out-{name}",
        )
        assert result.returncode == 1, (name, result.stdout, result.stderr)


def test_prepare_bounded_interpolation_and_edge_trim_are_explicit(repo_root: Path, tmp_path: Path) -> None:
    frame = _source_frame(16)
    frame.loc[0, "flow"] = np.nan
    frame.loc[7:8, "flow"] = np.nan
    frame.loc[15, "flow"] = np.nan
    source = tmp_path / "missing.csv"
    frame.to_csv(source, index=False)
    output = tmp_path / "out"
    result = _run(
        repo_root, "prepare", "--input", source, "--time-column", "when", "--flow-column", "flow",
        "--flow-unit", "m3/s", "--timezone", "UTC", "--edge-missing", "trim",
        "--missing-policy", "interpolate", "--max-interpolation-gap-hours", "2", "--output-dir", output,
    )
    assert result.returncode == 0, result.stderr
    result_json = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result_json["status"] == "warning"
    table = pd.read_csv(output / "discharge_timeseries.csv")
    assert len(table) == 14
    assert int(table["is_imputed"].sum()) == 2


def test_eckhardt_known_bounds_balance_and_alpha_evidence(repo_root: Path) -> None:
    module = _load_module(repo_root / SCRIPTS["baseflow"], "test_eckhardt_example")
    discharge = np.array([10.0, 12.0, 20.0, 15.0, 11.0])
    baseflow = module.eckhardt_baseflow(discharge, 0.95, 0.8)
    assert np.all(baseflow >= 0)
    assert np.all(baseflow <= discharge)
    np.testing.assert_allclose(discharge, baseflow + (discharge - baseflow))
    with pytest.raises(module.ContractError, match="不足 20"):
        module.estimate_recession_alpha(np.array([3.0, 2.0, 1.0]))


def test_event_config_is_strict_and_scientific_qc_uses_exit_code_two(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module(repo_root / SCRIPTS["extract"], "test_extract_example")
    config = tmp_path / "config.yaml"
    _write_config(config)
    _, raw = module.load_config(config)
    raw["peak_detection"].pop("prominence_factor")
    config.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(module.ContractError, match="字段必须精确"):
        module.load_config(config)

    output = tmp_path / "qc-failure"
    monkeypatch.setattr(module, "prepare_output_dir", lambda path, overwrite, declared: path.mkdir(parents=True))
    monkeypatch.setattr(module, "run", lambda args: (_ for _ in ()).throw(module.ScientificQCError("synthetic QC failure")))
    code = module.main([
        "--baseflow-result", str(tmp_path / "upstream.json"), "--config", str(config),
        "--output-dir", str(output),
    ])
    assert code == 2
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "error"
    assert result["checks"][0]["status"] == "FAIL"


@pytest.mark.integration
@pytest.mark.parametrize("table_format", ["csv", "parquet", "xlsx"])
def test_synthetic_event_pipeline_formats_and_schemas(repo_root: Path, tmp_path: Path, table_format: str) -> None:
    _, _, event_dir = _pipeline(repo_root, tmp_path / f"管线（{table_format}）", table_format)
    result = json.loads((event_dir / "result.json").read_text(encoding="utf-8"))
    schema = json.loads((repo_root / "resources/schemas/data-processing-run-result.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(result)
    assert result["status"] == "success"
    manifest = json.loads((event_dir / "event_manifest.json").read_text(encoding="utf-8"))
    assert manifest["event_count"] >= 2
    assert len(manifest["files"]) == manifest["event_count"]
    qc = pd.read_csv(event_dir / "event_qc.csv")
    assert set(qc["status"]) == {"PASS"}


@pytest.mark.integration
def test_flood_atlas_fixed_outputs_schema_hashes_and_tamper_stop(repo_root: Path, tmp_path: Path, utf8_env: dict[str, str]) -> None:
    _, _, event_dir = _pipeline(repo_root, tmp_path / "事件管线 Unicode（测试）")
    atlas_dir = tmp_path / "洪水图册（中文）"
    environment = dict(utf8_env)
    environment["MPLCONFIGDIR"] = str(tmp_path / "mpl-cache")
    rendered = _run(
        repo_root, "atlas", "--extract-result", event_dir / "result.json", "--language", "zh",
        "--selection", "first", "--max-events", "1", "--output-dir", atlas_dir, env=environment,
    )
    assert rendered.returncode == 0, rendered.stderr
    pngs = sorted(atlas_dir.glob("*.png"))
    assert [path.name for path in pngs] == ["event-0001-hydrograph.png", "flood-event-overview.png"]
    for png in pngs:
        with Image.open(png) as image:
            assert image.size == (2400, 1600)
    figure_schema = json.loads((repo_root / "resources/schemas/flood-event-figure.schema.json").read_text(encoding="utf-8"))
    for metadata_path in atlas_dir.glob("*.figure.json"):
        Draft202012Validator(figure_schema).validate(json.loads(metadata_path.read_text(encoding="utf-8")))
    assert all("<svg" in path.read_text(encoding="utf-8") for path in atlas_dir.glob("*.svg"))
    run_schema = json.loads((repo_root / "resources/schemas/visualization-run-result.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(run_schema).validate(json.loads((atlas_dir / "result.json").read_text(encoding="utf-8")))

    first_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in atlas_dir.glob("*.png")}
    repeated = _run(
        repo_root, "atlas", "--extract-result", event_dir / "result.json", "--language", "zh",
        "--selection", "first", "--max-events", "1", "--output-dir", atlas_dir, "--overwrite", env=environment,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert first_hashes == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in atlas_dir.glob("*.png")}

    event_result = json.loads((event_dir / "result.json").read_text(encoding="utf-8"))
    summary_ref = event_result["artifacts"]["event_summary"]
    summary_path = event_dir / summary_ref["path"]
    summary_path.write_bytes(summary_path.read_bytes() + b"\n")
    rejected = _run(
        repo_root, "atlas", "--extract-result", event_dir / "result.json", "--language", "en",
        "--selection", "largest", "--max-events", "1", "--output-dir", tmp_path / "tampered", env=environment,
    )
    assert rejected.returncode == 1
    assert "SHA-256" in rejected.stderr
