"""P12-04: one typed application boundary for a patient's schedule.

The plane's HTTP adapters reach patient queries, evaluation, occurrences, typed commands,
amendments and the committed projection ONLY through ``PatientScheduleBoundary``; the boundary
owns no logic and forwards to the services that decide authority, idempotency and audit.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from app.application.patient_boundary import PatientScheduleBoundary
from app.domain.schedule.amendment import AdoptionMode, AdoptionPoint
from app.domain.schedule.models import OccurrenceFactKind

from _p6_world import HORIZON, TODAY, World

pytest_plugins = ["tests.contract.conftest"]

API = Path(__file__).resolve().parents[1] / "app" / "api"
ADAPTERS = ["uctsm_mongo.py", "uctsm_amendments.py", "uctsm_amendment_worker.py"]
PATIENT_SERVICES = {"PatientScheduleService", "PatientCommandService", "ProjectionSnapshotService"}


def imported_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.name.split(".")[-1] for alias in node.names)
    return names


@pytest.mark.parametrize("name", ["uctsm_mongo.py", "uctsm_amendments.py"])
def test_patient_adapters_do_not_import_the_patient_services_directly(name):
    assert not (imported_names(API / name) & PATIENT_SERVICES), name
    assert "AdoptionService" not in imported_names(API / name)


def test_the_boundary_exposes_a_typed_method_for_every_patient_operation():
    methods = {name for name, member in inspect.getmembers(PatientScheduleBoundary, inspect.isfunction)
               if not name.startswith("_")}
    assert methods >= {
        "calendar", "current_events", "projection", "command_history", "amendment_status",
        "evaluate", "record_occurrence", "record_unscheduled_visit", "record_activity",
        "preview_command", "confirm_command", "cancel_command",
        "simulate_adoption", "confirm_adoption", "execute_adoption", "cancel_adoption"}


def test_a_fact_recorded_through_the_boundary_is_the_same_committed_result(uow):
    world = World(uow, reviewed=True)
    patient_id = world.patient_ids[0]
    boundary = PatientScheduleBoundary(uow)
    boundary.record_occurrence(
        patient_id, organization_id=world.org, logical_key="C1D1:0",
        kind=OccurrenceFactKind.PERFORMED, visit_date=date(2026, 1, 8), actor_id=world.pi,
        reason="boundary", horizon=HORIZON, today=TODAY)
    entry = next(item for item in boundary.calendar(patient_id, organization_id=world.org)
                 if item.logical_key == "C1D1:0")
    assert entry.status == "COMPLETED"
    version = world.patient(patient_id).current_schedule_version_id
    assert any(row.logical_key == "C1D1:0" for row in boundary.current_events(patient_id, version))
    assert boundary.amendment_status(organization_id=world.org, patient_id=patient_id)


def test_an_adoption_driven_through_the_boundary_is_authorised_and_audited_like_the_service(uow):
    world = World(uow, reviewed=True)
    boundary = PatientScheduleBoundary(uow, clock=lambda: TODAY)
    preview = boundary.simulate_adoption(
        organization_id=world.org, actor_id=world.pi, patient_id=world.patient_ids[0],
        direction="ADOPT", point=AdoptionPoint(mode=AdoptionMode.ADOPT_IMMEDIATELY),
        to_schedule_version_id=world.v2, horizon=HORIZON, as_of=TODAY)
    decision = boundary.confirm_adoption(
        organization_id=world.org, actor_id=world.pi, simulation_id=UUID(preview["simulation_id"]),
        decision="ADOPT", reason="boundary")
    assert decision["status"] == "APPLIED"
    assert world.patient(world.patient_ids[0]).current_schedule_version_id == world.v2
    assert uow.read(lambda tx: tx.audit.list_for_organization(
        world.org, action="PATIENT_SCHEDULE_AMENDED"))
