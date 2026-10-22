"""P7-04: an approved schedule is revalidated at command preview, commit and adoption; unsafe ones write nothing."""
from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.application.amendment_common import load_version  # noqa: F401  (documents the verified-load path)
from app.application.patient_commands import PatientCommandService
from app.domain.schedule.commands import RecordActual
from app.domain.schedule.models import Severity, ValidationIssue
from app.domain.schedule.validator import ScheduleValidator
from _p6_world import HORIZON, TODAY, World

pytest_plugins = ["tests.contract.conftest"]


def stricter_validator():
    """A newer validator that now rejects the approved schedule (the artifact key binds the version)."""
    def validate(self, schedule, **kwargs):
        return [ValidationIssue(issue_code="NEWLY_FORBIDDEN_CONSTRUCT", severity=Severity.ERROR,
                                message="synthetic stricter rule", blocking=True)]
    return (patch.object(ScheduleValidator, "version", "validator.next"),
            patch.object(ScheduleValidator, "validate", validate))


def state(world, patient):
    def read(tx):
        version = tx.patients.get(world.org, patient).current_schedule_version_id
        return {"occurrences": len(tx.facts.list_occurrences(patient)),
                "evaluations": tx.evaluations.count_evaluations(patient, version),
                "ledger": len(tx.ledger.list_for_patient(world.org, patient)),
                "proposals": len(tx.impacts.list_for_patient(patient)),
                "decisions": len(tx.amendments.list_decisions_for_patient(world.org, patient))}
    return world.uow.read(read)


def command(world, patient, service, *, commit_preview=None):
    return service.preview(
        patient, organization_id=world.org, actor_id=world.pi,
        payload=RecordActual(logical_key="C1D1:0", visit_date=date(2026, 1, 8), treatment_date=date(2026, 1, 15)),
        effective_at=date(2026, 1, 15), reason="given a week late", today=TODAY, horizon=HORIZON)


def test_a_newly_rejected_schedule_is_refused_at_command_preview_and_commit_with_nothing_written(uow):
    world = World(uow); patient = world.patient_ids[0]
    service = PatientCommandService(uow)
    ok_preview = command(world, patient, service)                       # valid today: preview works
    before = state(world, patient)
    version_patch, validate_patch = stricter_validator()
    with version_patch, validate_patch:
        with pytest.raises(ValueError, match="integrity validation"):
            command(world, patient, service)                               # preview refused
        with pytest.raises(ValueError, match="integrity validation"):
            service.confirm(ok_preview.proposal_id, organization_id=world.org, actor_id=world.pi, today=TODAY)
    assert state(world, patient) == before                                # commit wrote nothing
    outcome = service.confirm(ok_preview.proposal_id, organization_id=world.org, actor_id=world.pi, today=TODAY)
    assert outcome.evaluation_id                                          # and works again once valid


def test_a_newly_rejected_schedule_cannot_be_adopted_and_nothing_changes(uow):
    world = World(uow, reviewed=True); patient = world.patient_ids[0]
    valid = world.simulate(patient)                                        # a simulation made while valid
    assert valid["can_confirm"] is True
    before = state(world, patient)
    version_patch, validate_patch = stricter_validator()
    with version_patch, validate_patch:
        blocked = world.simulate(patient)                                  # simulation reports the blocker
        assert blocked["can_confirm"] is False
        assert [row["code"] for row in blocked["blockers"]] == ["TARGET_NOT_EVALUABLE"]
        with pytest.raises(Exception):
            world.confirm(blocked)                                         # and confirming it is refused
        with pytest.raises(Exception):
            world.confirm(valid)                                           # so is confirming the older, valid one
    assert state(world, patient)["decisions"] == before["decisions"]
    assert state(world, patient)["evaluations"] == before["evaluations"]


def test_approval_time_blockers_already_stop_unsafe_content(uow):
    from app.application.schedule_review import ScheduleReviewService
    from test_p6_approval_hash import Draft
    draft = Draft(uow)
    issue = ValidationIssue(issue_code="NEWLY_FORBIDDEN_CONSTRUCT", severity=Severity.ERROR,
                            message="synthetic stricter rule", blocking=True)
    with patch.object(ScheduleValidator, "validate", lambda self, schedule, **kw: [issue]):
        with pytest.raises(Exception):
            ScheduleReviewService(uow).approve_reviewed_table(draft.version, reviewer_id=uuid4(), reason="reviewed")
    assert draft.header().content_hash is None and draft.header().status != "APPROVED"
