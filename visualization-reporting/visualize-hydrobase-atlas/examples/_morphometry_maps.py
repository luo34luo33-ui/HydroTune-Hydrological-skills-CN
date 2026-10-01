"""Self-contained geographic decorations for HydroBase morphometry."""
from typing import Any
import math
import numpy as np
from pyproj import Transformer
from matplotlib.ticker import MaxNLocator
from matplotlib.patches import Polygon
from shapely.geometry import LineString
from _atlas_common import AtlasError

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
        ticks = MaxNLocator(nbins=3, steps=[1, 2, 2.5, 5, 10]).tick_values(low, high)
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
    axis.tick_params(labelsize=13, direction="in", length=6, width=1, pad=7, colors="black",
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
    axis.text(x0 + scale / 2, y0 + height * 0.018, label, ha="center", va="bottom", fontsize=13, zorder=50,
              bbox={"facecolor": "white", "edgecolor": "none", "pad": 1})
    # Split black/white cartographic north arrow with a crisp kite silhouette.
    for vertices, fill in (([(0.94, 0.90), (0.921, 0.815), (0.94, 0.838)], "black"),
                           ([(0.94, 0.90), (0.94, 0.838), (0.959, 0.815)], "white")):
        axis.add_patch(Polygon(vertices, closed=True, transform=axis.transAxes,
                               facecolor=fill, edgecolor="black", linewidth=1.1,
                               zorder=50, gid="north-arrow"))
    axis.text(0.94, 0.913, "N", transform=axis.transAxes, ha="center", va="bottom",
              fontsize=16, fontfamily="Times New Roman", color="black", zorder=51)
    # CRS and status belong to figure metadata, without an outer footer/header.
