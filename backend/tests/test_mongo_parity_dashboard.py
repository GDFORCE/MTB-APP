"""Behavioural parity of the dashboard and the reminder projection: SQL vs port."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application import dashboard as repo_dashboard
from app.application.patients import PatientConditionRecord
from app.db import models as db
from app.domain.schedule.models import OccurrenceFactKind
from app.services import dashboard_service as sql_dashboard
from app.services import reminder_delivery as sql_reminders
from test_mongo_parity_impact import (
    RepoStack as ImpactRepoStack, SqlStack as ImpactSqlStack, two_versions,
)

pytest_plugins = ["tests.contract.conftest"]

BASELINE = date(2026, 10, 1)


class SqlStack(ImpactSqlStack):
    def dashboard(self, today):
        result = sql_dashboard.DashboardService(self.session).actions(
            organization_id=self.organization_id, today=today)
        return result

    def projection(self, patient_id, today):
        patient = self.session.get(db.Patient, patient_id)
        return sql_reminders.compute_patient_projection(self.session, patient, today=today)

    def condition(self, patient_id, code, state, when):
        self.session.add(db.PatientCondition(
            patient_id=patient_id, condition_code=code, state=state, occurrence_date=when))
        self.session.commit()


class RepoStack(ImpactRepoStack):
    def dashboard(self, today):
        return repo_dashboard.DashboardService(self.uow).actions(
            organization_id=self.organization_id, today=today)

    def projection(self, patient_id, today):
        patient = self.uow.read(lambda tx: tx.patients.get(self.organization_id, patient_id))
        return repo_dashboard.compute_patient_projection(self.uow, patient, today=today)

    def condition(self, patient_id, code, state, when):
        record = PatientConditionRecord(
            organization_id=self.organization_id, patient_id=patient_id, condition_code=code,
            state=state, occurrence_date=when)
        self.uow.run(lambda tx: tx.facts.add_condition(record))


@pytest.fixture
def sql_stack():
    stack = SqlStack()
    yield stack
    stack.session.close()


@pytest.fixture
def repo_stack(uow):
    return RepoStack(uow)


def both(sql_stack, repo_stack, scenario):
    expected = scenario(sql_stack)
    actual = scenario(repo_stack)
    assert actual == expected
    return actual


def board(result):
    """Actions without the per-run identifiers."""
    return [(a.patient_code, a.category.value, a.message, a.due_date, a.target_type, a.priority)
            for a in result.actions]


def test_the_board_lists_due_overdue_and_waiting_work_but_not_completed_visits(sql_stack, repo_stack):
    def scenario(stack):
        patient = two_versions(stack)
        stack.fact(patient, "VISIT_1:0", OccurrenceFactKind.PERFORMED, date(2026, 10, 8))
        stack.evaluate(patient)
        stack.candidate(patient, "BASELINE", date(2026, 10, 3))
        stack.condition(patient, "PREGNANT", "ACTIVE", date(2026, 10, 9))
        stack.condition(patient, "ALLERGY", "PENDING_CONFIRMATION", date(2026, 10, 9))
        return board(stack.dashboard(date(2026, 10, 25)))

    actions = both(sql_stack, repo_stack, scenario)
    categories = {item[1] for item in actions}
    assert "ANCHOR_CONFIRMATION" in categories and "CONDITION_ACTIVE" in categories
    assert "CONDITION_CONFIRMATION" in categories
    assert not any("Visit_1" in item[2] or "Visit 1" in item[2] for item in actions
                   if item[1] in {"VISIT_DUE", "VISIT_OVERDUE"})


def test_a_previewed_amendment_appears_until_it_expires_or_is_confirmed(sql_stack, repo_stack):
    def scenario(stack):
        patient = two_versions(stack)
        proposal = stack.preview(patient, stack.version_ids[1])
        before = board(stack.dashboard(date(2026, 10, 2)))
        stack.confirm(proposal)
        after = board(stack.dashboard(date(2026, 10, 2)))
        return before, after

    before, after = both(sql_stack, repo_stack, scenario)
    assert any(item[1] == "IMPACT_CONFIRMATION" for item in before)
    assert not any(item[1] == "IMPACT_CONFIRMATION" for item in after)


def test_an_overdue_visit_is_reported_once_with_the_same_deadline(sql_stack, repo_stack):
    def scenario(stack):
        patient = two_versions(stack)
        return board(stack.dashboard(date(2026, 10, 20)))

    actions = both(sql_stack, repo_stack, scenario)
    overdue = [item for item in actions if item[1] == "VISIT_OVERDUE"]
    assert len(overdue) == 1 and overdue[0][3] == date(2026, 10, 8)


def test_the_reminder_projection_is_the_same_calculation(sql_stack, repo_stack):
    def scenario(stack):
        patient = two_versions(stack)
        stack.fact(patient, "VISIT_1:0", OccurrenceFactKind.PERFORMED, date(2026, 10, 8))
        stack.evaluate(patient)
        projection = stack.projection(patient, date(2026, 10, 6))
        return projection.model_dump(mode="json")

    both(sql_stack, repo_stack, scenario)


def test_a_patient_without_an_evaluation_has_an_empty_projection(sql_stack, repo_stack):
    def scenario(stack):
        from test_mongo_parity_patient_plane import from_baseline, schedule_of

        stack.publish(schedule_of(from_baseline("V1", 7, sequence=1)))
        stack.pin_first()
        patient = stack.enrol("P-009", {"BASELINE": BASELINE})
        return stack.projection(patient, date(2026, 10, 2)).model_dump(mode="json")

    both(sql_stack, repo_stack, scenario)
