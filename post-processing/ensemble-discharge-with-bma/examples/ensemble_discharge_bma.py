"""Train or apply censored-Gaussian BMA to existing basin-outlet simulations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from _bma_probability import SIGMA_FLOOR, fit, summarize
from _outlet_adapter import adapter, read_table, stamped
from _skill_common import QCError, error_result, load_result, prepare, reference, resolve, version, write_result

SKILL = "post-processing/ensemble-discharge-with-bma"
OUTPUTS = ("bma_model.json", "model_card.json", "fit_diagnostics.json", "alignment_qc.json",
           "ensemble_discharge.csv", "component_predictions.csv", "result.json")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def timestamp(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise QCError("period boundaries must carry timezone")
    return stamp.tz_convert("UTC")


def select(frame, args):
    event_mode = "event_id" in frame
    if event_mode:
        if not args.events or args.start or args.end:
            raise QCError("event data requires --events and forbids --start/--end")
        if len(set(args.events)) != len(args.events):
            raise QCError("event selection contains duplicates")
        if not set(args.events) <= set(frame.event_id):
            raise QCError("selected event missing from member")
        return frame.loc[frame.event_id.isin(args.events)].copy()
    if args.events or not args.start or not args.end:
        raise QCError("continuous data requires --start and --end, and forbids --events")
    start, end = timestamp(args.start), timestamp(args.end)
    if start > end:
        raise QCError("period start is after end")
    if start not in set(frame.time) or end not in set(frame.time):
        raise QCError("requested period endpoints must be covered exactly by each member")
    return frame.loc[frame.time.between(start, end)].copy()


def time_axis(frame, dt, keys):
    if frame.empty or frame.duplicated(keys).any():
        raise QCError("empty or duplicate member time axis")
    groups = frame.groupby("event_id", sort=False) if "event_id" in frame else [(None, frame)]
    for _, part in groups:
        if not part.time.is_monotonic_increasing:
            raise QCError("member time axis is not increasing")
        differences = part.time.diff().dropna().dt.total_seconds().to_numpy()
        if len(differences) and not np.allclose(differences, dt * 3600, rtol=0, atol=1e-6):
            raise QCError("member time step mismatch or missing timestamp")


def load_members(args):
    manifest = read_json(args.members)
    required = {"schema_version", "outlet_id", "unit", "timestep_hours", "timezone", "members"}
    if not isinstance(manifest, dict) or not required <= manifest.keys() or manifest["schema_version"] != "1.0":
        raise QCError("member manifest requires schema_version 1.0 and outlet/unit/time evidence")
    dt = float(manifest["timestep_hours"])
    if manifest["unit"] != "m3/s" or not manifest["outlet_id"] or not np.isfinite(dt) or dt <= 0:
        raise QCError("manifest needs basin outlet, m3/s and positive timestep_hours")
    try:
        pd.Timestamp("2020-01-01", tz=manifest["timezone"])
    except Exception as exc:
        raise QCError("invalid declared timezone") from exc
    members = manifest["members"]
    if not isinstance(members, list) or len(members) < 2:
        raise QCError("at least two model members required")
    names, frames, inputs = [], [], {"members": reference(args.members)}
    base = args.members.resolve().parent
    event_mode = None
    def source_evidence(path, member_index, suffix):
        source = read_json(path)
        parameters = source.get("parameters", {})
        if "timestep_hours" in parameters and float(parameters["timestep_hours"]) != dt:
            raise QCError("source timestep_hours disagrees with manifest")
        if "outlet_id" in parameters and str(parameters["outlet_id"]) != manifest["outlet_id"]:
            raise QCError("source outlet_id disagrees with manifest")
        if "timezone" in parameters and parameters["timezone"] != manifest["timezone"]:
            raise QCError("source timezone disagrees with manifest")
        if "unit" in parameters and parameters["unit"] != "m3/s":
            raise QCError("source unit disagrees with manifest")
        for key in ("simulation_table", "process.csv", "outlet_flow", "routed_table"):
            if key in source.get("artifacts", {}):
                artifact = resolve(source["artifacts"][key], path.parent)
                inputs[f"table_{member_index}_{suffix}_{key}"] = reference(artifact)
    for member in members:
        if not isinstance(member, dict):
            raise QCError("each member must be an object")
        name = member.get("model_id")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise QCError("model_id must be a nonempty unique string")
        if member.get("outlet_id") != manifest["outlet_id"]:
            raise QCError("members must declare the same outlet_id")
        if bool(member.get("simulation_result")) == bool(member.get("event_manifest")):
            raise QCError("member requires exactly one simulation_result or event_manifest")
        declaration = None
        if member.get("outlet_declaration"):
            path = (base / member["outlet_declaration"]).resolve()
            declaration = read_json(path)
            declaration["_config_base"] = str(path.parent)
            inputs[f"declaration_{len(names)}"] = reference(path)
            if declaration.get("outlet_id") != manifest["outlet_id"]:
                raise QCError("routed outlet declaration disagrees with manifest")
            upstream_refs = list(declaration.get("upstream_xaj_results", {}).values())
            if declaration.get("upstream_xaj_result"):
                upstream_refs.append(declaration["upstream_xaj_result"])
            for i, ref in enumerate(upstream_refs):
                inputs[f"upstream_{len(names)}_{i}"] = reference((path.parent / ref).resolve())
        if member.get("event_manifest"):
            path = (base / member["event_manifest"]).resolve()
            event_map = read_json(path)
            inputs[f"event_manifest_{len(names)}"] = reference(path)
            if not isinstance(event_map, dict) or not event_map:
                raise QCError("event manifest must map event IDs to result paths")
            if not args.events or not set(args.events) <= set(event_map):
                raise QCError("selected event missing from member event manifest")
            chunks = []
            source_kinds = set()
            for i, (event, ref) in enumerate(event_map.items()):
                if event not in args.events:
                    continue
                result_path = (path.parent / ref).resolve()
                chunk, source_kind = adapter(result_path, declaration, str(event))
                source_kinds.add(source_kind)
                if "event_id" in chunk and set(chunk.event_id) != {str(event)}:
                    raise QCError("event manifest ID disagrees with simulation")
                chunk["event_id"] = str(event)
                chunks.append(chunk)
                inputs[f"simulation_{len(names)}_{i}"] = reference(result_path)
                source_evidence(result_path, len(names), str(i))
            if len(source_kinds) != 1:
                raise QCError("one member event manifest cannot mix model types")
            frame = pd.concat(chunks, ignore_index=True)
        else:
            path = (base / member["simulation_result"]).resolve()
            frame, _ = adapter(path, declaration)
            inputs[f"simulation_{len(names)}"] = reference(path)
            source_evidence(path, len(names), "continuous")
        # A declaration confirms member spatial/unit semantics; source metadata, if
        # available, must agree as well. Timezone-bearing timestamps are normalized to UTC.
        if frame.get("event_id", pd.Series(dtype=str)).isna().any():
            raise QCError("event_id missing")
        this_mode = "event_id" in frame
        if event_mode is not None and event_mode != this_mode:
            raise QCError("cannot mix continuous and event members")
        event_mode = this_mode
        frame = select(frame, args)
        keys = ["event_id", "time"] if event_mode else ["time"]
        time_axis(frame, dt, keys)
        names.append(name)
        frames.append(frame)
    keys = ["event_id", "time"] if event_mode else ["time"]
    warm = pd.concat([frame.loc[frame.is_warmup, keys] for frame in frames]).drop_duplicates()
    effective = []
    for frame in frames:
        filtered = frame.merge(warm.assign(_warm=True), on=keys, how="left", validate="one_to_one")
        filtered = filtered.loc[filtered._warm.isna()].drop(columns="_warm").sort_values(keys).reset_index(drop=True)
        time_axis(filtered, dt, keys)
        effective.append(filtered)
    axis = effective[0][keys]
    for frame in effective[1:]:
        if not axis.equals(frame[keys]):
            raise QCError("members do not cover the same effective time axis")
    if event_mode and set(axis.event_id) != set(args.events):
        raise QCError("selected event has no effective rows")
    matrix = np.column_stack([frame.simulated_m3_s.to_numpy() for frame in effective])
    qc = {"mode": "event" if event_mode else "continuous", "rows": len(axis),
          "member_count": len(names), "warmup_union_rows": len(warm),
          "selected_rows_per_member": dict(zip(names, [len(frame) for frame in frames]))}
    return manifest, names, axis, matrix, inputs, qc


def validate_model(model):
    required = {"schema_version", "distribution", "model_ids", "weights", "theta", "scale_m3_s", "unit",
                "outlet_id", "timestep_hours", "timezone", "series_mode", "training_end", "training_events"}
    if not isinstance(model, dict) or not required <= model.keys():
        raise QCError("BMA model fields missing")
    if model["schema_version"] != "1.0" or model["distribution"] != "censored_gaussian":
        raise QCError("unsupported BMA model version or distribution")
    if model["unit"] != "m3/s":
        raise QCError("BMA model unit must be m3/s")
    ids = model["model_ids"]
    t, w = np.asarray(model["theta"], float), np.asarray(model["weights"], float)
    if len(ids) < 2 or len(set(ids)) != len(ids) or t.shape != (len(ids), 3) or w.shape != (len(ids),):
        raise QCError("invalid BMA member/parameter dimensions")
    if (not np.isfinite(t).all() or not np.isfinite(w).all() or (w < 0).any()
            or not np.isclose(w.sum(), 1, rtol=0, atol=1e-10)
            or (t[:, 1] < 0).any() or (t[:, 2] < SIGMA_FLOOR).any()
            or not np.isfinite(model["scale_m3_s"]) or model["scale_m3_s"] <= 0):
        raise QCError("invalid BMA parameter constraints")


def run(args):
    if args.mode == "train":
        if not args.observed_result or args.random_state is None or args.model_result:
            raise ValueError("train requires --observed-result and --random-state; forbids --model-result")
        if args.random_state < 0:
            raise ValueError("random-state must be nonnegative")
    elif not args.model_result or args.observed_result or args.random_state is not None:
        raise ValueError("predict requires --model-result and forbids observation/training seed")
    probabilities = args.quantiles
    if (not probabilities or any(not np.isfinite(p) or not 0 < p < 1 for p in probabilities)
            or probabilities != sorted(set(probabilities))):
        raise ValueError("quantiles must be unique increasing probabilities strictly between zero and one")
    labels = [f"p{p * 100:02g}_m3_s" for p in probabilities]
    if len(set(labels)) != len(labels):
        raise ValueError("quantile column names collide")
    manifest, names, axis, x, inputs, qc = load_members(args)
    warnings = []
    if any(np.allclose(x[:, i], x[:, j], rtol=1e-12, atol=0) for i in range(len(names)) for j in range(i)):
        warnings.append("duplicate member predictions: individual weights are not identifiable")
    if args.mode == "train":
        obs_doc = load_result(args.observed_result, {"data-processing/prepare-discharge-timeseries"})
        obs = read_table(resolve(obs_doc["artifacts"]["discharge_timeseries"], args.observed_result.parent))
        if not {"time", "discharge_m3_s"} <= set(obs):
            raise QCError("observations require time and discharge_m3_s")
        obs["time"] = stamped(obs, "observations")
        if obs.time.duplicated().any():
            raise QCError("duplicate observation time")
        joined = axis.merge(obs[["time", "discharge_m3_s"]], on="time", how="left", validate="many_to_one")
        y = pd.to_numeric(joined.discharge_m3_s, errors="coerce").to_numpy()
        if not np.isfinite(y).all() or (y < 0).any() or len(y) < 2 or np.ptp(y) == 0:
            raise QCError("training observations must be complete, finite, nonnegative and varying")
        inputs["observed_result"] = reference(args.observed_result)
        inputs["observations"] = reference(resolve(obs_doc["artifacts"]["discharge_timeseries"], args.observed_result.parent))
        if len(y) <= 4 * len(names) - 1:
            warnings.append("training rows do not exceed nominal free parameter count (4K-1)")
        model, diagnostic = fit(y, x, args.random_state, args.n_starts, args.max_iter, args.tol)
        write_result(args.output_dir / "fit_diagnostics.json", diagnostic)
        write_result(args.output_dir / "alignment_qc.json", qc)
        if model is None:
            raise QCError("no converged BMA initialization; see fit_diagnostics.json")
        model.update({"schema_version": "1.0", "distribution": "censored_gaussian", "model_ids": names,
                      "outlet_id": manifest["outlet_id"], "timestep_hours": manifest["timestep_hours"],
                      "timezone": manifest["timezone"], "unit": "m3/s", "series_mode": qc["mode"],
                      "training_start": axis.time.min().isoformat(), "training_end": axis.time.max().isoformat(),
                      "training_events": sorted(set(axis.event_id)) if "event_id" in axis else [],
                      "random_state": args.random_state})
        theta = np.array(model["theta"])
        if (theta[:, 1] <= 1e-8).any() or (theta[:, 2] <= SIGMA_FLOOR * 1.01).any() or (np.array(model["weights"]) <= 1e-8).any():
            warnings.append("fitted parameter near constraint/numerical boundary")
        validate_model(model)
        write_result(args.output_dir / "bma_model.json", model)
        card = {"distribution": model["distribution"], "estimator": "generalized_EM_maximum_likelihood",
                "parameter_units": {"theta_a": "normalized discharge", "theta_b": "dimensionless", "theta_sigma": "normalized discharge"},
                "training_rows": len(y), "row_weighting": "equal_per_timestep", "model": model,
                "inputs": inputs, "warnings": warnings,
                "interpretation": "conditional predictive uncertainty; no parameter posterior sampling or trajectory dependence"}
        write_result(args.output_dir / "model_card.json", card)
        products = {"model": "bma_model.json", "model_card": "model_card.json", "fit_diagnostics": "fit_diagnostics.json"}
    else:
        trained = load_result(args.model_result, {SKILL})
        if trained.get("parameters", {}).get("mode") != "train":
            raise QCError("model-result must reference a training run")
        model_path = resolve(trained["artifacts"]["model"], args.model_result.parent)
        model = read_json(model_path)
        validate_model(model)
        if set(names) != set(model["model_ids"]):
            raise QCError("predict member set differs from training")
        for key in ("outlet_id", "timestep_hours", "timezone"):
            if manifest[key] != model[key]:
                raise QCError(f"predict {key} differs from training")
        if qc["mode"] != model["series_mode"]:
            raise QCError("predict series mode differs from training")
        if axis.time.min() <= timestamp(model["training_end"]):
            raise QCError("application must be entirely later than training")
        if args.events and set(args.events) & set(model["training_events"]):
            raise QCError("training and prediction cannot share an event")
        x = x[:, [names.index(name) for name in model["model_ids"]]]
        names = model["model_ids"]
        inputs["model_result"] = reference(args.model_result)
        inputs["model"] = reference(model_path)
        rows, components = [], []
        for index, flows in enumerate(x):
            keys = axis.iloc[index].to_dict()
            mean, zero, quantiles, mu, sigma, member_zero = summarize(flows, model, probabilities)
            rows.append({**keys, "ensemble_mean_m3_s": mean, **dict(zip(labels, quantiles)), "probability_zero": zero})
            for j, name in enumerate(names):
                components.append({**keys, "model_id": name, "simulated_m3_s": flows[j],
                                   "corrected_center_m3_s": mu[j], "sigma_m3_s": sigma[j],
                                   "weight": model["weights"][j], "probability_zero": member_zero[j]})
        prediction, component = pd.DataFrame(rows), pd.DataFrame(components)
        for frame in (prediction, component):
            numbers = frame.select_dtypes(include="number")
            if not np.isfinite(numbers.to_numpy()).all():
                raise QCError("nonfinite probability output")
            frame["time"] = frame.time.map(lambda value: value.isoformat())
        prediction.to_csv(args.output_dir / "ensemble_discharge.csv", index=False)
        component.to_csv(args.output_dir / "component_predictions.csv", index=False)
        products = {"ensemble": "ensemble_discharge.csv", "components": "component_predictions.csv"}
    write_result(args.output_dir / "alignment_qc.json", qc)
    products["qc"] = "alignment_qc.json"
    write_result(args.output_dir / "result.json", {
        "schema_version": "1.0", "skill": SKILL, "status": "warning" if warnings else "success",
        "parameters": {"mode": args.mode, "distribution": "censored_gaussian", "random_state": args.random_state,
                       "n_starts": args.n_starts, "max_iter": args.max_iter, "tol": args.tol, "quantiles": probabilities,
                       "selection": {"start": args.start, "end": args.end, "events": args.events}},
        "inputs": inputs, "artifacts": {key: reference(args.output_dir / filename, args.output_dir) for key, filename in products.items()},
        "checks": [{"check": "alignment_and_probability", "status": "PASS", "details": qc}],
        "warnings": warnings, "provenance": {"python": sys.version.split()[0], **{name: version(name) for name in ("numpy", "pandas", "scipy")}}
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["train", "predict"], required=True)
    parser.add_argument("--members", type=Path, required=True)
    parser.add_argument("--observed-result", type=Path)
    parser.add_argument("--model-result", type=Path)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--events", nargs="+")
    parser.add_argument("--random-state", type=int)
    parser.add_argument("--n-starts", type=int, default=5)
    parser.add_argument("--max-iter", type=int, default=500)
    parser.add_argument("--tol", type=float, default=1e-8)
    parser.add_argument("--quantiles", type=float, nargs="+", default=[0.05, 0.5, 0.95])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    prepared = False
    try:
        protected = [args.members, args.observed_result, args.model_result]
        if any(path and path.resolve().parent == args.output_dir.resolve() for path in protected):
            raise ValueError("output directory must differ from manifest, observation and model input directories")
        # Reserve output before executing, but do not overwrite an existing run on
        # argument/input errors unless the caller explicitly requested overwrite.
        prepare(args.output_dir, args.overwrite, OUTPUTS)
        prepared = True
        run(args)
        return 0
    except (QCError, ValueError, OSError, KeyError, TypeError, OverflowError) as exc:
        code = 2 if isinstance(exc, QCError) else 1
        if prepared:
            for filename in ("bma_model.json", "model_card.json", "ensemble_discharge.csv", "component_predictions.csv"):
                (args.output_dir / filename).unlink(missing_ok=True)
            doc = error_result(SKILL, str(exc))
            doc["artifacts"] = {key: reference(args.output_dir / filename, args.output_dir)
                                for key, filename in (("fit_diagnostics", "fit_diagnostics.json"), ("qc", "alignment_qc.json"))
                                if (args.output_dir / filename).is_file()}
            write_result(args.output_dir / "result.json", doc)
        print(str(exc), file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
