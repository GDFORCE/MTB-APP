"""Generate the legacy scheduling consumer inventory (P11-01) from the actual source.

Routes are read from ``server.py`` with ``ast`` (decorators on ``api``), the legacy collections each
touches from ``db.<collection>`` accesses in its body, frontend callers by path-literal search, and
background workers from ``asyncio.create_task`` startup hooks. The CLASSIFICATION is a rule-based
proposal: every row is ``PROPOSED`` with ``owner: UNASSIGNED`` until a named person approves it, and
this script never invents an owner or evidence.

    python scripts/legacy_inventory.py            # rewrite docs/legacy-consumer-inventory.json/.md
    python scripts/legacy_inventory.py --check    # exit 1 when the checked-in files are stale
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
DOCS = REPO / "docs"
SCHEDULING = {"visits", "visit_instances", "schedule_definitions", "schedule_versions", "schedule_reviews",
              "protocol_extractions"}
RETAINED = {"reminders": "patient medication alarms are a separate system",
            "dose_logs": "medication adherence, not protocol scheduling", "medications": "medication list"}
VERBS = {"get", "post", "put", "patch", "delete"}
FRONTEND_ROOTS = [REPO / "frontend" / "app", REPO / "frontend" / "src"]


def _routes(tree: ast.AST) -> list[dict]:
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "api"
                    and decorator.func.attr in VERBS and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)):
                collections = sorted({sub.attr for sub in ast.walk(node)
                                      if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                                      and sub.value.id == "db"})
                rows.append({"method": decorator.func.attr.upper(), "path": "/api" + decorator.args[0].value,
                             "handler": node.name, "line": node.lineno, "collections": collections})
    return sorted(rows, key=lambda row: (row["path"], row["method"]))


def _frontend_files() -> list[tuple[Path, str]]:
    files = []
    for root in FRONTEND_ROOTS:
        for path in root.rglob("*"):
            if path.suffix in {".ts", ".tsx"} and "node_modules" not in path.parts:
                files.append((path, path.read_text(encoding="utf-8", errors="ignore")))
    return files


def _consumers(path: str, files) -> list[str]:
    """Files containing the FULL route shape (``{param}`` matches ``${expr}`` or any one segment)."""
    segments = path.removeprefix("/api").strip("/").split("/")
    param = r"(?:\$\{[^}]*\}|[^/'\"`\s?$]+)"
    pattern = "/" + "/".join(param if segment.startswith("{") else re.escape(segment) for segment in segments)
    needle = re.compile(pattern + r"(?![A-Za-z0-9_/-])")
    return sorted({str(file.relative_to(REPO)).replace("\\", "/") for file, text in files if needle.search(text)})


#: Legacy visit comment/task features have no UCTSM equivalent (MTB_IMPLEMENTATION_HANDOFF.txt C3/D-4).
RETAINED_FEATURES = {"add_visit_instance_comment", "patch_visit_instance_task"}


def classify(row: dict) -> dict:
    touched = set(row["collections"])
    if row["handler"] in RETAINED_FEATURES:
        return {"classification": "RETAIN", "target": "unchanged",
                "because": "legacy visit comment/task feature with no UCTSM equivalent (handoff C3/D-4)"}
    scheduling = sorted(touched & SCHEDULING)
    if scheduling:
        write = row["method"] != "GET"
        return {"classification": "MIGRATE" if write else "ADAPT", "target": (
            "UCTSM authoring/patient plane (schedule authoring is blocked after UCTSM_AUTHORITATIVE)"
            if write else "UCTSM read projection via Trial.schedule_read_mode"),
            "because": "touches legacy scheduling collections: " + ", ".join(scheduling)}
    retained = sorted(touched & set(RETAINED))
    if retained:
        return {"classification": "RETAIN", "target": "unchanged", "because": RETAINED[retained[0]]}
    return {"classification": "OUT_OF_SCOPE", "target": "unchanged",
            "because": "does not read or write protocol scheduling data"}


def workers(source: str) -> list[dict]:
    names = re.findall(r"asyncio\.create_task\(([A-Za-z_.]+)\(", source)
    scheduling = {"reminder_worker_loop": ("ADAPT", "visit-reminder bridge reads UCTSM projections; medication alarms stay separate"),
                  "_migrate_visit_instances": ("ARCHIVE", "materializes legacy visit instances; stop once cut over"),
                  "amendment_worker_loop": ("RETAIN", "UCTSM amendment adoption worker")}
    rows = []
    for name in sorted(set(names)):
        kind, why = scheduling.get(name.split(".")[-1], ("OUT_OF_SCOPE", "not a scheduling job"))
        rows.append({"name": name, "classification": kind, "because": why})
    return rows


def build() -> dict:
    source = (BACKEND / "server.py").read_text(encoding="utf-8")
    files = _frontend_files()
    routes = []
    for row in _routes(ast.parse(source)):
        decision = classify(row)
        routes.append({**row, **decision,
                       "frontend_consumers": _consumers(row["path"], files) if decision["classification"]
                       in {"MIGRATE", "ADAPT"} else [],
                       "status": "PROPOSED", "owner": "UNASSIGNED", "evidence": None})
    counts: dict[str, int] = {}
    for row in routes:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    return {"schema": "uctsm-legacy-inventory.v1",
            "note": "Rule-based PROPOSAL generated from source. Every row needs a named owner's approval; "
                    "frontend_consumers are full-route-shape matches in app/ and src/ (dynamic URL building is not detected).",
            "counts": counts, "routes": routes, "workers": workers(source)}


def markdown(inventory: dict) -> str:
    lines = ["# Legacy scheduling consumer inventory", "",
             "Generated by `backend/scripts/legacy_inventory.py` (P11-01); do not edit by hand.", "",
             inventory["note"], "", "Counts: " + ", ".join(f"{k} {v}" for k, v in sorted(inventory["counts"].items())), "",
             "## Scheduling routes (migrate or adapt)", "",
             "| Route | Handler | Collections | Proposed | Frontend callers |", "|---|---|---|---|---|"]
    for row in inventory["routes"]:
        if row["classification"] in {"MIGRATE", "ADAPT"}:
            lines.append(f"| `{row['method']} {row['path']}` | {row['handler']} | {', '.join(row['collections'])} "
                         f"| {row['classification']} | {', '.join(row['frontend_consumers']) or '-'} |")
    lines += ["", "## Background jobs", "", "| Job | Proposed | Reason |", "|---|---|---|"]
    lines += [f"| `{row['name']}` | {row['classification']} | {row['because']} |" for row in inventory["workers"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    inventory = build()
    json_text = json.dumps(inventory, indent=2, sort_keys=True) + "\n"
    md_text = markdown(inventory)
    targets = {DOCS / "legacy-consumer-inventory.json": json_text, DOCS / "legacy-consumer-inventory.md": md_text}
    if args.check:
        stale = [path.name for path, text in targets.items()
                 if not path.is_file() or path.read_text(encoding="utf-8") != text]
        print(json.dumps({"stale": stale}))
        return 1 if stale else 0
    for path, text in targets.items():
        path.write_text(text, encoding="utf-8")
    print(json.dumps({"written": [p.name for p in targets], "counts": inventory["counts"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
