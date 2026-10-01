"""Generate a standalone, evidence-only data-processing results report."""
import argparse
from pathlib import Path
import shutil
import sys

from _report_common import (IntakeError, auxiliaries, clean_outputs, collect, declared_inputs, output_dir, read_json,
                            result, declared_inputs, validate, write_csv, write_json)
from _analysis_report import render_analysis, materialize_figures

SKILL = "generate-timeseries-data-processing-report"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processing-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    out = None
    try:
        manifest = read_json(args.processing_manifest)
        inputs = [args.processing_manifest]
        source_paths = [(args.processing_manifest.resolve().parent / s["result"]).resolve() for s in manifest.get("sources", [])]
        inputs.extend(source_paths)
        for source in source_paths:
            if source.is_file():
                try:
                    doc = read_json(source)
                    inputs.extend(source.parent / r["path"] for r in doc.get("artifacts", {}).values()
                                  if isinstance(r, dict) and "path" in r)
                except (ValueError, TypeError, AttributeError):
                    pass
        out = output_dir(args.output_dir, args.overwrite, inputs + declared_inputs(args.processing_manifest))
        clean_outputs(out)
        report = collect(args.processing_manifest, profile="timeseries")
        validate(report, "processing-report.schema.json")
        write_json(out / "report.json", report)
        auxiliaries(report, out)
        materialize_figures(report, out)
        (out / "report.md").write_text(render_analysis(report), encoding="utf-8")
        status = "warning" if report["warnings"] or report["gaps"] else "success"
        result(out, SKILL, status, "Data-processing report generated from verified upstream outputs", report["warnings"],
               [{"check": "source_integrity", "status": "PASS", "details": "Declared outputs and results verified"}])
        print(status)
        return 0
    except Exception as exc:
        if out is not None:
            clean_outputs(out)
            gaps = exc.gaps if isinstance(exc, IntakeError) else [{"source_id": "intake", "severity": "error",
                    "reason": str(exc), "section": "all", "action": "Correct inputs and retry"}]
            write_json(out / "data-gaps.json", gaps)
            write_csv(out / "data-gaps.csv", gaps, ["source_id", "severity", "reason", "section", "action"])
            result(out, SKILL, "error", str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
