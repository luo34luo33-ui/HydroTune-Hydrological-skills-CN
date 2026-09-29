"""Shared repository discovery and parsing helpers for HydroTune-skills."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable

from jsonschema import Draft202012Validator
import yaml


NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_PATTERN = re.compile(
    r"\A---\s*\r?\n(?P<yaml>.*?)\r?\n---\s*(?:\r?\n|\Z)(?P<body>.*)\Z",
    re.DOTALL,
)
RESERVED_TOP_LEVEL_DIRS = {
    ".git",
    ".github",
    ".pytest_cache",
    "resources",
    "scripts",
    "tests",
    "workflows",
}


class RepositoryError(RuntimeError):
    """Raised when repository metadata cannot be read safely."""


@dataclass(frozen=True)
class ValidationIssue:
    path: Path
    message: str

    def format(self, root: Path | None = None) -> str:
        if root is not None:
            try:
                display = self.path.resolve().relative_to(root.resolve())
            except ValueError:
                display = self.path
        else:
            display = self.path
        return f"{display}: {self.message}"


@dataclass(frozen=True)
class SkillRecord:
    kind: str
    name: str
    source_dir: Path
    install_name: str
    category: str | None = None

    @property
    def source_ref(self) -> str:
        if self.kind == "atomic":
            return f"{self.category}/{self.name}"
        return f"workflows/{self.name}"


def read_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise RepositoryError(f"无法读取 YAML {path}: {exc}") from exc


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RepositoryError(f"无法读取 JSON {path}: {exc}") from exc


def validate_with_schema(instance: Any, schema_path: Path, source_path: Path) -> list[ValidationIssue]:
    try:
        schema = read_json(schema_path)
    except RepositoryError as exc:
        return [ValidationIssue(schema_path, str(exc))]

    issues: list[ValidationIssue] = []
    validator = Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path)):
        location = ".".join(str(part) for part in error.absolute_path)
        message = f"schema 校验失败{f' ({location})' if location else ''}: {error.message}"
        issues.append(ValidationIssue(source_path, message))
    return issues


def load_category_registry(root: Path) -> tuple[dict[str, dict[str, Any]], list[ValidationIssue]]:
    registry_path = root / "resources" / "categories.yaml"
    schema_path = root / "resources" / "schemas" / "categories.schema.json"
    try:
        document = read_yaml(registry_path)
    except RepositoryError as exc:
        return {}, [ValidationIssue(registry_path, str(exc))]

    issues = validate_with_schema(document, schema_path, registry_path)
    if not isinstance(document, dict) or not isinstance(document.get("categories"), list):
        return {}, issues

    categories: dict[str, dict[str, Any]] = {}
    for category in document["categories"]:
        if not isinstance(category, dict) or not isinstance(category.get("id"), str):
            continue
        category_id = category["id"]
        if category_id in categories:
            issues.append(ValidationIssue(registry_path, f"重复 category id: {category_id}"))
        categories[category_id] = category

    for category_id, category in categories.items():
        for overlap in category.get("overlaps", []):
            if overlap not in categories:
                issues.append(
                    ValidationIssue(registry_path, f"{category_id} 引用了不存在的 overlap: {overlap}")
                )
            elif category_id not in categories[overlap].get("overlaps", []):
                issues.append(
                    ValidationIssue(
                        registry_path,
                        f"overlap 必须双向声明: {category_id} -> {overlap}",
                    )
                )
    return categories, issues


def split_frontmatter_text(text: str, source: Path) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        raise RepositoryError(f"{source} 缺少完整 YAML frontmatter")
    try:
        frontmatter = yaml.safe_load(match.group("yaml"))
    except yaml.YAMLError as exc:
        raise RepositoryError(f"{source} frontmatter YAML 无效: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise RepositoryError(f"{source} frontmatter 必须是 YAML object")
    return frontmatter, match.group("body")


def read_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise RepositoryError(f"无法读取 {path}: {exc}") from exc
    return split_frontmatter_text(text, path)


def render_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    header = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip()
    return f"---\n{header}\n---\n\n{body.lstrip()}"


def discover_atomic_skills(root: Path, categories: Iterable[str]) -> list[SkillRecord]:
    records: list[SkillRecord] = []
    for category in sorted(categories):
        category_dir = root / category
        if not category_dir.is_dir():
            continue
        for child in sorted(category_dir.iterdir(), key=lambda path: path.name):
            if child.is_dir() and (child / "SKILL.md").is_file():
                records.append(
                    SkillRecord(
                        kind="atomic",
                        name=child.name,
                        category=category,
                        source_dir=child,
                        install_name=f"hydro-{category}-{child.name}",
                    )
                )
    return records


def discover_workflows(root: Path) -> list[SkillRecord]:
    workflow_root = root / "workflows"
    if not workflow_root.is_dir():
        return []
    records: list[SkillRecord] = []
    for child in sorted(workflow_root.iterdir(), key=lambda path: path.name):
        if child.is_dir() and (child / "SKILL.md").is_file():
            records.append(
                SkillRecord(
                    kind="workflow",
                    name=child.name,
                    source_dir=child,
                    install_name=f"hydro-workflow-{child.name}",
                )
            )
    return records


def contains_trigger_boundaries(description: str) -> bool:
    lowered = description.lower()
    positive = any(token in lowered for token in ("use when", "适用于", "用于"))
    negative = any(token in lowered for token in ("do not use", "不适用于", "不要用于"))
    return positive and negative


def find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        if node in visiting:
            start = stack.index(node)
            return stack[start:] + [node]
        if node in visited:
            return None
        visiting.add(node)
        stack.append(node)
        for dependency in graph.get(node, []):
            cycle = visit(dependency)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return None

    for candidate in graph:
        cycle = visit(candidate)
        if cycle:
            return cycle
    return None

