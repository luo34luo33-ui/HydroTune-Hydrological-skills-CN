#!/usr/bin/env python3
"""Validate HydroTune workflow skills and workflow contracts."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from repository import (
    NAME_PATTERN,
    RepositoryError,
    ValidationIssue,
    contains_trigger_boundaries,
    discover_atomic_skills,
    discover_workflows,
    find_cycle,
    load_category_registry,
    read_frontmatter,
    read_yaml,
    validate_with_schema,
)


EXPECTED_WORKFLOWS = {
    "data-preprocessing",
    "hydrological-modeling",
    "simulation-analysis",
}
REQUIRED_METADATA = {
    "category",
    "domains",
    "tool_type",
    "primary_tool",
    "related_skills",
}


def _validate_workflow(
    root: Path,
    record: Any,
    known_atomic_refs: set[str],
    known_workflows: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    skill_file = record.source_dir / "SKILL.md"
    usage_file = record.source_dir / "usage-guide.md"
    workflow_file = record.source_dir / "workflow.yaml"

    if not usage_file.is_file():
        issues.append(ValidationIssue(usage_file, "缺少 usage-guide.md"))
    if not workflow_file.is_file():
        issues.append(ValidationIssue(workflow_file, "缺少 workflow.yaml"))

    try:
        frontmatter, _ = read_frontmatter(skill_file)
    except RepositoryError as exc:
        return issues + [ValidationIssue(skill_file, str(exc))]
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    metadata = frontmatter.get("metadata")
    if name != record.name:
        issues.append(ValidationIssue(skill_file, f"name 必须等于目录名 {record.name}"))
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        issues.append(ValidationIssue(skill_file, "name 必须使用小写字母、数字和连字符"))
    if not isinstance(description, str) or not contains_trigger_boundaries(description):
        issues.append(ValidationIssue(skill_file, "description 必须明确适用与不适用边界"))
    if not isinstance(metadata, dict):
        issues.append(ValidationIssue(skill_file, "metadata 必须是 object"))
        metadata = {}
    missing = REQUIRED_METADATA - set(metadata)
    if missing:
        issues.append(ValidationIssue(skill_file, f"metadata 缺少字段: {', '.join(sorted(missing))}"))
    if metadata.get("category") != "workflow":
        issues.append(ValidationIssue(skill_file, "workflow metadata.category 必须为 workflow"))
    if metadata.get("tool_type") != "instruction":
        issues.append(ValidationIssue(skill_file, "workflow metadata.tool_type 必须为 instruction"))

    if not workflow_file.is_file():
        return issues
    try:
        document = read_yaml(workflow_file)
    except RepositoryError as exc:
        return issues + [ValidationIssue(workflow_file, str(exc))]
    issues.extend(
        validate_with_schema(
            document,
            root / "resources" / "schemas" / "workflow.schema.json",
            workflow_file,
        )
    )
    if not isinstance(document, dict):
        return issues
    if document.get("workflow") != record.name:
        issues.append(ValidationIssue(workflow_file, f"workflow 必须等于 {record.name}"))

    stages = document.get("stages")
    if not isinstance(stages, list):
        return issues
    stage_map: dict[str, dict[str, Any]] = {}
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("id"), str):
            continue
        stage_id = stage["id"]
        if stage_id in stage_map:
            issues.append(ValidationIssue(workflow_file, f"stage id 重复: {stage_id}"))
        stage_map[stage_id] = stage

    graph: dict[str, list[str]] = {}
    required_qc_count = 0
    for stage_id, stage in stage_map.items():
        dependencies = stage.get("depends_on", [])
        if isinstance(dependencies, list):
            graph[stage_id] = [item for item in dependencies if isinstance(item, str)]
            for dependency in graph[stage_id]:
                if dependency not in stage_map:
                    issues.append(
                        ValidationIssue(workflow_file, f"{stage_id} 依赖不存在的 stage: {dependency}")
                    )
        qc = stage.get("qc")
        if isinstance(qc, dict) and qc.get("required") is True:
            required_qc_count += 1
        if stage.get("type") == "skill":
            skill_ref = stage.get("skill_ref")
            if skill_ref not in known_atomic_refs:
                issues.append(
                    ValidationIssue(workflow_file, f"{stage_id} 引用了不存在的 Skill: {skill_ref}")
                )
    cycle = find_cycle(graph)
    if cycle:
        issues.append(ValidationIssue(workflow_file, f"stage 依赖存在循环: {' -> '.join(cycle)}"))
    if required_qc_count == 0:
        issues.append(ValidationIssue(workflow_file, "workflow 至少需要一个必需 QC 节点"))

    feedback_targets = document.get("feedback_targets", [])
    if isinstance(feedback_targets, list):
        for feedback in feedback_targets:
            if not isinstance(feedback, dict):
                continue
            if feedback.get("from_stage") not in stage_map:
                issues.append(
                    ValidationIssue(
                        workflow_file,
                        f"feedback.from_stage 不存在: {feedback.get('from_stage')}",
                    )
                )
            if feedback.get("workflow") not in known_workflows:
                issues.append(
                    ValidationIssue(
                        workflow_file,
                        f"feedback workflow 不存在: {feedback.get('workflow')}",
                    )
                )
            if feedback.get("automatic") is not False:
                issues.append(ValidationIssue(workflow_file, "第一阶段 feedback.automatic 必须为 false"))
    return issues


def validate_repository(root: Path) -> tuple[list[ValidationIssue], list[Any]]:
    root = root.resolve()
    categories, category_issues = load_category_registry(root)
    issues = list(category_issues)
    atomic_records = discover_atomic_skills(root, categories)
    workflow_records = discover_workflows(root)
    known_workflows = {record.name for record in workflow_records}
    missing = EXPECTED_WORKFLOWS - known_workflows
    extra = known_workflows - EXPECTED_WORKFLOWS
    for workflow in sorted(missing):
        issues.append(ValidationIssue(root / "workflows" / workflow, "缺少固定 workflow"))
    for workflow in sorted(extra):
        issues.append(ValidationIssue(root / "workflows" / workflow, "第一阶段不允许新增 workflow"))

    known_atomic_refs = {record.source_ref for record in atomic_records}
    installed_names: set[str] = set()
    for record in workflow_records:
        if record.install_name in installed_names:
            issues.append(ValidationIssue(record.source_dir, f"安装名重复: {record.install_name}"))
        installed_names.add(record.install_name)
        issues.extend(_validate_workflow(root, record, known_atomic_refs, known_workflows))
    return issues, workflow_records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 HydroTune Workflow Skills")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)

    issues, records = validate_repository(args.root)
    if issues:
        print(f"Workflow 校验失败：{len(issues)} 个问题")
        for issue in issues:
            print(f"  - {issue.format(args.root)}")
        return 1
    print(f"Workflow 校验通过：{len(records)} 个 Workflow")
    return 0


if __name__ == "__main__":
    sys.exit(main())

