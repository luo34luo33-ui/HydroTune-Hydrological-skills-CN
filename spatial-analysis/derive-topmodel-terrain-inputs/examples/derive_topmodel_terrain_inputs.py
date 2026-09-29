"""Derive reproducible TOPMODEL terrain distributions from checked HydroBase rasters."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
import rasterio

SKILL = "spatial-analysis/derive-topmodel-terrain-inputs"
OUTPUTS = ("topographic_index.tif", "topographic_index_classes.csv", "distance_area.csv", "result.json")
D8 = {1: (-1, 1), 2: (0, 1), 4: (1, 1), 8: (1, 0),
      16: (1, -1), 32: (0, -1), 64: (-1, -1), 128: (-1, 0)}


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
        raise QCError(f"missing or modified artifact: {path}")
    return path.resolve()


def read_result(path: Path, skill: str) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("skill") != skill or value.get("status") not in ("success", "warning"):
        raise QCError(f"invalid upstream result: {path}")
    if any(c.get("status") == "FAIL" for c in value.get("checks", [])):
        raise QCError(f"upstream QC failed: {path}")
    return value


def prepare(path: Path, overwrite: bool) -> None:
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


def terrain_arrays(dem: np.ndarray, pointer: np.ndarray, accumulation: np.ndarray,
                   mask: np.ndarray, dx: float, dy: float, minimum_slope: float):
    """Use cell-centre D8 drop and direction-specific contour width."""
    if not mask.any():
        raise QCError("empty true-basin mask")
    h, w = mask.shape
    if any(a.shape != mask.shape for a in (dem, pointer, accumulation)):
        raise QCError("raster shape mismatch")
    if not np.all(np.isfinite(dem[mask])) or not np.all(np.isfinite(accumulation[mask])) or np.any(accumulation[mask] < 1):
        raise QCError("invalid DEM or accumulation within basin")
    downstream = {}
    exits = []
    slope = np.full(mask.shape, np.nan)
    clipped = 0
    for row, col in np.argwhere(mask):
        row, col = int(row), int(col)
        code = float(pointer[row, col])
        if not math.isfinite(code) or code != int(code) or int(code) not in D8:
            raise QCError(f"invalid D8 pointer at {row},{col}")
        dr, dc = D8[int(code)]
        rr, cc = row + dr, col + dc
        step = math.hypot(dr * dy, dc * dx)
        downstream[row, col] = (rr, cc, step)
        if rr < 0 or rr >= h or cc < 0 or cc >= w or not mask[rr, cc]:
            exits.append((row, col))
            raw = math.nan
        else:
            raw = (dem[row, col] - dem[rr, cc]) / step
        if not math.isfinite(raw) or raw < minimum_slope:
            clipped += 1
            raw = minimum_slope
        slope[row, col] = raw
    if len(exits) != 1:
        raise QCError(f"D8 basin must have exactly one exit cell; found {len(exits)}")
    exit_cell = exits[0]
    distance = np.full(mask.shape, np.nan)
    distance[exit_cell] = 0.0
    for start in downstream:
        if math.isfinite(distance[start]):
            continue
        chain = []
        seen = set()
        cell = start
        while not math.isfinite(distance[cell]):
            if cell in seen:
                raise QCError("D8 flow path contains a cycle")
            seen.add(cell)
            rr, cc, length = downstream[cell]
            if (rr, cc) not in downstream:
                raise QCError("flow path leaves basin before outlet")
            chain.append((cell, (rr, cc), length))
            cell = (rr, cc)
        for current, successor, length in reversed(chain):
            distance[current] = distance[successor] + length
    ti = np.full(mask.shape, np.nan)
    for (r, c), (rr, cc, _) in downstream.items():
        dr, dc = D8[int(pointer[r, c])]
        contour_width = math.hypot(dc * dy, dr * dx)
        a = accumulation[r, c] * dx * dy / contour_width
        ti[r, c] = math.log(a / slope[r, c])
    return ti, distance, clipped, exit_cell


def distributions(ti: np.ndarray, distance: np.ndarray, mask: np.ndarray,
                  classes: int, bins: int, cell_area: float):
    values = ti[mask]
    if not np.isfinite(values).all():
        raise QCError("nonfinite topographic index")
    edges = np.linspace(float(values.min()), float(values.max()) + 1e-9, classes + 1)
    counts, _ = np.histogram(values, edges)
    class_rows = []
    for i, count in enumerate(counts):
        selected = values[(values >= edges[i]) & ((values < edges[i + 1]) if i < classes - 1 else (values <= edges[i + 1]))]
        class_rows.append((i + 1, float(edges[i]), float(edges[i + 1]),
                           float(selected.mean()) if len(selected) else float((edges[i] + edges[i + 1]) / 2),
                           int(count), float(count / len(values))))
    d = distance[mask]
    max_d = float(d.max())
    d_edges = np.linspace(0, max_d + max(1e-9, max_d * 1e-12), bins + 1)
    dist_rows = [(i + 1, float(d_edges[i + 1]), int(np.count_nonzero(d <= d_edges[i + 1])),
                  float(np.count_nonzero(d <= d_edges[i + 1]) * cell_area),
                  float(np.count_nonzero(d <= d_edges[i + 1]) / len(d))) for i in range(bins)]
    dist_rows[-1] = (bins, dist_rows[-1][1], len(d), len(d) * cell_area, 1.0)
    return class_rows, dist_rows


def run(args):
    if args.classes < 2 or args.distance_bins < 1 or not math.isfinite(args.minimum_slope) or args.minimum_slope <= 0:
        raise ValueError("classes>=2, distance-bins>=1 and minimum-slope>0 required")
    stream = read_result(args.stream_result, "spatial-analysis/extract-dem-stream-network")
    build = read_result(args.topology_result, "spatial-analysis/build-hydrological-topology")
    qc = read_result(args.topology_qc_result, "spatial-analysis/validate-hydrological-topology")
    if not qc.get("checks") or qc.get("parameters", {}).get("expected_outlets") != 1:
        raise QCError("single-outlet validated topology required")
    if digest(args.topology_result) != qc.get("inputs", {}).get("build_result", {}).get("sha256"):
        raise QCError("topology QC does not match build result")
    stream_paths = {k: resolve(stream["artifacts"][k], args.stream_result.parent)
                    for k in ("filled_dem", "d8_pointer", "flow_accumulation")}
    for key, path in stream_paths.items():
        if build.get("inputs", {}).get(key, {}).get("sha256") != digest(path):
            raise QCError(f"HydroBase build used a different {key} raster")
    basin_path = resolve(build["artifacts"]["subbasins_clipped"], args.topology_result.parent)
    paths = [*stream_paths.values(), basin_path]
    arrays = []
    with rasterio.open(paths[0]) as ds:
        profile = ds.profile.copy()
        transform, crs, shape = ds.transform, ds.crs, ds.shape
        arrays.append(ds.read(1).astype(float))
    if crs is None or not crs.is_projected or crs.linear_units.lower() not in ("metre", "meter", "metres", "meters"):
        raise QCError("projected metre CRS required")
    if abs(transform.b) > 1e-12 or abs(transform.d) > 1e-12:
        raise QCError("rotated grids unsupported")
    dx, dy = abs(transform.a), abs(transform.e)
    for path in paths[1:]:
        with rasterio.open(path) as ds:
            if ds.shape != shape or ds.crs != crs or not ds.transform.almost_equals(transform):
                raise QCError("input rasters are not on one grid")
            arrays.append(ds.read(1).astype(float))
    dem, pointer, accum, basin = arrays
    mask = np.isfinite(basin) & (basin > 0)
    ti, distance, clipped, exit_cell = terrain_arrays(dem, pointer, accum, mask, dx, dy, args.minimum_slope)
    class_rows, distance_rows = distributions(ti, distance, mask, args.classes, args.distance_bins, dx * dy)
    output = args.output_dir
    prepare(output, args.overwrite)
    ti_path = output / OUTPUTS[0]
    profile.update(dtype="float32", nodata=-9999.0, count=1)
    with rasterio.open(ti_path, "w", **profile) as ds:
        ds.write(np.where(mask, ti, -9999).astype("float32"), 1)
    for name, header, rows in ((OUTPUTS[1], ("class_id", "ti_lower", "ti_upper", "ti_mean", "cell_count", "area_fraction"), class_rows),
                               (OUTPUTS[2], ("bin_id", "distance_m", "cumulative_cells", "cumulative_area_m2", "cumulative_fraction"), distance_rows)):
        with (output / name).open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
    result = {"schema_version": "1.0", "skill": SKILL, "status": "success",
              "message": "TOPMODEL terrain inputs derived from validated single-outlet basin",
              "parameters": {"classes": args.classes, "distance_bins": args.distance_bins,
                             "minimum_slope": args.minimum_slope, "slope_method": "D8 centre drop; clipped at minimum_slope",
                             "specific_area": "accumulation_cells * cell_area / D8 contour_width",
                             "d8_encoding": "whitebox-default-non-esri",
                             "basin_area_m2": int(mask.sum()) * dx * dy, "cell_area_m2": dx * dy,
                             "exit_cell": list(exit_cell), "slope_clipped_cells": clipped},
              "inputs": {"stream_result": ref(args.stream_result), "topology_result": ref(args.topology_result),
                         "topology_qc_result": ref(args.topology_qc_result),
                         **{k: ref(v) for k, v in stream_paths.items()}, "subbasins_clipped": ref(basin_path)},
              "artifacts": {k: ref(output / k, output) for k in OUTPUTS[:-1]},
              "checks": [{"check": n, "status": "PASS", "details": "validated"} for n in ("upstream_hashes", "single_outlet", "same_grid", "d8_paths", "finite_index", "distribution_weights")],
              "warnings": [], "provenance": {"python": sys.version.split()[0], "numpy": np.__version__, "rasterio": rasterio.__version__}}
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ("stream-result", "topology-result", "topology-qc-result", "output-dir"):
        p.add_argument("--" + arg, type=Path, required=True)
    p.add_argument("--classes", type=int, required=True)
    p.add_argument("--distance-bins", type=int, required=True)
    p.add_argument("--minimum-slope", type=float, required=True)
    p.add_argument("--overwrite", action="store_true")
    a = p.parse_args()
    try:
        run(a)
    except (QCError, ValueError, FileNotFoundError, KeyError, FileExistsError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        if not isinstance(e, FileExistsError):
            try:
                prepare(a.output_dir, a.overwrite)
                evidence = {k: ref(v) for k, v in (("stream_result", a.stream_result), ("topology_result", a.topology_result),
                                                    ("topology_qc_result", a.topology_qc_result)) if v.is_file()}
                failure = {"schema_version": "1.0", "skill": SKILL, "status": "error", "message": str(e),
                           "parameters": {"classes": a.classes, "distance_bins": a.distance_bins, "minimum_slope": a.minimum_slope},
                           "inputs": evidence, "artifacts": {}, "checks": [{"check": "run", "status": "FAIL", "details": str(e)}],
                           "warnings": [], "provenance": {"python": sys.version.split()[0]}}
                (a.output_dir / "result.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except (OSError, ValueError, FileExistsError):
                pass
        return 2 if isinstance(e, QCError) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
