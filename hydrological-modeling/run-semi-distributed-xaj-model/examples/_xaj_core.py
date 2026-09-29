"""Source-faithful local HHU XAJ equations and component-wise Muskingum routing.

The source's independent-zone outlet sum is intentionally not implemented here.
"""

from __future__ import annotations

import math


PARAMETERS = ("KC", "B", "C", "IMP", "WM", "WUM", "WLM", "SM", "EX",
              "KG", "KI", "CG", "CI", "CS", "KE", "XE")


def validate_parameters(p: dict, dt_seconds: float) -> tuple[dict[str, float], tuple[float, float, float]]:
    if not math.isfinite(dt_seconds) or dt_seconds <= 0:
        raise ValueError("time.step_seconds must be positive")
    if set(p) != set(PARAMETERS):
        raise ValueError(f"xaj must contain exactly {', '.join(PARAMETERS)}")
    p = {key: float(value) for key, value in p.items()}
    if not all(math.isfinite(v) for v in p.values()):
        raise ValueError("xaj parameters must be finite")
    for key in ("KC", "WM", "WUM", "WLM", "SM", "KE"):
        if p[key] <= 0:
            raise ValueError(f"{key} must be positive")
    if p["WM"] < p["WUM"] + p["WLM"]:
        raise ValueError("WM must be at least WUM + WLM")
    for key in ("B", "EX"):
        if p[key] < 0:
            raise ValueError(f"{key} must be nonnegative")
    for key in ("C", "IMP", "KG", "KI", "CG", "CI", "CS"):
        if not 0 <= p[key] < 1:
            raise ValueError(f"{key} must be in [0, 1)")
    if not 0 <= p["XE"] <= 0.5:
        raise ValueError("XE must be in [0, 0.5]")
    if p["KG"] <= 0 or p["KG"] + p["KI"] >= 1:
        raise ValueError("KG must be positive and KG + KI must be below 1")
    if dt_seconds != 86400.0:
        total = p["KI"] + p["KG"]
        ratio = p["KI"] / p["KG"]
        p["KG"] = (1.0 - (1.0 - total) ** (dt_seconds / 86400.0)) / (1.0 + ratio)
        p["KI"] = p["KG"] * ratio
        p["CI"] = p["CI"] ** (dt_seconds / 86400.0)
        p["CG"] = p["CG"] ** (dt_seconds / 86400.0)
    dt_hours = dt_seconds / 3600.0
    denominator = p["KE"] * (1.0 - p["XE"]) + 0.5 * dt_hours
    c0 = (0.5 * dt_hours - p["KE"] * p["XE"]) / denominator
    c1 = (0.5 * dt_hours + p["KE"] * p["XE"]) / denominator
    c2 = (p["KE"] * (1.0 - p["XE"]) - 0.5 * dt_hours) / denominator
    if min(c0, c1, c2) < -1e-12 or abs(c0 + c1 + c2 - 1.0) > 1e-10:
        raise ValueError("unstable Muskingum coefficients for KE, XE and timestep")
    return p, (c0, c1, c2)


def initial_state(p: dict, init: dict) -> dict[str, float]:
    required = {"soil_fraction", "s_mm", "fr", "qi_m3s", "qg_m3s",
                "qs_cs_m3s", "qi_cs_m3s", "qg_cs_m3s"}
    if set(init) != required:
        raise ValueError(f"initial must contain exactly {sorted(required)}")
    values = {key: float(value) for key, value in init.items()}
    if not all(math.isfinite(v) for v in values.values()):
        raise ValueError("initial states must be finite")
    if not 0 <= values["soil_fraction"] <= 1 or not 0 <= values["fr"] <= 1:
        raise ValueError("soil_fraction and fr must be in [0, 1]")
    if any(value < 0 for key, value in values.items() if key not in ("soil_fraction", "fr")):
        raise ValueError("initial flows and storage must be nonnegative")
    if values["s_mm"] > p["SM"]:
        raise ValueError("initial s_mm must not exceed SM")
    fraction = values["soil_fraction"]
    return {
        "WU": fraction * p["WUM"], "WL": fraction * p["WLM"],
        "WD": fraction * (p["WM"] - p["WUM"] - p["WLM"]),
        "S": values["s_mm"], "FR": values["fr"],
        "QI": values["qi_m3s"], "QG": values["qg_m3s"],
        "QS_CS": values["qs_cs_m3s"], "QI_CS": values["qi_cs_m3s"],
        "QG_CS": values["qg_cs_m3s"],
    }


def local_step(prec: float, evapo: float, area_km2: float, dt_seconds: float,
               p: dict[str, float], state: dict[str, float]) -> tuple[dict, dict]:
    """One zone and one interval, matching _simulate's Yield/Divide3Source/routing order."""
    if not all(math.isfinite(v) and v >= 0 for v in (prec, evapo, area_km2)) or area_km2 == 0:
        raise ValueError("forcing and area must be finite and nonnegative; area must be positive")
    wu, wl, wd = state["WU"], state["WL"], state["WD"]
    w_ini = wu + wl + wd
    wmm = (1.0 + p["B"]) * p["WM"] / (1.0 - p["IMP"])
    ek = evapo * p["KC"]
    pe = prec - ek
    if abs(pe) < 0.001:
        pe = 0.0
    nd = min(400, int(pe / 5.0) + 1) if pe > 5.0 else 1
    ped = pe / nd
    rd = [0.0] * nd
    runoff = 0.0
    if pe > 0:
        a = wmm if abs(w_ini - p["WM"]) < 0.001 else wmm * (
            1.0 - (1.0 - w_ini / p["WM"]) ** (1.0 / (1.0 + p["B"])))
        peds = 0.0
        for i in range(nd):
            a += ped
            peds += ped
            previous = runoff
            if a < wmm:
                runoff = peds - (p["WM"] - w_ini) + p["WM"] * (1.0 - a / wmm) ** (1.0 + p["B"])
            else:
                runoff = peds - (p["WM"] - w_ini)
            rd[i] = runoff - previous
        if wu + pe - runoff > p["WUM"]:
            if wu + pe - runoff - p["WUM"] + wl > p["WLM"]:
                wu, wl = p["WUM"], p["WLM"]
                wd = w_ini + peds - runoff - wu - wl
            else:
                wl += wu + pe - runoff - p["WUM"]
                wu = p["WUM"]
        else:
            wu += pe - runoff
    else:
        if wu + pe >= 0:
            wu += pe
        else:
            eu = wu + ek + pe
            wu = 0.0
            if wl > p["C"] * p["WLM"]:
                wl -= (ek - eu) * wl / p["WLM"]
            elif wl > p["C"] * (ek - eu):
                wl -= p["C"] * (ek - eu)
            else:
                el = wl
                wl = 0.0
                wd = max(0.0, wd - (p["C"] * (ek - eu) - el))
    fr, s = state["FR"], state["S"]
    if pe <= 0:
        rs = 0.0
        rg = s * p["KG"] * fr
        ri = s * p["KI"] * fr
        s *= 1.0 - p["KG"] - p["KI"]
    else:
        kid = (1.0 - (1.0 - (p["KG"] + p["KI"])) ** (1.0 / nd)) / (p["KG"] + p["KI"])
        kgd, kid = kid * p["KG"], kid * p["KI"]
        rs = ri = rg = 0.0
        smm = (1.0 + p["EX"]) * p["SM"]
        for depth in rd:
            td = depth - p["IMP"] * ped
            old_fr = fr
            fr = max(1e-5, td / ped)
            s = old_fr * s / fr
            adjustment = 0.0
            if s > p["SM"]:
                adjustment = (s - p["SM"]) * fr
                s = p["SM"]
            au = smm * (1.0 - (1.0 - s / p["SM"]) ** (1.0 / (1.0 + p["EX"])))
            if au + ped < smm:
                rsd = (ped - p["SM"] + s + p["SM"] *
                       (1.0 - (ped + au) / smm) ** (1.0 + p["EX"])) * fr
            else:
                rsd = (ped + s - p["SM"]) * fr
            rsd = max(0.0, rsd)
            rs += rsd + adjustment
            s = s + ped - rsd / fr
            rid = s * kid * fr
            ri += rid
            rgd = s * kgd * fr
            rg += rgd
            s = s + ped - (rid + rgd) / fr
        rs += p["IMP"] * pe
    cp = 1000.0 * area_km2 / dt_seconds
    qs = rs * cp
    qi = state["QI"] * p["CI"] + ri * (1.0 - p["CI"]) * cp
    qg = state["QG"] * p["CG"] + rg * (1.0 - p["CG"]) * cp
    qs_cs = state["QS_CS"] * p["CS"] + qs * (1.0 - p["CS"])
    qi_cs = state["QI_CS"] * p["CS"] + qi * (1.0 - p["CS"])
    qg_cs = state["QG_CS"] * p["CS"] + qg * (1.0 - p["CS"])
    new_state = {"WU": wu, "WL": wl, "WD": wd, "S": s, "FR": fr,
                 "QI": qi, "QG": qg, "QS_CS": qs_cs, "QI_CS": qi_cs, "QG_CS": qg_cs}
    row = {**new_state, "P_mm": prec, "E0_mm": evapo, "PE_mm": pe,
           "R_mm": sum(rd), "RS_mm": rs, "RI_mm": ri, "RG_mm": rg, "QS_m3s": qs,
           "local_QS_m3s": qs_cs, "local_QI_m3s": qi_cs,
           "local_QG_m3s": qg_cs, "soil_delta_mm": wu + wl + wd - w_ini,
           "rain_substeps": nd}
    if not all(math.isfinite(float(v)) for v in row.values()):
        raise ValueError("non-finite XAJ state or flux")
    if min(wu, wl, wd, s, fr, qs_cs, qi_cs, qg_cs) < -1e-8:
        raise ValueError("negative XAJ state or flux")
    if wu > p["WUM"] + 1e-8 or wl > p["WLM"] + 1e-8 or wd > p["WM"] - p["WUM"] - p["WLM"] + 1e-8:
        raise ValueError("soil moisture exceeds layer capacity")
    return new_state, row


def muskingum_step(inflow: float, stages: list[tuple[float, float]],
                   coefficients: tuple[float, float, float]) -> tuple[float, list[tuple[float, float]]]:
    """A source-compatible DP-stage recurrence. Each tuple is previous (inflow, outflow)."""
    c0, c1, c2 = coefficients
    current = inflow
    updated = []
    for previous_in, previous_out in stages:
        outgoing = c0 * current + c1 * previous_in + c2 * previous_out
        updated.append((current, outgoing))
        current = outgoing
    return current, updated
