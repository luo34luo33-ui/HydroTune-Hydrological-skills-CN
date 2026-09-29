"""Compute measured-input FAO-56 reference evapotranspiration."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from _skill_common import QCError, error_result, prepare, reference, version, write_result

SKILL = "data-processing/derive-potential-evapotranspiration"
OUTPUTS = ("eto.csv", "mapped_evap.csv", "result.json")


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, encoding="utf-8-sig")
    if path.suffix.lower() == ".xlsx":
        return pd.read_excel(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    raise QCError("meteorology must be CSV, XLSX or Parquet")


def compute(source: pd.DataFrame, stations: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    scale = config["scale"]
    if scale not in ("daily", "hourly") or config["timestep_seconds"] != (86400 if scale == "daily" else 3600):
        raise QCError("scale and timestep_seconds disagree")
    cols = config["columns"]
    required = ["time", "source_id", "t_c", "rn_mj_m2_step", "ea_kpa", "u2_m_s"]
    required += ["tmax_c", "tmin_c"] if scale == "daily" else ["g_mj_m2_step"]
    if not set(required) <= cols.keys() or not set(cols[k] for k in required) <= set(source.columns):
        raise QCError("required measured meteorology columns missing")
    if not {"source_id", "latitude_deg", "elevation_m"} <= set(stations.columns):
        raise QCError("station location columns missing")
    if stations.source_id.duplicated().any() or stations[["latitude_deg", "elevation_m"]].isna().any().any():
        raise QCError("station metadata incomplete or duplicated")
    work = source.rename(columns={cols[k]: k for k in required})[required].copy()
    if work.isna().any().any() or work.source_id.isna().any():
        raise QCError("meteorology has missing values")
    zone = ZoneInfo(config["timezone"])
    try:
        stamps = pd.to_datetime(work.time)
        if stamps.dt.tz is None:
            if config["source_timezone_mode"] != "local_naive":
                raise QCError("naive time needs local_naive declaration")
            stamps = stamps.dt.tz_localize(zone, ambiguous="raise", nonexistent="raise")
        else:
            if config["source_timezone_mode"] != "aware":
                raise QCError("aware source time requires aware declaration")
            stamps = stamps.dt.tz_convert(zone)
        if config["timestamp_semantics"] == "interval_start":
            stamps += pd.Timedelta(seconds=config["timestep_seconds"])
        work.time = stamps
    except (ValueError, TypeError) as exc:
        raise QCError(f"invalid time: {exc}") from exc
    work = work.merge(stations[["source_id", "latitude_deg", "elevation_m"]], on="source_id", validate="many_to_one")
    if len(work) != len(source):
        raise QCError("meteorology station without location")
    for field in required[2:] + ["latitude_deg", "elevation_m"]:
        work[field] = pd.to_numeric(work[field], errors="raise")
    if not np.isfinite(work[required[2:] + ["latitude_deg", "elevation_m"]].to_numpy(dtype=float)).all():
        raise QCError("nonfinite meteorology")
    if (work.latitude_deg.abs() > 90).any() or (work.elevation_m >= 44330).any() or (work.t_c <= -273).any():
        raise QCError("invalid station location or temperature")
    if (work.u2_m_s < 0).any() or (work.ea_kpa < 0).any():
        raise QCError("negative wind or vapor pressure")
    work.sort_values(["source_id", "time"], inplace=True)
    if work.duplicated(["source_id", "time"]).any():
        raise QCError("duplicate station time")
    axes = []
    for _, group in work.groupby("source_id"):
        axis = pd.DatetimeIndex(group.time)
        if len(axis) < 2 or not (np.diff(axis.asi8) == config["timestep_seconds"] * 10**9).all():
            raise QCError("missing or irregular meteorology step")
        axes.append(tuple(axis.tz_convert("UTC")))
    if any(axis != axes[0] for axis in axes[1:]):
        raise QCError("stations have different time axes")
    # FAO-56 chapter 4, equations 6, 11-13, 37 and 53; measured Rn and ea.
    es_t = lambda t: 0.6108 * np.exp(17.27 * t / (t + 237.3))
    t = work.t_c.to_numpy(float)
    es = ((es_t(work.tmax_c) + es_t(work.tmin_c)) / 2 if scale == "daily" else es_t(t))
    if scale == "daily" and ((work.tmax_c < work.tmin_c).any() or (work.t_c < work.tmin_c).any() or (work.t_c > work.tmax_c).any()):
        raise QCError("daily temperature range invalid")
    pressure = 101.3 * ((293 - 0.0065 * work.elevation_m.to_numpy(float)) / 293) ** 5.26
    gamma = 0.000665 * pressure
    delta = 4098 * es_t(t) / (t + 237.3) ** 2
    g = 0 if scale == "daily" else work.g_mj_m2_step.to_numpy(float)
    cn = 900 if scale == "daily" else 37
    cd = 0.34
    eto = (0.408 * delta * (work.rn_mj_m2_step.to_numpy(float) - g)
           + gamma * cn / (t + 273) * work.u2_m_s.to_numpy(float) * (es - work.ea_kpa.to_numpy(float))) / (delta + gamma * (1 + cd * work.u2_m_s.to_numpy(float)))
    if not np.isfinite(eto).all():
        raise QCError("nonfinite ETo")
    out = pd.DataFrame({"time": work.time.map(lambda x: x.isoformat()), "source_id": work.source_id, "ETo_mm": eto})
    mapping = config.get("mapping")
    mapped = None
    if mapping is not None:
        if mapping.get("target") not in ("PET", "E0") or not isinstance(mapping.get("factor"), (int, float)) or not np.isfinite(mapping["factor"]) or mapping["factor"] <= 0 or mapping.get("negative_policy") not in ("error", "floor_zero"):
            raise QCError("mapping needs target PET/E0, positive factor and negative_policy")
        values = eto * mapping["factor"]
        if mapping["negative_policy"] == "error" and (values < 0).any():
            raise QCError("negative mapped evapotranspiration")
        mapped = pd.DataFrame({"time": out.time, "source_id": out.source_id, "variable": mapping["target"], "depth_mm": np.maximum(0, values) if mapping["negative_policy"] == "floor_zero" else values})
    return out, mapped


def run(args):
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    for key in ("scale", "timestep_seconds", "timezone", "source_timezone_mode", "timestamp_semantics", "columns", "units"):
        if key not in config:
            raise QCError(f"missing config {key}")
    required_units = {"t_c": "degC", "rn_mj_m2_step": "MJ/m2/step", "ea_kpa": "kPa", "u2_m_s": "m/s"}
    if config["scale"] == "daily":
        required_units.update(tmax_c="degC", tmin_c="degC")
    elif config["scale"] == "hourly":
        required_units["g_mj_m2_step"] = "MJ/m2/step"
    if config["units"] != required_units:
        raise QCError("all meteorology units must be explicitly declared in canonical units")
    if config["source_timezone_mode"] not in ("aware", "local_naive") or config["timestamp_semantics"] not in ("interval_start", "interval_end"):
        raise QCError("invalid time declaration")
    ZoneInfo(config["timezone"])
    eto, mapped = compute(read_table(args.source), read_table(args.stations), config)
    prepare(args.output_dir, args.overwrite, OUTPUTS)
    eto.to_csv(args.output_dir / "eto.csv", index=False)
    artifacts = {"eto": reference(args.output_dir / "eto.csv", args.output_dir)}
    if mapped is not None:
        mapped.to_csv(args.output_dir / "mapped_evap.csv", index=False)
        artifacts["mapped_evap"] = reference(args.output_dir / "mapped_evap.csv", args.output_dir)
    doc = {"schema_version": "1.0", "skill": SKILL, "status": "success", "message": "FAO-56 ETo computed", "parameters": config,
           "inputs": {"source": reference(args.source), "stations": reference(args.stations), "config": reference(args.config)},
           "artifacts": artifacts, "checks": [{"check": "meteorology_and_axis", "status": "PASS", "details": "complete regular time axis and declared units"},
                                           {"check": "eto_finite", "status": "PASS", "details": f"rows={len(eto)}"}], "warnings": [],
           "provenance": {"method": "FAO-56 Penman-Monteith", "python": sys.version.split()[0], "pandas": version("pandas")}}
    write_result(args.output_dir / "result.json", doc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "stations", "config", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        run(args)
    except (QCError, ValueError, KeyError, OSError, FileExistsError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        if not isinstance(exc, FileExistsError):
            try:
                prepare(args.output_dir, args.overwrite, OUTPUTS)
                write_result(args.output_dir / "result.json", error_result(SKILL, str(exc)))
            except (OSError, ValueError, FileExistsError):
                pass
        return 2 if isinstance(exc, QCError) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
