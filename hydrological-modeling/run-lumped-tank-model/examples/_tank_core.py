"""Private lumped Tank core migrated line by line from HydroTune-AI-Demo/src/hydro/tank_simple.py
(function ``run_tank_model``), reduced to a single basin.

Deviations from the source module, all intentional and recorded in usage-guide.md:
- the sixteen ``params.get(key, default)`` silent fallbacks are removed; every parameter
  must be provided explicitly;
- the hard-coded area default (584.0) is removed; ``del_t`` becomes ``timestep_hours``;
- the combined top-tank side outlet is split into its two holes (QS0_LO / QS0_UO) in the
  output, which is a strict superset of the source return value and does not change any
  number.

This module is private to hydrological-modeling/run-lumped-tank-model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

PARAMETER_FIELDS = (
    "t0_is", "t0_boc", "t0_soc_uo", "t0_soc_lo", "t0_soh_uo", "t0_soh_lo",
    "t1_is", "t1_boc", "t1_soc", "t1_soh",
    "t2_is", "t2_boc", "t2_soc", "t2_soh",
    "t3_is", "t3_soc",
)

# Source: tank_simple.py TANK_PARAM_BOUNDS (calibration priors from the source project).
PARAMETER_BOUNDS = {
    "t0_is": (0.0, 50.0),
    "t0_boc": (0.15, 0.5),
    "t0_soc_uo": (0.2, 0.6),
    "t0_soc_lo": (0.15, 0.5),
    "t0_soh_uo": (50.0, 120.0),
    "t0_soh_lo": (10.0, 50.0),
    "t1_is": (0.0, 50.0),
    "t1_boc": (0.1, 0.4),
    "t1_soc": (0.1, 0.4),
    "t1_soh": (20.0, 80.0),
    "t2_is": (0.0, 50.0),
    "t2_boc": (0.05, 0.3),
    "t2_soc": (0.05, 0.3),
    "t2_soh": (10.0, 60.0),
    "t3_is": (0.0, 50.0),
    "t3_soc": (0.001, 0.05),
}


@dataclass(frozen=True)
class TankParameters:
    t0_is: float
    t0_boc: float
    t0_soc_uo: float
    t0_soc_lo: float
    t0_soh_uo: float
    t0_soh_lo: float
    t1_is: float
    t1_boc: float
    t1_soc: float
    t1_soh: float
    t2_is: float
    t2_boc: float
    t2_soc: float
    t2_soh: float
    t3_is: float
    t3_soc: float


def build_parameters(raw: dict[str, Any]) -> TankParameters:
    missing = [field for field in PARAMETER_FIELDS if field not in raw]
    if missing:
        raise ValueError(f"参数集缺少字段: {', '.join(missing)}")
    values: dict[str, float] = {}
    for field in PARAMETER_FIELDS:
        value = raw[field]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"参数 {field} 必须是数值")
        values[field] = float(value)
    return TankParameters(**values)


def validate_parameters(parameters: TankParameters, area_km2: float, timestep_hours: float) -> list[str]:
    problems: list[str] = []
    if area_km2 <= 0:
        problems.append("--area-km2 必须为正")
    if timestep_hours <= 0:
        problems.append("--timestep-hours 必须为正")
    for name in ("t0_is", "t1_is", "t2_is", "t3_is"):
        if getattr(parameters, name) < 0:
            problems.append(f"{name} 不得为负")
    for name, value in (
        ("t0_boc", parameters.t0_boc), ("t0_soc_uo", parameters.t0_soc_uo), ("t0_soc_lo", parameters.t0_soc_lo),
        ("t1_boc", parameters.t1_boc), ("t1_soc", parameters.t1_soc),
        ("t2_boc", parameters.t2_boc), ("t2_soc", parameters.t2_soc),
        ("t3_soc", parameters.t3_soc),
    ):
        if not 0 <= value <= 1:
            problems.append(f"{name} 必须落在 [0, 1]")
    for name in ("t0_soh_uo", "t0_soh_lo", "t1_soh", "t2_soh"):
        if getattr(parameters, name) < 0:
            problems.append(f"{name} 不得为负")
    if parameters.t0_soh_uo < parameters.t0_soh_lo:
        problems.append("t0_soh_uo 必须不小于 t0_soh_lo（上侧孔高度不得低于下侧孔）")
    return problems


def parameter_range_warnings(parameters: TankParameters) -> list[str]:
    warnings: list[str] = []
    for field in PARAMETER_FIELDS:
        low, high = PARAMETER_BOUNDS[field]
        value = getattr(parameters, field)
        if not low <= value <= high:
            warnings.append(
                f"参数 {field}={value:.6g} 超出源工程率定范围 [{low}, {high}]；"
                f"该范围是率定先验而非硬约束，请确认取值依据"
            )
    return warnings


def run_lumped_tank(
    precipitation: np.ndarray,
    evaporation: np.ndarray,
    parameters: TankParameters,
    area_km2: float,
    timestep_hours: float,
) -> dict[str, np.ndarray]:
    """Run the lumped four-tank Tank loop on one basin and return per-step arrays.

    The source initialises the four tanks from the ``t*_is`` parameters at step 0 and
    has no warmup mechanism; this implementation keeps that behaviour exactly.
    """
    steps = int(len(precipitation))
    p_arr = np.asarray(precipitation, dtype=float)
    e_arr = np.asarray(evaporation, dtype=float)
    net_arr = p_arr - e_arr

    storage = np.zeros((steps, 4))
    qs0_lo = np.zeros(steps)
    qs0_uo = np.zeros(steps)
    qs1 = np.zeros(steps)
    qs2 = np.zeros(steps)
    qs3 = np.zeros(steps)
    qb0 = np.zeros(steps)
    qb1 = np.zeros(steps)
    qb2 = np.zeros(steps)

    storage[0, 0] = max(parameters.t0_is, 0.0)
    storage[0, 1] = max(parameters.t1_is, 0.0)
    storage[0, 2] = max(parameters.t2_is, 0.0)
    storage[0, 3] = max(parameters.t3_is, 0.0)

    clips = [0]

    def floor_zero(value: float) -> float:
        if value < 0.0:
            clips[0] += 1
            return 0.0
        return value

    for t in range(steps):
        # top tank: two side outlets at different heights plus a bottom outlet
        qs0_lo[t] = parameters.t0_soc_lo * max(storage[t, 0] - parameters.t0_soh_lo, 0.0)
        qs0_uo[t] = parameters.t0_soc_uo * max(storage[t, 0] - parameters.t0_soh_uo, 0.0)
        side0 = qs0_lo[t] + qs0_uo[t]
        qs1[t] = parameters.t1_soc * max(storage[t, 1] - parameters.t1_soh, 0.0)
        qs2[t] = parameters.t2_soc * max(storage[t, 2] - parameters.t2_soh, 0.0)
        qs3[t] = parameters.t3_soc * storage[t, 3]
        qb0[t] = parameters.t0_boc * storage[t, 0]
        qb1[t] = parameters.t1_boc * storage[t, 1]
        qb2[t] = parameters.t2_boc * storage[t, 2]

        if t < steps - 1:
            storage[t + 1, 0] = floor_zero(
                storage[t, 0] + net_arr[t + 1] - (side0 + qb0[t])
            )
            storage[t + 1, 1] = floor_zero(
                storage[t, 1] + qb0[t] - (qs1[t] + qb1[t])
            )
            storage[t + 1, 2] = floor_zero(
                storage[t, 2] + qb1[t] - (qs2[t] + qb2[t])
            )
            storage[t + 1, 3] = floor_zero(
                storage[t, 3] + qb2[t] - qs3[t]
            )

    unit_conv = (area_km2 * 1000.0) / (timestep_hours * 3600.0)
    raw_discharge = unit_conv * (qs0_lo + qs0_uo + qs1 + qs2 + qs3)
    negative = int(np.sum(raw_discharge < 0.0))
    clips[0] += negative
    discharge = np.maximum(raw_discharge, 0.0)

    return {
        "P": p_arr,
        "EVAP": e_arr,
        "NET": net_arr,
        "S0": storage[:, 0],
        "S1": storage[:, 1],
        "S2": storage[:, 2],
        "S3": storage[:, 3],
        "QS0_LO": qs0_lo,
        "QS0_UO": qs0_uo,
        "QS1": qs1,
        "QS2": qs2,
        "QS3": qs3,
        "QB0": qb0,
        "QB1": qb1,
        "QB2": qb2,
        "Q": discharge,
        "clip_count": np.array([clips[0]], dtype=int),
    }
