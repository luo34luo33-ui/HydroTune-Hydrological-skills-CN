"""Shared, self-contained rendering helpers for one installed atlas skill."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, ListedColormap
import numpy as np
import rasterio


TEMPLATE_VERSION = "hydrotune.spatial-atlas.v1"


class AtlasError(RuntimeError):
    """Raised for a user-facing rendering or contract error."""


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return value


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


def load_result(path: Path, expected_skill: str, allow_scientific_error: bool = False) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise AtlasError(f"result.json 不存在: {resolved}")
    try:
        document = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AtlasError(f"无法读取 result.json: {resolved}: {exc}") from exc
    if document.get("skill") != expected_skill:
        raise AtlasError(f"result skill 不匹配，期望 {expected_skill}: {resolved}")
    if document.get("status") == "error" and not allow_scientific_error:
        raise AtlasError(f"上游 result 为 error: {resolved}")
    if not isinstance(document.get("artifacts"), dict):
        raise AtlasError(f"result 缺少 artifacts object: {resolved}")
    return document


def resolve_reference(reference: dict[str, str], result_path: Path, label: str) -> Path:
    if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
        raise AtlasError(f"{label} 缺少有效 path")
    raw = Path(reference["path"])
    resolved = raw.resolve() if raw.is_absolute() else (result_path.parent / raw).resolve()
    if not resolved.is_file():
        raise AtlasError(f"{label} 文件不存在: {resolved}")
    expected = reference.get("sha256")
    actual = sha256_file(resolved)
    if not isinstance(expected, str) or actual != expected:
        raise AtlasError(f"{label} SHA-256 不匹配: {resolved}")
    return resolved


def artifact_path(document: dict[str, Any], result_path: Path, key: str) -> Path:
    artifacts = document.get("artifacts", {})
    if key not in artifacts:
        raise AtlasError(f"result 缺少 artifact: {key}")
    return resolve_reference(artifacts[key], result_path, f"artifact {key}")


def load_style(script_path: Path) -> dict[str, Any]:
    style_path = script_path.resolve().parents[1] / "assets" / "atlas-style-v1.json"
    try:
        style = json.loads(style_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AtlasError(f"无法读取固定样式资产: {style_path}: {exc}") from exc
    if style.get("template_version") != TEMPLATE_VERSION:
        raise AtlasError(f"不支持的样式版本: {style.get('template_version')}")
    return style


def configure_rendering(style: dict[str, Any], language: str) -> str:
    available = {item.name.casefold(): item.name for item in font_manager.fontManager.ttflist}
    candidates = style["typography"][language]
    selected = next((available[name.casefold()] for name in candidates if name.casefold() in available), None)
    if selected is None:
        if language == "zh":
            raise AtlasError("中文模式需要 Microsoft YaHei、PingFang SC、Noto Sans CJK SC 或 Source Han Sans SC")
        raise AtlasError(f"找不到英文渲染字体: {', '.join(candidates)}")
    colors = style["colors"]
    matplotlib.rcParams.update({
        "font.family": selected,
        "font.size": style["typography"]["body_size"],
        "axes.unicode_minus": False,
        "text.color": colors["ink"],
        "axes.labelcolor": colors["ink"],
        "axes.edgecolor": colors["muted_ink"],
        "svg.fonttype": "none",
        "svg.hashsalt": "hydrotune-spatial-atlas-v1",
        "savefig.facecolor": style["canvas"]["background"],
        "figure.facecolor": style["canvas"]["background"],
    })
    return selected


def prepare_output_dir(output_dir: Path, overwrite: bool, template_ids: list[str]) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise AtlasError(f"输出路径不是目录: {output_dir}")
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"输出目录非空；如需替换本 Skill 产物请使用 --overwrite: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if overwrite:
        names = ["result.json"]
        for template_id in template_ids:
            names.extend(f"{template_id}.{suffix}" for suffix in ("png", "svg", "figure.json"))
        for title in ("HydroBase 水文拓扑", "HydroBase hydrological topology"):
            names.extend(f"{title}.{suffix}" for suffix in ("png", "svg", "figure.json"))
        for name in names:
            target = output_dir / name
            if target.is_file():
                target.unlink()


def read_raster(path: Path) -> tuple[np.ndarray, dict[str, Any]]:
    with rasterio.open(path) as source:
        if source.crs is None:
            raise AtlasError(f"栅格缺少 CRS: {path}")
        data = source.read(1, masked=True).astype("float64").filled(np.nan)
        metadata = {
            "crs": source.crs.to_string(),
            "transform": tuple(source.transform),
            "width": source.width,
            "height": source.height,
            "bounds": source.bounds,
            "nodata": source.nodata,
        }
    return data, metadata


def assert_same_grid(reference: dict[str, Any], others: list[tuple[str, dict[str, Any]]]) -> None:
    for label, candidate in others:
        if (
            candidate["crs"] != reference["crs"]
            or candidate["width"] != reference["width"]
            or candidate["height"] != reference["height"]
            or not np.allclose(candidate["transform"], reference["transform"], atol=1e-9, rtol=0)
        ):
            raise AtlasError(f"栅格网格不一致: {label}")


def extent_from_meta(metadata: dict[str, Any]) -> list[float]:
    bounds = metadata["bounds"]
    return [float(bounds.left), float(bounds.right), float(bounds.bottom), float(bounds.top)]


def linear_cmap(style: dict[str, Any], key: str) -> LinearSegmentedColormap:
    return LinearSegmentedColormap.from_list(f"hydrotune_{key}", style["colors"][key])


def listed_cmap(style: dict[str, Any], key: str) -> ListedColormap:
    return ListedColormap(style["colors"][key], name=f"hydrotune_{key}")


def new_figure(style: dict[str, Any], panels: int = 1):
    canvas = style["canvas"]
    figure = plt.figure(figsize=canvas["figsize_inches"], dpi=canvas["dpi"], facecolor=canvas["background"])
    if panels == 1:
        axes = [figure.add_axes([0.065, 0.105, 0.74, 0.76], facecolor=canvas["panel_background"])]
    elif panels == 3:
        axes = [
            figure.add_axes([0.055 + index * 0.305, 0.16, 0.27, 0.66], facecolor=canvas["panel_background"])
            for index in range(3)
        ]
    else:
        raise AtlasError(f"不支持的 panel 数量: {panels}")
    return figure, axes


def add_header(figure, title: str, subtitle: str, style: dict[str, Any]) -> None:
    figure.text(0.055, 0.945, title, fontsize=style["typography"]["title_size"], weight="bold", va="top")
    figure.text(0.057, 0.902, subtitle, fontsize=style["typography"]["subtitle_size"], color=style["colors"]["muted_ink"], va="top")
    figure.add_artist(plt.Line2D([0.055, 0.945], [0.88, 0.88], transform=figure.transFigure, color="#D5D2CA", lw=0.8))


def add_status_badge(figure, status: str, style: dict[str, Any]) -> None:
    if status == "success":
        return
    label = "QC WARNING" if status == "warning" else "QC FAILED"
    color = style["colors"]["warn" if status == "warning" else "fail"]
    figure.text(0.94, 0.942, label, ha="right", va="top", color="white", fontsize=9, weight="bold",
                bbox={"boxstyle": "round,pad=0.45", "facecolor": color, "edgecolor": "none"})


def _nice_scale_length(width_m: float) -> float:
    target = max(width_m * 0.22, 1.0)
    power = 10 ** math.floor(math.log10(target))
    candidates = [1, 2, 5, 10]
    valid = [value * power for value in candidates if value * power <= target]
    return max(valid) if valid else power


def decorate_map(axis, extent: list[float], style: dict[str, Any], crs: str, language: str) -> None:
    axis.set_xlim(extent[0], extent[1])
    axis.set_ylim(extent[2], extent[3])
    axis.set_aspect("equal", adjustable="box")
    axis.set_xticks([])
    axis.set_yticks([])
    for spine in axis.spines.values():
        spine.set_linewidth(0.6)
        spine.set_color("#B8B6AF")
    width = extent[1] - extent[0]
    height = extent[3] - extent[2]
    scale = _nice_scale_length(width)
    x0 = extent[0] + width * 0.055
    y0 = extent[2] + height * 0.055
    axis.plot([x0, x0 + scale], [y0, y0], color=style["colors"]["ink"], lw=3, solid_capstyle="butt", zorder=50)
    axis.plot([x0, x0], [y0 - height * 0.008, y0 + height * 0.008], color=style["colors"]["ink"], lw=1, zorder=50)
    axis.plot([x0 + scale, x0 + scale], [y0 - height * 0.008, y0 + height * 0.008], color=style["colors"]["ink"], lw=1, zorder=50)
    label = f"{scale / 1000:g} km" if scale >= 1000 else f"{scale:g} m"
    axis.text(x0 + scale / 2, y0 + height * 0.018, label, ha="center", va="bottom", fontsize=8, weight="bold", zorder=50)
    axis.annotate("N", xy=(0.94, 0.94), xytext=(0.94, 0.84), xycoords="axes fraction",
                  ha="center", va="center", fontsize=10, weight="bold",
                  arrowprops={"arrowstyle": "-|>", "color": style["colors"]["ink"], "lw": 1.3})
    footer = ("坐标参考系" if language == "zh" else "CRS") + f": {crs}"
    axis.text(0.0, -0.048, footer, transform=axis.transAxes, fontsize=7.5, color=style["colors"]["muted_ink"], va="top")


def save_figure(
    figure,
    output_dir: Path,
    template_id: str,
    language: str,
    title: str,
    source_results: dict[str, dict[str, str]],
    layers: list[dict[str, str]],
    crs: str,
    extent: list[float],
    warnings: list[str],
    publication_ready: bool,
) -> dict[str, dict[str, str]]:
    filename = getattr(figure, "hydrotune_filename", template_id)
    png_path = output_dir / f"{filename}.png"
    svg_path = output_dir / f"{filename}.svg"
    metadata = {"Software": "HydroTune spatial atlas v1"}
    figure.savefig(png_path, dpi=200, facecolor=figure.get_facecolor(), metadata=metadata)
    figure.savefig(svg_path, facecolor=figure.get_facecolor(), metadata={"Date": None})
    plt.close(figure)
    figure_document = {
        "schema_version": "1.0",
        "template_version": getattr(figure, "hydrotune_template_version", TEMPLATE_VERSION),
        "template_id": template_id,
        "language": language,
        "title": title,
        "pixel_size": {"width": figure.canvas.get_width_height()[0], "height": figure.canvas.get_width_height()[1]},
        "source_results": source_results,
        "layers": layers,
        "crs": crs,
        "extent": [float(value) for value in extent],
        "outputs": {
            "png": file_reference(png_path, output_dir),
            "svg": file_reference(svg_path, output_dir),
        },
        "warnings": list(warnings),
        "publication_ready": bool(publication_ready),
    }
    json_path = output_dir / f"{filename}.figure.json"
    json_path.write_text(json.dumps(figure_document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        f"{template_id}_png": file_reference(png_path, output_dir),
        f"{template_id}_svg": file_reference(svg_path, output_dir),
        f"{template_id}_figure": file_reference(json_path, output_dir),
    }


def write_result(output_dir: Path, document: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(
        json.dumps(json_safe(document), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def provenance(font_name: str) -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "matplotlib": package_version("matplotlib"),
        "rasterio": package_version("rasterio"),
        "geopandas": package_version("geopandas"),
        "numpy": package_version("numpy"),
        "font": font_name,
        "template_version": TEMPLATE_VERSION,
    }
