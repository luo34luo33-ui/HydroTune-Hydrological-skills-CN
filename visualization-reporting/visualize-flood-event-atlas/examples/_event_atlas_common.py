"""Fixed rendering and provenance helpers for the flood-event atlas."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
from pathlib import Path
import sys
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt
import pandas as pd


TEMPLATE_VERSION = "hydrotune.flood-event-atlas.v2"


class AtlasError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_reference(path: Path, relative_to: Path | None = None) -> dict[str, str]:
    resolved = path.resolve()
    display = str(resolved)
    if relative_to is not None:
        try:
            display = resolved.relative_to(relative_to.resolve()).as_posix()
        except ValueError:
            pass
    return {"path": display, "sha256": sha256_file(resolved)}


def load_result(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise AtlasError(f"extract result 不存在: {resolved}")
    try:
        document = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AtlasError(f"无法读取 extract result: {exc}") from exc
    if document.get("skill") != "data-processing/extract-flood-events":
        raise AtlasError("extract result 的 skill 不匹配")
    if document.get("status") == "error":
        raise AtlasError("extract result 为 error")
    return document


def resolve_artifact(document: dict[str, Any], result_path: Path, key: str) -> Path:
    reference = document.get("artifacts", {}).get(key)
    if not isinstance(reference, dict):
        raise AtlasError(f"extract result 缺少 artifact: {key}")
    raw = Path(reference.get("path", ""))
    resolved = raw.resolve() if raw.is_absolute() else (result_path.parent / raw).resolve()
    if not resolved.is_file() or sha256_file(resolved) != reference.get("sha256"):
        raise AtlasError(f"artifact 不存在或 SHA-256 不匹配: {key}")
    return resolved


def read_artifact_table(path: Path, sheet_name: str) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, encoding="utf-8-sig")
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".xlsx":
        return pd.read_excel(path, sheet_name=sheet_name)
    raise AtlasError(f"不支持的事件表格式: {suffix}")


def load_style(script_path: Path) -> dict[str, Any]:
    path = script_path.resolve().parents[1] / "assets" / "flood-event-style-v2.json"
    style = json.loads(path.read_text(encoding="utf-8-sig"))
    if style.get("template_version") != TEMPLATE_VERSION:
        raise AtlasError("固定样式版本不受支持")
    return style


def configure(style: dict[str, Any], language: str) -> str:
    available = {item.name.casefold(): item.name for item in font_manager.fontManager.ttflist}
    names = ["Times New Roman"] + (["SimSun"] if language == "zh" else [])
    missing = [name for name in names if name.casefold() not in available]
    if missing:
        raise AtlasError(f"缺少规定字体: {', '.join(missing)}；中文宋体，英文和数字 Times New Roman")
    selected = [available[name.casefold()] for name in names]
    colors = style["colors"]
    matplotlib.rcParams.update({
        "font.family": selected, "font.size": style["typography"]["body_size"], "axes.unicode_minus": False,
        "text.color": colors["ink"], "axes.labelcolor": colors["ink"], "axes.edgecolor": colors["muted_ink"],
        "svg.fonttype": "none", "svg.hashsalt": TEMPLATE_VERSION,
        "figure.facecolor": style["canvas"]["background"], "savefig.facecolor": style["canvas"]["background"],
    })
    return ", ".join(selected)


def prepare_output_dir(output_dir: Path, overwrite: bool) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise AtlasError(f"输出路径不是目录: {output_dir}")
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"输出目录非空；请使用 --overwrite: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if overwrite:
        for path in output_dir.iterdir():
            owned = path.name == "result.json" or re.fullmatch(
                r"(?:flood-event-overview|event-\d+-hydrograph)\.(?:png|svg|figure\.json)", path.name)
            if path.is_file() and owned:
                path.unlink()


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def save_figure(figure, output_dir: Path, template_id: str, event_id: int | None, language: str,
                title: str, source_results: dict, layers: list[dict], warnings: list[str], ready: bool) -> dict[str, dict[str, str]]:
    png = output_dir / f"{template_id}.png"
    svg = output_dir / f"{template_id}.svg"
    pixels = [round(value * figure.dpi) for value in figure.get_size_inches()]
    figure.savefig(png, dpi=figure.dpi, facecolor=figure.get_facecolor(), metadata={"Software": "HydroTune flood event atlas v2", "Title": title})
    figure.savefig(svg, facecolor=figure.get_facecolor(), metadata={"Date": None})
    plt.close(figure)
    metadata = {
        "schema_version": "1.0", "template_version": TEMPLATE_VERSION, "template_id": template_id,
        "event_id": event_id, "language": language, "title": title,
        "pixel_size": {"width": pixels[0], "height": pixels[1]}, "source_results": source_results,
        "layers": layers, "outputs": {"png": file_reference(png, output_dir), "svg": file_reference(svg, output_dir)},
        "warnings": warnings, "publication_ready": ready,
    }
    metadata_path = output_dir / f"{template_id}.figure.json"
    write_json(metadata_path, metadata)
    return {
        f"{template_id}_png": file_reference(png, output_dir),
        f"{template_id}_svg": file_reference(svg, output_dir),
        f"{template_id}_figure": file_reference(metadata_path, output_dir),
    }


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def provenance(font: str) -> dict[str, str]:
    return {"python": sys.version.split()[0], "pandas": package_version("pandas"), "matplotlib": package_version("matplotlib"), "font": font, "template_version": TEMPLATE_VERSION}
