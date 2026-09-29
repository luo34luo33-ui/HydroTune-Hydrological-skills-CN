"""Private lumped HBV core migrated line by line from HydroTune-AI-Demo/src/hydro/hbv_simple.py
(function ``run_hbv_model``), reduced to a single basin.

Deviations from the source module, all intentional and recorded in usage-guide.md:
- the nine ``params.get(key, default)`` silent fallbacks are removed; every parameter
  must be provided explicitly;
- the hard-coded area default (584.0) is removed;
- the hard-coded daily unit conversion (86400 seconds) becomes an explicit
  ``timestep_hours`` argument (the source value is equivalent to 24 hours);
- state columns (AE, SM, RECHARGE, SUZ, SLZ, Q0, Q1, Q2) are exported in addition to Q,
  which is a strict superset of the source return value and does not change any number.

This module is private to hydrological-modeling/run-lumped-hbv-model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

PARAMETER_FIELDS = ("fc", "beta", "c", "k0", "l", "k1", "k2", "kp", "lp")

# Source: hbv_simple.py HBV_PARAM_BOUNDS (calibration priors from the source project).
PARAMETER_BOUNDS = {
    "fc": (100.0, 200.0),
    "beta": (1.0, 7.0),
    "c": (0.01, 0.07),
    "k0": (0.05, 0.2),
    "l": (2.0, 5.0),
    "k1": (0.01, 0.1),
    "k2": (0.01, 0.05),
    "kp": (0.01, 0.05),
    "lp": (0.3, 1.0),
}


@dataclass(frozen=True)
class HBVParameters:
    fc: float
    beta: float
    c: float
    k0: float
    l: float
    k1: float
    k2: float
    kp: float
    lp: float


def build_parameters(raw: dict[str, Any]) -> HBVParameters:
    missing = [field for field in PARAMETER_FIELDS if field not in raw]
    if missing:
        raise ValueError(f"参数集缺少字段: {', '.join(missing)}")
    values: dict[str, float] = {}
    for field in PARAMETER_FIELDS:
        value = raw[field]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"参数 {field} 必须是数值")
        values[field] = float(value)
    return HBVParameters(
        fc=values["fc"], beta=values["beta"], c=values["c"], k0=values["k0"],
        l=values["l"], k1=values["k1"], k2=values["k2"], kp=values["kp"], lp=values["lp"],
    )


def validate_parameters(parameters: HBVParameters, area_km2: float, timestep_hours: float) -> list[str]:
    problems: list[str] = []
    if area_km2 <= 0:
        problems.append("--area-km2 必须为正")
    if timestep_hours <= 0:
        problems.append("--timestep-hours 必须为正")
    if parameters.fc <= 0:
        problems.append("fc 必须为正")
    if parameters.beta <= 0:
        problems.append("beta 必须为正")
    if not 0 < parameters.c <= 1:
        problems.append("c 必须落在 (0, 1]")
    if parameters.k0 <= 0:
        problems.append("k0 必须为正")
    if parameters.l < 0:
        problems.append("l 不得为负")
    for name, value in (("k1", parameters.k1), ("k2", parameters.k2), ("kp", parameters.kp)):
        if value < 0:
            problems.append(f"{name} 不得为负")
    if not 0 < parameters.lp <= 1:
        problems.append("lp 必须落在 (0, 1]")
    return problems


def parameter_range_warnings(parameters: HBVParameters) -> list[str]:
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


def run_lumped_hbv(
    precipitation: np.ndarray,
    evaporation: np.ndarray,
    parameters: HBVParameters,
    area_km2: float,
    timestep_hours: float,
) -> dict[str, np.ndarray]:
    """Run the lumped HBV loop on one basin and return per-step state and flux arrays.

    The source initialises SM/SUZ/SLZ at zero and has no warmup mechanism; this
    implementation keeps that behaviour exactly.
    """
    steps = int(len(precipitation))
    fc, beta, c = parameters.fc, parameters.beta, parameters.c
    k0, threshold, k1, k2, kp, lp = parameters.k0, parameters.l, parameters.k1, parameters.k2, parameters.kp, parameters.lp
    pwp = lp * fc

    p_arr = np.asarray(precipitation, dtype=float)
    e_arr = np.asarray(evaporation, dtype=float)
    pet_arr = e_arr * c

    sm = np.zeros(steps)
    suz = np.zeros(steps)
    slz = np.zeros(steps)
    pet_out = np.zeros(steps)
    ea_out = np.zeros(steps)
    effective_out = np.zeros(steps)
    recharge_out = np.zeros(steps)
    q0_arr = np.zeros(steps)
    q1_arr = np.zeros(steps)
    q2_arr = np.zeros(steps)

    clips = [0]

    def floor_zero(value: float) -> float:
        if value < 0.0:
            clips[0] += 1
            return 0.0
        return value

    for t in range(steps):
        prev_sm = sm[t - 1] if t > 0 else 0.0
        prev_suz = suz[t - 1] if t > 0 else 0.0
        prev_slz = slz[t - 1] if t > 0 else 0.0

        # evaporation with the lp-scaled wilting point
        if prev_sm > pwp:
            ae = pet_arr[t]
        else:
            ae = pet_arr[t] * max(prev_sm, 0.0) / max(pwp, 1e-10)
        ae = max(ae, 0.0)

        # saturation-excess effective rainfall
        ratio = min(max(prev_sm, 0.0) / max(fc, 1e-10), 1.0)
        effective = precipitation[t] * (ratio ** beta)

        sm_new = floor_zero(prev_sm + precipitation[t] - ae - effective)
        recharge = max(effective * (1 - ((fc - sm_new) / fc) ** 2), 0.0)

        q0 = k0 * max(sm_new - threshold, 0.0)
        q1 = k1 * max(prev_suz, 0.0) if t > 0 else 0.0
        q2 = k2 * max(prev_slz, 0.0) if t > 0 else 0.0
        uz_exchange = kp * (prev_slz - prev_suz) if t > 0 else 0.0

        if t > 0:
            suz_new = floor_zero(prev_suz + recharge - q1 + uz_exchange)
            slz_new = floor_zero(prev_slz - q2 - uz_exchange)
        else:
            suz_new = floor_zero(recharge)
            slz_new = 0.0

        pet_out[t] = pet_arr[t]
        ea_out[t] = ae
        effective_out[t] = effective
        recharge_out[t] = recharge
        sm[t] = sm_new
        suz[t] = suz_new
        slz[t] = slz_new
        q0_arr[t] = q0
        q1_arr[t] = q1
        q2_arr[t] = q2

    unit_conv = (area_km2 * 1000.0) / (timestep_hours * 3600.0)
    raw_discharge = unit_conv * (q0_arr + q1_arr + q2_arr)
    negative = int(np.sum(raw_discharge < 0.0))
    clips[0] += negative
    discharge = np.maximum(raw_discharge, 0.0)

    return {
        "P": p_arr,
        "PET": pet_out,
        "EA": ea_out,
        "SM": sm,
        "RECHARGE": recharge_out,
        "SUZ": suz,
        "SLZ": slz,
        "Q0": q0_arr,
        "Q1": q1_arr,
        "Q2": q2_arr,
        "Q": discharge,
        "clip_count": np.array([clips[0]], dtype=int),
    }
