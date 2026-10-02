"""Prepare evidence for Agent prose, or finalize an evidence-bound study report."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

from _report_common import (ContractError, IntakeError, auxiliaries, check_report, clean_outputs,
                            collect, declared_inputs, output_dir, read_json, render, result, declared_inputs, validate, write_json)

SKILL = "generate-hydrological-study-report"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "finalize"))
    parser.add_argument("--study-manifest", type=Path, required=True)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    out = None
    try:
        if args.command == "finalize" and not args.report_json:
            raise ContractError("finalize requires --report-json")
        # Read before cleaning, but never overwrite a source or draft inside the destination.
        draft = read_json(args.report_json) if args.report_json else None
        manifest = read_json(args.study_manifest)
        source_paths = [args.study_manifest] + ([args.report_json] if args.report_json else [])
        source_paths += [(args.study_manifest.resolve().parent / s["result"]).resolve()
                         for s in manifest.get("sources", [])]
        # Protect artifact directories even with --overwrite.
        for source in source_paths[2 if args.report_json else 1:]:
            if source.is_file():
                try:
                    doc = read_json(source)
                    source_paths += [source.parent / r["path"] for r in doc.get("artifacts", {}).values() if isinstance(r, dict) and "path" in r]
                except (ValueError, TypeError, AttributeError):
                    # Intake will report the invalid source after a safe destination is prepared.
                    pass
        out = output_dir(args.output_dir, args.overwrite, source_paths + declared_inputs(args.study_manifest))
        clean_outputs(out)
        fresh = collect(args.study_manifest)
        report = fresh
        if args.command == "finalize":
            check_report(draft, fresh)
            report = draft
        validate(report, "study-report.schema.json")
        write_json(out / "report.json", report)
        auxiliaries(report, out)
        if args.command == "finalize":
            from _analysis_report import materialize_figures
            materialize_figures(report, out)
            (out / "report.md").write_text(render(report), encoding="utf-8")
        status = "warning" if report["gaps"] or report["warnings"] or args.command == "prepare" else "success"
        result(out, SKILL, status, "Evidence draft prepared; Agent prose required" if args.command == "prepare"
               else "Report rendered; independent semantic review required", report["warnings"],
               [{"check": "source_integrity", "status": "PASS", "details": "Original sources verified"},
                {"check": "evaluation_scope", "status": "PASS", "details": "Timezone-aware scored periods and complete event scopes verified"},
                {"check": "run_series_identity", "status": "PASS", "details": "Evaluation values bound to original run series; declared comparisons use identical samples"}])
        print(status)
        return 0
    except Exception as exc:
        if out is not None:
            clean_outputs(out)
            gaps = exc.gaps if isinstance(exc, IntakeError) else [{"source_id": "intake", "severity": "error", "reason": str(exc), "section": "all", "action": "Correct inputs and retry"}]
            write_json(out / "data-gaps.json", gaps)
            from _report_common import write_csv
            write_csv(out / "data-gaps.csv", gaps, ["source_id", "severity", "reason", "section", "action"])
            result(out, SKILL, "error", str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
