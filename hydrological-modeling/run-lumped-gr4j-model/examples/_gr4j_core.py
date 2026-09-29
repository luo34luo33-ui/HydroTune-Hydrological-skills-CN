"""Private lumped GR4J core migrated line by line from C:/Users/DELL/Downloads/gr4j.py
(functions ``s_curves1``/``s_curves2``/``gr4j``), reduced to a single basin.

Deviations from the source module, all intentional and recorded in usage-guide.md:
- the returned discharge is exposed in two calibres: ``Q_MM`` (identical to the source
  ``qsim``, mm per step) and ``Q`` (m3/s) obtained with an explicit area/timestep
  conversion the source lacks;
- initial ``production_store``/``routing_store`` come in through ``initial_states``
  (source ``states``); UH1/UH2 array seeds are not exposed and stay at the source
  default of all zeros;
- every ``max(0, ...)`` clip is counted so silent water loss stays visible;
- internal state and flux columns are exported (strict superset of the source return).

This module is private to hydrological-modeling/run-lumped-gr4j-model.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, tanh
from typing import Any

import numpy as np

PARAMETER_FIELDS = ("X1", "X2", "X3", "X4")

TANH_SATURATION_LIMIT = 13.0  # source clamp protecting tanh from saturation


@dataclass(frozen=True)
class GR4JParameters:
    x1: float
    x2: float
    x3: float
    x4: float


def build_parameters(raw: dict[str, Any]) -> GR4JParameters:
    """All four parameters are required; the source raises KeyError on absence too."""
    missing = [field for field in PARAMETER_FIELDS if field not in raw]
    if missing:
        raise ValueError(f"参数集缺少字段: {', '.join(missing)}")
    values: dict[str, float] = {}
    for field in PARAMETER_FIELDS:
        value = raw[field]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"参数 {field} 必须是数值")
        values[field] = float(value)
    return GR4JParameters(x1=values["X1"], x2=values["X2"], x3=values["X3"], x4=values["X4"])


def validate_parameters(parameters: GR4JParameters, area_km2: float, timestep_hours: float) -> list[str]:
    problems: list[str] = []
    if area_km2 <= 0:
        problems.append("--area-km2 必须为正")
    if timestep_hours <= 0:
        problems.append("--timestep-hours 必须为正")
    if parameters.x1 <= 0:
        problems.append("X1 必须为正（产流水库容量）")
    if parameters.x3 <= 0:
        problems.append("X3 必须为正（汇流水库容量）")
    if parameters.x4 <= 0:
        problems.append("X4 必须为正（单位线历时）；X4<=0 会使单位线长度为 0")
    # X2 may be negative: a negative groundwater exchange means external inflow,
    # which is the standard GR4J semantic and must not be rejected.
    return problems


def s_curves1(t: float, x4: float) -> float:
    """Unit hydrograph ordinates for UH1 derived from the S-curve (source verbatim)."""
    if t <= 0:
        return 0.0
    if t < x4:
        return (t / x4) ** 2.5
    return 1.0


def s_curves2(t: float, x4: float) -> float:
    """Unit hydrograph ordinates for UH2 derived from the S-curve (source verbatim)."""
    if t <= 0:
        return 0.0
    if t < x4:
        return 0.5 * (t / x4) ** 2.5
    if t < 2 * x4:
        return 1.0 - 0.5 * (2 - t / x4) ** 2.5
    return 1.0


def unit_hydrograph_ordinates(n_ordinates: int, x4: float, second: bool) -> list[float]:
    curve = s_curves2 if second else s_curves1
    return [curve(t, x4) - curve(t - 1, x4) for t in range(1, n_ordinates + 1)]


def run_lumped_gr4j(
    precipitation: np.ndarray,
    pet: np.ndarray,
    parameters: GR4JParameters,
    area_km2: float,
    timestep_hours: float,
    initial_states: dict[str, float] | None = None,
) -> dict[str, np.ndarray]:
    """Run the lumped GR4J loop on one basin and return per-step state and flux arrays.

    The source initialises production/routing stores at zero and the UH registers at
    all zeros; ``initial_states`` may override ``production_store``/``routing_store``.
    """
    steps = int(len(precipitation))
    initial = {"production_store": 0.0, "routing_store": 0.0}
    if initial_states:
        for key in initial:
            if key in initial_states:
                initial[key] = float(initial_states[key])

    x1, x2, x3, x4 = parameters.x1, parameters.x2, parameters.x3, parameters.x4

    n_uh1 = int(ceil(x4))
    n_uh2 = int(ceil(2.0 * x4))
    uh1_ordinates = unit_hydrograph_ordinates(n_uh1, x4, second=False)
    uh2_ordinates = unit_hydrograph_ordinates(n_uh2, x4, second=True)

    uh1 = [0.0] * n_uh1
    uh2 = [0.0] * n_uh2

    production_store = initial["production_store"]
    routing_store = initial["routing_store"]

    p_arr = np.asarray(precipitation, dtype=float)
    pet_arr = np.asarray(pet, dtype=float)

    ea = np.zeros(steps)
    ps_out = np.zeros(steps)
    perc_out = np.zeros(steps)
    pr_out = np.zeros(steps)
    uh1_in = np.zeros(steps)
    uh2_in = np.zeros(steps)
    gw_out = np.zeros(steps)
    qr_out = np.zeros(steps)
    qd_out = np.zeros(steps)
    q_mm = np.zeros(steps)
    s_out = np.zeros(steps)
    r_out = np.zeros(steps)
    clips = [0]

    def floor_zero(value: float) -> float:
        if value < 0.0:
            clips[0] += 1
            return 0.0
        return value

    for t in range(steps):
        p = float(p_arr[t])
        e = float(pet_arr[t])

        if p > e:
            net_evap = 0.0
            scaled_net_precip = (p - e) / x1
            if scaled_net_precip > TANH_SATURATION_LIMIT:
                scaled_net_precip = TANH_SATURATION_LIMIT
                clips[0] += 1
            tanh_scaled_net_precip = tanh(scaled_net_precip)
            reservoir_production = (
                x1 * (1 - (production_store / x1) ** 2) * tanh_scaled_net_precip
            ) / (1 + production_store / x1 * tanh_scaled_net_precip)
            routing_pattern = p - e - reservoir_production
        else:
            scaled_net_evap = (e - p) / x1
            if scaled_net_evap > TANH_SATURATION_LIMIT:
                scaled_net_evap = TANH_SATURATION_LIMIT
                clips[0] += 1
            tanh_scaled_net_evap = tanh(scaled_net_evap)
            ps_div_x1 = (2 - production_store / x1) * tanh_scaled_net_evap
            net_evap = production_store * ps_div_x1 / (
                1 + (1 - production_store / x1) * tanh_scaled_net_evap
            )
            reservoir_production = 0.0
            routing_pattern = 0.0

        production_store = production_store - net_evap + reservoir_production

        percolation = production_store / (1 + (production_store / 2.25 / x1) ** 4) ** 0.25

        routing_pattern = routing_pattern + (production_store - percolation)
        production_store = percolation

        for i in range(0, len(uh1) - 1):
            uh1[i] = uh1[i + 1] + uh1_ordinates[i] * routing_pattern
        uh1[-1] = uh1_ordinates[-1] * routing_pattern

        for j in range(0, len(uh2) - 1):
            uh2[j] = uh2[j + 1] + uh2_ordinates[j] * routing_pattern
        uh2[-1] = uh2_ordinates[-1] * routing_pattern

        groundwater_exchange = x2 * (routing_store / x3) ** 3.5
        routing_store_new = floor_zero(routing_store + uh1[0] * 0.9 + groundwater_exchange)

        r2 = routing_store_new / (1 + (routing_store_new / x3) ** 4) ** 0.25
        qr = routing_store_new - r2
        qd_raw = uh2[0] * 0.1 + groundwater_exchange
        qd = floor_zero(qd_raw)
        q_mm_value = qr + qd
        routing_store = r2

        ea[t] = net_evap
        ps_out[t] = reservoir_production
        perc_out[t] = percolation
        pr_out[t] = routing_pattern
        uh1_in[t] = uh1[0] * 0.9
        uh2_in[t] = uh2[0] * 0.1
        gw_out[t] = groundwater_exchange
        qr_out[t] = qr
        qd_out[t] = qd
        q_mm[t] = q_mm_value
        s_out[t] = production_store
        r_out[t] = routing_store

    unit_conv = (area_km2 * 1000.0) / (timestep_hours * 3600.0)
    return {
        "P": p_arr,
        "PET": pet_arr,
        "EA": ea,
        "PS": ps_out,
        "PERC": perc_out,
        "PR": pr_out,
        "UH1_IN": uh1_in,
        "UH2_IN": uh2_in,
        "GW": gw_out,
        "QR": qr_out,
        "QD": qd_out,
        "Q_MM": q_mm,
        "PROD_STORE": s_out,
        "ROUT_STORE": r_out,
        "Q": q_mm * unit_conv,
        "clip_count": np.array([clips[0]], dtype=int),
    }
