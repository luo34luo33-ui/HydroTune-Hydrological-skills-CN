"""Run the semi-distributed SWAT(SWAT+-derived) daily model on HydroBase units.

Consumes:
  * ``build-hydrological-topology`` result (subbasin / reach / topology tables),
  * ``validate-hydrological-topology`` result (independent QC evidence),
  * an explicit HRU attribute table,
  * a ``time,sub_id,P_mm,E0_mm`` forcing table (daily, or aggregated opt-in),
  * an explicit model configuration.

Produces HRU / subbasin / reach process tables, outlet flow, a four-level water
balance report, the configuration snapshot and ``result.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml
from jsonschema import Draft202012Validator, ValidationError

from _swat_core import (  # private to this skill
    DEFAULT_CNFROZ,
    DEFAULT_SOIL_SLUG_MM,
    SUPPORTED_DT_S,
    WATER_BALANCE_TOLERANCES,
    AquiferParams,
    BasinConfig,
    HRU,
    ModelState,
    Reach,
    SWATSimulator,
    SoilLayer,
    Subbasin,
    UPSTREAM_COMMIT,
    UPSTREAM_TAG,
    evaluate_water_balance,
    initial_state,
    muskingum_negative_c3,
    validate_parameter_overrides,
)

SKILL = "hydrological-modeling/run-semi-distributed-swat-model"
OUTPUTS = (
    "hru_process.csv",
    "subbasin_process.csv",
    "reach_process.csv",
    "outlet_flow.csv",
    "water_balance.json",
    "model_config.json",
    "result.json",
)
DECLARED_ARTIFACTS = OUTPUTS[:-1]

HRU_COLUMNS = (
    "hru_id", "sub_id", "area_km2", "cn2", "canmx_mm", "brt", "latq_co",
    "lat_ttime_days", "slope_m_m", "lat_len_m", "perco_lim", "cn3_swf", "esco",
    "ep_frac", "aquifer_alpha", "aquifer_seep_frac", "aquifer_specific_yield",
    "aquifer_dep_bot_m", "aquifer_flo_min_m", "aquifer_revap_co", "aquifer_revap_min_m",
)
LAYER_FIELDS = ("thickness_mm", "fc_mm", "ul_mm", "wp_mm_per_mm", "ksat_mm_hr")


class ScienceError(ValueError):
    """A structurally valid input set failed a scientific or data QC gate."""


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------
def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reference(path: Path, base: Optional[Path] = None) -> Dict[str, str]:
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


def load_document(path: Path) -> Dict[str, Any]:
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


# ---------------------------------------------------------------------------
# HydroBase evidence
# ---------------------------------------------------------------------------
def load_hydrobase(build_path: Path, qc_path: Path):
    build = load_document(build_path)
    qc = load_document(qc_path)
    if build.get("skill") != "spatial-analysis/build-hydrological-topology":
        raise ScienceError("topology-result is not a HydroBase build result")
    if build.get("status") == "error":
        raise ScienceError("topology build has error status")
    if qc.get("skill") != "spatial-analysis/validate-hydrological-topology":
        raise ScienceError("topology-qc-result is not an independent validation result")
    if qc.get("status") not in ("success", "warning") or not qc.get("checks") or any(
        item.get("status") == "FAIL" for item in qc.get("checks", [])
    ):
        raise ScienceError("topology QC contains FAIL")
    resolve_ref(qc.get("inputs", {}).get("build_result"), qc_path.parent)
    if sha256(build_path) != qc["inputs"]["build_result"]["sha256"]:
        raise ScienceError("topology QC references a different build result")
    if int(qc.get("parameters", {}).get("expected_outlets", -1)) != 1:
        raise ScienceError("this skill requires QC with expected_outlets=1")
    artifacts = build.get("artifacts", {})
    tables = []
    for key in ("subbasins_table", "reaches_table", "topology_table"):
        tables.append(pd.read_csv(resolve_ref(artifacts.get(key), build_path.parent)))
    return *tables, qc


def positive_id(series: pd.Series, name: str) -> List[int]:
    numeric = pd.to_numeric(series, errors="raise")
    if numeric.isna().any() or (numeric <= 0).any() or (numeric % 1 != 0).any():
        raise ScienceError(f"{name} must contain positive integer IDs")
    values = numeric.astype(int).tolist()
    if len(set(values)) != len(values):
        raise ScienceError(f"duplicate {name}")
    return values


def parse_list(value: Any) -> List[int]:
    if pd.isna(value) or str(value).strip() == "":
        return []
    return [int(item) for item in str(value).split(";") if item.strip()]


def build_network(subs: pd.DataFrame, reaches: pd.DataFrame, topology: pd.DataFrame) -> Dict[str, Any]:
    if not {"sub_id", "area_km2", "outlet_reach_ids"}.issubset(subs):
        raise ScienceError("subbasins table lacks required fields")
    required = {"reach_id", "sub_id", "downstream_reach_id"}
    if not required.issubset(reaches) or not required.issubset(topology):
        raise ScienceError("reach or topology table lacks required fields")
    sub_ids = positive_id(subs["sub_id"], "sub_id")
    reach_ids = positive_id(reaches["reach_id"], "reach_id")
    if set(positive_id(topology["reach_id"], "topology.reach_id")) != set(reach_ids):
        raise ScienceError("reach IDs differ between reach and topology tables")
    areas_values = pd.to_numeric(subs["area_km2"], errors="raise")
    if areas_values.isna().any() or (areas_values <= 0).any() or not np.isfinite(areas_values).all():
        raise ScienceError("subbasin areas must be positive and finite")
    areas = dict(zip(sub_ids, areas_values.astype(float)))
    reach_to_sub = dict(zip(reach_ids, reaches["sub_id"].astype(int)))
    if set(reach_to_sub.values()) != set(sub_ids):
        raise ScienceError("some subbasins have no reaches or reaches reference unknown subbasins")
    downstream = dict(zip(reach_ids, reaches["downstream_reach_id"].astype(int)))
    topo_down = dict(zip(topology["reach_id"].astype(int), topology["downstream_reach_id"].astype(int)))
    if downstream != topo_down or any(dest != 0 and dest not in downstream for dest in downstream.values()):
        raise ScienceError("invalid or inconsistent downstream reach references")
    outlets = [reach for reach, dest in downstream.items() if dest == 0]
    if len(outlets) != 1:
        raise ScienceError("exactly one basin outlet is required")
    injection: Dict[int, int] = {}
    for row in subs.itertuples(index=False):
        candidates = parse_list(row.outlet_reach_ids)
        sub_id = int(row.sub_id)
        if len(candidates) != 1 or candidates[0] not in downstream or reach_to_sub[candidates[0]] != sub_id:
            raise ScienceError(f"subbasin {sub_id} must have exactly one valid outlet reach")
        reach = candidates[0]
        if downstream[reach] != 0 and reach_to_sub[downstream[reach]] == sub_id:
            raise ScienceError(f"subbasin {sub_id} outlet reach is internal to the subbasin")
        injection[sub_id] = reach
    incoming = {reach: [] for reach in reach_ids}
    indegree = {reach: 0 for reach in reach_ids}
    for reach, dest in downstream.items():
        if dest:
            incoming[dest].append(reach)
            indegree[dest] += 1
    ready = sorted(reach for reach in reach_ids if indegree[reach] == 0)
    order: List[int] = []
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
    return {
        "areas": areas,
        "sub_ids": sorted(sub_ids),
        "reach_ids": sorted(reach_ids),
        "downstream": downstream,
        "incoming": incoming,
        "order": order,
        "injection": injection,
        "outlet": outlets[0],
    }


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CONFIG_TOP_LEVEL = {
    "schema_version", "time", "swat", "initial", "reach_params_by_id",
    "parameter_overrides", "warmup_steps", "simulation",
}


def validate_config(config: Dict[str, Any], network: Dict[str, Any], aggregate: Optional[str]) -> Dict[str, Any]:
    schema = json.loads(Path(__file__).with_name("swat_model_config.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(config)
    if set(config) != CONFIG_TOP_LEVEL:
        raise ValueError("configuration has missing or unsupported top-level fields")
    if config["schema_version"] != "1.0":
        raise ValueError("configuration schema_version must be 1.0")

    time = config["time"]
    if set(time) != {"step_seconds", "timezone", "timestamp_meaning"}:
        raise ValueError("time requires step_seconds, timezone and timestamp_meaning")
    if time["timestamp_meaning"] != "interval_end":
        raise ValueError("timestamp_meaning must be interval_end")
    step = float(time["step_seconds"])
    if aggregate is None and abs(step - SUPPORTED_DT_S) > 1e-9:
        raise ValueError(
            f"this model only supports step_seconds={SUPPORTED_DT_S:g} (daily); "
            "aggregate the forcing upstream or pass --aggregate-to-daily explicitly"
        )
    if aggregate is not None and abs(step - SUPPORTED_DT_S) < 1e-9:
        raise ValueError("--aggregate-to-daily is meaningless for daily forcing")

    swat = config["swat"]
    try:
        reach_params = {int(key): value for key, value in config["reach_params_by_id"].items()}
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("reach_params_by_id must map reach IDs to objects") from exc
    if set(reach_params) != set(network["reach_ids"]):
        raise ScienceError("reach_params_by_id must cover every reach exactly once")
    if not isinstance(config["initial"], dict) or not 0.0 <= float(config["initial"]["soil_fraction"]) <= 1.0:
        raise ValueError("initial.soil_fraction must be within [0, 1]")
    simulation = config["simulation"]
    if set(simulation) != {"start", "end"}:
        raise ValueError("simulation requires explicit start and end")
    return reach_params


# ---------------------------------------------------------------------------
# HRU table
# ---------------------------------------------------------------------------
def load_hru_table(path: Path, network: Dict[str, Any]) -> List[HRU]:
    frame = pd.read_csv(path, comment="#")
    missing = [column for column in HRU_COLUMNS if column not in frame.columns]
    if missing:
        raise ScienceError(f"hru table lacks required columns: {', '.join(missing)}")
    layer_indexes = sorted({int(column.split("_")[1]) for column in frame.columns
                            if column.startswith("soil_") and column.endswith("_thickness_mm")})
    if not layer_indexes:
        raise ScienceError("hru table must declare at least one soil layer")
    if layer_indexes != list(range(1, len(layer_indexes) + 1)):
        raise ScienceError("soil layer indexes must start at 1 and be contiguous")
    for index in layer_indexes:
        for field in LAYER_FIELDS:
            column = f"soil_{index}_{field}"
            if column not in frame.columns:
                raise ScienceError(f"hru table lacks column {column}")

    if frame["hru_id"].duplicated().any():
        raise ScienceError("duplicate hru_id in HRU table")
    numeric = pd.to_numeric(frame["sub_id"], errors="raise")
    if numeric.isna().any() or (numeric % 1 != 0).any():
        raise ScienceError("hru table sub_id must be integer identifiers")
    frame = frame.assign(sub_id=numeric.astype(int))
    unknown = sorted(set(frame["sub_id"]) - set(network["sub_ids"]))
    if unknown:
        raise ScienceError(f"hru table references unknown sub_id: {unknown}")
    missing_subs = sorted(set(network["sub_ids"]) - set(frame["sub_id"]))
    if missing_subs:
        raise ScienceError(f"subbasins without any HRU: {missing_subs}")

    for column in HRU_COLUMNS[2:]:
        values = pd.to_numeric(frame[column], errors="raise")
        if values.isna().any() or not np.isfinite(values).all():
            raise ScienceError(f"hru table column {column} has missing or non-finite values")

    area_sum = frame.groupby("sub_id")["area_km2"].sum()
    for sub_id, expected in network["areas"].items():
        got = float(area_sum.get(sub_id, 0.0))
        if abs(got - expected) > 1.0e-6 * max(1.0, expected):
            raise ScienceError(
                f"subbasin {sub_id}: HRU area sum {got:.6f} km2 does not equal "
                f"HydroBase area_km2 {expected:.6f} km2"
            )

    hrus: List[HRU] = []
    for row in frame.itertuples(index=False):
        layers = []
        for index in layer_indexes:
            thickness = float(getattr(row, f"soil_{index}_thickness_mm"))
            if pd.isna(thickness):
                continue
            layers.append(SoilLayer(
                thickness_mm=thickness,
                fc_mm=float(getattr(row, f"soil_{index}_fc_mm")),
                ul_mm=float(getattr(row, f"soil_{index}_ul_mm")),
                wp_mm_per_mm=float(getattr(row, f"soil_{index}_wp_mm_per_mm")),
                ksat_mm_hr=float(getattr(row, f"soil_{index}_ksat_mm_hr")),
            ))
        if not layers:
            raise ScienceError(f"hru {row.hru_id} has no soil layer")
        aquifer = AquiferParams(
            alpha=float(row.aquifer_alpha),
            seep_frac=float(row.aquifer_seep_frac),
            specific_yield=float(row.aquifer_specific_yield),
            dep_bot_m=float(row.aquifer_dep_bot_m),
            flo_min_m=float(row.aquifer_flo_min_m),
            revap_co=float(row.aquifer_revap_co),
            revap_min_m=float(row.aquifer_revap_min_m),
        )
        hrus.append(HRU(
            id=str(row.hru_id),
            subbasin_id=int(row.sub_id),
            area_km2=float(row.area_km2),
            cn2=float(row.cn2),
            soil_layers=tuple(layers),
            canmx_mm=float(row.canmx_mm),
            brt=float(row.brt),
            latq_co=float(row.latq_co),
            lat_ttime_days=float(row.lat_ttime_days),
            slope_m_m=float(row.slope_m_m),
            lat_len_m=float(row.lat_len_m),
            perco_lim=float(row.perco_lim),
            cn3_swf=float(row.cn3_swf),
            esco=float(row.esco),
            ep_frac=float(row.ep_frac),
            aquifer=aquifer,
        ))
    return hrus


def build_reaches(network: Dict[str, Any], reach_params: Dict[int, Dict[str, float]]) -> List[Reach]:
    receiving = {reach: sub for sub, reach in network["injection"].items()}
    reaches: List[Reach] = []
    for reach_id in network["reach_ids"]:
        params = reach_params[reach_id]
        reaches.append(Reach(
            id=reach_id,
            subbasin_id=receiving.get(reach_id),
            downstream_id=network["downstream"][reach_id] or None,
            musk_k_hr=float(params["musk_k_hr"]),
            musk_x=float(params["musk_x"]),
            ttime_hr=float(params["ttime_hr"]),
            scoef=float(params["scoef"]),
            trans_loss_m3_day=float(params["trans_loss_m3_day"]),
            evap_m3_day=float(params["evap_m3_day"]),
        ))
    return reaches


# ---------------------------------------------------------------------------
# Forcing / boundary
# ---------------------------------------------------------------------------
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


def read_forcing_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if frame.empty:
        raise ScienceError("forcing table is empty")
    if set(frame.columns) != {"time", "sub_id", "P_mm", "E0_mm"}:
        raise ScienceError("forcing columns must be time,sub_id,P_mm,E0_mm")
    return frame


def check_regular_axis(times: List[pd.Timestamp], step_seconds: float) -> None:
    expected = pd.Timedelta(seconds=step_seconds)
    for i in range(1, len(times)):
        if times[i] - times[i - 1] != expected:
            raise ScienceError("forcing time axis is not regular at the declared step")


def load_forcing(
    path: Path, config: Dict[str, Any], network: Dict[str, Any], aggregate: Optional[str]
) -> Tuple[List[pd.Timestamp], Dict[pd.Timestamp, Dict[int, Tuple[float, float]]]]:
    timezone = config["time"]["timezone"]
    step = float(config["time"]["step_seconds"])
    frame = read_forcing_frame(path)
    frame["time"] = parse_time(frame["time"], timezone)
    frame["sub_id"] = pd.to_numeric(frame["sub_id"], errors="raise")
    for column in ("P_mm", "E0_mm"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if frame[column].isna().any() or not np.isfinite(frame[column]).all():
            raise ScienceError(f"forcing {column} has missing or non-finite values")
        if (frame[column] < 0).any():
            raise ScienceError(f"forcing {column} must be nonnegative (missing data is never filled with zero)")
    if frame.duplicated(["time", "sub_id"]).any():
        raise ScienceError("duplicate time/sub_id forcing rows")
    if set(frame["sub_id"].astype(int)) != set(network["sub_ids"]):
        raise ScienceError("forcing sub_id coverage differs from HydroBase")

    if aggregate is not None:
        frame = aggregate_daily(frame, step, aggregate, timezone)
        times = sorted(frame["time"].unique())
    else:
        times = sorted(frame["time"].unique())
        complete_coverage(frame, times, network)
    if not times:
        raise ScienceError("forcing has no timestamps")
    if len(times) <= config["warmup_steps"]:
        raise ScienceError("simulation period is empty after warmup")
    check_regular_axis(times, SUPPORTED_DT_S)

    start = pd.Timestamp(config["simulation"]["start"])
    end = pd.Timestamp(config["simulation"]["end"])
    if start.tzinfo is None or end.tzinfo is None or start > end:
        raise ScienceError("simulation start/end must be offset-aware and ordered")
    start = start.tz_convert(timezone)
    end = end.tz_convert(timezone)
    if times[config["warmup_steps"]] != start or times[-1] != end:
        raise ScienceError("simulation start/end must equal post-warmup first and final timestamps")

    table: Dict[pd.Timestamp, Dict[int, Tuple[float, float]]] = {}
    for stamp, group in frame.groupby("time", sort=True):
        if set(group["sub_id"].astype(int)) != set(network["sub_ids"]) or len(group) != len(network["sub_ids"]):
            raise ScienceError(f"incomplete forcing coverage at {stamp}")
        table[stamp] = {
            int(row.sub_id): (float(row.P_mm), float(row.E0_mm)) for row in group.itertuples(index=False)
        }
    return times, table


def complete_coverage(frame: pd.DataFrame, times: List[pd.Timestamp], network: Dict[str, Any]) -> None:
    expected = len(times) * len(network["sub_ids"])
    if len(frame) != expected:
        raise ScienceError("forcing rows do not cover every time/sub_id combination exactly once")


def aggregate_daily(frame: pd.DataFrame, step: float, mode: str, timezone: str) -> pd.DataFrame:
    """把亚日尺度 forcing 聚合为日历日 forcing。

    ``sum``   同日内各时步深度通量相加（每步值是该步的累计深度）。
    ``mean``  同日内取各时步平均再乘以每日步数（把每步值视为该步的代表深度）；
              当日历日步数完整时与 ``sum`` 数值一致，语义差别记入 result.json。
    """
    steps_per_day = SUPPORTED_DT_S / step
    if abs(steps_per_day - round(steps_per_day)) > 1.0e-9:
        raise ScienceError("--aggregate-to-daily requires a step that divides 86400 s exactly")
    steps_per_day = int(round(steps_per_day))
    day = (frame["time"] - pd.Timedelta(seconds=step)).dt.floor("D")
    frame = frame.assign(day=day)
    counts = frame.groupby(["day", "sub_id"]).size()
    if int(counts.min()) != steps_per_day or int(counts.max()) != steps_per_day:
        raise ScienceError(
            "every calendar day must contain the same complete number of steps "
            f"({steps_per_day}); incomplete or timezone-shifted days are rejected"
        )
    grouped = frame.groupby(["day", "sub_id"], as_index=False)
    if mode == "sum":
        aggregated = grouped.agg(P_mm=("P_mm", "sum"), E0_mm=("E0_mm", "sum"))
    else:
        aggregated = grouped.agg(P_mm=("P_mm", "mean"), E0_mm=("E0_mm", "mean"))
        aggregated["P_mm"] = aggregated["P_mm"] * steps_per_day
        aggregated["E0_mm"] = aggregated["E0_mm"] * steps_per_day
    aggregated["sub_id"] = aggregated["sub_id"].astype(int)
    aggregated["time"] = aggregated["day"] + pd.Timedelta(days=1)
    return aggregated[["time", "sub_id", "P_mm", "E0_mm"]]


def load_boundary(
    path: Optional[Path], times: List[pd.Timestamp], network: Dict[str, Any], timezone: str
) -> Tuple[Dict[pd.Timestamp, Dict[int, float]], Dict[int, int]]:
    """读取可选边界入流，返回 ``(按时间的入流表, 河段 -> 注入子流域映射)``。"""
    receiving_sub = {reach: sub for sub, reach in network["injection"].items()}
    boundary: Dict[pd.Timestamp, Dict[int, float]] = {stamp: {} for stamp in times}
    if path is None:
        return boundary, receiving_sub
    frame = pd.read_csv(path)
    if set(frame.columns) != {"time", "reach_id", "Q_m3s"}:
        raise ScienceError("boundary columns must be time,reach_id,Q_m3s")
    frame["time"] = parse_time(frame["time"], timezone)
    frame["reach_id"] = pd.to_numeric(frame["reach_id"], errors="raise").astype(int)
    frame["Q_m3s"] = pd.to_numeric(frame["Q_m3s"], errors="raise")
    if frame["Q_m3s"].isna().any() or not np.isfinite(frame["Q_m3s"]).all() or (frame["Q_m3s"] < 0).any():
        raise ScienceError("boundary flow must be finite and nonnegative")
    if frame.duplicated(["time", "reach_id"]).any():
        raise ScienceError("duplicate boundary time/reach_id rows")
    if not set(frame["time"]).issubset(set(times)):
        raise ScienceError("boundary references timestamps outside the forcing axis")
    unknown = sorted(set(frame["reach_id"]) - set(receiving_sub))
    if unknown:
        raise ScienceError(
            "boundary may only enter reaches that receive local runoff; "
            f"unsupported reaches: {unknown}"
        )
    for row in frame.itertuples(index=False):
        boundary[row.time][int(row.reach_id)] = float(row.Q_m3s)
    return boundary, receiving_sub


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------
def build_initial_state(basin: BasinConfig, initial: Dict[str, Any]) -> ModelState:
    state = initial_state(basin, float(initial["soil_fraction"]))
    if float(initial.get("canopy_mm", 0.0)):
        state.canopy_mm = np.full(basin.n_hru, float(initial["canopy_mm"]), dtype=np.float64)
    if float(initial.get("surface_lag_mm", 0.0)):
        state.surf_lag_mm = np.full(basin.n_hru, float(initial["surface_lag_mm"]), dtype=np.float64)
    if float(initial.get("lateral_lag_mm", 0.0)):
        state.lat_lag_mm = np.full(basin.n_hru, float(initial["lateral_lag_mm"]), dtype=np.float64)
    if float(initial.get("aquifer_storage_mm", 0.0)):
        state.aqu_stor_mm = np.full(basin.n_hru, float(initial["aquifer_storage_mm"]), dtype=np.float64)
    if float(initial.get("aquifer_baseflow_mm", 0.0)):
        state.aqu_flo_mm = np.full(basin.n_hru, float(initial["aquifer_baseflow_mm"]), dtype=np.float64)
    if float(initial.get("channel_storage_m3", 0.0)):
        state.ch_storage_m3 = np.full(basin.n_reach, float(initial["channel_storage_m3"]), dtype=np.float64)
    return state


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
def execute(args: argparse.Namespace) -> Dict[str, Any]:
    subs, reaches, topology, qc = load_hydrobase(args.topology_result, args.topology_qc_result)
    network = build_network(subs, reaches, topology)
    config = load_document(args.params)
    reach_params = validate_config(config, network, args.aggregate_to_daily)
    hrus = load_hru_table(args.hru_table, network)
    times, forcing = load_forcing(args.forcing, config, network, args.aggregate_to_daily)
    boundary, receiving_sub = load_boundary(
        args.boundary_inflow, times, network, config["time"]["timezone"]
    )

    swat = config["swat"]
    basin = BasinConfig(
        hrus=hrus,
        reaches=build_reaches(network, reach_params),
        subbasins=tuple(Subbasin(int(sub), float(area)) for sub, area in sorted(network["areas"].items())),
        profile=swat["profile"],
        cn_mode=swat["cn_mode"],
        channel_profile=swat["channel_profile"],
        cn_froz=float(swat.get("cn_froz", DEFAULT_CNFROZ)),
        soil_slug_mm=float(swat.get("soil_slug_mm", DEFAULT_SOIL_SLUG_MM)),
        outlet_reach_id=network["outlet"],
    )
    sub_pos = {sub: index for index, sub in enumerate(basin.subbasin_ids)}
    reach_index = {reach_id: index for index, reach_id in enumerate(basin.reach_ids)}

    n_steps = len(times)
    precip = np.zeros((basin.n_subbasin, n_steps), dtype=np.float64)
    pet = np.zeros((basin.n_subbasin, n_steps), dtype=np.float64)
    bnd = np.zeros((n_steps, basin.n_subbasin), dtype=np.float64)
    for t, stamp in enumerate(times):
        for sub_id, (p_mm, e0_mm) in forcing[stamp].items():
            precip[sub_pos[sub_id], t] = p_mm
            pet[sub_pos[sub_id], t] = e0_mm
        for reach_id, flow in boundary[stamp].items():
            bnd[t, sub_pos[receiving_sub[reach_id]]] += flow

    simulator = SWATSimulator(basin)
    result = simulator.run(
        precip,
        pet,
        bnd,
        overrides=validate_parameter_overrides(config.get("parameter_overrides") or {}),
        dt=SUPPORTED_DT_S,
        initial_state_obj=build_initial_state(basin, config["initial"]),
    )

    balance_checks = evaluate_water_balance(result)
    failed = [item["id"] for item in balance_checks if item["status"] == "FAIL"]
    if failed:
        raise ScienceError(
            "four-level water balance exceeded tolerance: " + ", ".join(failed)
            + " (see tolerances in this skill's data contract)"
        )
    warnings: List[str] = list(qc.get("warnings", []))
    if basin.channel_profile == "muskingum" and muskingum_negative_c3(
        basin.reach_param_array("musk_k_hr"), basin.reach_param_array("musk_x")
    ):
        warnings.append(
            "daily Muskingum has C3<0 for at least one reach; inflow stoppage can trap channel storage"
        )
    if args.aggregate_to_daily == "mean":
        warnings.append("--aggregate-to-daily mean multiplies the step mean by the complete daily step count")

    hru_rows: List[Dict[str, Any]] = []
    for i, hru in enumerate(basin.hrus):
        for t, stamp in enumerate(times):
            hru_rows.append({
                "time": stamp.isoformat(),
                "hru_id": hru.id,
                "sub_id": hru.subbasin_id,
                "is_warmup": t < config["warmup_steps"],
                "P_mm": result.hru["precip_mm"][i, t],
                "AET_mm": result.hru["aet_mm"][i, t],
                "surface_gen_mm": result.hru["surface_gen_mm"][i, t],
                "surface_mm": result.hru["surface_mm"][i, t],
                "lateral_mm": result.hru["lateral_mm"][i, t],
                "baseflow_mm": result.hru["baseflow_mm"][i, t],
                "percolation_mm": result.hru["percolation_mm"][i, t],
                "deep_seepage_mm": result.hru["deep_seepage_mm"][i, t],
                "revap_mm": result.hru["revap_mm"][i, t],
                "soil_water_mm": result.hru["soil_water_mm"][i, t],
                "canopy_mm": result.hru["canopy_mm"][i, t],
                "aquifer_mm": result.hru["aquifer_mm"][i, t],
                "cnday": result.hru["cnday"][i, t],
                "storage_mm": result.hru["storage_mm"][i, t],
                "residual_mm": result.hru_residual_mm[i, t],
            })
    hru_frame = pd.DataFrame(hru_rows)

    area_factor = basin.hru_area_km2 * 1000.0
    sub_rows: List[Dict[str, Any]] = []
    for s, sub_id in enumerate(basin.subbasin_ids):
        members = [i for i in range(basin.n_hru) if int(basin.hru_to_subbasin[i]) == s]
        for t, stamp in enumerate(times):
            surface = float((result.hru["surface_mm"][members, t] * area_factor[members]).sum())
            lateral = float((result.hru["lateral_mm"][members, t] * area_factor[members]).sum())
            baseflow = float((result.hru["baseflow_mm"][members, t] * area_factor[members]).sum())
            reach_i = reach_index[network["injection"][sub_id]]
            sub_rows.append({
                "time": stamp.isoformat(),
                "sub_id": sub_id,
                "is_warmup": t < config["warmup_steps"],
                "surface_m3_day": surface,
                "lateral_m3_day": lateral,
                "baseflow_m3_day": baseflow,
                "local_inflow_m3_day": surface + lateral + baseflow,
                "boundary_inflow_m3_day": float(result.reach["boundary_inflow_m3_day"][reach_i, t]),
                "upstream_inflow_m3_day": float(result.reach["upstream_inflow_m3_day"][reach_i, t]),
                "outflow_m3_day": float(result.reach["outflow_m3_day"][reach_i, t]),
                "residual_m3": float(result.subbasin_residual_m3[s, t]),
            })
    subbasin_frame = pd.DataFrame(sub_rows)

    reach_rows: List[Dict[str, Any]] = []
    for i, reach_id in enumerate(basin.reach_ids):
        for t, stamp in enumerate(times):
            reach_rows.append({
                "time": stamp.isoformat(),
                "reach_id": reach_id,
                "is_warmup": t < config["warmup_steps"],
                "local_inflow_m3_day": float(result.reach["local_inflow_m3_day"][i, t]),
                "boundary_inflow_m3_day": float(result.reach["boundary_inflow_m3_day"][i, t]),
                "upstream_inflow_m3_day": float(result.reach["upstream_inflow_m3_day"][i, t]),
                "inflow_m3_day": float(result.reach["inflow_m3_day"][i, t]),
                "outflow_m3_day": float(result.reach["outflow_m3_day"][i, t]),
                "loss_m3_day": float(result.reach["loss_m3_day"][i, t]),
                "storage_m3": float(result.reach["storage_m3"][i, t]),
            })
    reach_frame = pd.DataFrame(reach_rows)

    outlet_i = basin.outlet_index
    outlet_frame = pd.DataFrame([{
        "time": stamp.isoformat(),
        "outlet_reach_id": basin.outlet_reach_id,
        "is_warmup": t < config["warmup_steps"],
        "outflow_m3_day": float(result.reach["outflow_m3_day"][outlet_i, t]),
        "Q_m3s": float(result.outlet_Q_m3s[t]),
    } for t, stamp in enumerate(times)])

    for value in np.concatenate([result.outlet_Q_m3s, result.reach["outflow_m3_day"].ravel()]):
        if not math.isfinite(float(value)) or float(value) < 0.0:
            raise ScienceError("simulation produced non-finite or negative flow")

    water_balance = {
        "schema_version": "1.0",
        "skill": SKILL,
        "control_volumes": ["hru", "reach", "subbasin", "basin"],
        "tolerances": dict(WATER_BALANCE_TOLERANCES),
        "max_abs_residual": {
            "hru_mm": float(np.max(np.abs(result.hru_residual_mm))),
            "reach_m3": float(np.max(np.abs(result.reach_residual_m3))),
            "subbasin_m3": float(np.max(np.abs(result.subbasin_residual_m3))),
            "basin_residual_m3": result.basin_balance["residual_m3"],
        },
        "basin_fluxes_m3": dict(result.basin_balance),
        "checks": balance_checks,
        "note": (
            "residual = initial storage + all inflow - all outflow - final storage; "
            "the channel inflow minus outflow difference also carries end-of-run storage"
        ),
    }

    output = args.output_dir
    hru_frame.to_csv(output / "hru_process.csv", index=False, encoding="utf-8")
    subbasin_frame.to_csv(output / "subbasin_process.csv", index=False, encoding="utf-8")
    reach_frame.to_csv(output / "reach_process.csv", index=False, encoding="utf-8")
    outlet_frame.to_csv(output / "outlet_flow.csv", index=False, encoding="utf-8")
    (output / "water_balance.json").write_text(
        json.dumps(water_balance, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "model_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    checks = [
        {"check": "hydrobase_qc", "status": "PASS",
         "details": "independent validation, hashes and single-outlet constraint verified"},
        {"check": "finite_outputs", "status": "PASS", "details": "all simulated flows are finite and nonnegative"},
        {"check": "water_balance", "status": "PASS",
         "details": "HRU / reach / subbasin / basin residuals within tolerance"},
    ]
    if basin.channel_profile == "muskingum" and muskingum_negative_c3(
        basin.reach_param_array("musk_k_hr"), basin.reach_param_array("musk_x")
    ):
        checks.append({"check": "muskingum_daily_stability", "status": "WARN",
                       "details": "C3<0 detected; use linear_storage unless the limitation is accepted"})

    status = "warning" if warnings or any(item.get("status") == "WARN" for item in checks) else "success"
    inputs = {
        "topology_result": reference(args.topology_result),
        "topology_qc_result": reference(args.topology_qc_result),
        "hru_table": reference(args.hru_table),
        "forcing": reference(args.forcing),
        "params": reference(args.params),
    }
    if args.boundary_inflow:
        inputs["boundary_inflow"] = reference(args.boundary_inflow)
    return {
        "schema_version": "1.0",
        "skill": SKILL,
        "status": status,
        "message": "Semi-distributed SWAT daily simulation completed",
        "parameters": {
            "time": config["time"],
            "swat": config["swat"],
            "initial": config["initial"],
            "reach_params_by_id": {str(key): value for key, value in reach_params.items()},
            "parameter_overrides": config["parameter_overrides"],
            "warmup_steps": config["warmup_steps"],
            "simulation": config["simulation"],
            "aggregate_to_daily": args.aggregate_to_daily,
            "n_steps": n_steps,
            "n_hru": basin.n_hru,
            "n_subbasin": basin.n_subbasin,
            "n_reach": basin.n_reach,
            "outlet_reach_id": basin.outlet_reach_id,
        },
        "inputs": inputs,
        "artifacts": {
            name.removesuffix(".csv").removesuffix(".json"): reference(output / name, output)
            for name in DECLARED_ARTIFACTS
        },
        "checks": checks,
        "warnings": warnings,
        "provenance": {
            "python": sys.version.split()[0],
            "numpy": importlib.metadata.version("numpy"),
            "pandas": importlib.metadata.version("pandas"),
            "PyYAML": importlib.metadata.version("PyYAML"),
            "jsonschema": importlib.metadata.version("jsonschema"),
            "source_project": "MiniSWAT-RR 0.1.0",
            "source_upstream": f"swatplus {UPSTREAM_TAG} @ {UPSTREAM_COMMIT}",
            "source_license": "LGPL-2.1-or-later (see THIRD_PARTY_NOTICES.md)",
            "source_verification": "NOT VERIFIED: no numeric comparison against upstream SWAT+ was performed",
        },
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("topology-result", "topology-qc-result", "hru-table", "forcing", "params", "output-dir"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--boundary-inflow", type=Path)
    p.add_argument("--aggregate-to-daily", choices=("sum", "mean"), default=None,
                   help="opt-in aggregation of sub-daily forcing to calendar days; "
                        "sum adds step depths, mean scales the step mean by the complete daily step count")
    p.add_argument("--overwrite", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = parser().parse_args(argv)
    args.output_dir = args.output_dir.resolve()
    prepared = False
    try:
        prepare_output(args.output_dir, args.overwrite)
        prepared = True
        payload = execute(args)
        code = 0
    except ScienceError as exc:
        payload, code = _error_payload(str(exc), args), 2
    except (OSError, FileExistsError, json.JSONDecodeError, yaml.YAMLError, ValidationError,
            ValueError, KeyError, TypeError, RuntimeError, NotImplementedError) as exc:
        payload, code = _error_payload(str(exc), args), 1
    if prepared:
        for name in DECLARED_ARTIFACTS:
            if payload["status"] == "error":
                target = args.output_dir / name
                if target.is_file():
                    target.unlink()
        (args.output_dir / "result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(payload["message"], file=sys.stderr if code else sys.stdout)
    return code


def _error_payload(message: str, args: argparse.Namespace) -> Dict[str, Any]:
    inputs = {}
    for key in ("topology_result", "topology_qc_result", "hru_table", "forcing", "params", "boundary_inflow"):
        value = getattr(args, key, None)
        if value is not None:
            inputs[key] = reference(value)
    return {
        "schema_version": "1.0",
        "skill": SKILL,
        "status": "error",
        "message": message,
        "parameters": {"aggregate_to_daily": getattr(args, "aggregate_to_daily", None)},
        "inputs": inputs,
        "artifacts": {},
        "checks": [{"check": "run", "status": "FAIL", "details": message}],
        "warnings": [],
        "provenance": {"python": sys.version.split()[0],
                       "source_verification": "NOT VERIFIED against upstream SWAT+"},
    }


if __name__ == "__main__":
    raise SystemExit(main())
