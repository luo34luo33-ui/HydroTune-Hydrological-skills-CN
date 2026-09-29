"""Private lumped Xin'anjiang core: three-layer evaporation, saturation-excess runoff,
three-source separation and slope/channel routing up to the channel-entry discharge Qt.

This module is private to hydrological-modeling/run-lumped-xaj-model. Other skills must
not import it; they carry their own tested copies when they need a helper.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np

EPS = 1e-12

PARAMETER_FIELDS = (
    "K", "B", "C", "WM", "WUM", "WLM", "IM", "SM", "EX",
    "KG", "KI", "CG", "CI", "CS", "L",
    "WUM_init", "WLM_init", "WDM_init", "S1", "FR1", "Q",
)


@dataclass(frozen=True)
class XAJParameters:
    k: float
    b: float
    c: float
    wm: float
    wum: float
    wlm: float
    im: float
    sm: float
    ex: float
    kg: float
    ki: float
    cg: float
    ci: float
    cs: float
    lag_steps: int
    wum_init: float
    wlm_init: float
    wdm_init: float
    s1_init: float
    fr1: float
    q_init: float

    @property
    def wdm(self) -> float:
        return max(0.0, self.wm - self.wum - self.wlm)

    @property
    def wmm(self) -> float:
        return float(self.wm)

    @property
    def smm(self) -> float:
        return float(self.sm)


def build_parameters(raw: dict[str, Any]) -> XAJParameters:
    missing = [field for field in PARAMETER_FIELDS if field not in raw]
    if missing:
        raise ValueError(f"参数集缺少字段: {', '.join(missing)}")
    values: dict[str, float] = {}
    for field in PARAMETER_FIELDS:
        value = raw[field]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"参数 {field} 必须是数值")
        values[field] = float(value)
    return XAJParameters(
        k=values["K"],
        b=values["B"],
        c=values["C"],
        wm=values["WM"],
        wum=values["WUM"],
        wlm=values["WLM"],
        im=values["IM"],
        sm=values["SM"],
        ex=values["EX"],
        kg=values["KG"],
        ki=values["KI"],
        cg=values["CG"],
        ci=values["CI"],
        cs=values["CS"],
        lag_steps=max(0, int(round(values["L"]))),
        wum_init=values["WUM_init"],
        wlm_init=values["WLM_init"],
        wdm_init=values["WDM_init"],
        s1_init=values["S1"],
        fr1=values["FR1"],
        q_init=values["Q"],
    )


def validate_parameters(parameters: XAJParameters, area_km2: float, timestep_hours: float) -> list[str]:
    problems: list[str] = []
    if area_km2 <= 0:
        problems.append("--area-km2 必须为正")
    if timestep_hours <= 0:
        problems.append("--timestep-hours 必须为正")
    if parameters.b <= 0:
        problems.append("B 必须为正")
    if not 0 < parameters.c <= 1:
        problems.append("C 必须落在 (0, 1]")
    if parameters.wm <= 0:
        problems.append("WM 必须为正")
    if parameters.wum < 0 or parameters.wlm < 0:
        problems.append("WUM 与 WLM 不得为负")
    if parameters.wum + parameters.wlm > parameters.wm:
        problems.append("WUM + WLM 不得超过 WM")
    if not 0 <= parameters.im <= 1:
        problems.append("IM 必须落在 [0, 1]")
    if parameters.sm <= 0:
        problems.append("SM 必须为正")
    if parameters.ex <= 0:
        problems.append("EX 必须为正")
    if parameters.kg < 0 or parameters.ki < 0:
        problems.append("KG 与 KI 不得为负")
    if parameters.kg + parameters.ki > 1:
        problems.append("KG + KI 不得超过 1")
    for name, value in (("CG", parameters.cg), ("CI", parameters.ci), ("CS", parameters.cs)):
        if not 0 <= value < 1:
            problems.append(f"{name} 必须落在 [0, 1)")
    if not 0 <= parameters.wum_init <= parameters.wum:
        problems.append("WUM_init 必须落在 [0, WUM]")
    if not 0 <= parameters.wlm_init <= parameters.wlm:
        problems.append("WLM_init 必须落在 [0, WLM]")
    wdm = parameters.wdm
    if not 0 <= min(parameters.wdm_init, wdm) <= wdm:
        problems.append("WDM_init 必须落在 [0, WM - WUM - WLM]")
    if parameters.s1_init < 0:
        problems.append("S1 不得为负")
    if not 0 < parameters.fr1 <= 1:
        problems.append("FR1 必须落在 (0, 1]")
    if parameters.q_init < 0:
        problems.append("Q 不得为负")
    return problems


def _clip01(value: float, counter: list[int]) -> float:
    clipped = float(np.clip(value, 0.0, 1.0 - EPS))
    if abs(clipped - float(value)) > 0.0:
        counter[0] += 1
    return clipped


def _pos(value: float) -> float:
    return float(max(value, EPS))


def run_lumped_xaj(
    precipitation: np.ndarray,
    evaporation: np.ndarray,
    parameters: XAJParameters,
    area_km2: float,
    timestep_hours: float,
) -> dict[str, np.ndarray]:
    """Run the lumped Xin'anjiang loop and return per-step state and flux arrays."""
    steps = int(len(precipitation))
    unit = area_km2 / (3.6 * timestep_hours)
    wdm = parameters.wdm
    wdm_init = min(parameters.wdm_init, wdm)

    p_perv = (1.0 - parameters.im) * precipitation
    p_im = parameters.im * precipitation

    wu = np.zeros(steps)
    wl = np.zeros(steps)
    wd = np.zeros(steps)
    ep = np.zeros(steps)
    eu = np.zeros(steps)
    el = np.zeros(steps)
    ed = np.zeros(steps)
    evap_total = np.zeros(steps)
    pe = np.zeros(steps)
    soil = np.zeros(steps)
    runoff = np.zeros(steps)
    fr = np.zeros(steps)
    storage = np.zeros(steps)
    rs = np.zeros(steps)
    ri = np.zeros(steps)
    rg = np.zeros(steps)
    qs = np.zeros(steps)
    qi = np.zeros(steps)
    qg = np.zeros(steps)
    qt = np.zeros(steps)
    qt_out = np.zeros(steps)

    clips = [0]
    for i in range(steps):
        # --- 1. three-layer soil moisture redistribution ---
        if i == 0:
            wu_i = parameters.wum_init
            wl_i = parameters.wlm_init
            wd_i = wdm_init
            prev_wl = 0.0
            prev_wd = 0.0
        else:
            infiltration = pe[i - 1] - runoff[i - 1]
            wu_i = wu[i - 1] + infiltration
            wl_i = wl[i - 1]
            wd_i = wd[i - 1]
            prev_wl = wl[i - 1]
            prev_wd = wd[i - 1]
        if wu_i < 0:
            wl_i = prev_wl + wu_i
            wu_i = 0.0
            wd_i = prev_wd
            if wl_i < 0:
                wd_i = prev_wd + wl_i
                wl_i = 0.0
                wu_i = 0.0
                if wd_i < 0:
                    wd_i = 0.0
                    wl_i = 0.0
                    wu_i = 0.0
        if wu_i > parameters.wum:
            wl_i = wu_i - parameters.wum + prev_wl
            wu_i = parameters.wum
            wd_i = prev_wd
            if wl_i > parameters.wlm:
                wd_i = wl_i - parameters.wlm + prev_wd
                wu_i = parameters.wum
                wl_i = parameters.wlm
                if wd_i > wdm:
                    wd_i = wdm
                    wu_i = parameters.wum
                    wl_i = parameters.wlm
        wu[i] = wu_i
        wl[i] = wl_i
        wd[i] = wd_i

        # --- 2. three-layer evaporation ---
        ep_i = float(evaporation[i]) * parameters.k
        pp = float(p_perv[i])
        # The source notebook evaluates the EU term before assigning it for this step,
        # so the branch conditions use the unassigned value 0.0. That behaviour is kept.
        unassigned = 0.0
        if wu_i + pp >= ep_i:
            eu_i = ep_i
            el_i = 0.0
            ed_i = 0.0
        elif wl_i >= parameters.c * parameters.wlm:
            eu_i = wu_i + pp
            el_i = (ep_i - eu_i) * (wl_i / _pos(parameters.wlm))
            ed_i = 0.0
        elif parameters.c * (ep_i - unassigned) <= wl_i and wl_i < parameters.c * parameters.wlm:
            eu_i = wu_i + pp
            el_i = parameters.c * (ep_i - eu_i)
            ed_i = 0.0
        elif wl_i < parameters.c * (ep_i - unassigned):
            eu_i = wu_i + pp
            el_i = wl_i
            ed_i = parameters.c * (ep_i - eu_i) - el_i
        else:
            eu_i = wu_i + pp
            el_i = 0.0
            ed_i = 0.0
        ep[i] = ep_i
        eu[i] = eu_i
        el[i] = el_i
        ed[i] = ed_i
        evap_total[i] = eu_i + el_i + ed_i

        # --- 3. net rainfall and total soil moisture ---
        pe_i = pp - evap_total[i]
        soil_i = wu_i + wl_i + wd_i
        pe[i] = pe_i
        soil[i] = soil_i

        # --- 4. saturation-excess runoff ---
        if pe_i > 0:
            remaining = _clip01(1.0 - soil_i / _pos(parameters.wm), clips)
            a = parameters.wmm * (1.0 - math.pow(remaining, 1.0 / (1.0 + parameters.b)))
            if a + pe_i <= parameters.wmm:
                inner = _clip01(1.0 - (pe_i + a) / _pos(parameters.wmm), clips)
                r_i = pe_i + soil_i - parameters.wm + parameters.wm * math.pow(inner, parameters.b + 1.0)
            else:
                r_i = pe_i - (parameters.wm - soil_i)
        else:
            r_i = 0.0
        runoff[i] = r_i

        # --- 5. runoff coefficient ---
        if r_i > 0:
            fr_i = min(1.0, max(r_i / _pos(pe_i), 1e-9))
        else:
            fr_i = parameters.fr1 if i == 0 else fr[i - 1]
        fr[i] = fr_i

        # --- 6. three-source separation ---
        if i == 0:
            storage[i] = parameters.s1_init
        fr_current = max(float(fr[i]), 1e-9)
        fr_previous = max(float(fr[i - 1]) if i > 0 else parameters.fr1, 1e-9)
        free_ratio = parameters.fr1 if i == 0 else fr_previous
        free_storage = storage[i] * free_ratio / _pos(fr_current)

        if pe_i > 0:
            ratio = _clip01((free_storage) / _pos(parameters.smm), clips)
            au = parameters.smm * (1.0 - math.pow(1.0 - ratio, 1.0 / (1.0 + parameters.ex)))
            if pe_i + au < parameters.smm:
                base = pe_i + free_storage - parameters.sm
                inner = _clip01(1.0 - (pe_i + au) / _pos(parameters.smm), clips)
                rs_raw = fr_current * (base + parameters.sm * math.pow(inner, 1.0 + parameters.ex))
            else:
                rs_raw = fr_current * (pe_i + free_storage - parameters.sm)
            r_total = float(r_i) if np.isfinite(r_i) else 0.0
            rs_i = float(np.clip(rs_raw, 0.0, r_total))
            if abs(rs_i - float(rs_raw)) > 0.0:
                clips[0] += 1
            s_value = free_storage + (r_i - rs_i) / _pos(fr_current)
            ri_i = parameters.ki * s_value * fr_current
            rg_i = parameters.kg * s_value * fr_current
            if i < steps - 1:
                storage[i + 1] = s_value * (1.0 - parameters.ki - parameters.kg)
        else:
            s_value = free_storage
            if i < steps - 1:
                storage[i + 1] = s_value * (1.0 - parameters.kg - parameters.ki)
            rs_i = 0.0
            rg_i = parameters.kg * s_value * fr_current
            ri_i = parameters.ki * s_value * fr_current
        rs[i] = rs_i
        ri[i] = ri_i
        rg[i] = rg_i

        # --- 7. slope and channel routing up to the channel entry ---
        r_im = float(p_im[i])
        qs_i = max(0.0, float((rs_i + r_im) * unit))
        if i == 0:
            qi_i = parameters.q_init / 3.0
            qg_i = parameters.q_init / 3.0
        else:
            qi_i = parameters.ci * qi[i - 1] + (1 - parameters.ci) * ri_i * unit
            qg_i = parameters.cg * qg[i - 1] + (1 - parameters.cg) * rg_i * unit
        qs[i] = qs_i
        qi[i] = qi_i
        qg[i] = qg_i
        qt_i = qs_i + qi_i + qg_i
        qt[i] = qt_i
        if 0 <= i <= parameters.lag_steps:
            qt_out[i] = parameters.q_init
        else:
            qt_out[i] = parameters.cs * qt_out[i - 1] + (1 - parameters.cs) * qt[i - parameters.lag_steps]

    return {
        "P": np.asarray(precipitation, dtype=float),
        "E0": np.asarray(evaporation, dtype=float),
        "P_perv": p_perv,
        "P_im": p_im,
        "WU": wu,
        "WL": wl,
        "WD": wd,
        "EP": ep,
        "EU": eu,
        "EL": el,
        "ED": ed,
        "E": evap_total,
        "PE": pe,
        "W": soil,
        "R": runoff,
        "FR": fr,
        "S1": storage,
        "RS": rs,
        "RI": ri,
        "RG": rg,
        "QS": qs,
        "QI": qi,
        "QG": qg,
        "QT": qt,
        "Qt": qt_out,
        "R_im": p_im,
        "clip_count": np.array([clips[0]], dtype=int),
    }
