"""Offline report facts, schemas and rendering; packaged in reporting skills."""
from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys

from jsonschema import Draft202012Validator

ASSETS = Path(__file__).resolve().parents[1] / "assets"
SECTIONS = ["overview", "preprocessing", "spatial", "simulation", "calibration",
            "evaluation", "discussion", "limitations", "attachments"]
TITLES = {
    "zh": ["研究概况", "资料与预处理", "空间基础", "模型与模拟", "率定", "验证评价",
           "综合讨论", "限制与建议", "证据与附件"],
    "en": ["Study overview", "Data and preprocessing", "Spatial foundation", "Model and simulation",
           "Calibration", "Evaluation", "Discussion", "Limitations and recommendations", "Evidence and attachments"],
}
FILES = ["report.md", "report.json", "summary.csv", "evidence-index.json", "evidence-index.csv",
         "data-gaps.json", "data-gaps.csv", "result.json", "review.md", "review.json", "review-checklist.csv",
         "source-inventory.csv", "qc-checklist.csv", "events.csv", "subbasins.csv", "reaches.csv", "topology.csv", "worst-events.csv", "analysis-tables.json"]
PROCESSING_SECTIONS = ["overview", "source-inventory", "timeseries", "events", "spatial", "qc", "limitations", "attachments"]


class ContractError(ValueError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      parse_constant=lambda x: (_ for _ in ()).throw(ContractError(f"Nonfinite JSON: {x}")))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                                    allow_nan=False) + "\n", encoding="utf-8")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ref(path, base=None):
    path = Path(path).resolve()
    try:
        name = path.relative_to(base.resolve()).as_posix() if base else str(path)
    except ValueError:
        name = str(path)
    return {"path": name, "sha256": sha(path)}


def resolve(path, base):
    return (Path(base) / path).resolve()


def validate(value, schema):
    errors = sorted(Draft202012Validator(read_json(ASSETS / schema)).iter_errors(value),
                    key=lambda e: str(e.path))
    if errors:
        raise ContractError(f"{schema}: {list(errors[0].path)}: {errors[0].message}")


def output_dir(path, overwrite, inputs=()):
    path = Path(path).resolve()
    for source in inputs:
        source = Path(source).resolve()
        if path == source or path in source.parents:
            raise ContractError("Output directory must not contain any input")
    if path.exists() and any(path.iterdir()) and not overwrite:
        raise ContractError("Output directory is nonempty; use --overwrite")
    path.mkdir(parents=True, exist_ok=True)
    return path


def declared_inputs(manifest_path):
    """Protect original results, artifacts and explicitly linked diagnostic processes."""
    manifest_path = Path(manifest_path).resolve()
    manifest = read_json(manifest_path)
    paths = [manifest_path]
    for source in manifest.get("sources", []):
        path = resolve(source["result"], manifest_path.parent)
        paths.append(path)
        if not path.is_file():
            continue
        try:
            doc = read_json(path)
            for field in ("artifacts", "inputs"):
                for reference in doc.get(field, {}).values():
                    if isinstance(reference, dict) and "path" in reference:
                        paths.append(resolve(reference["path"], path.parent))
        except (ValueError, TypeError, AttributeError):
            pass  # Intake reports the malformed source after destination safety checks.
    paths.extend(resolve(r["series"]["path"], manifest_path.parent)
                 for r in manifest.get("runs", []) if "series" in r)
    paths.extend(resolve(p["path"], manifest_path.parent)
                 for p in manifest.get("analysis", {}).get("event_processes", []))
    return paths


def clean_outputs(path):
    # Never recursively delete user files. The dedicated figure directory retains unused files.
    for name in FILES:
        target = path / name
        if target.is_symlink():
            raise ContractError(f"Output symlink refused: {target}")
        if target.is_file():
            target.unlink()


def write_csv(path, rows, fields):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (dict, list))
                             else v for k, v in row.items()})


def scalar_rows(value, pointer=""):
    if isinstance(value, dict):
        for key in sorted(value):
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from scalar_rows(value[key], pointer + "/" + escaped)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from scalar_rows(item, pointer + f"/{index}")
    else:
        yield pointer, value


def tables(path):
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return {"": list(csv.DictReader(handle))}
    if path.suffix.lower() in (".xlsx", ".parquet"):
        try:
            import pandas as pd
            frames = pd.read_excel(path, sheet_name=None) if path.suffix.lower() == ".xlsx" else {"": pd.read_parquet(path)}
            # Preserve strings and identifiers; represent genuine missing cells explicitly.
            return {str(sheet): json.loads(frame.to_json(orient="records", date_format="iso"))
                    for sheet, frame in frames.items()}
        except ImportError as exc:
            raise ContractError("XLSX/Parquet input needs the timeseries optional dependencies") from exc
    return {}


def numeric(value):
    if isinstance(value, bool) or value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def display(value, digits=6):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return f"{float(value):.{digits}f}" if numeric(value) else str(value)


def gap(source, severity, reason):
    return {"source_id": source, "severity": severity, "reason": reason,
            "section": source, "action": "Provide or correct the upstream evidence; regenerate and review."}


class IntakeError(ContractError):
    def __init__(self, gaps):
        self.gaps = gaps
        super().__init__("; ".join(item["reason"] for item in gaps if item["severity"] == "error"))


def collect_legacy(manifest_path, profile="study", legacy_report=False):
    """Rebuild facts from original results; no report files or generator checks are trusted."""
    manifest_path = Path(manifest_path).resolve()
    manifest = read_json(manifest_path)
    processing = profile in ("data-processing", "timeseries", "spatial")
    if profile not in ("study", "data-processing", "timeseries", "spatial"):
        raise ContractError("Unknown reporting profile")
    if not processing and manifest.get("schema_version") != "2.0":
        raise ContractError("Study manifest requires schema_version=2.0; migrate split_periods and runs name/kind/series bindings")
    validate(manifest, ("spatial-processing-manifest.schema.json" if profile == "spatial" else "processing-manifest.schema.json") if processing else "study-manifest.schema.json")
    catalog = read_json(ASSETS / "upstream-catalog.json")
    ids = [s["id"] for s in manifest["sources"]]
    runs = manifest.get("runs", []) if processing else manifest["runs"]
    run_ids = [r["id"] for r in runs]
    if len(ids) != len(set(ids)) or len(run_ids) != len(set(run_ids)):
        raise ContractError("Duplicate source/run IDs")
    sources = {s["id"]: s for s in manifest["sources"]}
    required = set(manifest["required_sources"] if processing else manifest.get("required_preprocessing", []))
    if processing:
        if not required or any(x not in sources for x in required) or not any(sources[x]["stage"] in ("preprocessing", "spatial") for x in required):
            raise ContractError("required_sources must include at least one declared data/spatial processing source")
    elif any(x not in sources or sources[x]["stage"] != "preprocessing" for x in required):
        raise ContractError("required_preprocessing must reference declared preprocessing sources")
    owners = {}
    for run in runs:
        bindings = [(run["simulation"], "postprocessing" if run.get("kind") == "corrected" else "simulation")] + [(x, "evaluation") for x in run["evaluations"]]
        if run.get("calibration"):
            bindings.append((run["calibration"], "calibration"))
        for sid, stage in bindings:
            if sid not in sources or sources[sid]["stage"] != stage or sid in owners:
                raise ContractError(f"Ambiguous or invalid run binding: {sid}")
            owners[sid] = run["id"]
            if stage != "calibration":
                required.add(sid)
    for sid, source in sources.items():
        if source["stage"] in ("simulation", "postprocessing", "evaluation", "calibration") and sid not in owners:
            raise ContractError(f"Unbound run source: {sid}")
        if source.get("run_id"):
            if source["run_id"] not in run_ids or sid in owners and owners[sid] != source["run_id"]:
                raise ContractError(f"Conflicting source run_id: {sid}")
            owners[sid] = source["run_id"]
    evidence, figures, gaps, warnings = [], [], [], []
    hashes = {}
    def digest(path):
        key = str(path)
        if key not in hashes:
            hashes[key] = sha(path)
        return hashes[key]
    snapshots = []

    def add(s, path, pointer, value, kind, unit="unspecified", definition="", context=None):
        sid = s["id"]
        identity = f"{sid}|{path}|{pointer}"
        eid = "ev-" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        effective_context = dict(context if context is not None else s.get("context", {}))
        if s["stage"] == "calibration":
            for split in ("calibration", "validation"):
                if Path(path).name.startswith(split + "_"):
                    effective_context["split"] = split
        evidence.append({"id": eid, "source_id": sid, "run_id": owners.get(sid, ""),
                         "stage": s["stage"], "source_result": str(resolve(s["result"], manifest_path.parent)),
                         "artifact": str(path), "sha256": digest(path), "locator": pointer,
                         "kind": kind, "value": value, "unit": unit, "definition": definition,
                         "context": effective_context,
                         "availability": "unavailable" if value is None or value == "unavailable" else "available"})
        return eid

    for source in manifest["sources"]:
        sid = source["id"]
        result_path = resolve(source["result"], manifest_path.parent)
        try:
            result = read_json(result_path)
            skill = result.get("skill")
            if skill not in catalog or catalog[skill]["stage"] != source["stage"]:
                raise ContractError(f"Unknown or incompatible upstream skill: {skill}")
            if str(result.get("schema_version")) != "1.0":
                raise ContractError("Unsupported upstream schema_version")
            for field, typ in (("parameters", dict), ("artifacts", dict), ("inputs", dict),
                               ("checks", list), ("warnings", list), ("provenance", dict)):
                if not isinstance(result.get(field), typ):
                    raise ContractError(f"Invalid upstream field: {field}")
            if result.get("status") not in ("success", "warning", "error"):
                raise ContractError("Invalid upstream status")
            if result["status"] == "error" or any(c.get("status") == "FAIL" for c in result["checks"]):
                raise ContractError("Upstream error or scientific QC FAIL")
            if not result["artifacts"]:
                raise ContractError("Upstream result contains no scientific artifacts")
            if source.get("context", {}).get("mode") and result.get("data_shape") and source["context"]["mode"] != result["data_shape"]:
                raise ContractError("Manifest mode contradicts upstream data_shape")
            for field in ("timestep_hours", "warmup_steps"):
                if field in source.get("context", {}) and field in result["parameters"] and source["context"][field] != result["parameters"][field]:
                    raise ContractError(f"Manifest context contradicts upstream {field}")
            artifacts = {}
            for key, reference in result["artifacts"].items():
                if not isinstance(reference, dict) or not {"path", "sha256"} <= reference.keys():
                    raise ContractError(f"Invalid artifact reference: {key}")
                path = resolve(reference["path"], result_path.parent)
                if digest(path) != reference["sha256"]:
                    raise ContractError(f"Artifact hash mismatch: {key}")
                artifacts[key] = path
            # All declared existing sources must be trustworthy, even when the stage is optional.
            source_warnings = [str(w) for w in result["warnings"]]
            if result["status"] == "warning":
                source_warnings.append("upstream status=warning")
            warnings.extend(f"{sid}: {w}" for w in source_warnings)
            if source["stage"] == "evaluation":
                if not {"mode", "split"} <= source.get("context", {}).keys():
                    raise ContractError("Evaluation source requires explicit mode and split context")
                # Scope and identity are checked against scored input by the
                # shared strict validator below, not inferred from labels.
                ctx = source["context"]
                if ctx["mode"] == "continuous" and "period" not in ctx:
                    raise ContractError("Continuous evaluation requires an explicit period")
                if ctx["mode"] == "event_collection" and not ctx.get("event_ids"):
                    raise ContractError("Event evaluation requires explicit event_ids")
                for input_key, reference in result["inputs"].items():
                    if not isinstance(reference, dict) or not {"path", "sha256"} <= reference.keys():
                        continue
                    input_path = resolve(reference["path"], result_path.parent)
                    if digest(input_path) != reference["sha256"]:
                        raise ContractError(f"Evaluation input hash mismatch: {input_key}")
                    for sheet, rows in tables(input_path).items():
                        pointer = "/evaluation-inputs/" + input_key + "/" + sheet
                        add(source, input_path, pointer + "/row_count", len(rows), "table_metadata", "count")
                        time_col = result["parameters"].get("time_column", "time")
                        times = [str(r[time_col]) for r in rows if r.get(time_col) is not None]
                        if times:
                            start, end = min(times), max(times)
                            add(source, input_path, pointer + "/start", start, "table_metadata")
                            add(source, input_path, pointer + "/end", end, "table_metadata")
                            if ctx["mode"] == "continuous" and manifest["schema_version"] != "2.0":
                                for field, actual in (("start", start), ("end", end)):
                                    if datetime.fromisoformat(ctx["period"][field].replace("Z", "+00:00")) != datetime.fromisoformat(actual.replace("Z", "+00:00")):
                                        raise ContractError(f"Declared evaluation {field} contradicts hashed input")
                        if ctx["mode"] == "event_collection" and rows:
                            event_col = result["parameters"].get("event_id_column", "event_id")
                            observed_ids = {str(r[event_col]) for r in rows if event_col in r}
                            if not observed_ids and sheet:
                                observed_ids = {sheet}
                            if observed_ids and not observed_ids <= set(ctx["event_ids"]):
                                raise ContractError("Declared event IDs contradict hashed input")
            snapshots.append({"id": sid, "skill": skill, "result": ref(result_path),
                              "status": result["status"], "context": source.get("context", {})})
            if processing:
                snapshots[-1].update({"inputs": result["inputs"], "parameters": result["parameters"], "artifacts": result["artifacts"],
                                      "checks": result["checks"], "warnings": result["warnings"],
                                      "provenance": result["provenance"]})
                if source["stage"] != "atlas" and not result["checks"]:
                    gaps.append(gap(sid, "warning", "Upstream QC checks not provided / 未提供上游 QC 检查证据"))
            for field in ("parameters", "checks", "warnings"):
                for pointer, value in scalar_rows(result[field], "/" + field):
                    name = pointer.rsplit("/", 1)[-1]
                    spec = catalog[skill]["variables"].get(name, {})
                    add(source, result_path, pointer, value, field, spec.get("units", "unspecified"), spec.get("semantics", ""))
            for key, path in sorted(artifacts.items()):
                units = catalog[skill]["variables"]
                if path.suffix.lower() in (".png", ".svg"):
                    eid = add(source, result_path, "/artifacts/" + key, result["artifacts"][key], "figure")
                    filename = f"{sid}-{hashlib.sha256(key.encode()).hexdigest()[:12]}{path.suffix.lower()}"
                    figures.append({"id": eid, "source": str(path), "path": "figures/" + filename,
                                    "sha256": digest(path), "context": source.get("context", {})})
                elif path.suffix.lower() == ".json":
                    document = read_json(path)
                    # JSON artifact geometry is inventoried, not copied into prose tables.
                    if isinstance(document, dict) and document.get("type") in ("FeatureCollection", "Feature"):
                        add(source, path, "", {"type": document["type"]}, "artifact")
                        continue
                    context = dict(source.get("context", {}))
                    if isinstance(document, dict) and "split" in document:
                        if source["stage"] == "evaluation" and source.get("context", {}).get("split") != document["split"]:
                            raise ContractError("Manifest split contradicts artifact split")
                        context["split"] = document["split"]
                    for pointer, value in scalar_rows(document):
                        name = pointer.rsplit("/", 1)[-1]
                        spec = units.get(name, {})
                        add(source, path, pointer, value, "artifact", spec.get("units", "unspecified"), spec.get("semantics", ""), context)
                elif path.suffix.lower() in (".csv", ".xlsx", ".parquet"):
                    artifact_tables = tables(path)
                    if processing and key in artifact_tables:
                        artifact_tables = {key: artifact_tables[key]}
                    for sheet, rows in artifact_tables.items():
                        prefix = "/sheets/" + sheet if sheet else ""
                        add(source, path, prefix + "/row_count", len(rows), "table_metadata", "count")
                        # Time series remain attachments: summarize coverage, never recalculate performance.
                        is_metrics = "metric" in key or "parameter" in key or "index" in key or "summary" in key
                        if is_metrics:
                            if source["stage"] == "evaluation" and source["context"]["mode"] == "event_collection":
                                event_col = result["parameters"].get("event_id_column", "event_id")
                                event_ids = {str(r[event_col]) for r in rows if event_col in r}
                                if event_ids and event_ids != set(source["context"]["event_ids"]):
                                    raise ContractError("Declared event IDs differ from upstream metric rows")
                            for index, row in enumerate(rows):
                                context = dict(source.get("context", {}))
                                if source["stage"] == "evaluation" and "split" in row and row["split"] != context["split"]:
                                    raise ContractError("Declared split differs from upstream metric row")
                                context.update({k: row[k] for k in ("event_id", "split", "start_time", "end_time", "rows", "scored", "is_warmup") if k in row})
                                for name, value in row.items():
                                    metric = row.get("metric", name) if name == "value" else name
                                    spec = units.get(metric, {})
                                    unit = row.get("unit", spec.get("units", "unspecified"))
                                    if name == "value" and "unit" in row and spec.get("units") and row["unit"] != spec["units"]:
                                        raise ContractError(f"Metric unit contradicts upstream contract: {metric}")
                                    if name == "value" and "unit" in row and spec.get("units") and row["unit"] != spec["units"]:
                                        raise ContractError(f"Metric unit contradicts upstream contract: {metric}")
                                    if metric in ("observed_volume", "simulated_volume"):
                                        unit = result["parameters"].get("volume_unit", unit)
                                    add(source, path, prefix + f"/rows/{index}/{name}", value, "table_value", unit,
                                        spec.get("semantics", ""), context)
                        else:
                            headers = list(rows[0]) if rows else []
                            add(source, path, prefix + "/columns", headers, "table_metadata")
                            time_col = result["parameters"].get("time_column", "time")
                            time_values = [str(r[time_col]) for r in rows if r.get(time_col) is not None]
                            if time_values:
                                add(source, path, prefix + "/start", min(time_values), "table_metadata")
                                add(source, path, prefix + "/end", max(time_values), "table_metadata")
                            for flag in ("scored", "is_warmup"):
                                if flag in headers:
                                    counts = {str(v): sum(str(r.get(flag)) == str(v) for r in rows)
                                              for v in sorted({str(r.get(flag)) for r in rows})}
                                    add(source, path, prefix + "/" + flag, counts, "table_metadata", "count")
                            if "event_id" in headers:
                                add(source, path, prefix + "/event_ids", sorted({str(r["event_id"]) for r in rows}), "table_metadata")
                else:
                    add(source, result_path, "/artifacts/" + key, result["artifacts"][key], "artifact_reference")
        except FileNotFoundError as exc:
            gaps.append(gap(sid, "error" if sid in required else "warning", str(exc)))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            gaps.append(gap(sid, "error", str(exc)))
    for stage in (("spatial", "calibration", "atlas") if legacy_report and not processing else ()):
        if not any(s["stage"] == stage for s in manifest["sources"]):
            gaps.append(gap(stage, "warning", "Not provided / 未提供"))
    if any(g["severity"] == "error" for g in gaps):
        raise IntakeError(gaps)
    if not processing:
        from _study_validation import validate_study
        validate_study(manifest, manifest_path)
    report = {"schema_version": "1.0", "study": {"title": manifest["title"], "language": manifest.get("language", "zh")},
            "manifest": ref(manifest_path), "runs": runs, "sources": snapshots,
            "evidence": evidence, "figures": figures, "gaps": gaps, "warnings": warnings,
            "rounding": {"decimal_places": 6},
            "sections": [{"id": sid, "paragraphs": []} for sid in (PROCESSING_SECTIONS if processing else SECTIONS)],
            "narrative_provenance": {"author": "", "method": "agent", "completed": False}}
    if processing:
        report["report_type"] = "data-processing"
        report["narrative_provenance"] = {"author": "hydrotune.reporting.v1", "method": "template", "completed": True}
    return report


def check_report_legacy(report, fresh):
    validate(report, "study-report-v1.schema.json")
    for key in ("study", "manifest", "runs", "sources", "evidence", "figures", "gaps", "warnings", "rounding"):
        if report[key] != fresh[key]:
            raise ContractError(f"Immutable evidence differs from original sources: {key}")
    if [s["id"] for s in report["sections"]] != SECTIONS:
        raise ContractError("Section IDs/order must match the fixed template")
    if not report["narrative_provenance"]["completed"] or not report["narrative_provenance"]["author"].strip():
        raise ContractError("Agent narrative must be completed and attributed")
    for sid in ("overview", "evaluation", "discussion", "limitations"):
        if not next(s for s in report["sections"] if s["id"] == sid)["paragraphs"]:
            raise ContractError(f"Agent narrative required in section: {sid}")
    ids = set()
    by_id = {e["id"]: e for e in fresh["evidence"]}
    for section in report["sections"]:
        for paragraph in section["paragraphs"]:
            if paragraph["id"] in ids:
                raise ContractError("Duplicate paragraph ID")
            ids.add(paragraph["id"])
            if any(x not in by_id for x in paragraph["evidence_ids"]):
                raise ContractError(f"Unknown evidence in {paragraph['id']}")
            if paragraph["kind"] != "pending" and not paragraph["evidence_ids"]:
                raise ContractError(f"Unreferenced paragraph: {paragraph['id']}")
            for binding in paragraph["numeric_bindings"]:
                ev = by_id.get(binding["evidence_id"])
                if not ev or binding["evidence_id"] not in paragraph["evidence_ids"]:
                    raise ContractError("Numeric binding must reference paragraph evidence")
                if not numeric(ev["value"]) or binding["text"] != display(ev["value"], 6) or binding["text"] not in paragraph["text"]:
                    raise ContractError(f"Numeric binding mismatch: {paragraph['id']}")
            # Exclude stable evidence tokens; every prose number needs an explicit value binding.
            tokens = re.findall(r"(?<![\w])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", paragraph["text"])
            bound = [b["text"] for b in paragraph["numeric_bindings"]]
            if any(t not in bound for t in tokens):
                raise ContractError(f"Unbound numeric text in {paragraph['id']}; use exact six-place bindings or words")


def summary_rows(report):
    skills = {s["id"]: s["skill"] for s in report["sources"]}
    models = {r["id"]: skills[r["simulation"]] for r in report["runs"]}
    return [{"evidence_id": e["id"], "source_id": e["source_id"], "run_id": e["run_id"],
             "model": models.get(e["run_id"], ""), "source_skill": skills[e["source_id"]],
             "kind": e["kind"], "locator": e["locator"], "value": e["value"],
             "display_value": str(e["value"]) if e["locator"].rsplit("/", 1)[-1] in ("event_id", "time", "start_time", "end_time", "metric", "split") else display(e["value"]), "unit": e["unit"], "definition": e["definition"],
             "context": e["context"], "availability": e["availability"]}
            for e in report["evidence"] if e["kind"] in ("parameters", "table_value", "artifact", "table_metadata")]


def md(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_legacy(report):
    en = report["study"]["language"] == "en"
    lines = [f"# {md(report['study']['title'])}", "",
             "Evidence-bound draft; independent review required." if en else "证据绑定研究报告；需独立审查。", ""]
    stages = {e["id"]: e["stage"] for e in report["evidence"]}
    summary = summary_rows(report)
    for sid, title in zip(SECTIONS, TITLES[report["study"]["language"]]):
        lines += [f"<!-- section:{sid} -->", f"## {title}", ""]
        section = next(s for s in report["sections"] if s["id"] == sid)
        for p in section["paragraphs"]:
            lines += [f"<!-- paragraph:{p['id']} kind:{p['kind']} -->", p["text"],
                      "", "Evidence: " + ", ".join(p["evidence_ids"]), ""]
        if sid == "overview":
            lines += ["```json", json.dumps(report["runs"], ensure_ascii=False, sort_keys=True, indent=2), "```", ""]
            lines += ["| Source | Skill | Status | Context |", "|---|---|---|---|"]
            for source in report["sources"]:
                lines += ["| " + " | ".join(md(source[k]) for k in ("id", "skill", "status", "context")) + " |"]
            lines.append("")
        stage = {"preprocessing": "preprocessing", "spatial": "spatial", "simulation": "simulation",
                 "calibration": "calibration", "evaluation": "evaluation"}.get(sid)
        rows = [r for r in summary if stages[r["evidence_id"]] == stage] if stage else []
        if rows:
            lines += [f"Table {SECTIONS.index(sid) + 1} / 表 {SECTIONS.index(sid) + 1}", "",
                      "| Evidence | Run | Field | Value | Unit | Definition | Context | Availability |",
                      "|---|---|---|---|---|---|---|---|"]
            for r in rows:
                lines.append("| " + " | ".join(md(r[k]) for k in ("evidence_id", "run_id", "locator", "display_value", "unit", "definition", "context", "availability")) + " |")
            lines.append("")
        elif stage:
            lines += ["Not provided / 未提供", ""]
        if sid == "limitations":
            lines += ["- " + md(w) for w in report["warnings"]]
            lines += ["- " + md(g["source_id"] + ": " + g["reason"]) for g in report["gaps"]]
            lines.append("")
        if sid == "attachments":
            for index, f in enumerate(report["figures"], 1):
                lines += [f"Figure {index} / 图 {index}: {f['id']}", "", f"![{f['id']}]({f['path']})", ""]
            lines += ["[Evidence index](evidence-index.csv)", "", "[Summary](summary.csv)", ""]
    return "\n".join(lines) + "\n"


def auxiliaries_legacy(report, out):
    rows = summary_rows(report)
    write_csv(out / "summary.csv", rows, ["evidence_id", "source_id", "run_id", "model", "source_skill", "kind", "locator", "value", "display_value", "unit", "definition", "context", "availability"])
    for name, key, fields in (("evidence-index", "evidence", ["id", "source_id", "run_id", "stage", "source_result", "artifact", "sha256", "locator", "kind", "value", "unit", "definition", "context", "availability", "referenced_by"]),
                              ("data-gaps", "gaps", ["source_id", "severity", "reason", "section", "action"])):
        records = report[key]
        if key == "evidence":
            records = [{**e, "referenced_by": [p["id"] for s in report["sections"] for p in s["paragraphs"] if e["id"] in p["evidence_ids"]]} for e in records]
        write_json(out / (name + ".json"), records)
        write_csv(out / (name + ".csv"), records, fields)


def result(out, skill, status, message, warnings=(), checks=()):
    document = {"schema_version": "1.0", "skill": "visualization-reporting/" + skill,
                "status": status, "message": message, "parameters": {}, "inputs": {},
                "artifacts": {p.relative_to(out).as_posix(): ref(p, out) for p in sorted(out.iterdir())
                              if p.is_file() and p.name != "result.json" and p.name in FILES},
                "warnings": list(warnings), "checks": list(checks), "provenance": {"python": sys.version.split()[0], "runtime": "hydrotune.reporting.v1"}}
    if (out / "report.json").is_file():
        report = read_json(out / "report.json")
        manifest_key = "processing_manifest" if report.get("report_type") in ("data-processing", "timeseries", "spatial") else "study_manifest"
        document["inputs"] = {manifest_key: report["manifest"]}
        document["inputs"].update({s["id"]: s["result"] for s in report["sources"]})
        for figure in report["figures"]:
            path = out / figure["path"]
            if path.is_file():
                document["artifacts"][figure["path"]] = ref(path, out)
    write_json(out / "result.json", document)


def collect(manifest_path, profile="study"):
    from _analysis_report import enrich
    return enrich(collect_legacy(manifest_path, profile), manifest_path, profile)


def check_report(report, fresh):
    if report.get("schema_version") == "1.0":
        return check_report_legacy(report, fresh)
    from _analysis_report import check_narrative
    check_narrative(report, fresh)


def render(report):
    if report.get("schema_version") == "1.0":
        return render_legacy(report)
    from _analysis_report import render_analysis
    return render_analysis(report)


def auxiliaries(report, out):
    auxiliaries_legacy(report, out)
    if report.get("schema_version") == "2.0":
        from _analysis_report import write_business
        write_business(report, out)
