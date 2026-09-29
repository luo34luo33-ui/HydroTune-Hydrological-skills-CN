"""Hash-check and align observed discharge with genuine model outlet flow."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from _skill_common import QCError, error_result, load_result, prepare, reference, resolve, version, write_result

SKILL = "post-processing/align-observed-simulated-discharge"
OUTPUTS = ("aligned_discharge.csv", "evaluation_discharge.csv", "excluded_observations.csv", "alignment_qc.json", "result.json")
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
        out["event_id"] = frame.event_id.astype(str)
    if out.simulated_m3_s.isna().any() or not np.isfinite(out.simulated_m3_s).all() or (out.simulated_m3_s < 0).any():
        raise QCError("invalid outlet discharge")
    return out, name


def run(args):
    obs_result = load_result(args.observed_result, {"data-processing/prepare-discharge-timeseries"})
    obs = read_table(resolve(obs_result["artifacts"]["discharge_timeseries"], args.observed_result.parent))
    if not {"time", "discharge_m3_s"} <= set(obs):
        raise QCError("observed table lacks discharge")
    obs = obs.copy()
    obs["time"] = stamped(obs, "observation")
    obs.rename(columns={"discharge_m3_s": "observed_m3_s"}, inplace=True)
    if obs.time.duplicated().any() or obs.observed_m3_s.isna().any() or not np.isfinite(obs.observed_m3_s).all() or (obs.observed_m3_s < 0).any():
        raise QCError("observations duplicate, missing or invalid")
    declaration = json.loads(args.outlet_declaration.read_text(encoding="utf-8-sig")) if args.outlet_declaration else None
    if declaration is not None:
        declaration["_config_base"] = str(args.outlet_declaration.resolve().parent)
    if args.event_manifest and args.simulation_result:
        raise QCError("provide either simulation result or event manifest")
    if args.event_manifest:
        manifest = json.loads(args.event_manifest.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict) or not manifest:
            raise QCError("event manifest must map event IDs to result paths")
        chunks = []
        model_names = set()
        for event_id, path in manifest.items():
            frame, name = adapter((args.event_manifest.resolve().parent / path).resolve(), declaration, event_id)
            if "event_id" in frame and set(frame.event_id) != {str(event_id)}:
                raise QCError("event manifest ID disagrees with simulation")
            frame["event_id"] = str(event_id)
            chunks.append(frame)
            model_names.add(name)
        sim = pd.concat(chunks, ignore_index=True)
        if len(model_names) != 1:
            raise QCError("event manifest mixes models")
        model = next(iter(model_names))
    else:
        if not args.simulation_result:
            raise QCError("simulation result or event manifest required")
        sim, model = adapter(args.simulation_result, declaration)
    keys = ["event_id", "time"] if "event_id" in sim else ["time"]
    if sim.duplicated(keys).any():
        raise QCError("duplicate simulation event/time")
    merged = sim.merge(obs[["time", "observed_m3_s"]], on="time", how="left", validate="many_to_one", indicator=True)
    merged["matched"] = merged._merge.eq("both")
    merged.drop(columns="_merge", inplace=True)
    if not merged.matched.any():
        raise QCError("no overlapping observed and simulated timestamps")
    merged["excluded_reason"] = np.where(merged.is_warmup, "warmup", np.where(merged.matched, "", "observation_missing"))
    excluded_obs = obs.loc[~obs.time.isin(sim.time), ["time", "observed_m3_s"]].copy()
    excluded_obs["excluded_reason"] = "simulation_missing"
    eval_frame = merged[(~merged.is_warmup) & merged.matched].copy()
    if eval_frame.empty:
        raise QCError("no non-warmup matched discharge")
    if "event_id" in merged:
        eval_frame = eval_frame[["event_id", "time", "observed_m3_s", "simulated_m3_s"]]
    else:
        eval_frame = eval_frame[["time", "observed_m3_s", "simulated_m3_s"]]
    for frame in (merged, eval_frame, excluded_obs):
        frame["time"] = frame.time.map(lambda x: x.isoformat())
    prepare(args.output_dir, args.overwrite, OUTPUTS)
    merged.to_csv(args.output_dir / "aligned_discharge.csv", index=False)
    eval_frame.to_csv(args.output_dir / "evaluation_discharge.csv", index=False)
    excluded_obs.to_csv(args.output_dir / "excluded_observations.csv", index=False)
    qc = {"model": model, "simulation_rows": len(sim), "matched_rows": int(merged.matched.sum()),
          "warmup_excluded": int(merged.is_warmup.sum()), "observation_missing": int((~merged.matched).sum()),
          "simulation_missing": len(excluded_obs), "evaluation_rows": len(eval_frame)}
    write_result(args.output_dir / "alignment_qc.json", qc)
    inputs = {"observed_result": reference(args.observed_result)}
    if args.simulation_result:
        inputs["simulation_result"] = reference(args.simulation_result)
    if args.event_manifest:
        inputs["event_manifest"] = reference(args.event_manifest)
    if args.outlet_declaration:
        inputs["outlet_declaration"] = reference(args.outlet_declaration)
    doc = {"schema_version": "1.0", "skill": SKILL, "status": "success", "message": "Observed and outlet discharge aligned", "parameters": {"model": model}, "inputs": inputs,
           "artifacts": {key: reference(args.output_dir / filename, args.output_dir) for key, filename in
                         {"aligned": "aligned_discharge.csv", "evaluation": "evaluation_discharge.csv", "excluded_observations": "excluded_observations.csv", "qc": "alignment_qc.json"}.items()},
           "checks": [{"check": "input_hashes", "status": "PASS", "details": "all consumed artifacts resolved by hash"},
                      {"check": "outlet_semantics", "status": "PASS", "details": model},
                      {"check": "exact_time_match", "status": "PASS", "details": f"evaluation_rows={len(eval_frame)}"}],
           "warnings": [], "provenance": {"python": sys.version.split()[0], "pandas": version("pandas")}}
    write_result(args.output_dir / "result.json", doc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observed-result", required=True, type=Path)
    parser.add_argument("--simulation-result", type=Path)
    parser.add_argument("--event-manifest", type=Path)
    parser.add_argument("--outlet-declaration", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
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
