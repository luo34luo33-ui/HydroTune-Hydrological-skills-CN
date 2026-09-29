"""Private Muskingum routing core: single-reach routing, cascaded unit-channel routing
and the coefficient bookkeeping shared by both.

This module is private to hydrological-modeling/route-muskingum-channel. Other skills
must not import it; they carry their own tested copies when they need a helper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

EPS = 1e-12


@dataclass(frozen=True)
class RouteSpec:
    route_id: str
    column: str
    layout: str
    k: float
    x: float
    reaches: int
    q0: float | None
    output_level: str

    @property
    def cascade_x(self) -> float:
        """Unit-channel cascaded form used by the source notebook: x_l = 0.5 - n*(1-2X)/2."""
        return 0.5 - self.reaches * (1.0 - 2.0 * self.x) / 2.0


def build_route_specs(raw: Any) -> list[RouteSpec]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("route-spec 必须是非空数组")
    specs: list[RouteSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"route-spec 第 {index + 1} 项必须是 object")
        for field in ("id", "column", "layout", "k", "x"):
            if field not in item:
                raise ValueError(f"route-spec 第 {index + 1} 项缺少字段 {field}")
        route_id = str(item["id"])
        if route_id in seen:
            raise ValueError(f"route-spec 存在重复 id: {route_id}")
        seen.add(route_id)
        layout = str(item["layout"])
        if layout not in ("single", "cascade"):
            raise ValueError(f"route-spec {route_id} 的 layout 必须是 single 或 cascade")
        reaches_raw = item.get("reaches", 1)
        reaches = max(0, int(round(float(reaches_raw))))
        if layout == "cascade" and reaches < 1:
            raise ValueError(f"route-spec {route_id} 在 cascade 布局下 reaches 必须为正")
        output_level = str(item.get("output_level", "final"))
        if output_level not in ("final", "first"):
            raise ValueError(f"route-spec {route_id} 的 output_level 必须是 final 或 first")
        q0 = item.get("q0", None)
        specs.append(
            RouteSpec(
                route_id=route_id,
                column=str(item["column"]),
                layout=layout,
                k=float(item["k"]),
                x=float(item["x"]),
                reaches=reaches,
                q0=None if q0 is None else float(q0),
                output_level=output_level,
            )
        )
    return specs


def validate_route_spec(spec: RouteSpec, timestep_hours: float) -> list[str]:
    problems: list[str] = []
    if timestep_hours <= 0:
        problems.append(f"{spec.route_id}: 时间步长必须为正")
    if spec.k <= 0:
        problems.append(f"{spec.route_id}: K 必须为正")
    if spec.layout == "single" and spec.k <= 0:
        problems.append(f"{spec.route_id}: K 必须为正")
    return problems


def coefficients(k: float, x: float, dt: float) -> tuple[float, float, float, bool]:
    """Muskingum coefficients with the source notebook stabilisation."""
    x_used = float(np.clip(x, 0.0, 0.5))
    k_used = float(max(k, EPS))
    dt_used = float(max(dt, EPS))
    denom = k_used - k_used * x_used + 0.5 * dt_used
    if abs(denom) < EPS:
        return 0.0, 0.0, 1.0, True
    c0 = (-k_used * x_used + 0.5 * dt_used) / denom
    c1 = (k_used * x_used + 0.5 * dt_used) / denom
    clipped = False
    c0_clipped = float(np.clip(c0, 0.0, 1.0))
    c1_clipped = float(np.clip(c1, 0.0, 1.0))
    if abs(c0_clipped - c0) > 0.0 or abs(c1_clipped - c1) > 0.0:
        clipped = True
    c2 = 1.0 - c0_clipped - c1_clipped
    return c0_clipped, c1_clipped, c2, clipped


def route_single(inflow: np.ndarray, k: float, x: float, dt: float, q0: float) -> tuple[np.ndarray, dict[str, Any]]:
    steps = int(len(inflow))
    c0, c1, c2, clipped = coefficients(k, x, dt)
    outflow = np.zeros(steps, dtype=float)
    if steps == 0:
        return outflow, {"c0": c0, "c1": c1, "c2": c2, "clipped": clipped, "x_used": float(np.clip(x, 0.0, 0.5))}
    outflow[0] = q0
    for step in range(1, steps):
        outflow[step] = c0 * inflow[step] + c1 * inflow[step - 1] + c2 * outflow[step - 1]
    return outflow, {"c0": c0, "c1": c1, "c2": c2, "clipped": clipped, "x_used": float(np.clip(x, 0.0, 0.5))}


def route_cascade(
    inflow: np.ndarray,
    reaches: int,
    x: float,
    dt: float,
    q0: float,
    output_level: str = "final",
) -> tuple[np.ndarray, dict[str, Any]]:
    """Cascaded unit-channel routing: K_l = dt and x_l = 0.5 - n*(1-2X)/2, no clipping of x_l."""
    steps = int(len(inflow))
    k_l = float(dt)
    x_l = 0.5 - reaches * (1.0 - 2.0 * float(x)) / 2.0
    denom = 0.5 * dt + k_l - k_l * x_l
    if abs(denom) < EPS:
        denom = EPS
    c0 = (0.5 * dt - k_l * x_l) / denom
    c1 = (0.5 * dt + k_l * x_l) / denom
    c2 = 1.0 - c0 - c1
    levels = np.zeros((reaches + 1, steps), dtype=float)
    levels[0] = inflow
    for level in range(reaches):
        for step in range(steps):
            current_inflow = levels[level][step]
            if step == 0:
                previous_inflow = q0
                previous_outflow = q0
            else:
                previous_inflow = levels[level][step - 1]
                previous_outflow = levels[level + 1][step - 1]
            levels[level + 1][step] = c0 * current_inflow + c1 * previous_inflow + c2 * previous_outflow
    selected = levels[reaches] if output_level == "final" else levels[1]
    return selected, {
        "c0": c0,
        "c1": c1,
        "c2": c2,
        "clipped": False,
        "x_used": x_l,
        "reaches": reaches,
        "output_level": output_level,
    }
