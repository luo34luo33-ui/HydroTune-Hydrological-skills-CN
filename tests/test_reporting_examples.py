from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "visualization-reporting/generate-hydrological-study-report"
REV = ROOT / "visualization-reporting/review-hydrological-study-report"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run(script, *args):
    return subprocess.run([sys.executable, "-X", "utf8", str(script), *map(str, args)], capture_output=True, text=True, encoding="utf-8")


def source(tmp, sid, skill, artifacts, parameters=None):
    folder = tmp / sid
    folder.mkdir()
    references = {}
    for name, content in artifacts.items():
        path = folder / name
        path.write_text(content, encoding="utf-8")
        references[name.replace(".", "_")] = {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    path = folder / "result.json"
    write(path, {"schema_version": "1.0", "skill": skill, "status": "success", "message": "done",
                 "parameters": parameters or {}, "inputs": {}, "artifacts": references,
                 "checks": [], "warnings": [], "provenance": {"fixture": True}})
    return path


@pytest.fixture
def study(tmp_path):
    source(tmp_path, "prep", "data-processing/prepare-discharge-timeseries", {"series_metadata.json": '{"unit":"m3/s","timezone":"Asia/Shanghai"}'})
    source(tmp_path, "sim", "hydrological-modeling/run-lumped-hbv-model", {"simulation_table.csv": "time,Q,is_warmup\n2020-01-01,1,True\n2020-01-02,2,False\n"}, {"timestep_hours": 24, "warmup_steps": 1})
    source(tmp_path, "eval", "evaluation-diagnostics/compute-continuous-series-metrics",
           {"series_metrics.csv": "metric,value,unit,status\nnse,0.85,dimensionless,ok\nvolume_bias,-10,m3,ok\nrelative_volume_bias,-0.1,dimensionless,ok\nmae,unavailable,m3/s,unavailable\n"}, {"timestep_hours": 24})
    manifest = tmp_path / "study-manifest.json"
    write(manifest, {"schema_version": "1.0", "title": "研究 study", "required_preprocessing": ["prep"],
                     "sources": [{"id": "prep", "stage": "preprocessing", "result": "prep/result.json"},
                                 {"id": "sim", "stage": "simulation", "result": "sim/result.json"},
                                 {"id": "eval", "stage": "evaluation", "result": "eval/result.json",
                                  "context": {"mode": "continuous", "split": "validation", "period": {"start": "2020-01-02", "end": "2020-01-02"}}}],
                     "runs": [{"id": "hbv-run", "simulation": "sim", "evaluations": ["eval"]}]})
    return manifest


def prepare(manifest, out, script=None):
    r = run(script or GEN / "examples/generate_hydrological_study_report.py", "prepare", "--study-manifest", manifest, "--output-dir", out)
    assert r.returncode == 0, r.stderr
    report = read(out / "report.json")
    ev = next(e for e in report["evidence"] if e["value"] == "0.85")
    report["sections"][1]["paragraphs"] = [{"id": "nse-result", "kind": "fact", "text": "NSE = 0.850000.",
                                           "evidence_ids": [ev["id"]], "numeric_bindings": [{"evidence_id": ev["id"], "text": "0.850000"}]}]
    for index in (0, 4, 5):
        report["sections"][index]["paragraphs"] = [{"id": f"narrative-{index}", "kind": "inference",
                                                   "text": "Evidence is limited to this explicitly declared evaluation scope.",
                                                   "evidence_ids": [ev["id"]], "numeric_bindings": []}]
    report["narrative_provenance"] = {"author": "fixture-agent", "method": "agent", "completed": True}
    write(out / "report.json", report)
    return out / "report.json"


def finalize(manifest, draft, out, script=None):
    r = run(script or GEN / "examples/generate_hydrological_study_report.py", "finalize", "--study-manifest", manifest, "--report-json", draft, "--output-dir", out)
    assert r.returncode == 0, r.stderr
    return out


def review(manifest, report, out, semantic=None, script=None):
    args = ["--study-manifest", manifest, "--report-dir", report, "--output-dir", out]
    if semantic:
        args += ["--semantic-review", semantic]
    return run(script or REV / "examples/review_hydrological_study_report.py", *args)


def semantic_file(tmp, report, fingerprint, verdict="supported"):
    path = tmp / "semantic.json"
    paragraphs = [p for s in read(report / "report.json")["sections"] for p in s["paragraphs"]]
    write(path, {"schema_version": "1.0", "package_fingerprint": fingerprint, "reviewer": "independent-fixture-agent",
                 "paragraphs": [{"id": p["id"], "evidence_ids": p["evidence_ids"], "verdict": verdict,
                                 "rationale": "Synthetic evidence reviewed independently."} for p in paragraphs]})
    return path


def test_continuous_report_and_independent_review(study, tmp_path):
    draft = prepare(study, tmp_path / "draft")
    out = finalize(study, draft, tmp_path / "report")
    assert "0.850000" in (out / "report.md").read_text(encoding="utf-8")
    data = read(out / "report.json")
    assert any(e["value"] == "unavailable" and e["availability"] == "unavailable" for e in data["evidence"])
    assert any(e["locator"].endswith("is_warmup") for e in data["evidence"])
    assert any("模拟 - 观测" in e["definition"] for e in data["evidence"])
    assert review(study, out, tmp_path / "initial").returncode == 2
    initial = read(tmp_path / "initial/review.json")
    assert initial["status"] == "needs_human_review"
    semantic = semantic_file(tmp_path, out, initial["package_fingerprint"])
    r = review(study, out, tmp_path / "complete", semantic)
    assert r.returncode == 0, r.stderr
    assert read(tmp_path / "complete/review.json")["status"] == "pass"
    again = finalize(study, draft, tmp_path / "report-again")
    for name in ("report.md", "report.json", "summary.csv", "evidence-index.csv"):
        assert (out / name).read_bytes() == (again / name).read_bytes()


@pytest.mark.parametrize("failure", ["missing", "hash", "version", "skill", "error", "binding"])
def test_invalid_sources_stop_without_formal_report(study, tmp_path, failure):
    path = tmp_path / "eval/result.json"
    document = read(path)
    if failure == "missing":
        path.unlink()
    elif failure == "binding":
        m = read(study)
        m["runs"][0]["evaluations"] = ["sim"]
        write(study, m)
    else:
        if failure == "hash":
            (path.parent / "series_metrics.csv").write_text("corrupt", encoding="utf-8")
        elif failure == "version": document["schema_version"] = "9.0"
        elif failure == "skill": document["skill"] = "evaluation-diagnostics/unknown"
        elif failure == "error": document["status"] = "error"
        write(path, document)
    out = tmp_path / "failed"
    r = run(GEN / "examples/generate_hydrological_study_report.py", "prepare", "--study-manifest", study, "--output-dir", out)
    assert r.returncode == 1
    assert not (out / "report.md").exists()
    assert read(out / "result.json")["status"] == "error"
    assert (out / "data-gaps.csv").exists()


@pytest.mark.parametrize("field", ["value", "unit", "context", "definition", "markdown", "table"])
def test_review_locates_tampering(study, tmp_path, field):
    report = finalize(study, prepare(study, tmp_path / "draft"), tmp_path / "report")
    if field == "markdown":
        p = report / "report.md"
        p.write_text(p.read_text(encoding="utf-8").replace("0.850000", "0.950000"), encoding="utf-8")
    elif field == "table":
        (report / "summary.csv").write_text("wrong", encoding="utf-8")
    else:
        p = report / "report.json"
        data = read(p)
        e = next(e for e in data["evidence"] if e["value"] == "0.85")
        e[field] = {"split": "calibration"} if field == "context" else "wrong"
        write(p, data)
    r = review(study, report, tmp_path / "review")
    assert r.returncode == 2, r.stderr
    result = read(tmp_path / "review/review.json")
    assert result["status"] == "needs_revision"
    assert any(f["severity"] == "error" and f["report_location"] for f in result["findings"])


def test_semantic_unsupported_and_stale(study, tmp_path):
    report = finalize(study, prepare(study, tmp_path / "draft"), tmp_path / "report")
    review(study, report, tmp_path / "initial")
    fingerprint = read(tmp_path / "initial/review.json")["package_fingerprint"]
    semantic = semantic_file(tmp_path, report, fingerprint, "unsupported")
    assert review(study, report, tmp_path / "unsupported", semantic).returncode == 2
    assert read(tmp_path / "unsupported/review.json")["status"] == "needs_revision"
    data = read(semantic)
    data["package_fingerprint"] = "0" * 64
    write(semantic, data)
    assert review(study, report, tmp_path / "stale", semantic).returncode == 2
    assert not read(tmp_path / "stale/review.json")["semantic_review_completed"]


def test_event_multirun_english_and_warning(study, tmp_path):
    m = read(study)
    m["language"] = "en"
    source(tmp_path, "events", "evaluation-diagnostics/compute-event-flood-metrics",
           {"event_metrics.csv": "event_id,volume_error_relative,qualified\nevent-a,-0.2,unavailable\nevent-b,0.1,unavailable\n"})
    m["sources"].append({"id": "events", "stage": "evaluation", "result": "events/result.json",
                          "context": {"mode": "event_collection", "split": "validation", "event_ids": ["event-a", "event-b"]}})
    source(tmp_path, "sim-two", "hydrological-modeling/run-lumped-tank-model", {"simulation_table.csv": "time,Q\n2020-01-02,2\n"})
    m["sources"].append({"id": "sim-two", "stage": "simulation", "result": "sim-two/result.json"})
    m["runs"].append({"id": "tank-run", "simulation": "sim-two", "evaluations": ["events"]})
    write(study, m)
    report = finalize(study, prepare(study, tmp_path / "draft"), tmp_path / "report")
    data = read(report / "report.json")
    assert len(data["runs"]) == 2
    event_evidence = [e for e in data["evidence"] if "volume_error_relative" in e["locator"]]
    assert len(event_evidence) == 2
    assert all("观测洪量 - 模拟洪量" in e["definition"] for e in event_evidence)
    assert event_evidence[0]["context"]["event_id"] == "event-a"
    assert "Overall conclusions" in (report / "report.md").read_text(encoding="utf-8")


def test_numeric_prose_bindings_and_immutable_sources(study, tmp_path):
    draft = prepare(study, tmp_path / "draft")
    data = read(draft)
    data["sections"][5]["paragraphs"][0]["text"] += " Unsupported 42."
    write(draft, data)
    r = run(GEN / "examples/generate_hydrological_study_report.py", "finalize", "--study-manifest", study, "--report-json", draft, "--output-dir", tmp_path / "bad")
    assert r.returncode == 1
    assert not (tmp_path / "bad/report.md").exists()


def test_installed_reporting_skills_run_without_repository(study, tmp_path):
    from install_skills import render_installation
    from repository import discover_atomic_skills, load_category_registry
    categories, _ = load_category_registry(ROOT)
    scripts = {}
    for record in discover_atomic_skills(ROOT, categories):
        if record.name not in (GEN.name, REV.name): continue
        folder = tmp_path / record.install_name
        for name, content in render_installation(record).items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        scripts[record.name] = folder / "scripts" / (record.name.replace("-", "_") + ".py")
    draft = prepare(study, tmp_path / "draft", scripts[GEN.name])
    report = finalize(study, draft, tmp_path / "report", scripts[GEN.name])
    r = review(study, report, tmp_path / "review", script=scripts[REV.name])
    assert r.returncode == 2, r.stderr
    assert read(tmp_path / "review/review.json")["status"] == "needs_human_review"


def test_calibration_splits_and_figures_are_preserved(study, tmp_path):
    calibration = source(tmp_path, "cal", "model-calibration/calibrate-model-de",
                         {"calibration_metrics.json": '{"split":"calibration","metrics":{"nse":0.95}}',
                          "validation_metrics.json": '{"split":"validation","metrics":{"nse":0.75}}',
                          "validation_series.csv": "time,observed,simulated,scored\n2020-01-01,1,1,False\n2020-01-02,2,2,True\n"})
    atlas = source(tmp_path, "atlas", "visualization-reporting/visualize-model-calibration",
                   {"validation_scatter.svg": '<svg xmlns="http://www.w3.org/2000/svg"/>',
                    "validation_scatter.json": '{"figure_type":"validation-scatter","publication_ready":true}'})
    doc = read(calibration)
    doc["status"] = "warning"
    doc["warnings"] = ["Parameter on boundary"]
    write(calibration, doc)
    m = read(study)
    m["sources"] += [{"id": "cal", "stage": "calibration", "result": "cal/result.json"},
                      {"id": "atlas", "stage": "atlas", "result": "atlas/result.json"}]
    m["runs"][0]["calibration"] = "cal"
    write(study, m)
    report = finalize(study, prepare(study, tmp_path / "draft"), tmp_path / "report")
    doc = read(report / "report.json")
    metrics = [e for e in doc["evidence"] if e["locator"] == "/metrics/nse"]
    assert {e["context"]["split"] for e in metrics} == {"calibration", "validation"}
    assert any("Parameter on boundary" in w for w in doc["warnings"])
    figure = doc["figures"][0]
    assert (report / figure["path"]).read_bytes() == (atlas.parent / "validation_scatter.svg").read_bytes()
    assert figure["path"] in read(report / "result.json")["artifacts"]
    (report / figure["path"]).write_text("changed", encoding="utf-8")
    assert review(study, report, tmp_path / "review").returncode == 2
    assert any(f["category"] == "figure_integrity" for f in read(tmp_path / "review/review.json")["findings"])


def test_hashed_evaluation_input_period_cannot_be_misdeclared(study, tmp_path):
    series = tmp_path / "eval/input.csv"
    series.write_text("time,Q_obs,Q_total\n2020-01-02,2,2\n", encoding="utf-8")
    path = tmp_path / "eval/result.json"
    doc = read(path)
    doc["inputs"]["series"] = {"path": "input.csv", "sha256": hashlib.sha256(series.read_bytes()).hexdigest()}
    write(path, doc)
    prepare(study, tmp_path / "valid")
    m = read(study)
    m["sources"][2]["context"]["period"]["end"] = "2021-01-01"
    write(study, m)
    r = run(GEN / "examples/generate_hydrological_study_report.py", "prepare", "--study-manifest", study, "--output-dir", tmp_path / "invalid")
    assert r.returncode == 1
    assert "contradicts hashed input" in r.stderr


def test_semantic_coverage_and_incomplete_narrative_cannot_pass(study, tmp_path):
    draft = prepare(study, tmp_path / "draft")
    report = finalize(study, draft, tmp_path / "report")
    review(study, report, tmp_path / "initial")
    fingerprint = read(tmp_path / "initial/review.json")["package_fingerprint"]
    semantic = semantic_file(tmp_path, report, fingerprint)
    doc = read(semantic)
    doc["paragraphs"] = doc["paragraphs"][:-1]
    write(semantic, doc)
    assert review(study, report, tmp_path / "missing-coverage", semantic).returncode == 1
    data = read(draft)
    data["narrative_provenance"]["completed"] = False
    write(draft, data)
    r = run(GEN / "examples/generate_hydrological_study_report.py", "finalize", "--study-manifest", study, "--report-json", draft, "--output-dir", tmp_path / "invalid")
    assert r.returncode == 1


def test_overwrite_failure_removes_old_report_but_preserves_user_files(study, tmp_path):
    draft = prepare(study, tmp_path / "draft")
    report = finalize(study, draft, tmp_path / "report")
    user_file = report / "notes.txt"
    user_file.write_text("keep", encoding="utf-8")
    source_doc = read(tmp_path / "eval/result.json")
    source_doc["status"] = "error"
    write(tmp_path / "eval/result.json", source_doc)
    r = run(GEN / "examples/generate_hydrological_study_report.py", "finalize", "--study-manifest", study, "--report-json", draft, "--output-dir", report, "--overwrite")
    assert r.returncode == 1
    assert not (report / "report.md").exists()
    assert user_file.read_text(encoding="utf-8") == "keep"


def test_packaged_schemas_runtime_and_catalog_are_current():
    from repository import discover_atomic_skills, load_category_registry
    categories, _ = load_category_registry(ROOT)
    supported = {r.source_ref for r in discover_atomic_skills(ROOT, categories)
                 if r.category not in ("post-processing", "visualization-reporting") or r.name.startswith("visualize-")}
    assert set(read(GEN / "assets/upstream-catalog.json")) == supported
    assert (GEN / "examples/_report_common.py").read_bytes() == (REV / "examples/_report_common.py").read_bytes()
    for schema in (ROOT / "resources/schemas").glob("study-*.schema.json"):
        for skill in (GEN, REV):
            assert schema.read_bytes() == (skill / "assets" / schema.name).read_bytes()


def test_event_scope_and_metric_unit_contract_conflicts_stop(study, tmp_path):
    m = read(study)
    m["sources"][2]["context"] = {"mode": "event_collection", "split": "validation", "event_ids": ["declared-event"]}
    doc = read(tmp_path / "eval/result.json")
    doc["skill"] = "evaluation-diagnostics/compute-event-flood-metrics"
    path = tmp_path / "eval/event_metrics.csv"
    path.write_text("event_id,nse\nactual-event,0.85\n", encoding="utf-8")
    doc["artifacts"] = {"event_metrics": {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    write(tmp_path / "eval/result.json", doc)
    write(study, m)
    r = run(GEN / "examples/generate_hydrological_study_report.py", "prepare", "--study-manifest", study, "--output-dir", tmp_path / "bad-event")
    assert r.returncode == 1
    assert "event IDs differ" in r.stderr
    m["sources"][2]["context"] = {"mode": "continuous", "split": "validation", "period": {"start": "2020-01-02", "end": "2020-01-02"}}
    path = tmp_path / "eval/series_metrics.csv"
    path.write_text("metric,value,unit\nnse,0.85,m3/s\n", encoding="utf-8")
    doc["skill"] = "evaluation-diagnostics/compute-continuous-series-metrics"
    doc["artifacts"] = {"series_metrics": {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    write(tmp_path / "eval/result.json", doc)
    write(study, m)
    r = run(GEN / "examples/generate_hydrological_study_report.py", "prepare", "--study-manifest", study, "--output-dir", tmp_path / "bad-unit")
    assert r.returncode == 1
    assert "unit contradicts" in r.stderr


def test_declared_optional_calibration_missing_is_a_gap(study, tmp_path):
    m = read(study)
    m["sources"].append({"id": "optional-cal", "stage": "calibration", "result": "not-provided/result.json"})
    m["runs"][0]["calibration"] = "optional-cal"
    write(study, m)
    report = finalize(study, prepare(study, tmp_path / "draft"), tmp_path / "report")
    assert any(g["source_id"] == "optional-cal" and g["severity"] == "warning" for g in read(report / "data-gaps.json"))
    assert read(report / "result.json")["status"] == "warning"


def test_legacy_study_package_remains_reviewable(study, tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("legacy_report_runtime", GEN / "examples/_report_common.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.collect_legacy(study, legacy_report=True)
    ev = next(e for e in report["evidence"] if e["value"] == "0.85")
    for index in (0, 5, 6, 7):
        report["sections"][index]["paragraphs"] = [{"id": f"legacy-{index}", "kind": "inference", "text": "Conclusions apply only to the declared scope.", "evidence_ids": [ev["id"]], "numeric_bindings": []}]
    report["narrative_provenance"] = {"author": "legacy-agent", "method": "agent", "completed": True}
    out = tmp_path / "legacy"
    out.mkdir()
    write(out / "report.json", report)
    module.auxiliaries_legacy(report, out)
    (out / "report.md").write_text(module.render_legacy(report), encoding="utf-8")
    assert review(study, out, tmp_path / "legacy-review").returncode == 2
    initial = read(tmp_path / "legacy-review/review.json")
    assert initial["status"] == "needs_human_review"
    semantic = semantic_file(tmp_path, out, initial["package_fingerprint"])
    assert review(study, out, tmp_path / "legacy-pass", semantic).returncode == 0
