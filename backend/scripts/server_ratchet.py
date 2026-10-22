"""server.py ratchet (P12-05): the monolith may shrink, never grow.

New scheduling business logic belongs in app/application, app/domain or app/api, not here. This script records
a baseline (size, function count, scheduling-related function names) and `--check` fails when server.py grows
past it or gains a NEW scheduling-related function. Lowering the baseline is done by deleting code and running
`python scripts/server_ratchet.py --update`; raising it needs a reviewed change to server_baseline.json.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
BASELINE = BACKEND / "server_baseline.json"
SCHEDULING = re.compile(r"schedul|visit|uctsm|reminder|occurrence|amend|projection|calendar", re.I)


def measure() -> dict:
    source = (BACKEND / "server.py").read_text(encoding="utf-8")
    functions = [node for node in ast.walk(ast.parse(source))
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return {"lines": len(source.splitlines()), "functions": len(functions),
            "scheduling_functions": sorted(node.name for node in functions if SCHEDULING.search(node.name))}


def problems(current: dict, baseline: dict) -> list[str]:
    found = []
    if current["lines"] > baseline["lines"]:
        found.append(f"server.py grew from {baseline['lines']} to {current['lines']} lines")
    if current["functions"] > baseline["functions"]:
        found.append(f"server.py gained functions ({baseline['functions']} -> {current['functions']})")
    added = sorted(set(current["scheduling_functions"]) - set(baseline["scheduling_functions"]))
    if added:
        found.append("new scheduling-related functions in server.py: " + ", ".join(added))
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    current = measure()
    if args.update:
        old = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else None
        if old and problems(current, old):
            print(json.dumps({"refused": problems(current, old)}))
            return 1                                  # --update can only LOWER the baseline
        BASELINE.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"updated": current["lines"]}))
        return 0
    found = problems(current, json.loads(BASELINE.read_text(encoding="utf-8")))
    print(json.dumps({"problems": found, "lines": current["lines"]}))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
