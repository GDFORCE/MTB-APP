"""python -m app.quality --corpus ... --predictions ... --output ...

Exit 0: selected mode passed (engineering never means clinical qualification).
Exit 1: quality or evidence gate failed. Exit 2: invalid/unreadable configuration.
"""
import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from .models import Corpus, Predictions, Thresholds, TrustedReviewer
from .compare import compare_reports
from .runner import evaluate


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--trusted-reviewers", type=Path)
    parser.add_argument("--mode", choices=["engineering", "clinical"], default="clinical")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, help="an earlier report; any regression against it fails the run")
    args = parser.parse_args(argv)

    def read(path):
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("quality input exceeds the configured 32 MiB engineering limit")
        return json.loads(path.read_text(encoding="utf-8"))

    try:
        trusted = {key: TrustedReviewer.model_validate(row) for key, row in
                   (read(args.trusted_reviewers) if args.trusted_reviewers else {}).items()}
        result = evaluate(Corpus.model_validate(read(args.corpus)),
                          Predictions.model_validate(read(args.predictions)), root=args.source_root,
                          mode=args.mode, thresholds=Thresholds.model_validate(read(args.thresholds)), trusted=trusted)
    except (OSError, ValueError, ValidationError) as error:
        # Do not print input contents from validation exceptions to CI logs.
        result = {"schema_version": "uctsm-quality.v1", "decision": "ERROR", "mode": args.mode,
                  "clinical_release_qualified": False, "reason_codes": ["INVALID_QUALITY_INPUT"],
                  "error_type": type(error).__name__}
    if args.baseline is not None and result["decision"] in {"PASS", "FAIL"}:
        try:
            comparison = compare_reports(read(args.baseline), result)
        except (OSError, ValueError) as error:
            comparison = {"regressed": True, "error_type": type(error).__name__,
                          "regressions": [{"kind": "BASELINE_UNREADABLE"}]}
        result["comparison"] = comparison
        if comparison["regressed"] and result["decision"] == "PASS":
            result["decision"] = "FAIL"
            result["reason_codes"] = sorted({*result["reason_codes"], "REGRESSION_AGAINST_BASELINE"})
            result["clinical_release_qualified"] = False
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": result["decision"], "mode": args.mode,
                      "clinical_release_qualified": result["clinical_release_qualified"],
                      "reason_codes": result.get("reason_codes", [])}))
    return {"PASS": 0, "FAIL": 1, "ERROR": 2}[result["decision"]]


if __name__ == "__main__":
    raise SystemExit(main())
