"""Create a tiny synthetic geometry and forcing fixture for CLI demonstration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin

from _skill_common import reference


def write(path, doc):
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    grid = output / "subbasins_clipped.tif"
    with rasterio.open(grid, "w", driver="GTiff", width=2, height=1, count=1, dtype="int16", crs="EPSG:32649",
                       transform=from_origin(0, 1, 1, 1), nodata=0) as ds:
        ds.write(np.array([[1, 2]], dtype="int16"), 1)
    subs = output / "subbasins.csv"
    pd.DataFrame({"sub_id": [1, 2], "area_m2": [1, 1]}).to_csv(subs, index=False)
    build = output / "build-result.json"
    write(build, {"schema_version": "1.0", "skill": "spatial-analysis/build-hydrological-topology", "status": "success",
                  "artifacts": {"subbasins_clipped": reference(grid, output), "subbasins_table": reference(subs, output)},
                  "checks": [{"check": "synthetic_grid", "status": "PASS"}]})
    write(output / "qc-result.json", {"schema_version": "1.0", "skill": "spatial-analysis/validate-hydrological-topology", "status": "success",
                                      "parameters": {"expected_outlets": 1}, "inputs": {"build_result": reference(build, output)},
                                      "checks": [{"check": "expected_outlets", "status": "PASS", "details": "synthetic fixture: one outlet"}]})
    coords = output / "stations.csv"
    pd.DataFrame({"source_id": ["A", "B"], "x": [0.5, 1.5], "y": [0.5, 0.5]}).to_csv(coords, index=False)
    layers = {}
    for variable, values in (("P", [1, 3]), ("PET", [0.2, 0.4])):
        series = output / f"{variable}.csv"
        pd.DataFrame({"time": ["2020-01-01T01:00:00+00:00"] * 2 + ["2020-01-01T02:00:00+00:00"] * 2,
                      "source_id": ["A", "B", "A", "B"], "variable": [variable] * 4, "depth_mm": values * 2}).to_csv(series, index=False)
        result = output / f"{variable}-result.json"
        write(result, {"schema_version": "1.0", "skill": "data-processing/prepare-model-forcing-timeseries", "status": "success",
                       "artifacts": {"forcing": reference(series, output)}, "checks": [{"check": "synthetic_axis", "status": "PASS"}]})
        layers["P" if variable == "P" else "evap"] = {"kind": "station", "variable": variable, "result": result.name,
                                                     "stations": coords.name, "station_crs": "EPSG:32649"}
    write(output / "sources.json", {"min_coverage_fraction": 0.999999, "layers": layers})


if __name__ == "__main__":
    main()
