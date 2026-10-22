"""P11-01: the legacy consumer inventory is complete, current and drives deprecation telemetry."""
import ast
import json
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.observability.install import install, legacy_routes
from app.observability.metrics import METRICS

BACKEND = Path(__file__).resolve().parents[1]
INVENTORY = json.loads((BACKEND.parent / "docs" / "legacy-consumer-inventory.json").read_text(encoding="utf-8"))


def declared_routes():
    tree = ast.parse((BACKEND / "server.py").read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                if (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                        and isinstance(deco.func.value, ast.Name) and deco.func.value.id == "api" and deco.args):
                    found.add((deco.func.attr.upper(), "/api" + deco.args[0].value))
    return found


def test_every_legacy_route_has_exactly_one_classified_proposal_row():
    rows = [(row["method"], row["path"]) for row in INVENTORY["routes"]]
    assert set(rows) == declared_routes() and len(rows) == len(set(rows))
    for row in INVENTORY["routes"]:
        assert row["classification"] in {"MIGRATE", "ADAPT", "RETAIN", "OUT_OF_SCOPE", "RETIRE", "ARCHIVE"}
        assert row["because"] and row["target"]
        # nobody is named, and nothing is approved, on this script's authority
        assert row["status"] == "PROPOSED" and row["owner"] == "UNASSIGNED" and row["evidence"] is None
    assert all(worker["classification"] for worker in INVENTORY["workers"]) and INVENTORY["workers"]


def test_the_checked_in_inventory_is_current():
    done = subprocess.run([sys.executable, "scripts/legacy_inventory.py", "--check"], cwd=BACKEND,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout


def test_scheduling_routes_are_found_and_the_known_protected_screens_are_listed_as_callers():
    by_path = {(row["method"], row["path"]): row for row in INVENTORY["routes"]}
    assert by_path[("PATCH", "/api/visit-instances/{instance_id}")]["classification"] == "MIGRATE"
    callers = by_path[("PATCH", "/api/visit-instances/{instance_id}")]["frontend_consumers"]
    assert any(path.endswith("clinical/visit-detail.tsx") for path in callers)
    assert by_path[("GET", "/api/visits/mine")]["classification"] == "ADAPT"


def test_legacy_route_requests_are_counted_by_route_template_and_classification():
    app = FastAPI(); api = APIRouter(prefix="/api")

    @api.get("/visits/mine")
    def mine():
        return {}

    @api.get("/auth/other")
    def other():
        return {}

    app.include_router(api)
    install(app)
    before = METRICS.legacy_consumers.value(route="/api/visits/mine", method="GET", classification="ADAPT")
    client = TestClient(app)
    client.get("/api/visits/mine"); client.get("/api/visits/mine"); client.get("/api/auth/other")
    assert METRICS.legacy_consumers.value(route="/api/visits/mine", method="GET", classification="ADAPT") == before + 2
    assert ("GET", "/api/visits/mine") in legacy_routes() and ("GET", "/api/auth/other") not in legacy_routes()
