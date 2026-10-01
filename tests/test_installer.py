from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import yaml


def _run_engine(
    repo: Path,
    target: Path,
    *arguments: str,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(repo / "scripts" / "install_skills.py"),
        "--repo-root",
        str(repo),
        "--tool-name",
        "Test Agent",
        "--default-target",
        str(target),
        "--project-subdir",
        ".agents/skills",
        *arguments,
    ]
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


def test_default_dry_run_lists_skills_and_workflows_without_writes(
    repo_root: Path, tmp_path: Path, utf8_env: dict[str, str]
) -> None:
    target = tmp_path / "不存在的 目标（测试）"
    result = _run_engine(repo_root, target, "--dry-run", environment=utf8_env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("将安装: hydro-workflow-") == 3
    assert result.stdout.count("将安装: hydro-data-processing-") == 5
    assert result.stdout.count("将安装: hydro-spatial-analysis-") == 6
    assert result.stdout.count("将安装: hydro-visualization-reporting-") == 8
    assert result.stdout.count("将安装: hydro-model-calibration-") == 5
    assert result.stdout.count("将安装: hydro-hydrological-modeling-") == 11
    assert result.stdout.count("将安装: hydro-post-processing-") == 2
    assert result.stdout.count("将安装: hydro-evaluation-diagnostics-") == 3
    assert not target.exists()


def test_category_filter_excludes_workflows(
    repo_root: Path, tmp_path: Path, utf8_env: dict[str, str]
) -> None:
    target = tmp_path / "target"
    result = _run_engine(
        repo_root,
        target,
        "--categories",
        "data-processing",
        environment=utf8_env,
    )
    assert result.returncode == 0, result.stderr
    installed = sorted(path.name for path in target.iterdir() if path.is_dir())
    assert installed == sorted([
        "hydro-data-processing-derive-potential-evapotranspiration",
        "hydro-data-processing-extract-flood-events",
        "hydro-data-processing-prepare-discharge-timeseries",
        "hydro-data-processing-prepare-model-forcing-timeseries",
        "hydro-data-processing-separate-baseflow-eckhardt",
    ])
    assert not any(name.startswith("hydro-workflow-") for name in installed)


def test_category_filter_can_explicitly_include_workflows(
    repo_root: Path, tmp_path: Path, utf8_env: dict[str, str]
) -> None:
    target = tmp_path / "target"
    result = _run_engine(
        repo_root,
        target,
        "--categories",
        "data-processing",
        "--workflows",
        "simulation-analysis",
        environment=utf8_env,
    )
    assert result.returncode == 0, result.stderr
    installed = sorted(path.name for path in target.iterdir() if path.is_dir())
    assert installed == sorted([
        "hydro-data-processing-derive-potential-evapotranspiration",
        "hydro-data-processing-extract-flood-events",
        "hydro-data-processing-prepare-discharge-timeseries",
        "hydro-data-processing-prepare-model-forcing-timeseries",
        "hydro-data-processing-separate-baseflow-eckhardt",
        "hydro-workflow-simulation-analysis",
    ])


def test_spatial_category_installs_six_atomic_skills(
    repo_root: Path, tmp_path: Path, utf8_env: dict[str, str]
) -> None:
    target = tmp_path / "空间 skills（测试）"
    result = _run_engine(
        repo_root,
        target,
        "--categories",
        "spatial-analysis",
        environment=utf8_env,
    )
    assert result.returncode == 0, result.stderr
    installed = sorted(path.name for path in target.iterdir() if path.is_dir())
    assert installed == sorted([
        "hydro-spatial-analysis-aggregate-forcing-to-model-units",
        "hydro-spatial-analysis-build-hydrological-topology",
        "hydro-spatial-analysis-derive-topmodel-terrain-inputs",
        "hydro-spatial-analysis-extract-dem-stream-network",
        "hydro-spatial-analysis-prepare-dem-analysis-grid",
        "hydro-spatial-analysis-validate-hydrological-topology",
    ])
    for skill_dir in target.iterdir():
        if skill_dir.is_dir():
            assert any((skill_dir / "scripts").glob("*.py"))


def test_model_calibration_category_installs_five_self_contained_skills(
    repo_root: Path, tmp_path: Path, utf8_env: dict[str, str]
) -> None:
    target = tmp_path / "率定 skills（测试）"
    result = _run_engine(
        repo_root,
        target,
        "--categories",
        "model-calibration",
        environment=utf8_env,
    )
    assert result.returncode == 0, result.stderr
    installed = sorted(path.name for path in target.iterdir() if path.is_dir())
    assert installed == sorted([
        "hydro-model-calibration-calibrate-model-de",
        "hydro-model-calibration-calibrate-model-ga",
        "hydro-model-calibration-calibrate-model-pso",
        "hydro-model-calibration-calibrate-model-sce-ua",
        "hydro-model-calibration-calibrate-model-two-stage",
    ])
    for skill_dir in target.iterdir():
        if skill_dir.is_dir():
            assert (skill_dir / "scripts" / "_calibration_common.py").is_file()
            assert any((skill_dir / "scripts").glob("calibrate_model_*.py"))


def test_visualization_category_installs_eight_self_contained_skills(
    repo_root: Path, tmp_path: Path, utf8_env: dict[str, str]
) -> None:
    target = tmp_path / "图册 skills（测试）"
    result = _run_engine(
        repo_root,
        target,
        "--categories",
        "visualization-reporting",
        environment=utf8_env,
    )
    assert result.returncode == 0, result.stderr
    installed = sorted(path.name for path in target.iterdir() if path.is_dir())
    assert installed == sorted([
        "hydro-visualization-reporting-generate-spatial-data-processing-report",
        "hydro-visualization-reporting-generate-timeseries-data-processing-report",
        "hydro-visualization-reporting-generate-hydrological-study-report",
        "hydro-visualization-reporting-review-hydrological-study-report",
        "hydro-visualization-reporting-visualize-dem-hydrology-atlas",
        "hydro-visualization-reporting-visualize-flood-event-atlas",
        "hydro-visualization-reporting-visualize-hydrobase-atlas",
        "hydro-visualization-reporting-visualize-model-calibration",
    ])
    for skill_dir in target.iterdir():
        if skill_dir.is_dir():
            if "generate-timeseries-data-processing-report" in skill_dir.name:
                assert (skill_dir / "scripts" / "generate_timeseries_data_processing_report.py").is_file()
                assert (skill_dir / "scripts" / "_report_common.py").is_file()
                assert (skill_dir / "assets" / "processing-report.schema.json").is_file()
                continue
            elif "generate-spatial-data-processing-report" in skill_dir.name:
                assert (skill_dir / "scripts" / "generate_spatial_data_processing_report.py").is_file()
                assert (skill_dir / "assets" / "spatial-processing-manifest.schema.json").is_file()
                continue
            elif "hydrological-study-report" in skill_dir.name:
                assert (skill_dir / "scripts" / "_report_common.py").is_file()
                assert (skill_dir / "assets" / "study-report.schema.json").is_file()
                assert (skill_dir / "assets" / "upstream-catalog.json").is_file()
                continue
            elif "model-calibration" in skill_dir.name:
                assert (skill_dir / "assets" / "calibration-atlas-style-v2.json").is_file()
                assert (skill_dir / "scripts" / "_calibration_plot.py").is_file()
                assert (skill_dir / "scripts" / "render_model_calibration_atlas.py").is_file()
            elif "flood-event" in skill_dir.name:
                assert (skill_dir / "assets" / "flood-event-style-v2.json").is_file()
                assert (skill_dir / "scripts" / "_event_atlas_common.py").is_file()
            elif "visualize-hydrobase-atlas" in skill_dir.name:
                assert (skill_dir / "assets" / "morphometry-style-v2.json").is_file()
                assert (skill_dir / "assets" / "topology-style-v2.json").is_file()
                assert (skill_dir / "scripts" / "_topology_plot.py").is_file()
                assert (skill_dir / "scripts" / "_morphometry_maps.py").is_file()
                assert (skill_dir / "scripts" / "_morphometry_plot.py").is_file()
            elif "visualize-dem-hydrology-atlas" in skill_dir.name:
                assert (skill_dir / "assets" / "atlas-style-v2.json").is_file()
                assert (skill_dir / "scripts" / "_atlas_common.py").is_file()
            else:
                assert (skill_dir / "assets" / "atlas-style-v1.json").is_file()
                assert (skill_dir / "scripts" / "_atlas_common.py").is_file()
            assert any((skill_dir / "scripts").glob("render_*_atlas.py"))


def test_install_rewrites_layout_name_and_links(
    repo_root: Path, tmp_path: Path, utf8_env: dict[str, str]
) -> None:
    target = tmp_path / "含 空格（测试）" / "skills"
    result = _run_engine(repo_root, target, environment=utf8_env)
    assert result.returncode == 0, result.stderr
    skill_dir = target / "hydro-workflow-data-preprocessing"
    frontmatter_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(frontmatter_text.split("---", 2)[1])
    assert frontmatter["name"] == "hydro-workflow-data-preprocessing"
    assert "(references/usage-guide.md)" in frontmatter_text
    assert "(references/workflow.yaml)" in frontmatter_text
    assert (skill_dir / "references" / "usage-guide.md").is_file()
    assert (skill_dir / "references" / "workflow.yaml").is_file()
    manifest = json.loads((target / ".hydrotune-skills-manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["installed"]) == 43


def test_update_uses_content_hash(
    repo_copy: Path, tmp_path: Path, utf8_env: dict[str, str]
) -> None:
    target = tmp_path / "target"
    first = _run_engine(repo_copy, target, environment=utf8_env)
    assert first.returncode == 0, first.stderr
    manifest_path = target / ".hydrotune-skills-manifest.json"
    before = json.loads(manifest_path.read_text(encoding="utf-8"))["installed"][
        "hydro-workflow-data-preprocessing"
    ]["sha256"]

    guide = repo_copy / "workflows" / "data-preprocessing" / "usage-guide.md"
    guide.write_text(guide.read_text(encoding="utf-8") + "\nHash update test.\n", encoding="utf-8")
    update = _run_engine(repo_copy, target, "--update", environment=utf8_env)
    assert update.returncode == 0, update.stderr
    after = json.loads(manifest_path.read_text(encoding="utf-8"))["installed"][
        "hydro-workflow-data-preprocessing"
    ]["sha256"]
    assert after != before
    assert "跳过 42 个" in update.stdout


def test_update_retires_managed_old_report_with_recoverable_archive(repo_root, tmp_path, utf8_env):
    import zipfile
    target = tmp_path / "installed"
    assert _run_engine(repo_root, target, "--categories", "visualization-reporting", environment=utf8_env).returncode == 0
    current = "hydro-visualization-reporting-generate-timeseries-data-processing-report"
    old = "hydro-visualization-reporting-generate-data-processing-report"
    (target / current).rename(target / old)
    (target / old / "personal-note.txt").write_text("preserve my edits", encoding="utf-8")
    manifest_path = target / ".hydrotune-skills-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["installed"][old] = manifest["installed"].pop(current)
    manifest["installed"][old]["source"] = "visualization-reporting/generate-data-processing-report"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    preview = _run_engine(repo_root, target, "--categories", "visualization-reporting", "--update", "--dry-run", environment=utf8_env)
    assert preview.returncode == 0, preview.stderr
    assert (target / old).exists() and not (target / ".hydrotune-retired-skills").exists()
    update = _run_engine(repo_root, target, "--categories", "visualization-reporting", "--update", environment=utf8_env)
    assert update.returncode == 0, update.stderr
    assert (target / current).is_dir() and not (target / old).exists()
    after = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert old not in after["installed"] and current in after["installed"]
    archive = next((target / ".hydrotune-retired-skills").glob("*.zip"))
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("personal-note.txt") == b"preserve my edits"


def test_uninstall_preserves_unrelated_content(
    repo_root: Path, tmp_path: Path, utf8_env: dict[str, str]
) -> None:
    target = tmp_path / "target"
    installed = _run_engine(repo_root, target, environment=utf8_env)
    assert installed.returncode == 0, installed.stderr
    unrelated = target / "unrelated-skill"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("keep", encoding="utf-8")

    removed = _run_engine(repo_root, target, "--uninstall", environment=utf8_env)
    assert removed.returncode == 0, removed.stderr
    assert unrelated.is_dir()
    assert not any(target.glob("hydro-workflow-*"))
    assert not (target / ".hydrotune-skills-manifest.json").exists()


def test_dry_run_uninstall_preserves_managed_content(
    repo_root: Path, tmp_path: Path, utf8_env: dict[str, str]
) -> None:
    target = tmp_path / "target"
    installed = _run_engine(repo_root, target, environment=utf8_env)
    assert installed.returncode == 0, installed.stderr
    manifest = target / ".hydrotune-skills-manifest.json"
    before = manifest.read_bytes()

    preview = _run_engine(
        repo_root,
        target,
        "--uninstall",
        "--dry-run",
        environment=utf8_env,
    )
    assert preview.returncode == 0, preview.stderr
    assert manifest.read_bytes() == before
    assert len(list(target.glob("hydro-workflow-*"))) == 3


def test_unmanaged_collision_requires_force(
    repo_root: Path, tmp_path: Path, utf8_env: dict[str, str]
) -> None:
    target = tmp_path / "target"
    collision = target / "hydro-workflow-data-preprocessing"
    collision.mkdir(parents=True)
    marker = collision / "unmanaged.txt"
    marker.write_text("do not silently overwrite", encoding="utf-8")

    rejected = _run_engine(repo_root, target, environment=utf8_env)
    assert rejected.returncode == 2
    assert marker.is_file()

    forced = _run_engine(repo_root, target, "--force", environment=utf8_env)
    assert forced.returncode == 0, forced.stderr
    assert not marker.exists()
    assert (collision / "SKILL.md").is_file()


def test_force_never_overwrites_a_same_name_file(
    repo_root: Path, tmp_path: Path, utf8_env: dict[str, str]
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    collision = target / "hydro-workflow-data-preprocessing"
    collision.write_text("keep", encoding="utf-8")
    result = _run_engine(repo_root, target, "--force", environment=utf8_env)
    assert result.returncode == 2
    assert collision.read_text(encoding="utf-8") == "keep"


def test_unknown_category_is_an_error(
    repo_root: Path, tmp_path: Path, utf8_env: dict[str, str]
) -> None:
    result = _run_engine(
        repo_root,
        tmp_path / "target",
        "--categories",
        "not-a-category",
        environment=utf8_env,
    )
    assert result.returncode == 2
    assert "未知 category" in result.stderr
