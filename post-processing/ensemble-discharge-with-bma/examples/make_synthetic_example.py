"""Generate reproducible continuous/event upstream artifacts, without fitting BMA."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from _skill_common import reference, write_result


def generate(root, mode="continuous", random_state=2026):
    root = Path(root)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("synthetic output directory must be empty")
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(random_state)
    count = 360
    time = pd.date_range("2020-01-01", periods=count, freq="h", tz="Asia/Shanghai")
    phase = np.arange(count)
    a = np.maximum(0, 25 + 35 * np.sin(phase / 15) + 10 * np.sin(phase / 5))
    b = np.maximum(0, 30 + 30 * np.sin(phase / 15 + 0.5))
    chooser = rng.random(count) < 0.65
    observed = np.maximum(0, np.where(chooser, -8 + 0.9 * a, -12 + 1.15 * b) + rng.normal(0, 6, count))
    observation_path = root / "observed.csv"
    pd.DataFrame({"time": [t.isoformat() for t in time], "discharge_m3_s": observed}).to_csv(observation_path, index=False)
    def artifact_result(filename, skill, key, table):
        path = root / filename
        write_result(path, {"schema_version": "1.0", "skill": skill, "status": "success",
                            "parameters": {"timestep_hours": 1}, "artifacts": {key: reference(table, root)},
                            "checks": [{"check": "synthetic_fixture", "status": "PASS"}]})
    artifact_result("observed-result.json", "data-processing/prepare-discharge-timeseries", "discharge_timeseries", observation_path)
    members = []
    for name, flow, skill in [("HBV", a, "run-lumped-hbv-model"), ("Tank", b, "run-lumped-tank-model")]:
        frame = pd.DataFrame({"time": [t.isoformat() for t in time], "Q": flow, "is_warmup": phase < 3})
        member = {"model_id": name, "outlet_id": "synthetic-outlet"}
        if mode == "event":
            event_map = {}
            for number in range(3):
                event = f"flood-{number + 1:03d}"
                chunk = frame.iloc[number * 120:(number + 1) * 120].copy()
                chunk["event_id"] = event
                chunk["is_warmup"] = np.arange(len(chunk)) < 3
                table = root / f"{name}-{event}.csv"
                chunk.to_csv(table, index=False)
                result_name = f"{name}-{event}-result.json"
                artifact_result(result_name, "hydrological-modeling/" + skill, "simulation_table", table)
                event_map[event] = result_name
            event_name = f"{name}-events.json"
            write_result(root / event_name, event_map)
            member["event_manifest"] = event_name
        else:
            table = root / f"{name}.csv"
            frame.to_csv(table, index=False)
            result_name = f"{name}-result.json"
            artifact_result(result_name, "hydrological-modeling/" + skill, "simulation_table", table)
            member["simulation_result"] = result_name
        members.append(member)
    write_result(root / "members.json", {"schema_version": "1.0", "outlet_id": "synthetic-outlet", "unit": "m3/s",
                                         "timestep_hours": 1, "timezone": "Asia/Shanghai", "members": members})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=["continuous", "event"], default="continuous")
    parser.add_argument("--random-state", type=int, default=2026)
    args = parser.parse_args()
    generate(args.output_dir, args.mode, args.random_state)


if __name__ == "__main__":
    main()
