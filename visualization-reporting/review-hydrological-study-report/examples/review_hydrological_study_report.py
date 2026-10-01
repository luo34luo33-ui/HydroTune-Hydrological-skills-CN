"""Independently inspect original evidence and a generated study report package."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import sys

from _report_common import (ContractError, auxiliaries, check_report, clean_outputs, collect,
                            output_dir, read_json, render, result, sha, validate, write_csv, write_json, collect_legacy, declared_inputs)

SKILL = "review-hydrological-study-report"


def issue(category, location, actual, expected, severity="error", human=False):
    return {"category": category, "report_location": location, "evidence_location": location,
            "actual": actual, "expected": expected, "severity": severity,
            "action": "Inspect evidence and revise; finalize again without changing upstream data.",
            "needs_human_judgment": human}


def differences(actual, expected, location=""):
    if isinstance(actual, dict) and isinstance(expected, dict):
        for key in sorted(set(actual) | set(expected)):
            yield from differences(actual.get(key), expected.get(key), location + "/" + key)
    elif isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            yield issue("structure", location, len(actual), len(expected))
        for index, (a, e) in enumerate(zip(actual, expected)):
            yield from differences(a, e, location + f"/{index}")
    elif actual != expected:
        category = next((k for k in ("unit", "context", "definition", "sha256", "value", "split") if k in location), "source_consistency")
        yield issue(category, location, actual, expected)


def inspect(report_dir, manifest_path):
    report = read_json(report_dir / "report.json")
    validate(report, "processing-report.schema.json" if report.get("report_type") in ("timeseries", "spatial", "data-processing") else "study-report.schema.json")
    profile = {"timeseries": "timeseries", "spatial": "spatial", "simulation": "study", "data-processing": "data-processing"}.get(report.get("report_type"), "study")
    fresh = collect_legacy(manifest_path, profile, legacy_report=True) if report["schema_version"] == "1.0" else collect(manifest_path, profile)

    findings = []
    for key in ("study", "manifest", "runs", "sources", "evidence", "figures", "gaps", "warnings", "rounding") + (("analysis", "report_type") if report["schema_version"] == "2.0" else ()):
        items = list(differences(report[key], fresh[key], "/" + key))
        if key == "evidence":
            for item in items:
                parts = item["report_location"].split("/")
                if len(parts) > 2 and parts[2].isdigit() and int(parts[2]) < len(fresh["evidence"]):
                    original = fresh["evidence"][int(parts[2])]
                    item["evidence_location"] = original["artifact"] + "#" + original["locator"]
        findings.extend(items)
    actual_md = (report_dir / "report.md").read_text(encoding="utf-8")
    legacy_processing = report["schema_version"] == "1.0" and report.get("report_type") == "data-processing"
    if legacy_processing:
        validate(report, "processing-report-v1.schema.json")
        # Old fixed processing templates had no bound paragraphs. Preserve source/table
        # review, but do not pretend the old body has the v2 deterministic coverage.
        findings.append(issue("legacy_processing_body", "report.md", "legacy unbound fixed template",
                              "Compare the body manually or regenerate as a v2 report", "warning", True))
        expected_md = actual_md
    else:
        try:
            check_report(report, fresh)
        except ContractError as exc:
            findings.append(issue("narrative_binding", "/sections", str(exc), "Valid evidence and numeric bindings"))
        expected_md = render(report)
    if actual_md != expected_md:
        actual_lines, expected_lines = actual_md.splitlines(), expected_md.splitlines()
        for index in range(max(len(actual_lines), len(expected_lines))):
            a = actual_lines[index] if index < len(actual_lines) else None
            e = expected_lines[index] if index < len(expected_lines) else None
            if a != e:
                findings.append(issue("markdown_sync", f"report.md:{index + 1}", a, e))
                if len(findings) >= 100:
                    break
    with tempfile.TemporaryDirectory() as directory:
        expected_dir = Path(directory)
        auxiliaries({**fresh, "sections": report["sections"]}, expected_dir)
        for name in [p.name for p in expected_dir.iterdir() if p.is_file()]:
            target = report_dir / name
            if not target.is_file() or sha(target) != sha(expected_dir / name):
                findings.append(issue("table_consistency", name, sha(target) if target.is_file() else "missing", sha(expected_dir / name)))
    for figure in fresh["figures"]:
        path = report_dir / figure["path"]
        if not path.is_file() or sha(path) != figure["sha256"]:
            findings.append(issue("figure_integrity", figure["path"], sha(path) if path.is_file() else "missing", figure["sha256"]))
    package_files = [p for p in sorted(report_dir.rglob("*")) if p.is_file() and p.name != "result.json"]
    fingerprint = hashlib.sha256(json.dumps({"files": {p.relative_to(report_dir).as_posix(): sha(p) for p in package_files},
                                            "sources": fresh["sources"], "manifest": fresh["manifest"]},
                                           sort_keys=True).encode()).hexdigest()
    return report, findings, fingerprint


def semantic_check(report, fingerprint, path):
    paragraphs = [p for s in report["sections"] for p in s["paragraphs"]]
    if path is None:
        return [issue("semantic_review", "/sections", "not completed", "Independent Agent evidence assessment", "warning", True)]
    review = read_json(path)
    validate(review, "study-semantic-review.schema.json")
    if review["package_fingerprint"] != fingerprint:
        return [issue("semantic_review_stale", str(path), review["package_fingerprint"], fingerprint)]
    if not review["reviewer"].strip():
        raise ContractError("Semantic reviewer attribution is required")
    verdicts = review["paragraphs"]
    ids = [v["id"] for v in verdicts]
    if len(ids) != len(set(ids)) or set(ids) != {p["id"] for p in paragraphs}:
        raise ContractError("Semantic assessment must cover every paragraph exactly once")
    findings = []
    by_id = {p["id"]: p for p in paragraphs}
    for v in verdicts:
        if set(v["evidence_ids"]) != set(by_id[v["id"]]["evidence_ids"]):
            raise ContractError("Semantic review must inspect all cited paragraph evidence")
        if v["verdict"] != "supported":
            findings.append(issue("semantic_support", "/paragraph/" + v["id"], v["rationale"],
                                  "Evidence supports wording, split, limitations and inference strength",
                                  "error" if v["verdict"] == "unsupported" else "warning", v["verdict"] == "uncertain"))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    manifests = parser.add_mutually_exclusive_group(required=True)
    manifests.add_argument("--study-manifest", type=Path)
    manifests.add_argument("--processing-manifest", type=Path)
    manifests.add_argument("--spatial-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--semantic-review", type=Path, help="Independent Agent assessment bound to the package fingerprint")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    args.study_manifest = args.study_manifest or args.processing_manifest or args.spatial_manifest
    out = None
    try:
        report_dir = args.report_dir.resolve()
        inputs = [report_dir, args.study_manifest] + ([args.semantic_review] if args.semantic_review else [])
        manifest = read_json(args.study_manifest)
        inputs += [(args.study_manifest.resolve().parent / s["result"]).resolve() for s in manifest.get("sources", [])]
        out = output_dir(args.output_dir, args.overwrite, inputs + declared_inputs(args.study_manifest))
        if report_dir in out.parents:
            raise ContractError("Review output must be outside the report package")
        clean_outputs(out)
        report, findings, fingerprint = inspect(report_dir, args.study_manifest)
        findings += semantic_check(report, fingerprint, args.semantic_review)
        status = "needs_revision" if any(f["severity"] == "error" for f in findings) else (
            "needs_human_review" if any(f["needs_human_judgment"] for f in findings) else "pass")
        review = {"schema_version": "1.0", "status": status, "package_fingerprint": fingerprint,
                  "semantic_review_completed": args.semantic_review is not None and not any(f["category"] == "semantic_review_stale" for f in findings),
                  "findings": findings, "checks": ["original_sources", "report_values_units_context", "markdown", "tables", "figures", "narrative_bindings"]}
        validate(review, "study-review.schema.json")
        write_json(out / "review.json", review)
        write_csv(out / "review-checklist.csv", findings, ["category", "report_location", "evidence_location", "actual", "expected", "severity", "action", "needs_human_judgment"])
        lines = ["# 独立审查 / Independent review", "", status, ""]
        lines += [f"- {f['category']} · {f['report_location']}: {f['actual']} → {f['expected']}" for f in findings]
        (out / "review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        result(out, SKILL, "success" if status == "pass" else "warning", status)
        print(status)
        return 0 if status == "pass" else 2
    except Exception as exc:
        if out is not None:
            result(out, SKILL, "error", str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
