"""Area-weight station or gridded forcing over validated HydroBase units."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.features import shapes
from shapely.geometry import Point, Polygon, shape
from shapely.ops import transform, unary_union, voronoi_diagram
from pyproj import CRS, Transformer

from _skill_common import QCError, error_result, load_result, prepare, reference, resolve, sha256, version, write_result

SKILL = "spatial-analysis/aggregate-forcing-to-model-units"
OUTPUTS = ("basin_forcing.csv", "subbasin_forcing.csv", "weights.csv", "coverage.csv", "result.json")


def topology(args):
    build = load_result(args.topology_result, {"spatial-analysis/build-hydrological-topology"})
    qc = load_result(args.topology_qc_result, {"spatial-analysis/validate-hydrological-topology"})
    if qc.get("parameters", {}).get("expected_outlets") != 1:
        raise QCError("topology QC must require one outlet")
    if not any(item.get("check") == "expected_outlets" and item.get("status") == "PASS" for item in qc.get("checks", [])):
        raise QCError("topology QC lacks passing single-outlet check")
    if resolve(qc["inputs"]["build_result"], args.topology_qc_result.parent) != args.topology_result.resolve():
        raise QCError("QC references a different topology build")
    grid_path = resolve(build["artifacts"]["subbasins_clipped"], args.topology_result.parent)
    table_path = resolve(build["artifacts"]["subbasins_table"], args.topology_result.parent)
    with rasterio.open(grid_path) as ds:
        if ds.crs is None or ds.crs.is_geographic or ds.res[0] <= 0 or ds.res[1] <= 0:
            raise QCError("HydroBase grid requires a projected CRS")
        data = ds.read(1)
        polygons = {}
        for geom, value in shapes(data.astype("int32"), mask=data > 0, transform=ds.transform):
            polygons.setdefault(int(value), []).append(shape(geom))
        polygons = {key: unary_union(geoms) for key, geoms in polygons.items()}
        crs = ds.crs
    sub_ids = set(pd.read_csv(table_path).sub_id.astype(int))
    if set(polygons) != sub_ids or not polygons:
        raise QCError("model units disagree with HydroBase table")
    return polygons, crs, {"grid": grid_path, "table": table_path}


def expected_times(frame, value_col):
    if not {"time", value_col} <= set(frame.columns) or frame.time.isna().any() or frame[value_col].isna().any():
        raise QCError("forcing missing time or values")
    times = pd.to_datetime(frame.time, utc=True)
    if times.isna().any() or frame.duplicated("time").any():
        raise QCError("forcing time invalid or duplicated")
    return times


def station_layer(result_path, spec, units, crs, config_base):
    result = load_result(result_path, {"data-processing/prepare-model-forcing-timeseries", "data-processing/derive-potential-evapotranspiration"})
    key = "forcing" if result["skill"].endswith("prepare-model-forcing-timeseries") else "mapped_evap"
    if key not in result["artifacts"]:
        raise QCError("ETo requires explicit PET/E0 mapping")
    series = pd.read_csv(resolve(result["artifacts"][key], result_path.parent))
    if set(series.variable) != {spec["variable"]}:
        raise QCError("forcing variable semantics disagree")
    coords_path = (config_base / spec["stations"]).resolve()
    coords = pd.read_csv(coords_path)
    if not {"source_id", "x", "y"} <= set(coords) or coords.source_id.duplicated().any():
        raise QCError("station coordinates missing or duplicated")
    coords["source_id"] = coords.source_id.astype(str)
    series["source_id"] = series.source_id.astype(str)
    if set(series.source_id) != set(coords.source_id):
        raise QCError("forcing and coordinate station IDs disagree")
    source_crs = CRS.from_user_input(spec["station_crs"])
    transformer = Transformer.from_crs(source_crs, crs, always_xy=True)
    points = {}
    for row in coords.itertuples():
        x, y = transformer.transform(float(row.x), float(row.y))
        points[str(row.source_id)] = Point(x, y)
    if len(set((p.x, p.y) for p in points.values())) != len(points):
        raise QCError("duplicate station location")
    basin = unary_union(list(units.values()))
    envelope = basin.envelope.buffer(max(basin.bounds[2]-basin.bounds[0], basin.bounds[3]-basin.bounds[1]) * 2)
    if len(points) == 1:
        cells = {next(iter(points)): envelope}
    else:
        all_cells = list(voronoi_diagram(unary_union(list(points.values())), envelope=envelope).geoms)
        cells = {}
        for source_id, point in points.items():
            matching = [cell for cell in all_cells if cell.covers(point)]
            if len(matching) != 1:
                raise QCError("ambiguous Thiessen cell")
            cells[source_id] = matching[0]
    weights = []
    for unit_id, polygon in units.items():
        for source_id, cell in cells.items():
            area = polygon.intersection(cell).area
            if area > 0:
                weights.append((unit_id, source_id, area / polygon.area, area))
    if series.duplicated(["time", "source_id"]).any():
        raise QCError("duplicate station forcing step")
    axes = [tuple(pd.to_datetime(g.time, utc=True)) for _, g in series.groupby("source_id")]
    if any(axis != axes[0] for axis in axes[1:]):
        raise QCError("missing station at one or more steps")
    return series.rename(columns={"depth_mm": "value"}), weights, {"stations": reference(coords_path), "result": reference(result_path)}


def grid_layer(spec, units, crs, config_base):
    fmt = spec["kind"]
    input_refs = {}
    if fmt == "netcdf":
        import xarray as xr
        path = (config_base / spec["path"]).resolve()
        input_refs["netcdf"] = reference(path)
        ds = xr.open_dataset(path)
        if spec["data_variable"] not in ds or spec.get("grid_crs") is None:
            raise QCError("NetCDF variable or CRS missing")
        arr = ds[spec["data_variable"]]
        if tuple(arr.dims) != tuple(spec["dimensions"]):
            raise QCError("NetCDF dimensions do not match declared time,y,x")
        raw_time = ds[spec["dimensions"][0]].values
        if spec.get("time_zone") is None:
            raise QCError("NetCDF time_zone must be explicit")
        axis = pd.to_datetime(raw_time)
        if axis.tz is None:
            axis = axis.tz_localize(spec["time_zone"]).tz_convert("UTC")
        else:
            axis = axis.tz_convert("UTC")
        xs = np.asarray(ds[spec["dimensions"][2]].values, float)
        ys = np.asarray(ds[spec["dimensions"][1]].values, float)
        if len(xs) < 2 or len(ys) < 2 or not np.allclose(np.diff(xs), np.diff(xs)[0]) or not np.allclose(np.diff(ys), np.diff(ys)[0]):
            raise QCError("NetCDF needs regular x/y center coordinates")
        dx, dy = abs(xs[1]-xs[0]), abs(ys[1]-ys[0])
        grid_crs = CRS.from_user_input(spec["grid_crs"])
        values = np.asarray(arr.values, float)
        ds.close()
        cells = {(r, c): Polygon([(x-dx/2,y-dy/2),(x+dx/2,y-dy/2),(x+dx/2,y+dy/2),(x-dx/2,y+dy/2)]) for r,y in enumerate(ys) for c,x in enumerate(xs)}
    elif fmt == "geotiff":
        manifest_path = (config_base / spec["manifest"]).resolve()
        input_refs["manifest"] = reference(manifest_path)
        manifest = pd.read_csv(manifest_path)
        if not {"time", "path"} <= set(manifest) or manifest.time.duplicated().any():
            raise QCError("GeoTIFF manifest invalid")
        if any(pd.Timestamp(value).tzinfo is None for value in manifest.time):
            raise QCError("GeoTIFF manifest timestamps require timezone")
        axis = pd.DatetimeIndex(pd.to_datetime(manifest.time, utc=True))
        arrays = []
        profile = None
        for name in manifest.path:
            path = (manifest_path.parent / name).resolve()
            input_refs[f"grid_{len(arrays)}"] = reference(path)
            with rasterio.open(path) as ds:
                if ds.count != 1 or ds.crs is None or ds.transform.b != 0 or ds.transform.d != 0:
                    raise QCError("GeoTIFF must be north-up single band with CRS")
                signature = (ds.width, ds.height, ds.transform, ds.crs, ds.nodata)
                if profile is not None and signature != profile:
                    raise QCError("GeoTIFF sequence grid mismatch")
                profile = signature
                arrays.append(ds.read(1).astype(float))
        values = np.stack(arrays)
        width, height, affine, grid_crs, nodata = profile
        if nodata is not None:
            values[values == nodata] = np.nan
        cells = {(r,c): Polygon([affine * (c,r), affine * (c+1,r), affine * (c+1,r+1), affine * (c,r+1)]) for r in range(height) for c in range(width)}
    else:
        raise QCError("grid kind must be netcdf or geotiff")
    if len(axis) < 2 or axis.has_duplicates or axis.isna().any() or not (np.diff(axis.asi8) == spec["timestep_seconds"] * 10**9).all():
        raise QCError("gridded time axis missing, duplicate or irregular")
    if spec["timestamp_semantics"] == "interval_start":
        axis += pd.Timedelta(seconds=spec["timestep_seconds"])
    if spec["unit"] not in ("mm/step", "mm/h"):
        raise QCError("gridded unit must be mm/step or mm/h")
    values *= 1 if spec["unit"] == "mm/step" else spec["timestep_seconds"] / 3600
    if grid_crs != crs:
        converter = Transformer.from_crs(grid_crs, crs, always_xy=True).transform
        cells = {key: transform(converter, cell) for key, cell in cells.items()}
    rows = []
    weights = []
    for unit_id, polygon in units.items():
        weighted = np.zeros(len(axis), dtype=float)
        for key, cell in cells.items():
            area = polygon.intersection(cell).area
            if area > 0:
                source_id = f"r{key[0]}c{key[1]}"
                fraction = area / polygon.area
                weights.append((unit_id, source_id, fraction, area))
                vals = values[:, key[0], key[1]]
                if not np.isfinite(vals).all() or (vals < 0).any():
                    raise QCError(f"missing or invalid grid cell {source_id} at a model step")
                weighted += vals * fraction
        rows.extend((unit_id, time.isoformat(), value) for time, value in zip(axis, weighted))
    series = pd.DataFrame(rows, columns=["unit_id", "time", "value"])
    return series, weights, input_refs


def aggregate(args):
    config = json.loads(args.source_config.read_text(encoding="utf-8-sig"))
    if not isinstance(config.get("min_coverage_fraction"), (int, float)) or not 0 < config["min_coverage_fraction"] <= 1:
        raise QCError("min_coverage_fraction must be in (0,1]")
    if args.target not in ("basin", "subbasin"):
        raise QCError("target must be basin or subbasin")
    units, crs, topo_refs = topology(args)
    if args.target == "basin":
        units = {"basin": unary_union(list(units.values()))}
    expected = {"P": "P", "evap": "PET" if args.target == "basin" else "E0"}
    if set(config.get("layers", {})) != set(expected):
        raise QCError("source config requires P and evap layers")
    all_values, weight_rows, coverage_rows, inputs = {}, [], [], {"topology_result": reference(args.topology_result), "topology_qc_result": reference(args.topology_qc_result), "source_config": reference(args.source_config)}
    config_base = args.source_config.resolve().parent
    for layer_name, variable in expected.items():
        spec = config["layers"][layer_name]
        if spec.get("variable") != variable:
            raise QCError("PET and E0 conversion requires explicit matching variable")
        if spec["kind"] == "station":
            result_path = (config_base / spec["result"]).resolve()
            series, weights, refs = station_layer(result_path, spec, units, crs, config_base)
        else:
            series, weights, refs = grid_layer(spec, units, crs, config_base)
        inputs.update({f"{layer_name}_{key}": value for key, value in refs.items()})
        weight_frame = pd.DataFrame(weights, columns=["unit_id", "source_id", "weight", "area_m2"])
        if set(weight_frame.unit_id) != set(units):
            raise QCError("one or more model units lack forcing coverage")
        for unit_id, group in weight_frame.groupby("unit_id"):
            coverage = float(group.weight.sum())
            coverage_rows.append({"variable": variable, "unit_id": unit_id, "coverage_fraction": coverage,
                                  "covered_area_m2": float(group.area_m2.sum()), "unit_area_m2": float(units[unit_id].area)})
            if coverage + 1e-8 < config["min_coverage_fraction"] or coverage > 1 + 1e-7:
                raise QCError(f"{variable} coverage insufficient for {unit_id}: {coverage}")
        weight_frame["variable"] = variable
        weight_rows.append(weight_frame)
        if spec["kind"] == "station":
            if series.duplicated(["time", "source_id"]).any():
                raise QCError("duplicate forcing source/time")
            if not set(weight_frame.source_id) <= set(series.source_id):
                raise QCError("weighted source lacks forcing values")
            merged = weight_frame.merge(series, on="source_id", how="left", validate="many_to_many")
            if merged.value.isna().any() or not np.isfinite(merged.value).all() or (merged.value < 0).any():
                raise QCError("missing or invalid source at a model step")
            counts = merged.groupby(["unit_id", "time"]).size()
            if any(count != len(weight_frame[weight_frame.unit_id == unit]) for (unit, _), count in counts.items()):
                raise QCError("incomplete forcing coverage at a model step")
            merged["weighted"] = merged.weight * merged.value
            values_by_unit = merged.groupby(["unit_id", "time"], as_index=False).weighted.sum().rename(columns={"weighted": variable})
        else:
            if series.duplicated(["unit_id", "time"]).any() or not np.isfinite(series.value).all():
                raise QCError("invalid gridded unit/time aggregation")
            values_by_unit = series.rename(columns={"value": variable})
        all_values[variable] = values_by_unit
    output = all_values["P"].merge(all_values[expected["evap"]], on=["unit_id", "time"], validate="one_to_one")
    if len(output) != len(all_values["P"]) or len(output) != len(all_values[expected["evap"]]):
        raise QCError("precipitation and evaporation time axes differ")
    output.sort_values(["time", "unit_id"], inplace=True)
    prepare(args.output_dir, args.overwrite, OUTPUTS)
    if args.target == "basin":
        output = output[["time", "P", "PET"]]
        outfile = "basin_forcing.csv"
    else:
        output.rename(columns={"unit_id": "sub_id", "P": "P_mm", "E0": "E0_mm"}, inplace=True)
        output = output[["time", "sub_id", "P_mm", "E0_mm"]]
        outfile = "subbasin_forcing.csv"
    output.to_csv(args.output_dir / outfile, index=False)
    pd.concat(weight_rows).to_csv(args.output_dir / "weights.csv", index=False)
    pd.DataFrame(coverage_rows).to_csv(args.output_dir / "coverage.csv", index=False)
    artifacts = {key: reference(args.output_dir / value, args.output_dir) for key, value in {"forcing": outfile, "weights": "weights.csv", "coverage": "coverage.csv"}.items()}
    doc = {"schema_version": "1.0", "skill": SKILL, "status": "success", "message": "Forcing aggregated to model units", "parameters": {"target": args.target, **config}, "inputs": inputs, "artifacts": artifacts,
           "checks": [{"check": "topology_qc_and_hash", "status": "PASS", "details": "single outlet QC and artifact hashes checked"},
                      {"check": "coverage_and_time_axis", "status": "PASS", "details": f"rows={len(output)}"}], "warnings": [],
           "provenance": {"python": sys.version.split()[0], "rasterio": version("rasterio"), "shapely": version("shapely")}}
    write_result(args.output_dir / "result.json", doc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("topology-result", "topology-qc-result", "source-config", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--target", required=True, choices=["basin", "subbasin"])
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        aggregate(args)
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
