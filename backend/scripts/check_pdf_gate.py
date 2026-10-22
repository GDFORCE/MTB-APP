"""Run the documented backend gate in isolated processes, with required local Mongo.

This preserves TestClient isolation and produces an inspectable result for EVERY file;
known failures are not dropped to manufacture a green subset.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.persistence.mongodb.settings import MongoSettings, require_local


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mongo-url", default="mongodb://127.0.0.1:27017/?replicaSet=rs0")
    parser.add_argument("--include", nargs="*", default=[])
    args = parser.parse_args()
    require_local(MongoSettings(url=args.mongo_url, database="gate_settings_check"))
    handoff = (BACKEND.parent / "MTB_IMPLEMENTATION_HANDOFF.txt").read_text(encoding="utf-8")
    section = handoff.split("E. VERIFICATION", 1)[1].split("Frontend (", 1)[0]
    files = list(dict.fromkeys(re.findall(r"tests/[a-zA-Z0-9_]+\.py", section)))
    files += [name for name in args.include if name not in files]
    removed = {"tests/test_uctsm_extraction.py", "tests/test_uctsm_footnote_completeness.py"}
    args.output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "MONGO_URL": args.mongo_url, "UCTSM_TEST_MONGO_URL": args.mongo_url,
           "UCTSM_REQUIRE_MONGO": "1", "DB_NAME": "mtb_pdf_isolated_gate"}
    results = []; started = time.monotonic()
    for name in files:
        if not (BACKEND / name).is_file():
            results.append({"file": name, "decision": "DOCUMENTED_DELETED" if name in removed else "MISSING_REQUIRED"})
        else:
            log = args.output / (Path(name).stem + ".log")
            with log.open("w", encoding="utf-8") as stream:
                outcome = subprocess.run([sys.executable, "-m", "pytest", "-q", name, "--tb=short"],
                                         cwd=BACKEND, env=env, stdout=stream, stderr=subprocess.STDOUT)
            content = log.read_text(encoding="utf-8", errors="replace")
            summary = [row for row in content.splitlines() if re.search(r"\d+ (passed|failed|error)", row)]
            results.append({"file": name, "decision": "PASS" if outcome.returncode == 0 else "FAIL",
                            "exit_code": outcome.returncode, "log": log.name,
                            "summary": summary[-1] if summary else "No pytest summary"})
        print(json.dumps(results[-1]), flush=True)
        report = {"schema_version": "uctsm-gate.v1", "seconds": round(time.monotonic()-started, 2),
                  "decision": "FAIL" if any(row["decision"] in {"FAIL", "MISSING_REQUIRED"} for row in results) else "PASS",
                  "results": results, "complete": len(results) == len(files)}
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
