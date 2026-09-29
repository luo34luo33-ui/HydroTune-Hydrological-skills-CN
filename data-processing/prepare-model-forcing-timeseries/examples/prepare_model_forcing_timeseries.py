"""Normalize one station forcing variable to interval-end mm/step values."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator
import numpy as np
import pandas as pd

from _skill_common import QCError, error_result, prepare, reference, version, write_result

SKILL = "data-processing/prepare-model-forcing-timeseries"
OUTPUTS = ("prepared_forcing.csv", "series_metadata.json", "result.json")


def read_table(path: Path, sheet: str | None) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        if sheet:
            raise QCError("sheet is only valid for XLSX")
        return pd.read_csv(path, encoding="utf-8-sig")
    if suffix == ".xlsx":
        return pd.read_excel(path, sheet_name=sheet or 0)
    if suffix == ".parquet":
        if sheet:
            raise QCError("sheet is only valid for XLSX")
        return pd.read_parquet(path)
    raise QCError("source must be CSV, XLSX or Parquet")


def parse_time(value: object, config: dict) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    zone = ZoneInfo(config["timezone"])
    if stamp.tzinfo is None:
        if config["source_timezone_mode"] != "local_naive":
            raise QCError("naive source time requires local_naive declaration")
        stamp = stamp.tz_localize(zone, ambiguous="raise", nonexistent="raise")
    elif config["source_timezone_mode"] != "aware":
        raise QCError("aware source time requires aware declaration")
    if config["timestamp_semantics"] == "interval_start":
        stamp += pd.Timedelta(seconds=config["timestep_seconds"])
    return stamp.tz_convert(zone)


def normalize(source: pd.DataFrame, config: dict) -> pd.DataFrame:
    cols = config["columns"]
    needed = [cols["time"], cols["source_id"], cols["value"]]
    if config["value_semantics"] == "running_cumulative":
        needed.append(cols["reset"])
    if len(set(needed)) != len(needed) or not set(needed) <= set(source.columns):
        raise QCError("source lacks distinct configured columns")
    work = pd.DataFrame({"time": source[cols["time"]], "source_id": source[cols["source_id"]],
                         "value": source[cols["value"]]})
    if work.isna().any().any():
        raise QCError("source has missing time, ID or value")
    work["source_id"] = work.source_id.astype(str).str.strip()
    if (work.source_id == "").any():
        raise QCError("empty source_id")
    try:
        work["time"] = [parse_time(v, config) for v in work.time]
        work["value"] = pd.to_numeric(work.value, errors="raise").astype(float)
    except (ValueError, TypeError) as exc:
        raise QCError(f"invalid time or value: {exc}") from exc
    if not np.isfinite(work.value).all() or (work.value < 0).any():
        raise QCError("forcing values must be finite and nonnegative")
    if config["value_semantics"] == "running_cumulative":
        resets = source[cols["reset"]]
        if resets.isna().any() or not resets.isin([0, 1, False, True]).all():
            raise QCError("reset marker must be explicit boolean 0/1")
        work["reset"] = resets.astype(bool).to_numpy()
    work.sort_values(["source_id", "time"], inplace=True, kind="mergesort")
    if work.duplicated(["source_id", "time"]).any():
        raise QCError("duplicate source_id/time")
    dt = pd.Timedelta(seconds=config["timestep_seconds"])
    converted = []
    for source_id, group in work.groupby("source_id", sort=True):
        group = group.copy()
        times = pd.DatetimeIndex(group.time)
        if len(times) < 2 or any(times[i] - times[i - 1] != dt for i in range(1, len(times))):
            raise QCError(f"irregular or too-short time axis for {source_id}")
        vals = group.value.to_numpy(dtype=float)
        if config["value_semantics"] == "running_cumulative":
            flags = group["reset"].to_numpy(dtype=bool)
            if flags[0]:
                raise QCError("first cumulative row is a baseline, not a reset")
            depth = np.diff(vals)
            for index in range(1, len(vals)):
                if flags[index]:
                    depth[index - 1] = vals[index]
                elif depth[index - 1] < 0:
                    raise QCError(f"unmarked cumulative reset for {source_id} at {times[index]}")
            if config["unit"] == "m/step":
                depth *= 1000
            if config["unit"] == "m/step":
                depth *= 1000
            group = group.iloc[1:].copy()
        else:
            factor = {"mm/step": 1, "m/step": 1000, "mm/h": config["timestep_seconds"] / 3600,
                      "mm/day": config["timestep_seconds"] / 86400}[config["unit"]]
            depth = vals * factor
        if not np.isfinite(depth).all() or (depth < 0).any():
            raise QCError("derived depth is negative or nonfinite")
        group["depth_mm"] = depth
        group["variable"] = config["variable"]
        converted.append(group[["time", "source_id", "variable", "depth_mm"]])
    out = pd.concat(converted, ignore_index=True)
    axes = [tuple(group.time.dt.tz_convert("UTC")) for _, group in out.groupby("source_id")]
    if any(axis != axes[0] for axis in axes[1:]):
        raise QCError("stations do not share a complete time axis")
    out["time"] = out.time.map(lambda t: t.isoformat())
    return out


def run(args):
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    schema = json.loads((Path(__file__).parent / "config.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(config))
    if errors:
        raise QCError("invalid configuration: " + errors[0].message)
    if config["value_semantics"] == "running_cumulative" and config["unit"] not in ("mm/step", "m/step"):
        raise QCError("cumulative counters must use depth units")
    if config["value_semantics"] == "running_cumulative" and "reset" not in config["columns"]:
        raise QCError("cumulative counters require explicit reset column")
    if config["value_semantics"] == "rate" and config["unit"] not in ("mm/h", "mm/day"):
        raise QCError("rate input must use mm/h or mm/day")
    if config["value_semantics"] == "step_depth" and config["unit"] not in ("mm/step", "m/step"):
        raise QCError("step depth must use mm/step or m/step")
    try:
        ZoneInfo(config["timezone"])
    except ZoneInfoNotFoundError as exc:
        raise QCError("unknown timezone") from exc
    if config["value_semantics"] != "running_cumulative" and "reset" in config["columns"]:
        raise QCError("reset column is only valid for running cumulative input")
    table = normalize(read_table(args.source, args.sheet), config)
    prepare(args.output_dir, args.overwrite, OUTPUTS)
    target = args.output_dir / OUTPUTS[0]
    table.to_csv(target, index=False, encoding="utf-8")
    meta = {"schema_version": "1.0", "variable": config["variable"], "unit": "mm/step",
            "timestamp_semantics": "interval_end", "timezone": config["timezone"],
            "timestep_seconds": config["timestep_seconds"], "source_count": int(table.source_id.nunique()),
            "steps": int(table.time.nunique()), "source_semantics": config["value_semantics"]}
    (args.output_dir / OUTPUTS[1]).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    doc = {"schema_version": "1.0", "skill": SKILL, "status": "success", "message": "Forcing series standardized",
           "parameters": config, "inputs": {"source": reference(args.source), "config": reference(args.config)},
           "artifacts": {"forcing": reference(target, args.output_dir),
                         "metadata": reference(args.output_dir / OUTPUTS[1], args.output_dir)},
           "checks": [{"check": k, "status": "PASS", "details": "validated"} for k in
                      ("time_axis", "units", "missing_values", "source_coverage")], "warnings": [],
           "provenance": {"python": sys.version.split()[0], "pandas": version("pandas"),
                          "numpy": version("numpy"), "jsonschema": version("jsonschema")}}
    write_result(args.output_dir / "result.json", doc)
    return doc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--sheet")
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
