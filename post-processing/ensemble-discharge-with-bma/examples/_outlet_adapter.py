"""Hash-check and align observed discharge with genuine model outlet flow."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from _skill_common import QCError, load_result, resolve

DIRECT = {"run-lumped-hbv-model", "run-lumped-tank-model", "run-lumped-dhf-model", "run-lumped-gr4j-model", "run-lumped-sacsma-model"}


def read_table(path):
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, encoding="utf-8-sig")
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".xlsx":
        return pd.read_excel(path)
    raise QCError("discharge artifact must be CSV, Parquet or XLSX")


def stamped(frame, name):
    if "time" not in frame or frame.time.isna().any():
        raise QCError(f"{name} time missing")
    try:
        result = pd.to_datetime(frame.time, utc=True)
    except (ValueError, TypeError) as exc:
        raise QCError(f"{name} time invalid: {exc}") from exc
    if result.isna().any() or any(pd.Timestamp(value).tzinfo is None for value in frame.time):
        raise QCError(f"{name} timestamps must carry timezone")
    return result


def warmup_values(values):
    normalized = values.astype(str).str.lower()
    if not normalized.isin(["true", "false", "0", "1"]).all():
        raise QCError("invalid warmup flag")
    return normalized.isin(["true", "1"])


def adapter(result_path, declaration=None, event_id=None):
    result = load_result(result_path, {"hydrological-modeling/" + n for n in DIRECT} |
                         {"hydrological-modeling/run-lumped-xaj-model", "hydrological-modeling/run-lumped-topmodel",
                          "hydrological-modeling/run-semi-distributed-xaj-model", "hydrological-modeling/route-muskingum-channel", "hydrological-modeling/route-lohmann-channel"})
    name = result["skill"].split("/")[-1]
    upstream_xaj = None
    if name == "run-lumped-xaj-model":
        raise QCError("XAJ Qt is channel entry, not outlet flow; supply routed result")
    if name in DIRECT:
        key, column, warm = "simulation_table", "Q", "is_warmup"
    elif name == "run-lumped-topmodel":
        key, column, warm = "process.csv", "outlet_m3_s", "warmup"
    elif name == "run-semi-distributed-xaj-model":
        key, column, warm = "outlet_flow", "Q_m3s", "is_warmup"
    else:
        if not declaration or declaration.get("unit") != "m3/s" or not declaration.get("outlet_id") or declaration.get("location") != "basin_outlet":
            raise QCError("routed discharge needs explicit basin outlet and m3/s declaration")
        inflow = result.get("inputs", {}).get("inflow", {})
        if declaration.get("input_unit") != "m3/s" or declaration.get("inflow_sha256") != inflow.get("sha256"):
            raise QCError("routed m3/s declaration must match the hashed inflow and its unit")
        key = "routed_table"
        column = declaration.get("column", result.get("parameters", {}).get("output_column", "Q_total"))
        warm = "is_warmup"
        xaj_ref = declaration.get("upstream_xaj_results", {}).get(str(event_id)) if event_id is not None else declaration.get("upstream_xaj_result")
        if xaj_ref:
            xaj_path = (Path(declaration["_config_base"]) / xaj_ref).resolve()
            xaj = load_result(xaj_path, {"hydrological-modeling/run-lumped-xaj-model"})
            inflow = result.get("inputs", {}).get("inflow")
            if not inflow or inflow.get("sha256") != xaj["artifacts"]["simulation_table"]["sha256"]:
                raise QCError("routed inflow hash does not match XAJ Qt source")
            upstream_xaj = read_table(resolve(xaj["artifacts"]["simulation_table"], xaj_path.parent))
    frame = read_table(resolve(result["artifacts"][key], result_path.parent))
    if column not in frame:
        raise QCError(f"outlet column {column} missing")
    if name == "run-semi-distributed-xaj-model" and frame.outlet_reach_id.nunique() != 1:
        raise QCError("multiple outlet reaches")
    out = pd.DataFrame({"time": stamped(frame, "simulation"), "simulated_m3_s": pd.to_numeric(frame[column], errors="coerce")})
    if warm in frame:
        out["is_warmup"] = warmup_values(frame[warm])
    elif upstream_xaj is not None:
        if "is_warmup" not in upstream_xaj:
            raise QCError("XAJ upstream result lacks warmup flag")
        upstream_axis = stamped(upstream_xaj, "XAJ upstream")
        if upstream_axis.duplicated().any():
            raise QCError("XAJ upstream time duplicated")
        upstream_flags = pd.Series(warmup_values(upstream_xaj.is_warmup).to_numpy(), index=upstream_axis)
        if not out.time.isin(upstream_flags.index).all():
            raise QCError("routed time not covered by XAJ upstream")
        out["is_warmup"] = out.time.map(upstream_flags).astype(bool)
    elif name.startswith("route-") and declaration.get("warmup_steps") == 0:
        out["is_warmup"] = False
    else:
        raise QCError("warmup evidence missing from simulation or routed source")
    if "event_id" in frame:
        if frame.event_id.isna().any() or frame.event_id.astype(str).str.strip().eq("").any():
            raise QCError("event_id missing")
        out["event_id"] = frame.event_id.astype(str)
    if out.simulated_m3_s.isna().any() or not np.isfinite(out.simulated_m3_s).all() or (out.simulated_m3_s < 0).any():
        raise QCError("invalid outlet discharge")
    return out, name
