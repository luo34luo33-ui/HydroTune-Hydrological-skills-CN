"""Create minimal synthetic upstream artifacts for the alignment CLI demo."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from _skill_common import reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    observed = args.output_dir / "observed.csv"
    simulated = args.output_dir / "simulated.csv"
    time = ["2020-01-01T01:00:00+08:00", "2020-01-01T02:00:00+08:00", "2020-01-01T03:00:00+08:00"]
    pd.DataFrame({"time": time, "discharge_m3_s": [1.0, 2.0, 1.5]}).to_csv(observed, index=False)
    pd.DataFrame({"time": time, "Q": [1.1, 1.8, 1.6], "is_warmup": [True, False, False]}).to_csv(simulated, index=False)
    docs = [("observed-result.json", "data-processing/prepare-discharge-timeseries", "discharge_timeseries", observed),
            ("simulation-result.json", "hydrological-modeling/run-lumped-hbv-model", "simulation_table", simulated)]
    for name, skill, key, path in docs:
        result = {"schema_version": "1.0", "skill": skill, "status": "success", "artifacts": {key: reference(path, args.output_dir)},
                  "checks": [{"check": "synthetic_example", "status": "PASS", "details": "generated deterministic fixture"}]}
        (args.output_dir / name).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
