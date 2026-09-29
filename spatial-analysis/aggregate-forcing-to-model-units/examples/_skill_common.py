"""Small, install-local helpers for artifact evidence and result writing."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys


class QCError(ValueError):
    """Input exists but fails a scientific or data-quality contract."""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def reference(path: Path, base: Path | None = None) -> dict:
    path = path.resolve()
    try:
        display = path.relative_to(base.resolve()).as_posix() if base else str(path)
    except ValueError:
        display = str(path)
    return {"path": display, "sha256": sha256(path)}


def resolve(value: dict, base: Path) -> Path:
    if not isinstance(value, dict) or not {"path", "sha256"} <= value.keys():
        raise QCError("artifact reference requires path and sha256")
    raw = Path(value["path"])
    path = (raw if raw.is_absolute() else base / raw).resolve()
    if not path.is_file() or sha256(path) != value["sha256"]:
        raise QCError(f"missing or hash-mismatched artifact: {path}")
    return path


def load_result(path: Path, allowed: set[str]) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("skill") not in allowed or value.get("status") not in ("success", "warning"):
        raise QCError(f"invalid upstream result: {path}")
    if any(check.get("status") == "FAIL" for check in value.get("checks", [])):
        raise QCError(f"upstream result has FAIL: {path}")
    return value


def prepare(path: Path, overwrite: bool, outputs: tuple[str, ...]) -> None:
    if path.exists() and not path.is_dir():
        raise ValueError("output path is not a directory")
    if path.exists() and any(path.iterdir()) and not overwrite:
        raise FileExistsError("output directory is nonempty; use --overwrite")
    path.mkdir(parents=True, exist_ok=True)
    if overwrite:
        for name in outputs:
            target = path / name
            if target.is_file():
                target.unlink()


def version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def write_result(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def error_result(skill: str, message: str, inputs: dict | None = None) -> dict:
    return {"schema_version": "1.0", "skill": skill, "status": "error", "message": message,
            "parameters": {}, "inputs": inputs or {}, "artifacts": {},
            "checks": [{"check": "run", "status": "FAIL", "details": message}],
            "warnings": [], "provenance": {"python": sys.version.split()[0]}}
