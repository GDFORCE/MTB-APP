"""P11-04: no legacy schedule write succeeds for a migrated (pilot) patient."""
import ast
import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.legacy_guard import refuse_if_migrated
from app.application.cohort_cutover import CohortCutoverService
from app.observability.metrics import METRICS
from test_pdf_cohort_cutover import SNAP, principal, reconciled  # noqa: F401  (fixture reuse)

pytest_plugins = ["tests.contract.conftest"]
BACKEND = Path(__file__).resolve().parents[1]
INVENTORY = json.loads((BACKEND.parent / "docs" / "legacy-consumer-inventory.json").read_text(encoding="utf-8"))

#: MIGRATE handlers that are NOT patient-keyed legacy schedule writes, and why they need no per-patient guard.
EXEMPT = {
    "seed_demo": "demonstration seed, admin-only",
    "create_share": "creates a share link; does not change any schedule",
    "consume_protocol_extraction": "trial-level authoring: blocked by the UCTSM_AUTHORITATIVE middleware",
    "preview_trial_schedule": "trial-level authoring preview: blocked by the UCTSM_AUTHORITATIVE middleware",
    "build_canonical_schedule": "builds the UCTSM schedule itself",
    "patch_visit": "trial visit template edit: blocked by the UCTSM_AUTHORITATIVE middleware (/api/visits)",
}


def call(patient, uow, route="/test"):
    return asyncio.run(refuse_if_migrated(patient, route=route, uow_factory=lambda: uow))


def test_only_a_promoted_pilot_member_is_refused_with_an_explicit_migration_response(reconciled):  # noqa: F811
    world, service = reconciled
    maker = principal(world.org)
    cohort = service.create_shadow(maker, world.org, world.trial_id, "pilot-a", ["LEG-1"])
    call("LEG-1", world.uow)                                           # shadow: allowed
    service.promote(principal(world.org), world.org, cohort.id, reason="approved", snapshot=SNAP)
    before = METRICS.legacy_blocked.value(route="/api/test")
    with pytest.raises(HTTPException) as refused:
        call("LEG-1", world.uow, route="/api/test")
    assert refused.value.status_code == 409 and refused.value.detail["code"] == "MIGRATED_TO_UCTSM"
    assert METRICS.legacy_blocked.value(route="/api/test") == before + 1
    call("LEG-2", world.uow); call(None, world.uow)                     # non-members and unknown ids pass
    service.rollback(principal(world.org), world.org, cohort.id, reason="rehearsal")
    call("LEG-1", world.uow)                                           # lifted by rollback


def test_an_unreachable_authority_store_fails_closed_without_echoing_the_cause():
    def broken():
        raise RuntimeError("mongodb://admin:Secret@host failed")
    with pytest.raises(HTTPException) as refused:
        asyncio.run(refuse_if_migrated("LEG-1", route="/api/x", uow_factory=broken))
    assert refused.value.status_code == 503 and "Secret" not in str(refused.value.detail)


def test_the_guard_is_a_noop_when_the_plane_is_not_configured(monkeypatch):
    monkeypatch.delenv("UCTSM_MONGO_URL", raising=False)
    asyncio.run(refuse_if_migrated("LEG-1", route="/api/x"))


def test_every_legacy_schedule_writing_handler_is_guarded_or_explicitly_exempt():
    tree = ast.parse((BACKEND / "server.py").read_text(encoding="utf-8"))
    handlers = {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    migrate = [row["handler"] for row in INVENTORY["routes"] if row["classification"] == "MIGRATE"]
    assert migrate
    for name in migrate:
        guarded = any(isinstance(sub, ast.Call) and getattr(sub.func, "id", "") == "refuse_if_migrated"
                      for sub in ast.walk(handlers[name]))
        assert guarded or name in EXEMPT, f"{name} changes a schedule but is neither guarded nor exempt"
    assert not (set(EXEMPT) - set(migrate))                               # no stale exemptions
    assert {"add_patient_visit", "delete_patient_visit", "reconcile_patient_uctsm", "patch_visit_instance"} <= set(migrate)


def test_notes_and_visit_type_edits_are_not_treated_as_schedule_writes():
    source = (BACKEND / "server.py").read_text(encoding="utf-8")
    start = source.index("async def patch_visit_instance(")
    body = source[start:start + 1500]
    assert "body.status, body.scheduled_date, body.actual_visit_at, body.missed_reason, body.cancelled_reason" in body
