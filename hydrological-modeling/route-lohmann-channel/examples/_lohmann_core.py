"""Private standard Lohmann channel routing core, migrated from
KMarkert/sacsma (sacsma/routing.py, function ``lohmann``) and corrected to the
standard algorithm of Lohmann et al. (1998) / the VIC routing lineage.

Deviations from the source module, all intentional and recorded in usage-guide.md:
- numba ``@jit`` is dropped (pure Python + numpy + scipy);
- ``stats.gamma(N, K)`` placed K in the ``loc`` slot (scipy signature is
  ``gamma(a, loc, scale)``); the standard uses ``scale=K``;
- the HRU unit hydrograph integrated ``quad(pdf, 24*i, 24*i+1)`` (bin width 1);
  the standard integrates complete daily bins ``[i, i+1]`` in days;
- the daily aggregation sliced ``FR[(24*i-23):(24*i)]`` — the Fortran 1-based
  day bounds copied verbatim into 0-based Python — which drops the first hour
  of every day and loses UH mass; the standard slice is ``[24*i-24 : 24*i]``;
- the UH composition wrote ``uh_direct[k+u-1]``, which wraps to ``[-1]`` when
  ``k = u = 0``; the standard index is ``k+u`` (the array of length
  ``KE + UH_DAY - 1`` holds it exactly);
- the channel-response recursion read ``FR[t-L, 0]`` — a column that is never
  written anywhere in the source — so the diffusion-wave channel UH was dead
  code and collapsed to a one-day pulse regardless of VELO/DIFF/flowlen. The
  standard channel UH is the single convolution of the normalized hourly grid
  UH with a unit input spread uniformly over the first 24 hours (Lohmann et
  al. 1998), whose daily ordinates sum to 1; a recursive reading of the total
  response would re-route already-routed water and diverge.

The routing is a linear convolution with normalized unit hydrographs: the unit
of the inflow columns passes through unchanged, and no area conversion exists
in the source or here.

This module is private to hydrological-modeling/route-lohmann-channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import stats
from scipy.integrate import quad

PARAMETER_FIELDS = ("N", "K", "VELO", "DIFF")

KE = 12          # HRU unit hydrograph base time [days]
UH_DAY = 96      # river routing unit hydrograph base time [days]
DT = 3600.0      # time step in seconds for solving the Saint-Venant equation
TMAX = UH_DAY * 24  # base time of the river routing UH in hours
LE = 48 * 50     # grid unit hydrograph length in hours


@dataclass(frozen=True)
class LohmannParameters:
    n: float
    k: float
    velo: float
    diff: float


def build_parameters(raw: dict[str, Any]) -> LohmannParameters:
    """All four parameters are required; the source indexes par[0..3] directly."""
    missing = [field for field in PARAMETER_FIELDS if field not in raw]
    if missing:
        raise ValueError(f"路由参数缺少字段: {', '.join(missing)}")
    values: dict[str, float] = {}
    for field in PARAMETER_FIELDS:
        value = raw[field]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"路由参数 {field} 必须是数值")
        values[field] = float(value)
    return LohmannParameters(n=values["N"], k=values["K"], velo=values["VELO"], diff=values["DIFF"])


def validate_parameters(parameters: LohmannParameters, flowlen_m: float) -> list[str]:
    problems: list[str] = []
    if parameters.n <= 0:
        problems.append("N 必须为正（Gamma UH 形状参数）")
    if parameters.k <= 0:
        problems.append("K 必须为正（Gamma UH 尺度参数，天）")
    if parameters.velo <= 0:
        problems.append("VELO 必须为正（波速，m/s）")
    if parameters.diff <= 0:
        problems.append("DIFF 必须为正（扩散系数，m²/s）")
    if flowlen_m < 0:
        problems.append("--flowlen-m 必须非负（0 表示河道 UH 退化为脉冲）")
    return problems


def build_channel_uh(flowlen_m: float, velo: float, diff: float) -> np.ndarray:
    """Daily river-routing UH (96 days) from the linearized Saint-Venant solution.

    A unit input spread uniformly over the first 24 hours is routed through the
    hourly grid UH; the hourly response is aggregated to daily ordinates so the
    ordinates sum to 1.
    """
    uh_river = np.zeros(UH_DAY)
    if flowlen_m == 0:
        uh_river[0] = 1.0
        return uh_river

    uhm_grid = np.zeros(LE)
    elapsed = 0.0
    for k in range(LE):
        elapsed += DT
        pot = ((velo * elapsed - flowlen_m) ** 2) / (4 * diff * elapsed)
        if pot <= 69:
            H = (
                flowlen_m
                / (2 * elapsed * np.sqrt(np.pi * elapsed * diff))
                * np.exp(-pot)
            )
        else:
            H = 0.0
        uhm_grid[k] = H

    total = float(np.sum(uhm_grid))
    if total == 0:
        uhm_grid[0] = 1.0
    else:
        uhm_grid = uhm_grid / total

    # Route a unit input spread uniformly over the first 24 hours through the
    # grid UH once (single convolution; ordinates sum to 1).
    unit_day = np.zeros(TMAX)
    unit_day[0:24] = 1.0 / 24.0
    response = np.zeros(TMAX)
    for t in range(TMAX):
        lmax = min(t, LE)
        if lmax >= 1:
            lags = np.arange(1, lmax + 1)
            response[t] += np.sum(uhm_grid[lags - 1] * unit_day[t - lags])

    for i in range(1, UH_DAY + 1):
        uh_river[i - 1] = np.sum(response[(24 * i - 24): (24 * i)])
    return uh_river


def build_hru_uh(shape: float, scale: float) -> np.ndarray:
    """Daily HRU direct-runoff UH (12 days) from Gamma(shape, scale) daily bins."""
    uh_dist = stats.gamma(shape, scale=scale)
    uh_hru_direct = np.zeros(KE)
    for i in range(KE):
        uh_hru_direct[i] = quad(uh_dist.pdf, i, i + 1)[0]
    return uh_hru_direct


def run_lohmann_routing(
    direct: np.ndarray,
    base: np.ndarray,
    parameters: LohmannParameters,
    flowlen_m: float,
) -> dict[str, np.ndarray]:
    """Route the direct and base inflow columns and return per-step routed series.

    Both inflow arrays keep their input unit (the UH ordinates are normalized);
    initial history is zero (the source behaviour), so water still held in the
    UH tail after the last step is not recorded in the outflow.
    """
    uh_river = build_channel_uh(flowlen_m, parameters.velo, parameters.diff)
    uh_hru_direct = build_hru_uh(parameters.n, parameters.k)
    uh_hru_base = np.zeros(KE)
    uh_hru_base[0] = 1.0

    length = KE + UH_DAY - 1
    uh_direct = np.zeros(length)
    uh_base = np.zeros(length)
    for k in range(KE):
        for u in range(UH_DAY):
            uh_direct[k + u] = uh_direct[k + u] + uh_hru_direct[k] * uh_river[u]
            uh_base[k + u] = uh_base[k + u] + uh_hru_base[k] * uh_river[u]
    uh_direct = uh_direct / np.sum(uh_direct)
    uh_base = uh_base / np.sum(uh_base)

    def convolve_zero_history(inflow: np.ndarray, uh: np.ndarray) -> np.ndarray:
        steps = int(len(inflow))
        outflow = np.zeros(steps)
        for i in range(steps):
            j = np.arange(0, len(uh))
            j = j[i - j >= 0]
            outflow[i] = np.sum(uh[j] * inflow[i - j])
        return outflow

    direct_arr = np.asarray(direct, dtype=float)
    base_arr = np.asarray(base, dtype=float)
    routed_direct = convolve_zero_history(direct_arr, uh_direct)
    routed_base = convolve_zero_history(base_arr, uh_base)

    return {
        "routed_direct": routed_direct,
        "routed_base": routed_base,
        "uh_river": uh_river,
        "uh_direct": uh_direct,
        "uh_base": uh_base,
        "uh_hru_direct": uh_hru_direct,
    }
