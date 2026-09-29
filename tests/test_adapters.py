from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess

import pytest


@pytest.mark.parametrize(
    ("script_name", "project_subdir"),
    [
        ("install-codex.ps1", Path(".agents/skills")),
        ("install-claude.ps1", Path(".claude/skills")),
        ("install-opencode.ps1", Path(".opencode/skills")),
    ],
)
def test_powershell_adapters_install_to_expected_project_layout(
    repo_root: Path,
    tmp_path: Path,
    utf8_env: dict[str, str],
    script_name: str,
    project_subdir: Path,
) -> None:
    executable = shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is not available")
    project = tmp_path / f"项目 {script_name}（测试）"
    project.mkdir()
    result = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-File",
            str(repo_root / script_name),
            "--project",
            str(project),
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
        env=utf8_env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    target = project / project_subdir
    assert len(list(target.glob("hydro-workflow-*"))) == 3


@pytest.mark.parametrize(
    ("script_name", "project_subdir"),
    [
        ("install-codex.sh", Path(".agents/skills")),
        ("install-claude.sh", Path(".claude/skills")),
        ("install-opencode.sh", Path(".opencode/skills")),
    ],
)
def test_bash_adapters_install_to_expected_project_layout(
    repo_root: Path,
    tmp_path: Path,
    utf8_env: dict[str, str],
    script_name: str,
    project_subdir: Path,
) -> None:
    executable = shutil.which("bash")
    if executable is None and os.name == "nt":
        candidate = Path(r"C:\Program Files\Git\bin\bash.exe")
        if candidate.is_file():
            executable = str(candidate)
    if executable is None:
        pytest.skip("Bash is not available")
    project = tmp_path / f"project {script_name}"
    project.mkdir()
    if os.name == "nt":
        command = [
            executable,
            "-lc",
            f'./{script_name} --project "$1"',
            "--",
            str(project),
        ]
        cwd = repo_root
    else:
        command = [
            executable,
            str(repo_root / script_name),
            "--project",
            str(project),
        ]
        cwd = None
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        encoding="utf-8",
        env=utf8_env,
        cwd=cwd,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    target = project / project_subdir
    assert len(list(target.glob("hydro-workflow-*"))) == 3
