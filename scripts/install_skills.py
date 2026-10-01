#!/usr/bin/env python3
"""Cross-platform installation engine for HydroTune skills."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any
import uuid
import zipfile

from repository import (
    RepositoryError,
    SkillRecord,
    discover_atomic_skills,
    discover_workflows,
    load_category_registry,
    read_frontmatter,
    render_frontmatter,
)
from validate_skills import validate_repository as validate_skills_repository
from validate_workflows import validate_repository as validate_workflows_repository


MANIFEST_NAME = ".hydrotune-skills-manifest.json"
MANIFEST_SCHEMA_VERSION = 1
SAFE_INSTALL_NAME = re.compile(r"^hydro-[a-z0-9]+(?:-[a-z0-9]+)*$")


def retire_processing_report(target: Path, owned: dict[str, Any], *, dry_run: bool) -> None:
    """Preserve a managed old installation before retiring the renamed skill."""
    old = "hydro-visualization-reporting-generate-data-processing-report"
    previous = owned.get(old)
    if not isinstance(previous, dict) or previous.get("source") != "visualization-reporting/generate-data-processing-report":
        return
    root = target.resolve()
    path = target / old
    if path.is_symlink() or path.resolve() != root / old:
        raise InstallerError(f"旧报告安装目录不是安全的受管目录: {path}")
    if path.exists() and not path.is_dir():
        raise InstallerError(f"旧报告安装项不是目录: {path}")
    files = list(path.rglob("*")) if path.is_dir() else []
    if any(p.is_symlink() or not p.resolve().is_relative_to(path.resolve()) for p in files):
        raise InstallerError("旧报告安装项包含链接，拒绝自动迁移")
    if dry_run:
        print(f"  将归档并移除已重命名的受管安装项: {old}")
        return
    if path.is_dir():
        archives = target / ".hydrotune-retired-skills"
        if archives.is_symlink() or not archives.resolve().is_relative_to(root):
            raise InstallerError("归档目录超出安装目标，拒绝迁移")
        archives.mkdir(exist_ok=True)
        archive = archives / f"{old}-{uuid.uuid4().hex}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for item in sorted(files):
                if item.is_file():
                    bundle.write(item, item.relative_to(path))
        # Resolve again immediately before recursive removal; never remove a computed
        # target outside the explicitly selected installation directory.
        if path.resolve() != root / old:
            raise InstallerError("旧报告目录在归档期间发生变化，拒绝移除")
        shutil.rmtree(path)
        print(f"  旧报告已归档: {archive}")
    del owned[old]


class InstallerError(RuntimeError):
    """Raised for a user-facing installation failure."""


def _csv_values(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        raise InstallerError("筛选参数不能为空")
    return values


def _rewrite_resource_links(body: str) -> str:
    replacements = {
        "(usage-guide.md)": "(references/usage-guide.md)",
        "(data-contract.yaml)": "(references/data-contract.yaml)",
        "(workflow.yaml)": "(references/workflow.yaml)",
    }
    for source, target in replacements.items():
        body = body.replace(source, target)
    return body


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise InstallerError(f"无法读取 {path}: {exc}") from exc


def render_installation(record: SkillRecord) -> dict[str, bytes]:
    """Render one canonical source skill into Agent Skills layout."""
    try:
        frontmatter, body = read_frontmatter(record.source_dir / "SKILL.md")
    except RepositoryError as exc:
        raise InstallerError(str(exc)) from exc
    frontmatter["name"] = record.install_name
    rendered_skill = render_frontmatter(frontmatter, _rewrite_resource_links(body))
    files: dict[str, bytes] = {"SKILL.md": rendered_skill.encode("utf-8")}

    usage_guide = record.source_dir / "usage-guide.md"
    if usage_guide.is_file():
        files["references/usage-guide.md"] = _read_bytes(usage_guide)

    if record.kind == "atomic":
        data_contract = record.source_dir / "data-contract.yaml"
        if data_contract.is_file():
            files["references/data-contract.yaml"] = _read_bytes(data_contract)
        examples = record.source_dir / "examples"
        if examples.is_dir():
            for source in sorted(examples.rglob("*")):
                if source.is_symlink():
                    raise InstallerError(f"examples/ 不允许符号链接: {source}")
                if not source.is_file() or source.suffix == ".pyc" or "__pycache__" in source.parts:
                    continue
                relative = source.relative_to(examples).as_posix()
                files[f"scripts/{relative}"] = _read_bytes(source)
    else:
        workflow = record.source_dir / "workflow.yaml"
        if workflow.is_file():
            files["references/workflow.yaml"] = _read_bytes(workflow)

    assets = record.source_dir / "assets"
    if assets.is_dir():
        for source in sorted(assets.rglob("*")):
            if source.is_symlink():
                raise InstallerError(f"assets/ 不允许符号链接: {source}")
            if source.is_file():
                relative = source.relative_to(assets).as_posix()
                files[f"assets/{relative}"] = _read_bytes(source)
    return files


def content_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative_path in sorted(files):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[relative_path])
        digest.update(b"\0")
    return digest.hexdigest()


def _empty_manifest(tool_name: str) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "project": "HydroTune-skills",
        "tool": tool_name,
        "installed": {},
    }


def load_manifest(target: Path, tool_name: str) -> dict[str, Any]:
    manifest_path = target / MANIFEST_NAME
    if not manifest_path.exists():
        return _empty_manifest(tool_name)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallerError(f"安装清单无效，拒绝继续: {manifest_path}: {exc}") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION
        or manifest.get("project") != "HydroTune-skills"
        or not isinstance(manifest.get("installed"), dict)
    ):
        raise InstallerError(f"安装清单格式不受支持，拒绝继续: {manifest_path}")
    return manifest


def write_manifest(target: Path, manifest: dict[str, Any]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / MANIFEST_NAME
    temporary = target / f".{MANIFEST_NAME}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)


def _write_rendered_directory(target: Path, install_name: str, files: dict[str, bytes]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    destination = target / install_name
    # Keep staging paths short: long skill names plus nested project paths can
    # exceed Windows MAX_PATH before the final directory is installed.
    temporary = Path(tempfile.mkdtemp(prefix=".hydrotune-tmp-", dir=target))
    backup = target / f".hydrotune-backup-{uuid.uuid4().hex}"
    try:
        for relative_path, content in files.items():
            output = temporary / Path(relative_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
        had_destination = destination.exists()
        if had_destination:
            destination.rename(backup)
        try:
            temporary.rename(destination)
        except Exception:
            if had_destination and backup.exists() and not destination.exists():
                backup.rename(destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        if backup.exists() and destination.exists():
            shutil.rmtree(backup)


def validate_all(root: Path, verbose: bool = True) -> bool:
    skill_issues, skills = validate_skills_repository(root)
    workflow_issues, workflows = validate_workflows_repository(root)
    unique_issues = {
        (str(issue.path.resolve()), issue.message): issue
        for issue in skill_issues + workflow_issues
    }
    if unique_issues:
        print(f"仓库校验失败：{len(unique_issues)} 个问题")
        for issue in unique_issues.values():
            print(f"  - {issue.format(root)}")
        return False
    if verbose:
        print(f"仓库校验通过：{len(skills)} 个 Atomic Skill，{len(workflows)} 个 Workflow")
    return True


def list_repository(root: Path) -> int:
    categories, issues = load_category_registry(root)
    if issues:
        for issue in issues:
            print(f"错误: {issue.format(root)}", file=sys.stderr)
        return 1
    atomic = discover_atomic_skills(root, categories)
    workflows = discover_workflows(root)
    counts = {category: 0 for category in categories}
    for record in atomic:
        counts[record.category] += 1

    print("可用 Category：")
    for category, details in categories.items():
        print(f"  {category:<28} {counts[category]:>3}  {details['title_zh']}")
    print("\nAtomic Skills：")
    if atomic:
        for record in atomic:
            print(f"  {record.source_ref} -> {record.install_name}")
    else:
        print("  当前无可安装 Atomic Skill")
    print("\nWorkflows：")
    for record in workflows:
        print(f"  {record.name} -> {record.install_name}")
    return 0


def _resolve_target(args: argparse.Namespace) -> Path:
    if args.project is not None:
        project = Path(args.project).expanduser().resolve()
        if not project.is_dir():
            raise InstallerError(f"项目目录不存在: {project}")
        return (project / Path(args.project_subdir)).resolve()
    return Path(args.default_target).expanduser().resolve()


def _select_records(
    root: Path,
    categories: dict[str, dict[str, Any]],
    category_filter: list[str] | None,
    workflow_filter: list[str] | None,
) -> tuple[list[SkillRecord], int]:
    atomic = discover_atomic_skills(root, categories)
    workflows = discover_workflows(root)
    if category_filter is not None:
        unknown = sorted(set(category_filter) - set(categories))
        if unknown:
            raise InstallerError(
                f"未知 category: {', '.join(unknown)}。可用值: {', '.join(categories)}"
            )
        atomic = [record for record in atomic if record.category in category_filter]

    selected_workflows: list[SkillRecord]
    if workflow_filter is None:
        selected_workflows = workflows if category_filter is None else []
    else:
        available = {record.name: record for record in workflows}
        if workflow_filter == ["all"]:
            selected_workflows = workflows
        else:
            unknown = sorted(set(workflow_filter) - set(available))
            if unknown:
                raise InstallerError(
                    f"未知 workflow: {', '.join(unknown)}。可用值: {', '.join(available)}"
                )
            selected_workflows = [available[name] for name in workflow_filter]
    return atomic + selected_workflows, len(atomic)


def install_records(
    records: list[SkillRecord],
    target: Path,
    tool_name: str,
    *,
    dry_run: bool,
    force: bool,
    update: bool,
    verbose: bool,
) -> int:
    manifest = load_manifest(target, tool_name)
    owned = manifest["installed"]
    rendered: dict[str, tuple[SkillRecord, dict[str, bytes], str]] = {}
    collisions: list[str] = []
    for record in records:
        files = render_installation(record)
        digest = content_hash(files)
        rendered[record.install_name] = (record, files, digest)
        destination = target / record.install_name
        if destination.exists() and not destination.is_dir():
            raise InstallerError(f"同名目标不是目录，拒绝覆盖: {destination}")
        if destination.exists() and record.install_name not in owned and not force:
            collisions.append(record.install_name)
    if collisions:
        raise InstallerError(
            "目标目录包含不受本项目管理的同名内容: "
            + ", ".join(collisions)
            + "。确认后可使用 --force 覆盖。"
        )

    action = "预览安装到" if dry_run else "安装到"
    print(f"{action}: {target}")
    installed = 0
    skipped = 0
    for install_name, (record, files, digest) in rendered.items():
        previous = owned.get(install_name)
        destination = target / install_name
        if (
            update
            and isinstance(previous, dict)
            and previous.get("sha256") == digest
            and destination.is_dir()
        ):
            skipped += 1
            if verbose:
                print(f"  未变化: {install_name}")
            continue
        if dry_run:
            print(f"  将安装: {install_name}")
        else:
            _write_rendered_directory(target, install_name, files)
            owned[install_name] = {
                "kind": record.kind,
                "source": record.source_ref,
                "sha256": digest,
                "files": sorted(files),
            }
            print(f"  已安装: {install_name}")
        installed += 1

    renamed = "hydro-visualization-reporting-generate-timeseries-data-processing-report"
    if update and renamed in rendered:
        retire_processing_report(target, owned, dry_run=dry_run)
    if not dry_run:
        manifest["tool"] = tool_name
        write_manifest(target, manifest)
    if dry_run:
        print(f"预览完成：将安装 {installed} 个，跳过 {skipped} 个")
    else:
        print(f"安装完成：安装 {installed} 个，跳过 {skipped} 个")
    return 0


def uninstall(target: Path, tool_name: str, dry_run: bool) -> int:
    manifest = load_manifest(target, tool_name)
    installed = manifest["installed"]
    if not installed:
        print(f"没有由 HydroTune-skills 管理的安装项: {target}")
        return 0
    removed = 0
    for install_name in sorted(installed):
        if not SAFE_INSTALL_NAME.fullmatch(install_name) or Path(install_name).name != install_name:
            raise InstallerError(f"安装清单包含不安全的目录名，拒绝卸载: {install_name}")
        destination = target / install_name
        if dry_run:
            print(f"  将移除: {destination}")
        elif destination.is_dir():
            shutil.rmtree(destination)
            print(f"  已移除: {install_name}")
        elif destination.exists():
            raise InstallerError(f"受管目标不是目录，拒绝删除: {destination}")
        removed += 1
    if not dry_run:
        manifest["installed"] = {}
        manifest_path = target / MANIFEST_NAME
        if manifest_path.exists():
            manifest_path.unlink()
    print(f"{'预览' if dry_run else '卸载'}完成：{removed} 个受管安装项")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="安装 HydroTune Skills",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", required=True, type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--tool-name", required=True, help=argparse.SUPPRESS)
    parser.add_argument("--default-target", required=True, help=argparse.SUPPRESS)
    parser.add_argument("--project-subdir", required=True, help=argparse.SUPPRESS)

    location = parser.add_mutually_exclusive_group()
    location.add_argument("--global", dest="global_install", action="store_true", help="安装到默认全局目录（默认）")
    location.add_argument(
        "--project",
        nargs="?",
        const=".",
        metavar="PATH",
        help="安装到当前或指定项目目录",
    )
    parser.add_argument("--categories", metavar="CATS", help="仅安装逗号分隔的 category")
    parser.add_argument("--workflows", metavar="IDS", help="安装 all 或逗号分隔的 workflow")

    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="列出可用 Skill 和 Workflow")
    action.add_argument("--validate", action="store_true", help="校验仓库并退出")
    action.add_argument("--uninstall", action="store_true", help="卸载清单记录的所有受管 Skill")
    parser.add_argument("--update", action="store_true", help="仅更新内容哈希发生变化的 Skill")
    parser.add_argument("--dry-run", action="store_true", help="预览操作且不写入文件")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")
    parser.add_argument("--force", action="store_true", help="覆盖不受本项目管理的同名目录")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    try:
        if args.list:
            return list_repository(root)
        if args.validate:
            return 0 if validate_all(root) else 1

        target = _resolve_target(args)
        if args.uninstall:
            return uninstall(target, args.tool_name, args.dry_run)

        categories, category_issues = load_category_registry(root)
        if category_issues:
            for issue in category_issues:
                print(f"错误: {issue.format(root)}", file=sys.stderr)
            return 1
        category_filter = _csv_values(args.categories)
        workflow_filter = _csv_values(args.workflows)
        records, atomic_count = _select_records(
            root,
            categories,
            category_filter,
            workflow_filter,
        )
        if not validate_all(root, verbose=args.verbose):
            return 1
        if category_filter is not None and atomic_count == 0:
            print(f"当前无可安装 Atomic Skill: {', '.join(category_filter)}")
        if not records:
            print("没有符合筛选条件的安装项；未写入目标目录。")
            return 0
        return install_records(
            records,
            target,
            args.tool_name,
            dry_run=args.dry_run,
            force=args.force,
            update=args.update,
            verbose=args.verbose,
        )
    except InstallerError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
