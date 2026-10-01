from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest
from jsonschema import Draft202012Validator


ALGORITHMS = {
    "de": ("calibrate-model-de", "calibrate_model_de.py", {
        "population_multiplier": 3, "mutation_factor": 0.8, "crossover_probability": 0.7,
        "max_generations": 4, "tolerance": 1e-6, "polish": False,
    }),
    "ga": ("calibrate-model-ga", "calibrate_model_ga.py", {
        "population_size": 8, "max_generations": 4, "tournament_size": 3, "elite_count": 1,
        "crossover_probability": 0.8, "mutation_probability": 0.4, "gene_mutation_probability": 0.5,
    }),
    "pso": ("calibrate-model-pso", "calibrate_model_pso.py", {
        "particle_count": 8, "max_iterations": 5, "inertia_weight": 0.7,
        "cognitive_coefficient": 1.5, "social_coefficient": 1.5, "velocity_limit_fraction": 0.5,
    }),
    "sce-ua": ("calibrate-model-sce-ua", "calibrate_model_sce_ua.py", {
        "complex_count": 2, "points_per_complex": 4, "evolution_steps": 2, "max_loops": 3,
        "reflection_coefficient": 1.0, "contraction_coefficient": 0.5, "stall_loops": 3,
        "objective_tolerance": 1e-12,
    }),
    "two-stage": ("calibrate-model-two-stage", "calibrate_model_two_stage.py", {
        "annealing_max_evaluations": 30, "local_max_evaluations": 20,
        "annealing_max_iterations": 4, "local_max_iterations": 10, "initial_temperature": 5230.0,
        "restart_temperature_ratio": 0.0001, "visit": 2.62, "accept": -5.0, "local_ftol": 1e-7,
    }),
}


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture(root: Path, algorithm: str, data_shape: str = "continuous") -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    adapter = root / "synthetic_adapter.py"
    adapter.write_text(
        """from __future__ import annotations
import numpy as np
import pandas as pd

class Evaluator:
    def __init__(self, problem): self.problem = problem
    def evaluate(self, parameters, split):
        if split != 'calibration': raise ValueError('search evaluate must use calibration split only')
        loss = (parameters['x'] - 0.25) ** 2 + (parameters['y'] - 0.75) ** 2
        score = 1.0 - loss
        return {'objective': score, 'metrics': {'score': score, 'rmse': loss ** 0.5}}
    def materialize(self, parameters, split):
        loss = (parameters['x'] - 0.25) ** 2 + (parameters['y'] - 0.75) ** 2
        score = 1.0 - loss
        result = {'objective': score, 'metrics': {'score': score, 'rmse': loss ** 0.5}}
        observed = np.linspace(1.0, 2.0, 12)
        shift = (parameters['x'] - 0.25) + (parameters['y'] - 0.75)
        frame = pd.DataFrame({'observed': observed, 'simulated': observed + shift, 'scored': [False, False] + [True] * 10, 'precipitation': np.linspace(0, 5, 12)})
        if self.problem['data_shape'] == 'continuous': frame.insert(0, 'time', pd.date_range('2020-01-01', periods=12, freq='D'))
        else:
            frame.insert(0, 'event_id', ['E01'] * 6 + ['E02'] * 6)
            frame.insert(1, 'step', list(range(6)) * 2)
        return {**result, 'series': frame}

def create_evaluator(problem, problem_dir): return Evaluator(problem)
""",
        encoding="utf-8",
    )
    model_input = root / "input.json"
    model_input.write_text('{"fixture": true}\n', encoding="utf-8")
    problem = {
        "schema_version": "1.0", "adapter": {"path": adapter.name, "sha256": _hash(adapter)},
        "inputs": [{"id": "fixture", "path": model_input.name, "sha256": _hash(model_input)}],
        "parameters": [{"name": "x", "lower": 0.0, "upper": 1.0}, {"name": "y", "lower": 0.0, "upper": 1.0}],
        "objective": {"metric": "score", "direction": "maximize"},
        "splits": {"calibration": "calibration", "validation": "validation"},
        "data_shape": data_shape, "warmup": {"confirmed": True, "semantics": "first two rows are unscored"},
        "variables": {"observed": {"semantics": "observed flow", "units": "m3/s"}, "simulated": {"semantics": "simulated flow", "units": "m3/s"}},
        "objective_consistency_tolerance": 1e-12,
    }
    problem_path = root / "calibration-problem.json"
    problem_path.write_text(json.dumps(problem, ensure_ascii=False, indent=2), encoding="utf-8")
    _, _, specific = ALGORITHMS[algorithm]
    config = {"schema_version": "1.0", "algorithm": algorithm, "seed": 42, "max_evaluations": 100, "max_failed_evaluations": 3, **specific}
    config_path = root / "optimizer-config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return problem_path, config_path


def _run_calibration(repo: Path, root: Path, algorithm: str, data_shape: str = "continuous") -> Path:
    problem, config = _write_fixture(root, algorithm, data_shape)
    folder, script, _ = ALGORITHMS[algorithm]
    output = root / f"输出 {algorithm}（测试）"
    result = subprocess.run(
        [sys.executable, str(repo / "model-calibration" / folder / "examples" / script), "--problem", str(problem), "--optimizer-config", str(config), "--output-dir", str(output)],
        text=True, capture_output=True, encoding="utf-8", errors="replace", check=False,
    )
    assert result.returncode == 0, result.stderr
    return output


@pytest.mark.parametrize("algorithm", sorted(ALGORITHMS))
def test_each_calibration_algorithm_produces_bounded_split_artifacts(repo_root: Path, tmp_path: Path, algorithm: str) -> None:
    output = _run_calibration(repo_root, tmp_path / algorithm, algorithm)
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    result_schema = json.loads((repo_root / "resources" / "schemas" / "model-calibration-result.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(result_schema).validate(result)
    assert result["status"] in {"success", "warning"}
    assert 0 < result["evaluation_count"] <= 100
    assert (output / "calibration_series.csv").is_file()
    assert (output / "validation_series.csv").is_file()
    best = json.loads((output / "best_parameters.json").read_text(encoding="utf-8"))
    assert all(0.0 <= item["relative_position"] <= 1.0 for item in best["parameters"].values())


def test_calibration_problem_and_all_optimizer_configs_match_schemas(repo_root: Path, tmp_path: Path) -> None:
    problem_schema = json.loads((repo_root / "resources" / "schemas" / "calibration-problem.schema.json").read_text(encoding="utf-8"))
    config_schema = json.loads((repo_root / "resources" / "schemas" / "optimizer-config.schema.json").read_text(encoding="utf-8"))
    for algorithm in ALGORITHMS:
        problem_path, config_path = _write_fixture(tmp_path / algorithm, algorithm)
        Draft202012Validator(problem_schema).validate(json.loads(problem_path.read_text(encoding="utf-8")))
        Draft202012Validator(config_schema).validate(json.loads(config_path.read_text(encoding="utf-8")))


def test_ga_is_reproducible_for_same_seed(repo_root: Path, tmp_path: Path) -> None:
    first = _run_calibration(repo_root, tmp_path / "first", "ga")
    second = _run_calibration(repo_root, tmp_path / "second", "ga")
    assert (first / "best_parameters.json").read_text(encoding="utf-8") == (second / "best_parameters.json").read_text(encoding="utf-8")
    assert (first / "calibration_trace.csv").read_text(encoding="utf-8-sig") == (second / "calibration_trace.csv").read_text(encoding="utf-8-sig")


def test_de_returns_warning_at_hard_evaluation_budget(repo_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "budget"
    problem, config_path = _write_fixture(root, "de")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["max_evaluations"] = 5
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    folder, script, _ = ALGORITHMS["de"]
    output = root / "output"
    completed = subprocess.run(
        [sys.executable, str(repo_root / "model-calibration" / folder / "examples" / script), "--problem", str(problem), "--optimizer-config", str(config_path), "--output-dir", str(output)],
        text=True, capture_output=True, encoding="utf-8", errors="replace", check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "warning"
    assert result["evaluation_count"] == 5
    assert result["termination_reason"] == "evaluation_budget_exhausted"


@pytest.mark.parametrize("data_shape", ["continuous", "event_collection"])
def test_calibration_atlas_renders_fixed_figures(repo_root: Path, tmp_path: Path, data_shape: str) -> None:
    output = _run_calibration(repo_root, tmp_path / data_shape, "ga", data_shape)
    atlas = tmp_path / f"atlas {data_shape}（测试）"
    script = repo_root / "visualization-reporting" / "visualize-model-calibration" / "examples" / "render_model_calibration_atlas.py"
    rendered = subprocess.run([sys.executable, str(script), "--calibration-result", str(output / "result.json"), "--language", "zh", "--output-dir", str(atlas)], text=True, capture_output=True, encoding="utf-8", errors="replace", check=False)
    assert rendered.returncode == 0, rendered.stderr
    result = json.loads((atlas / "result.json").read_text(encoding="utf-8"))
    assert result["status"] in {"success", "warning"}
    pngs = list(atlas.glob("*.png"))
    assert len(pngs) == (4 if data_shape == "event_collection" else 3)
    for path in pngs:
        with Image.open(path) as image:
            assert image.size == (2400, 1600)
    assert all(path.read_text(encoding="utf-8").lstrip().startswith("<?xml") for path in atlas.glob("*.svg"))
    figure_schema = json.loads((repo_root / "resources" / "schemas" / "calibration-figure.schema.json").read_text(encoding="utf-8"))
    for path in atlas.glob("*.json"):
        if path.name != "result.json":
            Draft202012Validator(figure_schema).validate(json.loads(path.read_text(encoding="utf-8")))


def test_atlas_rejects_tampered_calibration_artifact(repo_root: Path, tmp_path: Path) -> None:
    output = _run_calibration(repo_root, tmp_path / "tamper", "ga")
    with (output / "validation_series.csv").open("a", encoding="utf-8") as handle:
        handle.write("tampered\n")
    script = repo_root / "visualization-reporting" / "visualize-model-calibration" / "examples" / "render_model_calibration_atlas.py"
    rendered = subprocess.run([sys.executable, str(script), "--calibration-result", str(output / "result.json"), "--language", "en", "--output-dir", str(tmp_path / "atlas")], text=True, capture_output=True, encoding="utf-8", errors="replace", check=False)
    assert rendered.returncode == 1
    error_result = json.loads((tmp_path / "atlas" / "result.json").read_text(encoding="utf-8"))
    assert "SHA-256" in error_result["message"]


def test_calibration_atlas_multisimulation_alignment_and_event_nse(repo_root: Path, tmp_path: Path) -> None:
    import numpy as np
    import pandas as pd
    output = _run_calibration(repo_root, tmp_path / "comparison", "ga", "event_collection")
    original = pd.read_csv(output / "validation_series.csv")
    comparison = original[["event_id", "step"]].copy()
    comparison["corrected"] = original["observed"]
    # A deliberately huge warm-up deviation must not affect the scored NSE.
    comparison.loc[~original["scored"].astype(bool), "corrected"] = 99999
    comparison.iloc[::-1].to_csv(tmp_path / "comparison.csv", index=False)
    manifest = {"schema_version": "1.0", "join_on": ["event_id", "step"],
                "series": [{"label": "后处理", "path": "comparison.csv", "column": "corrected", "unit": "m3/s", "split": "validation"}]}
    manifest_path = tmp_path / "comparisons.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    script = repo_root / "visualization-reporting/visualize-model-calibration/examples/render_model_calibration_atlas.py"
    atlas = tmp_path / "multi-atlas"
    command = [sys.executable, str(script), "--calibration-result", str(output / "result.json"),
               "--comparison-manifest", str(manifest_path), "--simulation-label", "原始模型",
               "--language", "zh", "--output-dir", str(atlas), "--overwrite"]
    rendered = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace")
    assert rendered.returncode == 0, rendered.stderr
    meta = json.loads((atlas / "validation_event_001.json").read_text(encoding="utf-8"))
    assert meta["template"] == "hydrotune.model-calibration-atlas.v2"
    first, corrected = meta["simulation_metrics"]
    subset = original[original["event_id"] == "E01"]
    scored = subset[subset["scored"].astype(bool)]
    expected = 1 - np.sum((scored["simulated"] - scored["observed"]) ** 2) / np.sum((scored["observed"] - scored["observed"].mean()) ** 2)
    assert first["nse"] == pytest.approx(expected)
    assert corrected["nse"] == 1.0
    assert corrected["scored_samples"] == 4
    assert first["color"] != corrected["color"]
    assert meta["scope"]["event_ids"] == ["E01"]
    svg = (atlas / "validation_event_001.svg").read_text(encoding="utf-8")
    assert "NSE = 1.000" in svg
    assert "验证期观测" not in svg
    assert "时间步" in svg and "m³/s" in svg
    with Image.open(atlas / "validation_event_001.png") as image:
        assert image.convert("RGB").getpixel((0,0)) == (255,255,255)
    for field, value in [("unit", "mm"), ("split", "calibration")]:
        altered = json.loads(json.dumps(manifest));altered["series"][0][field] = value
        manifest_path.write_text(json.dumps(altered), encoding="utf-8")
        rendered = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace")
        assert rendered.returncode == 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    comparison.iloc[:-1].to_csv(tmp_path / "comparison.csv", index=False)
    rendered = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace")
    assert rendered.returncode == 1
    assert "coverage" in rendered.stderr


def test_calibration_plot_nse_degenerate_samples(repo_root: Path, monkeypatch) -> None:
    import importlib.util
    import pandas as pd
    path = repo_root / "visualization-reporting/visualize-model-calibration/examples/_calibration_plot.py"
    spec = importlib.util.spec_from_file_location("calibration_plot_test", path)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    frame = pd.DataFrame({"observed": [2.,2.,2.], "simulated": [1.,1.,1.], "scored": [False,True,True]})
    assert module._nse(frame,"simulated") == (None,2,"constant observations")
    frame["scored"] = [False,False,True]
    assert module._nse(frame,"simulated")[0] is None
