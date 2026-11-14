"""Run or re-score the whole protocol bench: the BP11-301 benchmark plus every constructed fixture.

    python scripts/kit_bench_all.py <run name> [--run] [--replay <earlier run name>] [--only id,id] [--hand]

  --run      analyse each protocol PDF with the kit pipeline first (spends model credits)
  --replay   re-run the pipeline on the model replies recorded in an earlier run (no credits)
  --hand     score the hand-written models instead (schedule engine only, no AI)
  (neither)  only compare the results already saved under that run name

Results are kept outside the repo, in KIT_BENCH_DIR (default D:/mtb_tmp/kit-bench):
<fixture>/<run name>/{calls, normalize, result.json, compare.json} and summary-<run name>.json.
The BP11-301 PDF is read from the path in its source-manifest.json and is skipped when absent.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import kit_bench  # noqa: E402
import kit_bench_compare  # noqa: E402

ROOT = Path(os.getenv("KIT_BENCH_DIR") or "D:/mtb_tmp/kit-bench")
DOCS = BACKEND.parent / "docs" / "protocol-benchmarks"


def cases() -> list[dict]:
    out = []
    manifest = json.loads((DOCS / "bp11-301-v2.1" / "source-manifest.json").read_text(encoding="utf-8"))
    out.append({"id": "bp11", "pdf": Path(manifest["local_source_path"]), "expected": DOCS / "bp11-301-v2.1" / "kit-expected.json",
                "hand": None})
    for folder in sorted((DOCS / "synthetic").iterdir()):
        if (folder / "expected.json").is_file():
            out.append({"id": folder.name, "pdf": folder / "protocol.pdf", "expected": folder / "expected.json",
                        "hand": folder / "hand-model.json"})
    return out


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        raise SystemExit(2)
    opt = lambda name: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None  # noqa: E731
    replay, only = opt("--replay"), opt("--only")
    args = [a for a in args if a not in (replay, only)]
    run = args[0]
    hand = "--hand" in sys.argv
    summary = []
    for case in cases():
        if only and case["id"] not in only.split(","):
            continue
        folder = ROOT / case["id"] / run
        if hand:
            if not case["hand"]:
                continue
            model = kit_bench_compare.load_model(case["hand"])
        else:
            if "--run" in sys.argv or replay:
                if not case["pdf"].is_file():
                    print(f"{case['id']}: protocol file not found ({case['pdf']}), skipped")
                    continue
                print(f"== {case['id']}: analysing", flush=True)
                try:
                    kit_bench.run(case["pdf"], folder, ROOT / case["id"] / replay if replay else None)
                except kit_bench.AnalysisError as error:
                    print(f"{case['id']}: analysis failed ({error.code}): {error}")
                    summary.append({"id": case["id"], "error": f"{error.code}: {error}"})
                    continue
            if not (folder / "result.json").is_file():
                print(f"{case['id']}: no result under {folder}, skipped")
                continue
            model = kit_bench_compare.load_model(folder)
        report = kit_bench_compare.compare(model, json.loads(case["expected"].read_text(encoding="utf-8")))
        if not hand:
            (folder / "compare.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        summary.append({"id": case["id"], "checks": report["checks"], "passed": report["passed"], "findings": report["findings"],
                        "open_points": len(model.get("open_points") or []), "notes": len(model.get("notes") or []),
                        "review_questions": len(model.get("review_questions") or []),
                        "cases": [{"name": c["name"], "checks": c["checks"], "passed": c["passed"], "findings": c["findings"]}
                                  for c in report["cases"]]})
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / f"summary-{run}{'-hand' if hand else ''}.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{'case':32} {'passed':>12}  findings")
    total = [0, 0]
    for s in summary:
        if "error" in s:
            print(f"{s['id']:32} {'-':>12}  {s['error']}")
            continue
        total[0] += s["passed"]
        total[1] += s["checks"]
        print(f"{s['id']:32} {s['passed']:>5}/{s['checks']:<6}  {s['findings'] or ''}")
    print(f"{'TOTAL':32} {total[0]:>5}/{total[1]:<6}  wrong or missing: {total[1] - total[0]}")
    if "--details" in sys.argv:
        for s in summary:
            for c in s.get("cases", []):
                for f in c["findings"]:
                    print(f"{s['id']} | {c['name'][:40]} | [{f['kind']}] {f['text']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    main()
