"""P12-04: every mutation the plane's routes declare goes through an application service that leaves an audit trail."""
import importlib
from datetime import date
from uuid import UUID, uuid4

import pytest

from app.application.patient_commands import PatientCommandService
from app.application.schedule_review import ScheduleReviewService
from app.authorization.manifest import _MONGO
from app.domain.schedule.amendment import AdoptionMode, AdoptionPoint
from app.domain.schedule.commands import RecordActual
from test_p6_approval_hash import Draft
from _p6_world import HORIZON, TODAY, World

pytest_plugins = ["tests.contract.conftest"]


def audit_count(uow, org):
    return len(uow.read(lambda tx: tx.audit.list_for_organization(org, limit=5000)))


def adds_audit(uow, org, action):
    before = audit_count(uow, org)
    result = action()
    assert audit_count(uow, org) > before, "the mutation left no audit event"
    return result


def test_every_declared_plane_mutation_resolves_to_an_application_service_method():
    targets = {rule.mutation for rule in _MONGO.values() if rule.mutation}
    assert len(targets) >= 20
    for target in sorted(targets):
        module, _, attribute = target.rpartition(".")
        module_name, _, class_name = module.rpartition(".")
        assert module_name.startswith("app.application."), f"{target} is not an application service"
        assert callable(getattr(getattr(importlib.import_module(module_name), class_name), attribute)), target


def test_schedule_approval_is_audited(uow):
    draft = Draft(uow)
    adds_audit(uow, draft.org, lambda: ScheduleReviewService(uow).approve_reviewed_table(
        draft.version, reviewer_id=uuid4(), reason="Reviewed"))


def test_recording_an_occurrence_and_the_command_lifecycle_are_audited(uow):
    world = World(uow)
    patient = world.patient_ids[0]
    adds_audit(uow, world.org, lambda: world.record(patient, "C1D1:0", date(2026, 1, 8), treatment=date(2026, 1, 15)))
    service = PatientCommandService(uow)
    preview = adds_audit(uow, world.org, lambda: service.preview(
        patient, organization_id=world.org, actor_id=world.pi,
        payload=RecordActual(logical_key="DOSE:0", visit_date=date(2026, 1, 15)), effective_at=date(2026, 1, 15),
        reason="dose given", today=TODAY, horizon=HORIZON))
    adds_audit(uow, world.org, lambda: service.confirm(preview.proposal_id, organization_id=world.org,
                                                       actor_id=world.pi, today=TODAY))


def test_adoption_confirm_and_cancel_are_audited(uow):
    world = World(uow, reviewed=True)
    point = AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 3, 1))
    decision = adds_audit(uow, world.org, lambda: world.confirm(world.simulate(point=point)))
    adds_audit(uow, world.org, lambda: world.adoption.cancel(
        organization_id=world.org, actor_id=world.pi, decision_id=UUID(decision["id"]), reason="changed my mind"))
    again = adds_audit(uow, world.org, lambda: world.confirm(world.simulate()))
    assert again["status"] == "APPLIED"


def test_the_pilot_seeds_leave_an_audit_trail(uow):
    world = World(uow)
    actions = {row.action for row in uow.read(lambda tx: tx.audit.list_for_organization(world.org, limit=5000))}
    assert {"OPERATIONAL_PATIENT_LINKED" if False else "PILOT_PATIENT_ENROLLED", "PILOT_AMENDMENT_SEEDED"} <= actions
