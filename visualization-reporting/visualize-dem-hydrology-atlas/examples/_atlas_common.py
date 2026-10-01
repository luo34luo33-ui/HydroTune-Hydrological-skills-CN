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
from matplotlib.ticker import MaxNLocator
from matplotlib.patches import Polygon
import numpy as np
import rasterio
from pyproj import Transformer
from shapely.geometry import LineString


TEMPLATE_VERSION = "hydrotune.dem-hydrology-atlas.v2"


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
    style_path = script_path.resolve().parents[1] / "assets" / "atlas-style-v2.json"
    try:
        style = json.loads(style_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AtlasError(f"无法读取固定样式资产: {style_path}: {exc}") from exc
    if style.get("template_version") != TEMPLATE_VERSION:
        raise AtlasError(f"不支持的样式版本: {style.get('template_version')}")
    return style


def configure_rendering(style: dict[str, Any], language: str) -> str:
    available = {item.name.casefold(): item.name for item in font_manager.fontManager.ttflist}
    required = ["Times New Roman"] + (["SimSun"] if language == "zh" else [])
    missing = [name for name in required if name.casefold() not in available]
    if missing:
        raise AtlasError(f"缺少规定字体: {', '.join(missing)}；中文使用宋体 SimSun，英文与数字使用 Times New Roman")
    # Per-glyph fallback: Latin/digits use Times; Chinese falls back to SimSun.
    selected = [available[name.casefold()] for name in required]
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
    return ", ".join(selected)


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


def new_figure(style: dict[str, Any], extent: list[float]):
    canvas = style["canvas"]
    map_ratio = (extent[1] - extent[0]) / (extent[3] - extent[2])
    left, bottom, right, top = style["layout"]["frame_margins"]
    ratio = map_ratio * (1 - bottom - top) / (1 - left - right)
    longest = canvas["longest_edge_px"] / canvas["dpi"]
    size = [longest, longest / ratio] if ratio >= 1 else [longest * ratio, longest]
    # Round to exact output pixels so metadata matches the actual PNG dimensions.
    pixels = [max(1, round(value * canvas["dpi"])) for value in size]
    figure = plt.figure(figsize=[value / canvas["dpi"] for value in pixels],
                        dpi=canvas["dpi"], facecolor="white")
    axis = figure.add_axes([left, bottom, 1 - left - right, 1 - bottom - top], facecolor="white")
    return figure, [axis]


def _nice_scale_length(width_m: float) -> float:
    target = max(width_m * 0.22, 1.0)
    power = 10 ** math.floor(math.log10(target))
    candidates = [1, 2, 5, 10]
    valid = [value * power for value in candidates if value * power <= target]
    return max(valid) if valid else power


def add_graticule(axis, extent: list[float], crs: str) -> None:
    """Project WGS84 meridians/parallels, with labels at frame intersections."""
    to_geo = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    from_geo = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    xmin, xmax, ymin, ymax = extent
    x = np.linspace(xmin, xmax, 101)
    y = np.linspace(ymin, ymax, 101)
    lon, lat = to_geo.transform(np.r_[x, x, np.full(101, xmin), np.full(101, xmax)],
                                np.r_[np.full(101, ymin), np.full(101, ymax), y, y])
    if not np.isfinite(lon).all() or not np.isfinite(lat).all():
        raise AtlasError("地图范围无法转换为有效经纬度")
    lonmin, lonmax, latmin, latmax = min(lon), max(lon), min(lat), max(lat)
    if lonmax - lonmin > 180:
        raise AtlasError("当前固定经纬网模板不支持跨日期变更线的地图范围")
    bottom_edge = LineString([(xmin, ymin), (xmax, ymin)])
    left_edge = LineString([(xmin, ymin), (xmin, ymax)])
    for low, high, is_longitude, edge in ((lonmin, lonmax, True, bottom_edge),
                                         (latmin, latmax, False, left_edge)):
        ticks = MaxNLocator(nbins=5, steps=[1, 2, 2.5, 5, 10]).tick_values(low, high)
        step = ticks[1] - ticks[0]
        decimals = max(0, min(7, int(math.ceil(-math.log10(step))) + 1))
        positions, labels = [], []
        for value in ticks[(ticks > low) & (ticks < high)]:
            if is_longitude:
                varying = np.linspace(latmin - (latmax - latmin) * 0.1,
                                      latmax + (latmax - latmin) * 0.1, 201)
                gx, gy = from_geo.transform(np.full(201, value), varying)
            else:
                varying = np.linspace(lonmin - (lonmax - lonmin) * 0.1,
                                      lonmax + (lonmax - lonmin) * 0.1, 201)
                gx, gy = from_geo.transform(varying, np.full(201, value))
            line = LineString(np.column_stack([gx, gy]))
            axis.plot(gx, gy, color="#777777", linewidth=0.55, linestyle=(0, (4, 5)),
                      alpha=0.65, zorder=10, gid="geographic-graticule")
            intersection = line.intersection(edge)
            points = [intersection] if intersection.geom_type == "Point" else [
                part for part in getattr(intersection, "geoms", []) if part.geom_type == "Point"]
            for point in points:
                position = point.x if is_longitude else point.y
                frame_low, frame_high = (xmin, xmax) if is_longitude else (ymin, ymax)
                if not frame_low + (frame_high - frame_low) * 0.05 < position < frame_high - (frame_high - frame_low) * 0.05:
                    continue  # avoid labels clipped at the frame corners
                positions.append(position)
                number = f"{abs(value):.{decimals}f}".rstrip("0").rstrip(".") if decimals else f"{abs(value):.0f}"
                hemisphere = ("E" if value >= 0 else "W") if is_longitude else ("N" if value >= 0 else "S")
                labels.append(f"{number}°{hemisphere}")
        if is_longitude:
            axis.set_xticks(positions, labels)
        else:
            axis.set_yticks(positions, labels, rotation=90, va="center")
    axis.tick_params(labelsize=16, direction="in", length=6, width=1, pad=7, colors="black",
                     top=False, right=False, labeltop=False, labelright=False)


def decorate_map(axis, extent: list[float], style: dict[str, Any], crs: str, language: str) -> None:
    axis.set_xlim(extent[0], extent[1])
    axis.set_ylim(extent[2], extent[3])
    axis.set_aspect("equal", adjustable="box")
    axis.set_xticks([])
    axis.set_yticks([])
    add_graticule(axis, extent, crs)
    for spine in axis.spines.values():
        spine.set_linewidth(1.2)
        spine.set_color("#000000")
    width = extent[1] - extent[0]
    height = extent[3] - extent[2]
    scale = _nice_scale_length(width)
    x0 = extent[0] + width * 0.055
    y0 = extent[2] + height * 0.055
    axis.plot([x0, x0 + scale], [y0, y0], color=style["colors"]["ink"], lw=3, solid_capstyle="butt", zorder=50)
    axis.plot([x0, x0], [y0 - height * 0.008, y0 + height * 0.008], color=style["colors"]["ink"], lw=1, zorder=50)
    axis.plot([x0 + scale, x0 + scale], [y0 - height * 0.008, y0 + height * 0.008], color=style["colors"]["ink"], lw=1, zorder=50)
    label = f"{scale / 1000:g} km" if scale >= 1000 else f"{scale:g} m"
    axis.text(x0 + scale / 2, y0 + height * 0.018, label, ha="center", va="bottom", fontsize=12, zorder=50,
              bbox={"facecolor": "white", "edgecolor": "none", "pad": 1})
    # Split black/white cartographic north arrow with a crisp kite silhouette.
    for vertices, fill in (([(0.94, 0.935), (0.921, 0.85), (0.94, 0.873)], "black"),
                           ([(0.94, 0.935), (0.94, 0.873), (0.959, 0.85)], "white")):
        axis.add_patch(Polygon(vertices, closed=True, transform=axis.transAxes,
                               facecolor=fill, edgecolor="black", linewidth=1.1,
                               zorder=50, gid="north-arrow"))
    axis.text(0.94, 0.948, "N", transform=axis.transAxes, ha="center", va="bottom",
              fontsize=18, fontfamily="Times New Roman", color="black", zorder=51)
    # CRS and status belong to figure metadata, without an outer footer/header.


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
    png_path = output_dir / f"{template_id}.png"
    svg_path = output_dir / f"{template_id}.svg"
    width, height = [round(value * figure.dpi) for value in figure.get_size_inches()]
    metadata = {"Software": "HydroTune DEM hydrology atlas v2", "Title": title}
    figure.savefig(png_path, dpi=200, facecolor=figure.get_facecolor(), metadata=metadata)
    figure.savefig(svg_path, facecolor=figure.get_facecolor(), metadata={"Date": None})
    plt.close(figure)
    figure_document = {
        "schema_version": "1.0",
        "template_version": TEMPLATE_VERSION,
        "template_id": template_id,
        "language": language,
        "title": title,
        "pixel_size": {"width": width, "height": height},
        "source_results": source_results,
        "layers": [{**layer, "display_transform": layer["display_transform"] + "; display clipped to true basin polygon"} for layer in layers],
        "crs": crs,
        "extent": [float(value) for value in extent],
        "outputs": {
            "png": file_reference(png_path, output_dir),
            "svg": file_reference(svg_path, output_dir),
        },
        "warnings": list(warnings),
        "publication_ready": bool(publication_ready),
    }
    json_path = output_dir / f"{template_id}.figure.json"
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
