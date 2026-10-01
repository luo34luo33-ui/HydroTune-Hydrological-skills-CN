"""Run HHU XAJ on HydroBase subbasins and route three components through its reach DAG."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
import yaml
from jsonschema import Draft202012Validator, ValidationError

from _xaj_core import initial_state, local_step, muskingum_step, validate_parameters


SKILL = "hydrological-modeling/run-semi-distributed-xaj-model"
OUTPUTS = ("subbasin_process.csv", "reach_process.csv", "outlet_flow.csv",
           "model_config.json", "result.json")


class ScienceError(ValueError):
    """A valid file set failed a scientific or data QC gate."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reference(path: Path, base: Path | None = None) -> dict[str, str]:
    path = path.resolve()
    try:
        display = path.relative_to(base.resolve()).as_posix() if base else str(path)
    except ValueError:
        display = str(path)
    return {"path": display, "sha256": sha256(path)}


def resolve_ref(value: Any, base: Path) -> Path:
    if not isinstance(value, dict) or set(value) < {"path", "sha256"}:
        raise ScienceError("artifact reference requires path and sha256")
    path = Path(value["path"])
    path = path.resolve() if path.is_absolute() else (base / path).resolve()
    if not path.is_file() or sha256(path) != value["sha256"]:
        raise ScienceError(f"missing or hash-mismatched artifact: {path}")
    return path


def load_document(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) if path.suffix.lower() in (".yaml", ".yml") else json.load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"expected object in {path}")
    return data


def prepare_output(path: Path, overwrite: bool) -> None:
    if path.exists() and not path.is_dir():
        raise ValueError("output path is not a directory")
    if path.exists() and any(path.iterdir()) and not overwrite:
        raise FileExistsError("output directory is nonempty; use --overwrite")
    path.mkdir(parents=True, exist_ok=True)
    if overwrite:
        for name in OUTPUTS:
            target = path / name
            if target.is_file():
                target.unlink()


def load_hydrobase(build_path: Path, qc_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    build = load_document(build_path)
    qc = load_document(qc_path)
    if build.get("skill") != "spatial-analysis/build-hydrological-topology":
        raise ScienceError("topology-result is not a HydroBase build result")
    if build.get("status") == "error":
        raise ScienceError("topology build has error status")
    if qc.get("skill") != "spatial-analysis/validate-hydrological-topology":
        raise ScienceError("topology-qc-result is not an independent validation result")
    if qc.get("status") not in ("success", "warning") or not qc.get("checks") or any(item.get("status") == "FAIL" for item in qc.get("checks", [])):
        raise ScienceError("topology QC contains FAIL")
    resolve_ref(qc.get("inputs", {}).get("build_result"), qc_path.parent)
    linked = qc["inputs"]["build_result"]
    if sha256(build_path) != linked["sha256"]:
        raise ScienceError("topology QC references a different build result")
    if int(qc.get("parameters", {}).get("expected_outlets", -1)) != 1:
        raise ScienceError("first version requires QC with expected_outlets=1")
    artifacts = build.get("artifacts", {})
    tables = []
    for key in ("subbasins_table", "reaches_table", "topology_table"):
        tables.append(pd.read_csv(resolve_ref(artifacts.get(key), build_path.parent)))
    return *tables, qc


def positive_id(series: pd.Series, name: str) -> list[int]:
    numeric = pd.to_numeric(series, errors="raise")
    if numeric.isna().any() or (numeric <= 0).any() or (numeric % 1 != 0).any():
        raise ScienceError(f"{name} must contain positive integer IDs")
    values = numeric.astype(int).tolist()
    if len(set(values)) != len(values):
        raise ScienceError(f"duplicate {name}")
    return values


def parse_list(value: Any) -> list[int]:
    if pd.isna(value) or str(value).strip() == "":
        return []
    return [int(item) for item in str(value).split(";") if item.strip()]


def build_network(subs: pd.DataFrame, reaches: pd.DataFrame, topology: pd.DataFrame) -> dict:
    if not {"sub_id", "area_km2", "outlet_reach_ids"}.issubset(subs):
        raise ScienceError("subbasins table lacks required fields")
    required = {"reach_id", "sub_id", "downstream_reach_id"}
    if not required.issubset(reaches) or not required.issubset(topology):
        raise ScienceError("reach or topology table lacks required fields")
    sub_ids = positive_id(subs["sub_id"], "sub_id")
    reach_ids = positive_id(reaches["reach_id"], "reach_id")
    if set(positive_id(topology["reach_id"], "topology.reach_id")) != set(reach_ids):
        raise ScienceError("reach IDs differ between reach and topology tables")
    areas = dict(zip(sub_ids, pd.to_numeric(subs["area_km2"], errors="raise")))
    if not all(math.isfinite(float(area)) and area > 0 for area in areas.values()):
        raise ScienceError("subbasin areas must be positive and finite")
    reach_to_sub = dict(zip(reach_ids, reaches["sub_id"].astype(int)))
    if set(reach_to_sub.values()) != set(sub_ids):
        raise ScienceError("some subbasins have no reaches or reaches have unknown subbasins")
    downstream = dict(zip(reach_ids, reaches["downstream_reach_id"].astype(int)))
    topo_down = dict(zip(topology["reach_id"].astype(int), topology["downstream_reach_id"].astype(int)))
    if downstream != topo_down or any(dest != 0 and dest not in downstream for dest in downstream.values()):
        raise ScienceError("invalid or inconsistent downstream reach references")
    outlets = [reach for reach, dest in downstream.items() if dest == 0]
    if len(outlets) != 1:
        raise ScienceError("exactly one basin outlet is required")
    injection = {}
    for row in subs.itertuples(index=False):
        candidates = parse_list(row.outlet_reach_ids)
        sub_id = int(row.sub_id)
        if len(candidates) != 1 or candidates[0] not in downstream or reach_to_sub[candidates[0]] != sub_id:
            raise ScienceError(f"subbasin {sub_id} must have one valid outlet reach")
        reach = candidates[0]
        if downstream[reach] != 0 and reach_to_sub[downstream[reach]] == sub_id:
            raise ScienceError(f"subbasin {sub_id} outlet reach is internal")
        injection[sub_id] = reach
    incoming = {reach: [] for reach in reach_ids}
    indegree = {reach: 0 for reach in reach_ids}
    for reach, dest in downstream.items():
        if dest:
            incoming[dest].append(reach)
            indegree[dest] += 1
    ready = sorted(reach for reach in reach_ids if indegree[reach] == 0)
    order = []
    while ready:
        reach = ready.pop(0)
        order.append(reach)
        dest = downstream[reach]
        if dest:
            indegree[dest] -= 1
            if indegree[dest] == 0:
                ready.append(dest)
                ready.sort()
    if len(order) != len(reach_ids):
        raise ScienceError("reach topology contains a cycle")
    return {"areas": areas, "sub_ids": sorted(sub_ids), "reach_ids": sorted(reach_ids),
            "downstream": downstream, "incoming": incoming, "order": order,
            "injection": injection, "outlet": outlets[0]}


def validate_config(config: dict, network: dict) -> tuple[dict, dict, tuple]:
    schema = json.loads(Path(__file__).with_name("model_config.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(config)
    if set(config) != {"schema_version", "time", "xaj", "routing", "initial", "warmup_steps", "simulation"}:
        raise ValueError("configuration has missing or unsupported top-level fields")
    if config.get("schema_version") != "1.0":
        raise ValueError("configuration schema_version must be 1.0")
    time = config.get("time", {})
    if not isinstance(time, dict) or set(time) != {"step_seconds", "timezone", "timestamp_meaning"}:
        raise ValueError("time requires step_seconds, timezone and timestamp_meaning")
    if not isinstance(time["timezone"], str) or not time["timezone"]:
        raise ValueError("timezone must be explicit")
    if time["timestamp_meaning"] != "interval_end":
        raise ValueError("timestamp_meaning must be interval_end")
    dt = float(time["step_seconds"])
    adjusted, coefficients = validate_parameters(config.get("xaj", {}), dt)
    initial_state(adjusted, config.get("initial", {}))
    routing = config.get("routing", {})
    if not isinstance(routing, dict) or set(routing) != {"dp_by_reach", "lag_steps"}:
        raise ValueError("routing requires dp_by_reach and lag_steps")
    try:
        dp = {int(key): value for key, value in routing["dp_by_reach"].items()}
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("dp_by_reach must map reach IDs to integers") from exc
    if set(dp) != set(network["reach_ids"]):
        raise ScienceError("dp_by_reach must cover every reach exactly")
    if any(type(value) is not int or value < 0 for value in dp.values()):
        raise ValueError("DP must be a nonnegative integer")
    lag = routing["lag_steps"]
    warmup = config.get("warmup_steps")
    if type(lag) is not int or lag < 0 or type(warmup) is not int or warmup < 0:
        raise ValueError("lag_steps and warmup_steps must be nonnegative integers")
    simulation = config.get("simulation", {})
    if not isinstance(simulation, dict) or set(simulation) != {"start", "end"}:
        raise ValueError("simulation requires explicit start and end")
    return adjusted, dp, coefficients


def parse_time(series: pd.Series, timezone: str) -> pd.Series:
    try:
        values = pd.to_datetime(series, errors="raise")
        if values.dt.tz is None:
            raise ScienceError("timestamps must carry explicit UTC offsets")
        return values.dt.tz_convert(timezone)
    except (TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, ScienceError):
            raise
        raise ScienceError("invalid or inconsistent timezone-aware timestamps") from exc


def load_forcing(path: Path, config: dict, network: dict) -> tuple[list, dict]:
    frame = pd.read_csv(path)
    if set(frame.columns) != {"time", "sub_id", "P_mm", "E0_mm"}:
        raise ScienceError("forcing columns must be time,sub_id,P_mm,E0_mm")
    frame["time"] = parse_time(frame["time"], config["time"]["timezone"])
    frame["sub_id"] = pd.to_numeric(frame["sub_id"], errors="raise")
    if frame[["P_mm", "E0_mm"]].isna().any().any():
        raise ScienceError("forcing has missing values")
    for col in ("P_mm", "E0_mm"):
        frame[col] = pd.to_numeric(frame[col], errors="raise")
        if not np.isfinite(frame[col]).all() or (frame[col] < 0).any():
            raise ScienceError(f"{col} must be finite and nonnegative")
    if frame.duplicated(["time", "sub_id"]).any():
        raise ScienceError("duplicate time/sub_id forcing rows")
    if set(frame["sub_id"]) != set(network["sub_ids"]):
        raise ScienceError("forcing sub_id coverage differs from HydroBase")
    times = sorted(frame["time"].unique())
    if not times or len(times) <= config["warmup_steps"]:
        raise ScienceError("simulation period is empty after warmup")
    expected = pd.Timedelta(seconds=float(config["time"]["step_seconds"]))
    if any(times[i] - times[i - 1] != expected for i in range(1, len(times))):
        raise ScienceError("forcing time axis is not regular at declared step")
    start = pd.Timestamp(config["simulation"]["start"])
    end = pd.Timestamp(config["simulation"]["end"])
    if start.tzinfo is None or end.tzinfo is None or start > end:
        raise ScienceError("simulation start/end must be offset-aware and ordered")
    if times[config["warmup_steps"]] != start.tz_convert(config["time"]["timezone"]) or times[-1] != end.tz_convert(config["time"]["timezone"]):
        raise ScienceError("simulation start/end must equal post-warmup first and final timestamps")
    table = {}
    for stamp, group in frame.groupby("time", sort=True):
        if set(group["sub_id"]) != set(network["sub_ids"]) or len(group) != len(network["sub_ids"]):
            raise ScienceError(f"incomplete forcing coverage at {stamp}")
        table[stamp] = {int(row.sub_id): (float(row.P_mm), float(row.E0_mm)) for row in group.itertuples(index=False)}
    return times, table


def load_boundary(path: Path | None, times: list, network: dict, timezone: str) -> dict:
    boundary = {stamp: {} for stamp in times}
    if path is None:
        return boundary
    frame = pd.read_csv(path)
    if set(frame.columns) != {"time", "reach_id", "Q_m3s"}:
        raise ScienceError("boundary columns must be time,reach_id,Q_m3s")
    frame["time"] = parse_time(frame["time"], timezone)
    if frame.duplicated(["time", "reach_id"]).any():
        raise ScienceError("duplicate boundary time/reach_id rows")
    if not set(frame["time"]).issubset(set(times)) or not set(frame["reach_id"]).issubset(set(network["reach_ids"])):
        raise ScienceError("boundary references unknown time or reach")
    for row in frame.itertuples(index=False):
        flow = float(row.Q_m3s)
        if not math.isfinite(flow) or flow < 0:
            raise ScienceError("boundary flow must be finite and nonnegative")
        boundary[row.time][int(row.reach_id)] = flow
    return boundary


def run_model(config: dict, network: dict, times: list, forcing: dict, boundary: dict,
              adjusted: dict, dp: dict, coefficients: tuple, *,
              save_internal_process: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    dt = float(config["time"]["step_seconds"])
    lag = config["routing"]["lag_steps"]
    states = {sub: initial_state(adjusted, config["initial"]) for sub in network["sub_ids"]}
    stage_states = {reach: [[(0.0, 0.0)] * dp[reach] for _ in range(3)] for reach in network["reach_ids"]}
    delay = {sub: {} for sub in network["sub_ids"]}
    sub_rows, reach_rows, outlet_rows = [], [], []
    channel_in, channel_out, rainfall, evap, local_runoff, soil_change, split_residual = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    for index, stamp in enumerate(times):
        local = {}
        for sub in network["sub_ids"]:
            p_mm, e0_mm = forcing[stamp][sub]
            states[sub], row = local_step(p_mm, e0_mm, float(network["areas"][sub]), dt, adjusted, states[sub])
            if save_internal_process:
                row.update({"time": stamp.isoformat(), "sub_id": sub, "is_warmup": index < config["warmup_steps"]})
                sub_rows.append(row)
            delay[sub][index + lag] = (row["local_QS_m3s"], row["local_QI_m3s"], row["local_QG_m3s"])
            local[sub] = delay[sub].pop(index, (0.0, 0.0, 0.0))
            rainfall += p_mm * network["areas"][sub] * 1000.0
            evap += e0_mm * adjusted["KC"] * network["areas"][sub] * 1000.0
            local_runoff += row["R_mm"] * network["areas"][sub] * 1000.0
            soil_change += row["soil_delta_mm"] * network["areas"][sub] * 1000.0
            split_residual += (row["RS_mm"] + row["RI_mm"] + row["RG_mm"] - row["R_mm"]) * network["areas"][sub] * 1000.0
        routed = {}
        for reach in network["order"]:
            in_components = [sum(routed[parent][component] for parent in network["incoming"][reach])
                             for component in range(3)]
            for sub, injection_reach in network["injection"].items():
                if injection_reach == reach:
                    for component in range(3):
                        in_components[component] += local[sub][component]
            external = boundary[stamp].get(reach, 0.0)
            in_components[0] += external
            outputs = []
            for component in range(3):
                out, stages = muskingum_step(in_components[component], stage_states[reach][component], coefficients)
                if not math.isfinite(out) or out < 0:
                    raise ScienceError("non-finite or negative routed flow")
                stage_states[reach][component] = stages
                outputs.append(out)
            routed[reach] = outputs
            channel_in += sum(in_components) * dt
            channel_out += sum(outputs) * dt
            if save_internal_process:
                reach_rows.append({"time": stamp.isoformat(), "reach_id": reach,
                                   "is_warmup": index < config["warmup_steps"],
                                   "in_QS_m3s": in_components[0], "in_QI_m3s": in_components[1],
                                   "in_QG_m3s": in_components[2], "out_QS_m3s": outputs[0],
                                   "out_QI_m3s": outputs[1], "out_QG_m3s": outputs[2],
                                   "out_Q_m3s": sum(outputs), "boundary_Q_m3s": external})
        outlet = routed[network["outlet"]]
        outlet_rows.append({"time": stamp.isoformat(), "outlet_reach_id": network["outlet"],
                            "is_warmup": index < config["warmup_steps"],
                            "QS_m3s": outlet[0], "QI_m3s": outlet[1], "QG_m3s": outlet[2],
                            "Q_m3s": sum(outlet)})
    metrics = {"rainfall_m3": rainfall, "potential_evaporation_m3": evap,
               "local_runoff_m3": local_runoff, "soil_storage_change_m3": soil_change,
               "source_split_residual_m3": split_residual,
               "channel_in_minus_out_m3": channel_in - channel_out,
               "lag_tail_steps": lag}
    return pd.DataFrame(sub_rows), pd.DataFrame(reach_rows), pd.DataFrame(outlet_rows), metrics


def execute(args: argparse.Namespace) -> dict:
    subs, reaches, topology, qc = load_hydrobase(args.topology_result, args.topology_qc_result)
    network = build_network(subs, reaches, topology)
    config = load_document(args.params)
    adjusted, dp, coefficients = validate_config(config, network)
    times, forcing = load_forcing(args.forcing, config, network)
    boundary = load_boundary(args.boundary_inflow, times, network, config["time"]["timezone"])
    output_detail = getattr(args, "output_detail", "full")
    save_internal_process = output_detail == "full"
    sub, reach, outlet, metrics = run_model(config, network, times, forcing, boundary, adjusted, dp, coefficients,
                                          save_internal_process=save_internal_process)
    output = args.output_dir
    if save_internal_process:
        sub.to_csv(output / OUTPUTS[0], index=False, encoding="utf-8")
        reach.to_csv(output / OUTPUTS[1], index=False, encoding="utf-8")
    generated_outputs = OUTPUTS[:-1] if save_internal_process else OUTPUTS[2:-1]
    outlet.to_csv(output / OUTPUTS[2], index=False, encoding="utf-8")
    (output / OUTPUTS[3]).write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    warnings = list(qc.get("warnings", []))
    if config["routing"]["lag_steps"]:
        warnings.append("lag tail remains outside the supplied simulation period")
    inputs = {"topology_result": reference(args.topology_result),
              "topology_qc_result": reference(args.topology_qc_result),
              "forcing": reference(args.forcing), "params": reference(args.params)}
    if args.boundary_inflow:
        inputs["boundary_inflow"] = reference(args.boundary_inflow)
    return {"schema_version": "1.0", "skill": SKILL, "status": "warning" if warnings else "success",
            "message": "Semi-distributed XAJ simulation completed",
            "parameters": {"time": config["time"], "warmup_steps": config["warmup_steps"],
                           "simulation": config["simulation"], "routing": config["routing"],
                           "xaj": config["xaj"], "initial": config["initial"],
                           "muskingum_coefficients": coefficients, "output_detail": output_detail},
            "inputs": inputs, "artifacts": {name.removesuffix(".csv").removesuffix(".json"): reference(output / name, output)
                                         for name in generated_outputs},
            "checks": [{"check": "hydrobase_qc", "status": "PASS", "details": "independent validation and hashes checked"},
                       {"check": "finite_outputs", "status": "PASS", "details": "all simulated flows are finite"},
                       {"check": "accounting_reported", "status": "PASS", "details": metrics}],
            "warnings": warnings, "provenance": {"python": sys.version.split()[0],
                                               "numpy": importlib.metadata.version("numpy"),
                                               "pandas": importlib.metadata.version("pandas"),
                                               "PyYAML": importlib.metadata.version("PyYAML"),
                                               "jsonschema": importlib.metadata.version("jsonschema"),
                                               "source": "model_hhu.py, read-only HHU equation reference",
                                               "source_sha256": "c90504d13581cb1ee809ba713a6a34f262718caaf968d814e24eba822c667be1"}}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("topology-result", "topology-qc-result", "forcing", "params", "output-dir"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--boundary-inflow", type=Path)
    p.add_argument("--output-detail", choices=("outlet-only", "full"), default="full",
                   help="outlet-only omits internal process tables and their in-memory history; full preserves legacy outputs (default)")
    p.add_argument("--overwrite", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.output_dir = args.output_dir.resolve()
    prepared = False
    try:
        prepare_output(args.output_dir, args.overwrite)
        prepared = True
        result = execute(args)
        code = 0
    except (OSError, FileExistsError, json.JSONDecodeError, yaml.YAMLError, ValidationError, ValueError, KeyError) as exc:
        code = 2 if isinstance(exc, ScienceError) else 1
        if prepared:
            for name in OUTPUTS[:-1]:
                target = args.output_dir / name
                if target.is_file():
                    target.unlink()
        result = {"schema_version": "1.0", "skill": SKILL, "status": "error",
                  "message": str(exc), "parameters": {}, "inputs": {}, "artifacts": {},
                  "checks": [{"check": "run", "status": "FAIL", "details": str(exc)}],
                  "warnings": [], "provenance": {"python": sys.version.split()[0]}}
    if prepared:
        (args.output_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                                               sort_keys=True) + "\n", encoding="utf-8")
    print(result["message"], file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
