"""Private lumped SAC-SMA core migrated line by line from
KMarkert/sacsma (sacsma/land_surface.py, function ``sacsma``), reduced to a single basin.

Deviations from the source module, all intentional and recorded in usage-guide.md:
- numba ``@jit`` is dropped; the loop is plain Python + numpy (identical arithmetic);
- ``ninc`` is cast with ``int()`` explicitly (the source relied on numba handling the
  float returned by ``np.floor``);
- every clamp (negative-flow fallback, threshold resets, overflow transfers) is
  counted so silent water adjustments stay visible;
- internal state and flux columns are exported (strict superset of the source's
  3 x N [total, surface, base] return);
- the source semantics ``tot_outflow = surf + base - et4`` is preserved verbatim,
  including the possibility that ``Q_MM`` differs from ``SURF + BASE`` after the
  post-adjustment (the source never recomputes the total).

This module is private to hydrological-modeling/run-lumped-sacsma-model.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any

import numpy as np

PARAMETER_FIELDS = (
    "UZTWM", "UZFWM", "LZTWM", "LZFPM", "LZFSM", "ADIMP", "UZK", "LZPK",
    "LZSK", "ZPERC", "REXP", "PCTIM", "PFREE", "RIVA", "SIDE", "RSERV",
)
CAPACITY_FIELDS = ("UZTWM", "UZFWM", "LZTWM", "LZFPM", "LZFSM")
FRACTION_FIELDS = ("ADIMP", "PCTIM", "RIVA", "PFREE", "RSERV")
DEPLETION_FIELDS = ("UZK", "LZPK", "LZSK")
STATE_FIELDS = ("UZTWC", "UZFWC", "LZTWC", "LZFSC", "LZFPC", "ADIMC")
SOURCE_EQUIVALENT_INITIAL_STATE = {"UZTWC": 0.0, "UZFWC": 0.0, "LZTWC": 500.0, "LZFSC": 500.0, "LZFPC": 500.0, "ADIMC": 0.0}

THRESHOLD_ZERO = 0.00001  # source threshold to be considered as zero
BASEFLOW_FLOOR = 0.0001  # source free-water depletion recovery floor


@dataclass(frozen=True)
class SacsmaParameters:
    uztwm: float
    uzfwm: float
    lztwm: float
    lzfpm: float
    lzfsm: float
    adimp: float
    uzk: float
    lzpk: float
    lzsk: float
    zperc: float
    rexp: float
    pctim: float
    pfree: float
    riva: float
    side: float
    rserv: float


def build_parameters(raw: dict[str, Any]) -> SacsmaParameters:
    """All sixteen parameters are required; the source indexes par[0..15] directly."""
    missing = [field for field in PARAMETER_FIELDS if field not in raw]
    if missing:
        raise ValueError(f"参数集缺少字段: {', '.join(missing)}")
    values: dict[str, float] = {}
    for field in PARAMETER_FIELDS:
        value = raw[field]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"参数 {field} 必须是数值")
        values[field] = float(value)
    return SacsmaParameters(
        uztwm=values["UZTWM"], uzfwm=values["UZFWM"], lztwm=values["LZTWM"],
        lzfpm=values["LZFPM"], lzfsm=values["LZFSM"], adimp=values["ADIMP"],
        uzk=values["UZK"], lzpk=values["LZPK"], lzsk=values["LZSK"],
        zperc=values["ZPERC"], rexp=values["REXP"], pctim=values["PCTIM"],
        pfree=values["PFREE"], riva=values["RIVA"], side=values["SIDE"],
        rserv=values["RSERV"],
    )


def validate_parameters(parameters: SacsmaParameters, area_km2: float, timestep_hours: float) -> list[str]:
    problems: list[str] = []
    if area_km2 <= 0:
        problems.append("--area-km2 必须为正")
    if timestep_hours <= 0:
        problems.append("--timestep-hours 必须为正")
    for field in CAPACITY_FIELDS:
        if getattr(parameters, field.lower()) <= 0:
            problems.append(f"{field} 必须为正（水库容量）")
    for field in FRACTION_FIELDS:
        value = getattr(parameters, field.lower())
        if not 0.0 <= value <= 1.0:
            problems.append(f"{field} 必须在 [0, 1] 内")
    for field in DEPLETION_FIELDS:
        value = getattr(parameters, field.lower())
        if not 0.0 < value <= 1.0:
            problems.append(f"{field} 必须在 (0, 1] 内（日消耗率）")
    if parameters.rexp < 0:
        problems.append("REXP 必须非负")
    if parameters.side < 0:
        problems.append("SIDE 必须非负")
    if parameters.adimp + parameters.pctim >= 1.0:
        problems.append("ADIMP + PCTIM 必须小于 1（透水面积 PAREA 必须为正）")
    return problems


def run_lumped_sacsma(
    precipitation: np.ndarray,
    pet: np.ndarray,
    parameters: SacsmaParameters,
    area_km2: float,
    timestep_hours: float,
    initial_states: dict[str, float] | None = None,
) -> dict[str, np.ndarray]:
    """Run the lumped SAC-SMA loop on one basin and return per-step state and flux arrays.

    The source initialises UZTWC/UZFWC/LZTWC/LZFSC/LZFPC/ADIMC at
    [0, 0, 500, 500, 500, 0] mm; ``initial_states`` may override any subset of the
    six keys (source-equivalent defaults, not silent ones).
    """
    states = dict(SOURCE_EQUIVALENT_INITIAL_STATE)
    if initial_states:
        for key, value in initial_states.items():
            states[key] = float(value)

    uztwm = parameters.uztwm
    uzfwm = parameters.uzfwm
    lztwm = parameters.lztwm
    lzfpm = parameters.lzfpm
    lzfsm = parameters.lzfsm
    adimp = parameters.adimp
    uzk = parameters.uzk
    lzpk = parameters.lzpk
    lzsk = parameters.lzsk
    zperc = parameters.zperc
    rexp = parameters.rexp
    pctim = parameters.pctim
    pfree = parameters.pfree
    riva = parameters.riva
    side = parameters.side
    rserv = parameters.rserv

    uztwc = states["UZTWC"]
    uzfwc = states["UZFWC"]
    lztwc = states["LZTWC"]
    lzfsc = states["LZFSC"]
    lzfpc = states["LZFPC"]
    adimc = states["ADIMC"]

    prcp = np.asarray(precipitation, dtype=float)
    pet = np.asarray(pet, dtype=float)
    steps = int(len(prcp))

    parea = 1.0 - adimp - pctim
    clips = [0]

    def clamp_zero(value: float) -> float:
        if value < THRESHOLD_ZERO:
            if value < 0.0:
                clips[0] += 1
            return 0.0
        return value

    et1_out = np.zeros(steps)
    et2_out = np.zeros(steps)
    et3_out = np.zeros(steps)
    et4_out = np.zeros(steps)
    et5_out = np.zeros(steps)
    tet_out = np.zeros(steps)
    roimp_out = np.zeros(steps)
    ssur_out = np.zeros(steps)
    sif_out = np.zeros(steps)
    sdro_out = np.zeros(steps)
    surf_out = np.zeros(steps)
    base_out = np.zeros(steps)
    sperc_out = np.zeros(steps)
    q_mm = np.zeros(steps)
    uztwc_out = np.zeros(steps)
    uzfwc_out = np.zeros(steps)
    lztwc_out = np.zeros(steps)
    lzfsc_out = np.zeros(steps)
    lzfpc_out = np.zeros(steps)
    adimc_out = np.zeros(steps)

    for i in range(steps):
        pr = float(prcp[i])
        edmnd = float(pet[i])

        ## ET(1), ET from Upper zone tension water storage
        et1 = edmnd * uztwc / uztwm
        red = edmnd - et1
        uztwc = uztwc - et1
        et2 = 0.0
        if uztwc <= 0:
            et1 = et1 + uztwc
            uztwc = 0.0
            red = edmnd - et1
            if uzfwc < red:
                et2 = uzfwc
                uzfwc = 0.0
                red = red - et2
                uztwc = clamp_zero(uztwc)
                uzfwc = clamp_zero(uzfwc)
            else:
                et2 = red
                uzfwc = uzfwc - et2
                red = 0.0
        else:
            if (uztwc / uztwm) < (uzfwc / uzfwm):
                uzrat = (uztwc + uzfwc) / (uztwm + uzfwm)
                uztwc = uztwm * uzrat
                uzfwc = uzfwm * uzrat
                uztwc = clamp_zero(uztwc)
                uzfwc = clamp_zero(uzfwc)

        ## ET(3), ET from Lower zone tension water storage when residual ET > 0
        et3 = red * lztwc / (uztwm + lztwm)
        lztwc = lztwc - et3
        if lztwc < 0:
            et3 = et3 + lztwc
            lztwc = 0.0

        ## Water resupply from Lower free water storages to Lower tension water storage
        saved = rserv * (lzfpm + lzfsm)
        ratlzt = lztwc / lztwm
        ratlz = (lztwc + lzfpc + lzfsc - saved) / (lztwm + lzfpm + lzfsm - saved)
        if ratlzt < ratlz:
            delta = (ratlz - ratlzt) * lztwm
            lztwc = lztwc + delta
            lzfsc = lzfsc - delta
            if lzfsc < 0:
                lzfpc = lzfpc + lzfsc
                lzfsc = 0.0
            lztwc = clamp_zero(lztwc)

        ## ET(5), ET from additional impervious (ADIMP) area
        et5 = et1 + (red + et2) * (adimc - et1 - uztwc) / (uztwm + lztwm)
        adimc = adimc - et5
        if adimc < 0:
            et5 = et5 + adimc
            adimc = 0.0
        et5 = et5 * adimp

        ## Time interval available moisture in excess of UZTW requirements
        twx = pr + uztwc - uztwm
        if twx < 0:
            uztwc = uztwc + pr
            twx = 0.0
        else:
            uztwc = uztwm
        adimc = adimc + pr - twx

        ## Impervious area runoff
        roimp = pr * pctim

        sbf = 0.0
        ssur = 0.0
        sif = 0.0
        sperc = 0.0
        sdro = 0.0

        ninc = int(floor(1.0 + 0.2 * (uzfwc + twx)))
        dinc = 1.0 / ninc
        pinc = twx / ninc

        duz = 1 - (1 - uzk) ** dinc
        dlzp = 1 - (1 - lzpk) ** dinc
        dlzs = 1 - (1 - lzsk) ** dinc

        for _ in range(ninc):
            adsur = 0.0
            ratio = (adimc - uztwc) / lztwm
            if ratio < 0:
                ratio = 0.0
                clips[0] += 1
            addro = pinc * (ratio ** 2)

            bf_p = lzfpc * dlzp
            lzfpc = lzfpc - bf_p
            if lzfpc <= BASEFLOW_FLOOR:
                bf_p = bf_p + lzfpc
                lzfpc = 0.0
            sbf = sbf + bf_p

            bf_s = lzfsc * dlzs
            lzfsc = lzfsc - bf_s
            if lzfsc <= BASEFLOW_FLOOR:
                bf_s = bf_s + lzfsc
                lzfsc = 0.0
            sbf = sbf + bf_s

            if (pinc + uzfwc) <= 0.01:
                uzfwc = uzfwc + pinc
            else:
                percm = lzfpm * dlzp + lzfsm * dlzs
                perc = percm * uzfwc / uzfwm

                defr = 1.0 - (lztwc + lzfpc + lzfsc) / (lztwm + lzfpm + lzfsm)
                if defr < 0:
                    defr = 0.0
                    clips[0] += 1
                perc = perc * (1.0 + zperc * (defr ** rexp))

                if perc >= uzfwc:
                    perc = uzfwc
                uzfwc = uzfwc - perc

                check = lztwc + lzfpc + lzfsc + perc - lztwm - lzfpm - lzfsm
                if check > 0:
                    perc = perc - check
                    uzfwc = uzfwc + check

                sperc = sperc + perc

                delta = uzfwc * duz
                sif = sif + delta
                uzfwc = uzfwc - delta

                perct = perc * (1.0 - pfree)
                if (perct + lztwc) <= lztwm:
                    lztwc = lztwc + perct
                    percf = 0.0
                else:
                    percf = lztwc + perct - lztwm
                    lztwc = lztwm

                percf = percf + (perc * pfree)

                if percf != 0:
                    hpl = lzfpm / (lzfpm + lzfsm)
                    ratlp = lzfpc / lzfpm
                    ratls = lzfsc / lzfsm
                    fracp = hpl * 2 * (1 - ratlp) / (2 - ratlp - ratls)
                    if fracp > 1.0:
                        fracp = 1.0
                        clips[0] += 1
                    percp = percf * fracp
                    percs = percf - percp
                    lzfsc = lzfsc + percs
                    if lzfsc > lzfsm:
                        percs = percs - lzfsc + lzfsm
                        lzfsc = lzfsm
                        clips[0] += 1
                    lzfpc = lzfpc + percf - percs
                    if lzfpc >= lzfpm:
                        excess = lzfpc - lzfpm
                        lztwc = lztwc + excess
                        lzfpc = lzfpm

                if pinc != 0:
                    if (pinc + uzfwc) <= uzfwm:
                        uzfwc = uzfwc + pinc
                    else:
                        sur = pinc + uzfwc - uzfwm
                        uzfwc = uzfwm
                        ssur = ssur + (sur * parea)
                        adsur = sur * (1.0 - addro / pinc)
                        ssur = ssur + adsur * adimp
                        adimc = adimc + pinc - addro - adsur
                        if adimc > (uztwm + lztwm):
                            addro = addro + adimc - (uztwm + lztwm)
                            adimc = uztwm + lztwm
                        sdro = sdro + (addro * adimp)
                        if adimc < THRESHOLD_ZERO:
                            adimc = 0.0
                            clips[0] += 1

        eused = et1 + et2 + et3
        sif = sif * parea

        tbf = sbf * parea
        bfcc = tbf / (1 + side)

        base = bfcc
        surf = roimp + sdro + ssur + sif

        ## ET(4)- ET from riparian vegetation, subtracted directly from outflow
        et4 = (edmnd - eused) * riva
        eused = eused * parea
        tet = eused + et4 + et5

        if adimc < uztwc:
            adimc = uztwc
            clips[0] += 1

        tot_outflow = surf + base - et4

        if tot_outflow < 0:
            tot_outflow = 0.0
            surf = 0.0
            base = 0.0
            clips[0] += 1
        else:
            surf_remainder = surf - et4
            surf = max(0, surf_remainder)
            if surf_remainder < 0:
                base = base + surf_remainder
                base = max(base, 0)
                clips[0] += 1

        et1_out[i] = et1
        et2_out[i] = et2
        et3_out[i] = et3
        et4_out[i] = et4
        et5_out[i] = et5
        tet_out[i] = tet
        roimp_out[i] = roimp
        ssur_out[i] = ssur
        sif_out[i] = sif
        sdro_out[i] = sdro
        surf_out[i] = surf
        base_out[i] = base
        sperc_out[i] = sperc
        q_mm[i] = tot_outflow
        uztwc_out[i] = uztwc
        uzfwc_out[i] = uzfwc
        lztwc_out[i] = lztwc
        lzfsc_out[i] = lzfsc
        lzfpc_out[i] = lzfpc
        adimc_out[i] = adimc

    unit_conv = (area_km2 * 1000.0) / (timestep_hours * 3600.0)
    return {
        "P": prcp,
        "PET": pet,
        "ET1": et1_out,
        "ET2": et2_out,
        "ET3": et3_out,
        "ET4": et4_out,
        "ET5": et5_out,
        "TET": tet_out,
        "ROIMP": roimp_out,
        "SSUR": ssur_out,
        "SIF": sif_out,
        "SDRO": sdro_out,
        "SURF": surf_out,
        "BASE": base_out,
        "PERC": sperc_out,
        "Q_MM": q_mm,
        "UZTWC": uztwc_out,
        "UZFWC": uzfwc_out,
        "LZTWC": lztwc_out,
        "LZFSC": lzfsc_out,
        "LZFPC": lzfpc_out,
        "ADIMC": adimc_out,
        "Q": q_mm * unit_conv,
        "clip_count": np.array([clips[0]], dtype=int),
    }
