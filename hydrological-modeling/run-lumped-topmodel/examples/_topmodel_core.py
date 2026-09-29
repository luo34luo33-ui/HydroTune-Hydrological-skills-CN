"""Independent, mass-conserving TOPMODEL equations (metres and hours)."""
from __future__ import annotations

import math
import numpy as np


def green_ampt(rain_m: float, dt_h: float, k0_m_h: float, psi_m: float,
               dtheta: float, cumulative_m: float) -> tuple[float, float]:
    """Constant-intensity step with explicit ponding time and implicit wetting front."""
    if rain_m <= 0:
        return 0.0, cumulative_m
    head = psi_m * dtheta
    if head == 0:
        infiltrated = min(rain_m, k0_m_h * dt_h)
        return infiltrated, cumulative_m + infiltrated
    intensity = rain_m / dt_h
    if intensity <= k0_m_h:
        return rain_m, cumulative_m + rain_m
    ponding_front = head / (intensity / k0_m_h - 1)
    ponding_time = max(0.0, (ponding_front - cumulative_m) / intensity)
    if ponding_time >= dt_h:
        return rain_m, cumulative_m + rain_m
    front_at_ponding = max(cumulative_m, ponding_front)
    wet_time = dt_h - ponding_time
    lo, hi = front_at_ponding, cumulative_m + rain_m
    for _ in range(70):
        mid = (lo + hi) / 2
        elapsed = (mid - front_at_ponding - head * math.log((mid + head) / (front_at_ponding + head))) / k0_m_h
        if elapsed < wet_time:
            lo = mid
        else:
            hi = mid
    infiltrated = min(rain_m, (lo + hi) / 2 - cumulative_m)
    return infiltrated, cumulative_m + infiltrated


def routing_kernel(distance_m: np.ndarray, cumulative: np.ndarray, dt_h: float,
                   vch: float, vr: float) -> np.ndarray:
    if len(distance_m) == 0 or np.any(np.diff(distance_m) <= 0) or np.any(np.diff(cumulative) < -1e-12):
        raise ValueError("invalid distance-area curve")
    if cumulative[0] < 0 or abs(cumulative[-1] - 1) > 1e-8:
        raise ValueError("distance-area curve must end at 1")
    # Channel speed applies over the first distance bin; hillslope speed afterwards.
    d0 = distance_m[0]
    times = d0 / vch + np.maximum(0.0, distance_m - d0) / vr
    if not np.isfinite(times).all() or times[-1] / dt_h > 1_000_000:
        raise ValueError("routing travel time is nonfinite or exceeds one million steps")
    increments = np.diff(np.r_[0.0, cumulative])
    max_index = int(math.ceil(times[-1] / dt_h))
    kernel = np.zeros(max_index + 1)
    for t, fraction in zip(times / dt_h, increments):
        low = int(math.floor(t))
        frac = t - low
        kernel[low] += fraction * (1 - frac)
        if frac > 0:
            kernel[low + 1] += fraction * frac
    if not np.isfinite(kernel).all() or abs(kernel.sum() - 1) > 1e-8:
        raise ValueError("routing kernel is invalid")
    return kernel


def initial_state(ti: np.ndarray, weights: np.ndarray, params: dict, dt_h: float, kernel: np.ndarray) -> dict:
    lam = float(np.dot(ti, weights))
    exponent = params["lnTe"] + math.log(dt_h) - lam
    if not math.isfinite(exponent) or exponent > 700:
        raise ValueError("transmissivity exponent is nonfinite or overflows")
    qss = math.exp(exponent)
    qs0_step = params["qs0"] * dt_h
    if not math.isfinite(qs0_step):
        raise ValueError("initial baseflow is nonfinite")
    if qs0_step >= qss:
        raise ValueError("qs0 must be less than exp(lnTe-lambda)")
    deficit = -params["m"] * math.log(qs0_step / qss)
    initial_routing = np.array([qs0_step * float(kernel[i + 1:].sum()) for i in range(len(kernel))])
    return {"mean_deficit_m": deficit, "root_deficit_m": np.full(len(ti), params["Sr0"]),
            "unsat_m": np.zeros(len(ti)), "ga_cumulative_m": 0.0,
            "routing_m": initial_routing, "lambda": lam, "qss_step_m": qss}


def step(state: dict, rain_m: float, pet_m: float, params: dict, ti: np.ndarray,
         weights: np.ndarray, dt_h: float, kernel: np.ndarray) -> dict:
    m = params["m"]
    local_deficit = np.maximum(0.0, state["mean_deficit_m"] + m * (state["lambda"] - ti))
    if params.get("infiltration_excess", False):
        infiltration, state["ga_cumulative_m"] = green_ampt(
            rain_m, dt_h, params["K0"], params["psi"], params["dtheta"], state["ga_cumulative_m"])
    else:
        infiltration = rain_m
    infex = rain_m - infiltration
    root = state["root_deficit_m"]
    unsat = state["unsat_m"]
    root_take = np.minimum(root, infiltration)
    root -= root_take
    unsat += infiltration - root_take
    sat_excess = np.maximum(0.0, unsat - local_deficit)
    unsat -= sat_excess
    if params["td"] > 0:
        recharge = np.where(local_deficit > 0, unsat * dt_h / (np.maximum(local_deficit, 1e-12) * params["td"]), unsat)
    else:
        recharge = (-params["td"]) * params["K0"] * dt_h * np.exp(-local_deficit / m)
    recharge = np.minimum(unsat, recharge)
    unsat -= recharge
    et = np.minimum(params["Srmax"] - root, pet_m * np.maximum(0.0, 1.0 - root / params["Srmax"]))
    root += et
    base = state["qss_step_m"] * math.exp(-state["mean_deficit_m"] / m)
    mean_new = state["mean_deficit_m"] + base - float(np.dot(weights, recharge))
    if mean_new < 0:
        sat_excess += -mean_new
        mean_new = 0.0
    state["mean_deficit_m"] = mean_new
    surface = float(np.dot(weights, sat_excess)) + infex
    actual_et = float(np.dot(weights, et))
    generated = surface + base
    queue = state["routing_m"]
    queue += generated * kernel
    outlet = float(queue[0])
    queue[:-1] = queue[1:]
    queue[-1] = 0.0
    return {"saturation_excess_m": float(np.dot(weights, sat_excess)),
            "infiltration_excess_m": infex, "baseflow_m": base,
            "generated_m": generated, "actual_et_m": actual_et,
            "outlet_m": outlet, "root_deficit_m": float(np.dot(weights, root)),
            "unsat_m": float(np.dot(weights, unsat)), "mean_deficit_m": mean_new,
            "routing_storage_m": float(queue.sum())}
