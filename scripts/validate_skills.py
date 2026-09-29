#!/usr/bin/env python3
"""Validate HydroTune atomic skills and the category registry."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from repository import (
    NAME_PATTERN,
    RESERVED_TOP_LEVEL_DIRS,
    RepositoryError,
    ValidationIssue,
    contains_trigger_boundaries,
    discover_atomic_skills,
    load_category_registry,
    read_frontmatter,
    read_yaml,
    validate_with_schema,
)


REQUIRED_METADATA = {
    "category",
    "domains",
    "tool_type",
    "primary_tool",
    "related_skills",
}
TOOL_TYPES = {"instruction", "python", "r", "cli", "mixed"}
PLACEHOLDER_MARKERS = ("TODO", "TBD", "FIXME", "<placeholder>", "待补充", "占位内容")


def _validate_category_skeleton(
    root: Path, categories: dict[str, dict[str, Any]]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for category in categories:
        category_dir = root / category
        if not category_dir.is_dir():
            issues.append(ValidationIssue(category_dir, "category 目录不存在"))
        elif not (category_dir / "README.md").is_file():
            issues.append(ValidationIssue(category_dir / "README.md", "category 边界 README 不存在"))
    return issues


def _find_misplaced_skill_files(
    root: Path, categories: dict[str, dict[str, Any]]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for skill_file in root.rglob("SKILL.md"):
        try:
            relative = skill_file.relative_to(root)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] == "workflows":
            continue
        valid = len(relative.parts) == 3 and relative.parts[0] in categories
        if not valid:
            issues.append(
                ValidationIssue(
                    skill_file,
                    "Atomic Skill 必须严格位于 <category>/<skill>/SKILL.md",
                )
            )
    return issues


def _validate_atomic_skill(
    root: Path,
    record: Any,
    known_refs: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    skill_file = record.source_dir / "SKILL.md"
    usage_file = record.source_dir / "usage-guide.md"
    contract_file = record.source_dir / "data-contract.yaml"
    examples_dir = record.source_dir / "examples"

    for required in (usage_file, contract_file):
        if not required.is_file():
            issues.append(ValidationIssue(required, "缺少必需文件"))
    if not examples_dir.is_dir():
        issues.append(ValidationIssue(examples_dir, "缺少 examples/ 目录"))
    elif not any(path.is_file() for path in examples_dir.rglob("*")):
        issues.append(ValidationIssue(examples_dir, "examples/ 必须包含至少一个经过验证的文件"))

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
    if not isinstance(description, str) or not description.strip():
        issues.append(ValidationIssue(skill_file, "description 不能为空"))
    elif not contains_trigger_boundaries(description):
        issues.append(ValidationIssue(skill_file, "description 必须明确适用与不适用边界"))

    if not isinstance(metadata, dict):
        issues.append(ValidationIssue(skill_file, "metadata 必须是 object"))
        metadata = {}
    missing_metadata = REQUIRED_METADATA - set(metadata)
    if missing_metadata:
        issues.append(
            ValidationIssue(skill_file, f"metadata 缺少字段: {', '.join(sorted(missing_metadata))}")
        )
    if metadata.get("category") != record.category:
        issues.append(ValidationIssue(skill_file, "metadata.category 必须等于物理目录 category"))
    domains = metadata.get("domains")
    if not isinstance(domains, list) or record.category not in domains:
        issues.append(ValidationIssue(skill_file, "metadata.domains 必须包含主要 category"))
    if metadata.get("tool_type") not in TOOL_TYPES:
        issues.append(ValidationIssue(skill_file, f"metadata.tool_type 必须属于 {sorted(TOOL_TYPES)}"))
    if not isinstance(metadata.get("primary_tool"), str) or not metadata.get("primary_tool", "").strip():
        issues.append(ValidationIssue(skill_file, "metadata.primary_tool 必须是单个非空字符串"))

    related = metadata.get("related_skills")
    if not isinstance(related, list) or any(not isinstance(item, str) for item in related):
        issues.append(ValidationIssue(skill_file, "metadata.related_skills 必须是字符串数组"))
    else:
        for related_ref in related:
            if related_ref not in known_refs:
                issues.append(ValidationIssue(skill_file, f"related skill 不存在: {related_ref}"))

    if contract_file.is_file():
        try:
            contract = read_yaml(contract_file)
        except RepositoryError as exc:
            issues.append(ValidationIssue(contract_file, str(exc)))
        else:
            issues.extend(
                validate_with_schema(
                    contract,
                    root / "resources" / "schemas" / "data-contract.schema.json",
                    contract_file,
                )
            )
            if isinstance(contract, dict) and contract.get("skill") != record.source_ref:
                issues.append(
                    ValidationIssue(contract_file, f"skill 必须等于 {record.source_ref}")
                )

    inspect_files = [skill_file, usage_file, contract_file]
    if examples_dir.is_dir():
        inspect_files.extend(path for path in examples_dir.rglob("*") if path.is_file())
    for path in inspect_files:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        for marker in PLACEHOLDER_MARKERS:
            if marker in text:
                issues.append(ValidationIssue(path, f"包含未完成占位标记: {marker}"))
                break
    return issues


def validate_repository(root: Path) -> tuple[list[ValidationIssue], list[Any]]:
    root = root.resolve()
    categories, issues = load_category_registry(root)
    issues.extend(_validate_category_skeleton(root, categories))
    issues.extend(_find_misplaced_skill_files(root, categories))

    records = discover_atomic_skills(root, categories)
    installed_names: set[str] = set()
    known_refs = {record.source_ref for record in records}
    for record in records:
        if record.install_name in installed_names:
            issues.append(ValidationIssue(record.source_dir, f"安装名重复: {record.install_name}"))
        installed_names.add(record.install_name)
        issues.extend(_validate_atomic_skill(root, record, known_refs))

    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name in categories or child.name in RESERVED_TOP_LEVEL_DIRS:
            continue
        if any(child.rglob("SKILL.md")):
            issues.append(ValidationIssue(child, "未注册目录不能包含 Atomic Skill"))
    return issues, records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 HydroTune Atomic Skills")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)

    issues, records = validate_repository(args.root)
    if issues:
        print(f"Atomic Skill 校验失败：{len(issues)} 个问题")
        for issue in issues:
            print(f"  - {issue.format(args.root)}")
        return 1
    print(f"Atomic Skill 校验通过：{len(records)} 个 Skill")
    return 0


if __name__ == "__main__":
    sys.exit(main())

