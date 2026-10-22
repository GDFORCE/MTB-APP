"""Build the release manifest and fail on any blocking issue (P13-05). Deploys nothing."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.release.manifest import blocking_issues, build_manifest  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quality-report", type=Path, help="report.json from python -m app.quality")
    parser.add_argument("--release-kind", choices=["engineering", "clinical"], default="engineering")
    parser.add_argument("--approve-flag", action="append", default=[], help="a flag a named approver allowed ON")
    args = parser.parse_args()
    manifest = build_manifest(gate_report=args.gate_report, quality_report=args.quality_report,
                              release_kind=args.release_kind)
    manifest["blocking_issues"] = blocking_issues(manifest, approved_flags=frozenset(args.approve_flag))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(args.output), "blocking_issues": manifest["blocking_issues"]}))
    return 1 if manifest["blocking_issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
