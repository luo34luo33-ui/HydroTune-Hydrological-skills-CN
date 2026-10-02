from __future__ import annotations

from pathlib import Path

import yaml

from repository import discover_atomic_skills, load_category_registry, read_frontmatter, render_frontmatter
from validate_skills import validate_repository as validate_skills
from validate_workflows import validate_repository as validate_workflows


def _add_valid_atomic_skill(root: Path, name: str = "test-skill") -> Path:
    skill_dir = root / "data-processing" / name
    (skill_dir / "examples").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: test-skill
description: 适用于验证测试契约；不适用于真实水文计算。
metadata:
  category: data-processing
  domains:
    - data-processing
  tool_type: instruction
  primary_tool: agent-reasoning
  related_skills: []
---

# Test skill

This synthetic skill exists only inside a temporary test repository.
""",
        encoding="utf-8",
    )
    (skill_dir / "usage-guide.md").write_text("# Test usage\n", encoding="utf-8")
    (skill_dir / "data-contract.yaml").write_text(
        """schema_version: "1.0"
skill: data-processing/test-skill
inputs: []
outputs: []
invariants:
  - Test fixture remains deterministic.
""",
        encoding="utf-8",
    )
    (skill_dir / "examples" / "verified.txt").write_text(
        "deterministic test fixture\n", encoding="utf-8"
    )
    return skill_dir


def test_repository_skeleton_is_valid(repo_root: Path) -> None:
    skill_issues, skills = validate_skills(repo_root)
    workflow_issues, workflows = validate_workflows(repo_root)
    assert skill_issues == []
    assert workflow_issues == []
    assert {skill.source_ref for skill in skills} == {
        "spatial-analysis/prepare-dem-analysis-grid",
        "spatial-analysis/extract-dem-stream-network",
        "spatial-analysis/build-hydrological-topology",
        "spatial-analysis/validate-hydrological-topology",
        "spatial-analysis/derive-topmodel-terrain-inputs",
        "spatial-analysis/aggregate-forcing-to-model-units",
        "data-processing/prepare-discharge-timeseries",
        "data-processing/prepare-model-forcing-timeseries",
        "data-processing/derive-potential-evapotranspiration",
        "data-processing/separate-baseflow-eckhardt",
        "data-processing/extract-flood-events",
        "visualization-reporting/visualize-dem-hydrology-atlas",
        "visualization-reporting/visualize-hydrobase-atlas",
        "visualization-reporting/visualize-flood-event-atlas",
        "model-calibration/calibrate-model-de",
        "model-calibration/calibrate-model-ga",
        "model-calibration/calibrate-model-pso",
        "model-calibration/calibrate-model-sce-ua",
        "model-calibration/calibrate-model-two-stage",
        "visualization-reporting/visualize-model-calibration",
        "visualization-reporting/generate-hydrological-study-report",
        "visualization-reporting/generate-timeseries-data-processing-report",
        "visualization-reporting/generate-spatial-data-processing-report",
        "visualization-reporting/review-hydrological-study-report",
        "hydrological-modeling/run-lumped-xaj-model",
            "hydrological-modeling/run-semi-distributed-xaj-model",
        "hydrological-modeling/run-semi-distributed-swat-model",
        "hydrological-modeling/route-muskingum-channel",
        "hydrological-modeling/route-lohmann-channel",
        "hydrological-modeling/run-lumped-dhf-model",
        "hydrological-modeling/run-lumped-hbv-model",
        "hydrological-modeling/run-lumped-tank-model",
        "hydrological-modeling/run-lumped-gr4j-model",
        "hydrological-modeling/run-lumped-sacsma-model",
        "hydrological-modeling/run-lumped-topmodel",
        "post-processing/correct-residual-with-ml",
        "post-processing/align-observed-simulated-discharge",
        "post-processing/ensemble-discharge-with-bma",
        "evaluation-diagnostics/compute-event-flood-metrics",
        "evaluation-diagnostics/compute-continuous-series-metrics",
        "evaluation-diagnostics/aggregate-event-metrics",
    }
    assert {workflow.name for workflow in workflows} == {
        "data-preprocessing",
        "hydrological-modeling",
        "simulation-analysis",
    }


def test_category_readmes_are_not_discovered_as_skills(repo_root: Path) -> None:
    categories, issues = load_category_registry(repo_root)
    assert issues == []
    records = discover_atomic_skills(repo_root, categories)
    assert all(record.source_dir.name != "spatial-analysis" for record in records)
    assert {record.source_ref for record in records} == {
        "spatial-analysis/prepare-dem-analysis-grid",
        "spatial-analysis/extract-dem-stream-network",
        "spatial-analysis/build-hydrological-topology",
        "spatial-analysis/validate-hydrological-topology",
        "spatial-analysis/derive-topmodel-terrain-inputs",
        "spatial-analysis/aggregate-forcing-to-model-units",
        "data-processing/prepare-discharge-timeseries",
        "data-processing/prepare-model-forcing-timeseries",
        "data-processing/derive-potential-evapotranspiration",
        "data-processing/separate-baseflow-eckhardt",
        "data-processing/extract-flood-events",
        "visualization-reporting/visualize-dem-hydrology-atlas",
        "visualization-reporting/visualize-hydrobase-atlas",
        "visualization-reporting/visualize-flood-event-atlas",
        "model-calibration/calibrate-model-de",
        "model-calibration/calibrate-model-ga",
        "model-calibration/calibrate-model-pso",
        "model-calibration/calibrate-model-sce-ua",
        "model-calibration/calibrate-model-two-stage",
        "visualization-reporting/visualize-model-calibration",
        "visualization-reporting/generate-hydrological-study-report",
        "visualization-reporting/generate-timeseries-data-processing-report",
        "visualization-reporting/generate-spatial-data-processing-report",
        "visualization-reporting/review-hydrological-study-report",
        "hydrological-modeling/run-lumped-xaj-model",
            "hydrological-modeling/run-semi-distributed-xaj-model",
        "hydrological-modeling/run-semi-distributed-swat-model",
        "hydrological-modeling/route-muskingum-channel",
        "hydrological-modeling/route-lohmann-channel",
        "hydrological-modeling/run-lumped-dhf-model",
        "hydrological-modeling/run-lumped-hbv-model",
        "hydrological-modeling/run-lumped-tank-model",
        "hydrological-modeling/run-lumped-gr4j-model",
        "hydrological-modeling/run-lumped-sacsma-model",
        "hydrological-modeling/run-lumped-topmodel",
        "post-processing/correct-residual-with-ml",
        "post-processing/align-observed-simulated-discharge",
        "post-processing/ensemble-discharge-with-bma",
        "evaluation-diagnostics/compute-event-flood-metrics",
        "evaluation-diagnostics/compute-continuous-series-metrics",
        "evaluation-diagnostics/aggregate-event-metrics",
    }


def test_valid_atomic_fixture_passes(repo_copy: Path) -> None:
    _add_valid_atomic_skill(repo_copy)
    issues, records = validate_skills(repo_copy)
    assert issues == []
    assert "data-processing/test-skill" in {record.source_ref for record in records}
    assert len(records) == 42


def test_unknown_category_skill_is_rejected(repo_copy: Path) -> None:
    misplaced = repo_copy / "unknown-category" / "bad-skill"
    misplaced.mkdir(parents=True)
    (misplaced / "SKILL.md").write_text("---\nname: bad-skill\ndescription: bad\n---\n", encoding="utf-8")
    issues, _ = validate_skills(repo_copy)
    assert any("严格位于" in issue.message or "未注册目录" in issue.message for issue in issues)


def test_invalid_data_contract_is_rejected(repo_copy: Path) -> None:
    skill_dir = _add_valid_atomic_skill(repo_copy)
    contract = yaml.safe_load((skill_dir / "data-contract.yaml").read_text(encoding="utf-8"))
    contract["schema_version"] = "9.9"
    (skill_dir / "data-contract.yaml").write_text(
        yaml.safe_dump(contract, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    issues, _ = validate_skills(repo_copy)
    assert any("schema 校验失败" in issue.message for issue in issues)


def test_missing_related_skill_is_rejected(repo_copy: Path) -> None:
    skill_dir = _add_valid_atomic_skill(repo_copy)
    frontmatter, body = read_frontmatter(skill_dir / "SKILL.md")
    frontmatter["metadata"]["related_skills"] = ["data-processing/not-found"]
    (skill_dir / "SKILL.md").write_text(
        render_frontmatter(frontmatter, body), encoding="utf-8"
    )
    issues, _ = validate_skills(repo_copy)
    assert any("related skill 不存在" in issue.message for issue in issues)


def test_duplicate_workflow_stage_is_rejected(repo_copy: Path) -> None:
    workflow_file = repo_copy / "workflows" / "data-preprocessing" / "workflow.yaml"
    document = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
    document["stages"].append(dict(document["stages"][0]))
    workflow_file.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    issues, _ = validate_workflows(repo_copy)
    assert any("stage id 重复" in issue.message for issue in issues)


def test_workflow_cycle_is_rejected(repo_copy: Path) -> None:
    workflow_file = repo_copy / "workflows" / "data-preprocessing" / "workflow.yaml"
    document = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
    document["stages"][0]["depends_on"] = ["model_ready_output_validation"]
    workflow_file.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    issues, _ = validate_workflows(repo_copy)
    assert any("存在循环" in issue.message for issue in issues)


def test_unknown_feedback_workflow_is_rejected(repo_copy: Path) -> None:
    workflow_file = repo_copy / "workflows" / "simulation-analysis" / "workflow.yaml"
    document = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
    document["feedback_targets"][0]["workflow"] = "unknown-workflow"
    workflow_file.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    issues, _ = validate_workflows(repo_copy)
    assert any("feedback workflow 不存在" in issue.message for issue in issues)
