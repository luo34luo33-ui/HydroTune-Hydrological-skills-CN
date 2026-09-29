"""Private lumped DHF (Dahuofang) core migrated line by line from
hydromodel/hydromodel/models/dhf.py (function ``dhf``), reduced to a single basin.

Deviations from the source module, all intentional and recorded in usage-guide.md:
- the seven ``numba``-decorated atomic helpers are not migrated because the main
  function never calls them;
- the ``normalized_params="auto"`` detection is replaced by an explicit scale flag
  handled in the CLI layer;
- the ``[time, basin, 2]`` batch tensor is reduced to one-dimensional series.

This module is private to hydrological-modeling/run-lumped-dhf-model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

EPS = 1e-12
PAI = float(np.pi)

PARAMETER_FIELDS = (
    "S0", "U0", "D0", "KC", "KW", "K2", "KA", "G", "A", "B",
    "B0", "K0", "N", "DD", "CC", "COE", "DDL", "CCL",
)

# Source: hydromodel/models/model_config.py MODEL_PARAM_DICT["dhf"]["param_range"].
PARAMETER_RANGES = {
    "S0": (0.0, 50.0),
    "U0": (0.0, 90.0),
    "D0": (70.0, 160.0),
    "KC": (0.1, 0.9),
    "KW": (0.0, 1.0),
    "K2": (0.2, 0.9),
    "KA": (0.7, 1.0),
    "G": (0.0, 1.0),
    "A": (0.0, 5.0),
    "B": (1.0, 3.0),
    "B0": (0.1, 2.0),
    "K0": (0.0, 0.8),
    "N": (2.0, 6.0),
    "DD": (0.5, 4.0),
    "CC": (0.5, 4.0),
    "COE": (0.0, 0.8),
    "DDL": (0.5, 4.0),
    "CCL": (0.5, 4.0),
}


@dataclass(frozen=True)
class DHFParameters:
    s0: float
    u0: float
    d0: float
    kc: float
    kw: float
    k2: float
    ka: float
    g: float
    a: float
    b: float
    b0: float
    k0: float
    n: float
    dd: float
    cc: float
    coe: float
    ddl: float
    ccl: float


def build_parameters(raw: dict[str, Any], param_scale: str) -> DHFParameters:
    """Accept the canonical names plus the source-script alias ``K`` for ``KC``."""
    values: dict[str, float] = {}
    for field in PARAMETER_FIELDS:
        if field in raw:
            value = raw[field]
        elif field == "KC" and "K" in raw:
            value = raw["K"]
        else:
            raise ValueError(f"参数集缺少字段: {field}")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"参数 {field} 必须是数值")
        values[field] = float(value)
    if param_scale == "normalized":
        for field in PARAMETER_FIELDS:
            low, high = PARAMETER_RANGES[field]
            values[field] = low + values[field] * (high - low)
    return DHFParameters(
        s0=values["S0"], u0=values["U0"], d0=values["D0"], kc=values["KC"],
        kw=values["KW"], k2=values["K2"], ka=values["KA"], g=values["G"],
        a=values["A"], b=values["B"], b0=values["B0"], k0=values["K0"],
        n=values["N"], dd=values["DD"], cc=values["CC"], coe=values["COE"],
        ddl=values["DDL"], ccl=values["CCL"],
    )


def validate_parameters(
    parameters: DHFParameters, area_km2: float, river_length_km: float, timestep_hours: float
) -> list[str]:
    problems: list[str] = []
    if area_km2 <= 0:
        problems.append("--area-km2 必须为正")
    if river_length_km <= 0:
        problems.append("--river-length-km 必须为正")
    if timestep_hours <= 0:
        problems.append("--timestep-hours 必须为正")
    for name, value in (("S0", parameters.s0), ("U0", parameters.u0), ("D0", parameters.d0)):
        if value <= 0:
            problems.append(f"{name} 必须为正")
    if not 0 < parameters.kc <= 1:
        problems.append("KC 必须落在 (0, 1]")
    if not 0 <= parameters.kw <= 1:
        problems.append("KW 必须落在 [0, 1]")
    if parameters.k2 <= 0:
        problems.append("K2 必须为正")
    if not 0 <= parameters.g <= 1:
        problems.append("G 必须落在 [0, 1]")
    if parameters.a <= 0:
        problems.append("A 必须为正")
    if parameters.b <= 0:
        problems.append("B 必须为正")
    if parameters.b0 <= 0:
        problems.append("B0 必须为正")
    if parameters.k0 < 0:
        problems.append("K0 不得为负")
    if parameters.n <= 0:
        problems.append("N 必须为正")
    for name, value in (("DD", parameters.dd), ("CC", parameters.cc), ("DDL", parameters.ddl), ("CCL", parameters.ccl)):
        if value <= 0:
            problems.append(f"{name} 必须为正")
    if not 0 < parameters.coe <= 0.999:
        problems.append("COE 必须落在 (0, 1)；COE 趋近 0 会使 tan(PAI*COE) 除零、汇流退化为零")
    return problems


def parameter_range_warnings(parameters: DHFParameters) -> list[str]:
    warnings: list[str] = []
    for field in PARAMETER_FIELDS:
        low, high = PARAMETER_RANGES[field]
        value = getattr(parameters, field.lower())
        if not low <= value <= high:
            warnings.append(
                f"参数 {field}={value:.6g} 超出源工程率定范围 [{low}, {high}]；"
                f"该范围来自 hydromodel 的率定先验，不是硬约束，请确认取值依据"
            )
    return warnings


def _clip_counting(value: float, low: float, high: float, counter: list[int]) -> float:
    clipped = min(max(value, low), high)
    if clipped != value:
        counter[0] += 1
    return clipped


def run_lumped_dhf(
    precipitation: np.ndarray,
    pet: np.ndarray,
    parameters: DHFParameters,
    river_length_km: float,
    area_km2: float,
    timestep_hours: float,
    initial_states: dict[str, float] | None = None,
) -> dict[str, np.ndarray]:
    """Run the lumped DHF loop on one basin and return per-step state and flux arrays.

    Initial states follow the source defaults: SA=0, UA=0, YA=0.5; the optional
    ``initial_states`` mapping (keys ``sa0``/``ua0``/``ya0``) overrides them, matching
    the source behaviour of applying overrides after the warmup pass.
    """
    steps = int(len(precipitation))
    initial = {"sa0": 0.0, "ua0": 0.0, "ya0": 0.5}
    if initial_states:
        for key in ("sa0", "ua0", "ya0"):
            if key in initial_states:
                initial[key] = float(initial_states[key])

    s0, u0, d0 = parameters.s0, parameters.u0, parameters.d0
    kc, kw, k2, ka, g = parameters.kc, parameters.kw, parameters.k2, parameters.ka, parameters.g
    a, b = parameters.a, parameters.b
    b0, k0, n = parameters.b0, parameters.k0, parameters.n
    dd, cc, coe = parameters.dd, parameters.cc, parameters.coe
    ddl, ccl = parameters.ddl, parameters.ccl

    p_arr = np.asarray(precipitation, dtype=float)
    pet_arr = np.asarray(pet, dtype=float)

    edt_arr = np.zeros(steps)
    pe_arr = np.zeros(steps)
    pc_arr = np.zeros(steps)
    y0_arr = np.zeros(steps)
    eu_arr = np.zeros(steps)
    el_arr = np.zeros(steps)
    rr_arr = np.zeros(steps)
    y_arr = np.zeros(steps)
    yu_arr = np.zeros(steps)
    yl_arr = np.zeros(steps)
    runoff_arr = np.zeros(steps)
    sa_arr = np.zeros(steps)
    ua_arr = np.zeros(steps)
    ya_arr = np.zeros(steps)
    eb_arr = np.zeros(steps)

    clips = [0]
    sa_state = initial["sa0"]
    ua_state = initial["ua0"]
    eb = 0.0

    # ---- runoff generation, one step at a time (mirrors the source main loop) ----
    for i in range(steps):
        prcp = float(p_arr[i])
        pet_i = float(pet_arr[i])
        if i > 0:
            sa_state = sa_arr[i - 1]
            ua_state = ua_arr[i - 1]
            eb = eb_arr[i - 1]
        sa_used = min(sa_state, s0)
        ua_used = min(ua_state, u0)

        edt = kc * pet_i
        pe = prcp - edt
        y0 = g * pe
        pc = pe - y0
        eu = 0.0
        el = 0.0
        edt_arr[i] = edt
        pe_arr[i] = pe
        pc_arr[i] = pc
        # the source zeroes Y0 in the evaporation branch before writing it out
        y0_arr[i] = y0 if pc > 0.0 else 0.0

        if pc > 0.0:
            # production branch: surface storage curve then subsurface split
            temp = (1 - sa_used / s0) ** (1 / a)
            sm = a * s0 * (1 - temp)
            if sm + pc < a * s0:
                rr = pc + sa_used - s0 + s0 * (1 - (sm + pc) / (a * s0)) ** a
            else:
                rr = pc - (s0 - sa_used)

            temp = (1 - ua_used / u0) ** (1 / b)
            un = b * u0 * (1 - temp)
            temp = (1 - ua_used / u0) ** (u0 / (b * d0))
            dn = b * d0 * (1 - temp)

            z1 = 1 - np.exp(-k2 * timestep_hours * u0 / d0)
            z2 = 1 - np.exp(-k2 * timestep_hours)

            if rr + z2 * un < z2 * b * u0:
                y = rr + z2 * (ua_used - u0) + z2 * u0 * (1 - (z2 * un + rr) / (z2 * b * u0)) ** b
            else:
                y = rr + z2 * (ua_used - u0)

            temp = (1 - ua_used / u0) ** (u0 / d0)
            if z1 * dn + rr < z1 * b * d0:
                yu = rr - z1 * d0 * temp + z1 * d0 * (1 - (z1 * dn + rr) / (z1 * b * d0)) ** b
            else:
                yu = rr - z1 * d0 * temp
            yl = (y - yu) * kw

            if sm + pc < a * s0:
                sa_new = s0 * (1 - (1 - (sm + pc) / (a * s0)) ** a)
            else:
                sa_new = sa_used + pc - rr
            sa_new = _clip_counting(sa_new, 0.0, s0, clips)
            ua_new = _clip_counting(ua_used + rr - y, 0.0, u0, clips)
            eb = 0.0

            rr_arr[i] = rr
            y_arr[i] = y
            yu_arr[i] = yu
            yl_arr[i] = yl
        else:
            # evaporation-deficit branch (kept identical to the Chu version)
            ec = edt - prcp
            eb = eb + ec
            base = a * s0
            # negative bases occur outside the three source criteria; numpy yields NaN
            # there and the criteria discard it, exactly like the source array version
            with np.errstate(invalid="ignore"):
                temp1 = np.power(1 - (eb - ec) / base, a)
                temp2 = np.power(1 - eb / base, a)
            if eb / base <= 0.999999 and (eb - ec) / base <= 0.999999:
                eu = s0 * (temp1 - temp2)
            elif eb / base >= 1.00001 and (eb - ec) / base <= 0.999999:
                eu = s0 * temp1
            else:
                eu = 0.00001
            if sa_used - eu < 0.0:
                el = (ec - sa_used) * ua_used / u0
                sa_new = 0.0
            else:
                el = (ec - eu) * ua_used / u0
                sa_new = sa_used - eu
            ua_new = max(ua_used - el, 0.0)
            # production columns stay zero in this branch
            sa_new = _clip_counting(sa_new, 0.0, s0, clips)
            ua_new = _clip_counting(ua_new, 0.0, u0, clips)

        eu_arr[i] = eu
        el_arr[i] = el
        sa_arr[i] = sa_new
        ua_arr[i] = ua_new
        eb_arr[i] = eb

    runoff_arr = np.maximum(y_arr + y0_arr, 0.0)
    negative_runoff = int(np.sum(y_arr + y0_arr < 0.0))
    clips[0] += negative_runoff

    # ---- routing: empirical gamma-type unit hydrograph (kept inside the model) ----
    qs_arr = np.zeros(steps)
    ql_arr = np.zeros(steps)
    w0 = area_km2 / (3.6 * timestep_hours)
    ya_state = initial["ya0"]
    lb = river_length_km / b0

    for i in range(steps):
        ya_arr[i] = max((ya_state + runoff_arr[i]) * ka, 0.0)
        ya_val = max(ya_state, 0.5)
        rl = max(yl_arr[i], 0.0)

        tm = lb * (ya_val + runoff_arr[i]) ** (-k0)
        tt = int(n * tm)
        ts = int(coe * tm)

        temp_aa = (PAI * coe) ** (dd - 1)
        aa = cc / (dd * temp_aa * np.tan(PAI * coe))

        k3 = 0.0
        for j in range(int(np.ceil(tm))):
            if j < tm:
                temp = (PAI * j / tm) ** dd
                temp1 = np.sin(PAI * j / tm) ** cc
                k3 += np.exp(-aa * temp) * temp1
        if k3 != 0.0:
            k3 = tm * w0 / k3

        temp_aal = (PAI * coe / n) ** (ddl - 1)
        aal = ccl / (ddl * temp_aal * np.tan(PAI * coe / n))

        k3l = 0.0
        for j in range(int(np.ceil(tt))):
            if j < tt:
                temp = (PAI * j / tt) ** ddl
                temp1 = np.sin(PAI * j / tt) ** ccl
                k3l += np.exp(-aal * temp) * temp1
        if k3l != 0.0:
            k3l = tt * w0 / k3l

        tl = max(tt + ts - 1, 0)
        for j in range(int(np.ceil(tl))):
            if i + j >= steps:
                break
            temp0 = PAI * j / tm
            with np.errstate(invalid="ignore"):
                # j > tm can push temp0 past PAI where sin goes negative; the NaN
                # contribution is zeroed right after, matching the source
                q_surface = (
                    (runoff_arr[i] - rl) * k3 / tm
                    * np.exp(-aa * temp0 ** dd)
                    * np.sin(temp0) ** cc
                )
            if np.isnan(q_surface):
                q_surface = 0.0
                clips[0] += 1
            if j >= ts:
                temp00 = PAI * (j - ts) / tt
                q_subsurface = (
                    rl * k3l / tt
                    * np.exp(-aal * temp00 ** ddl)
                    * np.sin(temp00) ** ccl
                )
            else:
                # j < ts never contributes under the source masks (mask2 needs j > ts,
                # mask3 needs j > tm >= ts), so the unused value is set to zero
                q_subsurface = 0.0
            if j <= tm:
                qs_arr[i + j] += q_surface
                if j > ts:
                    ql_arr[i + j] += q_subsurface
            else:
                ql_arr[i + j] += q_subsurface

        ya_state = ya_arr[i]

    q_arr = np.maximum(qs_arr + ql_arr, 0.0)
    negative_q = int(np.sum(qs_arr + ql_arr < 0.0))
    clips[0] += negative_q

    return {
        "P": p_arr,
        "PET": pet_arr,
        "E": edt_arr,
        "PE": pe_arr,
        "PC": pc_arr,
        "Y0": y0_arr,
        "EU": eu_arr,
        "EL": el_arr,
        "RR": rr_arr,
        "Y": y_arr,
        "YU": yu_arr,
        "YL": yl_arr,
        "RUNOFF": runoff_arr,
        "QS": qs_arr,
        "QL": ql_arr,
        "Q": q_arr,
        "SA": sa_arr,
        "UA": ua_arr,
        "YA": ya_arr,
        "EB": eb_arr,
        "clip_count": np.array([clips[0]], dtype=int),
        "max_tm": np.array([lb * (0.5 + float(np.max(runoff_arr))) ** (-k0)] if steps else [0.0]),
    }
