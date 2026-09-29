"""Run one continuous lumped TOPMODEL series from verified terrain distributions."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator
import numpy as np
import pandas as pd
import yaml

from _topmodel_core import initial_state, routing_kernel, step

SKILL = "hydrological-modeling/run-lumped-topmodel"
OUTPUTS = ("process.csv", "final_state.json", "model_config.json", "result.json")


class QCError(ValueError):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def ref(path: Path, base: Path | None = None) -> dict:
    path = path.resolve()
    try:
        name = path.relative_to(base.resolve()).as_posix() if base else str(path)
    except ValueError:
        name = str(path)
    return {"path": name, "sha256": digest(path)}


def resolve(value: dict, base: Path) -> Path:
    if not isinstance(value, dict) or not {"path", "sha256"} <= value.keys():
        raise QCError("artifact reference requires path and sha256")
    path = Path(value["path"])
    path = path if path.is_absolute() else base / path
    if not path.is_file() or digest(path) != value["sha256"]:
        raise QCError(f"missing or hash-mismatched artifact: {path}")
    return path.resolve()


def prepare(path: Path, overwrite: bool):
    if path.exists() and not path.is_dir():
        raise ValueError("output path is not a directory")
    if path.exists() and any(path.iterdir()) and not overwrite:
        raise FileExistsError("output directory is nonempty; use --overwrite")
    path.mkdir(parents=True, exist_ok=True)
    if overwrite:
        for name in OUTPUTS:
            p = path / name
            if p.is_file():
                p.unlink()


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as f:
        value = yaml.safe_load(f) if path.suffix.lower() in (".yaml", ".yml") else json.load(f)
    schema = json.loads((Path(__file__).parent / "model_config.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise QCError("invalid model configuration: " + errors[0].message)
    p = value["parameters"]
    if not math.isfinite(float(value["timestep_hours"])) or not math.isfinite(float(value["water_balance_tolerance_m"])):
        raise QCError("timestep and water balance tolerance must be finite")
    if any(not math.isfinite(float(v)) for k, v in p.items() if k != "infiltration_excess"):
        raise QCError("all model parameters must be finite")
    if p["Sr0"] > p["Srmax"]:
        raise QCError("Sr0 must not exceed Srmax")
    try:
        ZoneInfo(value["timezone"])
    except ZoneInfoNotFoundError as e:
        raise QCError("invalid timezone") from e
    if p["td"] <= 0 and "K0" not in p:
        raise QCError("K0 required when td<=0")
    if p["infiltration_excess"] and not {"K0", "psi", "dtheta"} <= p.keys():
        raise QCError("K0, psi and dtheta required when infiltration excess is enabled")
    if not p["infiltration_excess"] and p["td"] > 0 and {"K0", "psi", "dtheta"} & p.keys():
        raise QCError("unused Green-Ampt parameters are not accepted when branch disabled")
    return value


def forcing_table(path: Path, config: dict) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if list(frame.columns) != ["time", "P", "PET"] or frame.empty:
        raise QCError("forcing must have exactly time,P,PET and at least one row")
    if frame.isna().any().any():
        raise QCError("forcing has missing values")
    try:
        timestamps = [pd.Timestamp(v) for v in frame.time]
        p = pd.to_numeric(frame.P, errors="raise").to_numpy(dtype=float)
        pet = pd.to_numeric(frame.PET, errors="raise").to_numpy(dtype=float)
    except (ValueError, TypeError) as e:
        raise QCError(f"invalid forcing: {e}") from e
    if any(t.tzinfo is None for t in timestamps):
        raise QCError("time values must carry timezone offsets")
    zone = ZoneInfo(config["timezone"])
    if any(t.utcoffset() != t.tz_convert(zone).utcoffset() for t in timestamps):
        raise QCError("timestamp offset disagrees with configured timezone")
    utc = pd.DatetimeIndex([t.tz_convert("UTC") for t in timestamps])
    if utc.has_duplicates or not utc.is_monotonic_increasing:
        raise QCError("time axis has duplicates or is unsorted")
    dt = pd.Timedelta(hours=config["timestep_hours"])
    if any((utc[i] - utc[i - 1]) != dt for i in range(1, len(utc))):
        raise QCError("time axis has gaps or incorrect step")
    if not np.isfinite(p).all() or not np.isfinite(pet).all() or (p < 0).any() or (pet < 0).any():
        raise QCError("P and PET must be finite nonnegative mm/step")
    start = pd.Timestamp(config["simulation_start"])
    end = pd.Timestamp(config["simulation_end"])
    if start.tzinfo is None or end.tzinfo is None or start.tz_convert("UTC") not in utc or end.tz_convert("UTC") not in utc:
        raise QCError("simulation bounds must be timezone-aware forcing timestamps")
    first = int(utc.get_loc(start.tz_convert("UTC")))
    last = int(utc.get_loc(end.tz_convert("UTC")))
    if first != config["warmup_steps"] or last != len(utc) - 1 or last < first:
        raise QCError("warmup and simulation bounds must cover one full continuous run")
    frame["time"] = [t.isoformat() for t in timestamps]
    frame["P"] = p
    frame["PET"] = pet
    return frame


def terrain_data(path: Path):
    doc = json.loads(path.read_text(encoding="utf-8-sig"))
    if doc.get("skill") != "spatial-analysis/derive-topmodel-terrain-inputs" or doc.get("status") not in ("success", "warning"):
        raise QCError("invalid terrain result")
    if not doc.get("checks") or any(c.get("status") == "FAIL" for c in doc.get("checks", [])):
        raise QCError("terrain QC failed")
    if not {"stream_result", "topology_result", "topology_qc_result", "filled_dem", "d8_pointer",
            "flow_accumulation", "subbasins_clipped"} <= doc.get("inputs", {}).keys():
        raise QCError("terrain result lacks required HydroBase provenance")
    for value in doc.get("inputs", {}).values():
        resolve(value, path.parent)
    classes = pd.read_csv(resolve(doc["artifacts"]["topographic_index_classes.csv"], path.parent))
    curve = pd.read_csv(resolve(doc["artifacts"]["distance_area.csv"], path.parent))
    resolve(doc["artifacts"]["topographic_index.tif"], path.parent)
    if not {"ti_mean", "area_fraction"} <= set(classes) or not {"distance_m", "cumulative_fraction"} <= set(curve):
        raise QCError("terrain tables lack required columns")
    ti = classes.ti_mean.to_numpy(dtype=float)
    weight = classes.area_fraction.to_numpy(dtype=float)
    distance = curve.distance_m.to_numpy(dtype=float)
    cumulative = curve.cumulative_fraction.to_numpy(dtype=float)
    area = float(doc["parameters"]["basin_area_m2"])
    if not all(np.isfinite(v).all() for v in (ti, weight, distance, cumulative)) or (weight < 0).any() or abs(weight.sum() - 1) > 1e-8 or area <= 0:
        raise QCError("invalid terrain weights or area")
    return ti, weight, distance, cumulative, area


def run(args):
    config = load_config(args.params)
    frame = forcing_table(args.forcing, config)
    ti, weights, distances, cumulative, area = terrain_data(args.terrain_result)
    dt = float(config["timestep_hours"])
    p = config["parameters"]
    kernel = routing_kernel(distances, cumulative, dt, p["vch"], p["vr"])
    state = initial_state(ti, weights, p, dt, kernel)
    initial_storage = -state["mean_deficit_m"] + float(np.dot(weights, p["Srmax"] - state["root_deficit_m"])) + float(state["routing_m"].sum())
    total_p = total_et = total_out = 0.0
    rows = []
    for i, row in enumerate(frame.itertuples(index=False)):
        result = step(state, row.P / 1000, row.PET / 1000, p, ti, weights, dt, kernel)
        if not all(math.isfinite(v) for v in result.values()):
            raise QCError(f"nonfinite model state at {row.time}")
        total_p += row.P / 1000
        total_et += result["actual_et_m"]
        total_out += result["outlet_m"]
        storage = -state["mean_deficit_m"] + float(np.dot(weights, p["Srmax"] - state["root_deficit_m"] + state["unsat_m"])) + result["routing_storage_m"]
        residual = total_p - total_et - total_out - (storage - initial_storage)
        if abs(residual) > config["water_balance_tolerance_m"]:
            raise QCError(f"water balance residual {residual:.8g} m at {row.time}")
        rows.append({"time": row.time, "warmup": i < config["warmup_steps"], "P_mm": row.P, "PET_mm": row.PET,
                     **{k.replace("_m", "_mm"): v * 1000 for k, v in result.items()},
                     "outlet_m3_s": result["outlet_m"] * area / (dt * 3600),
                     "water_balance_residual_mm": residual * 1000})
    output = args.output_dir
    prepare(output, args.overwrite)
    pd.DataFrame(rows).to_csv(output / OUTPUTS[0], index=False, float_format="%.12g")
    final = {"mean_deficit_m": state["mean_deficit_m"], "root_deficit_m": state["root_deficit_m"].tolist(),
             "unsat_m": state["unsat_m"].tolist(), "ga_cumulative_m": state["ga_cumulative_m"],
             "routing_m": state["routing_m"].tolist(), "routing_storage_m": float(state["routing_m"].sum())}
    (output / OUTPUTS[1]).write_text(json.dumps(final, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output / OUTPUTS[2]).write_text(json.dumps(config, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    result = {"schema_version": "1.0", "skill": SKILL, "status": "success",
              "message": "Lumped TOPMODEL continuous simulation completed", "basin_area_m2": area,
              "inputs": {"terrain_result": ref(args.terrain_result), "forcing": ref(args.forcing), "params": ref(args.params)},
              "artifacts": {k: ref(output / k, output) for k in OUTPUTS[:-1]},
              "parameters": config, "checks": [{"check": "input_hashes", "status": "PASS"},
                                                  {"check": "continuous_time_axis", "status": "PASS"},
                                                  {"check": "water_balance", "status": "PASS", "max_abs_residual_m": max(abs(r["water_balance_residual_mm"]) / 1000 for r in rows)}],
              "warnings": [], "provenance": {"python": sys.version.split()[0], "numpy": np.__version__, "pandas": pd.__version__,
                                             "PyYAML": importlib.metadata.version("PyYAML"),
                                             "jsonschema": importlib.metadata.version("jsonschema"),
                                             "reference": "GRASS GIS r.topmodel 8.5.0 manual; independent implementation"}}
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("terrain-result", "forcing", "params", "output-dir"):
        parser.add_argument("--" + arg, type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        run(args)
    except (QCError, ValueError, FileNotFoundError, KeyError, FileExistsError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        if not isinstance(e, FileExistsError):
            try:
                prepare(args.output_dir, args.overwrite)
                evidence = {k: ref(v) for k, v in (("terrain_result", args.terrain_result), ("forcing", args.forcing),
                                                    ("params", args.params)) if v.is_file()}
                failure = {"schema_version": "1.0", "skill": SKILL, "status": "error", "message": str(e),
                           "inputs": evidence, "artifacts": {}, "checks": [{"check": "run", "status": "FAIL", "details": str(e)}],
                           "warnings": []}
                (args.output_dir / "result.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except (OSError, ValueError, FileExistsError):
                pass
        return 2 if isinstance(e, QCError) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
