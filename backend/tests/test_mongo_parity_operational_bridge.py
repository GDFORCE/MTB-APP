"""Behavioural parity of the operational enrolment bridge: SQL service vs port."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application.operational_bridge import OperationalBridgeService as RepoBridge
from app.db import models as db
from app.services.operational_bridge import OperationalBridgeService as SqlBridge
from test_mongo_parity_impact import (
    RepoStack as ImpactRepoStack, SqlStack as ImpactSqlStack,
)
from test_mongo_parity_patient_plane import from_baseline, schedule_of as plain_schedule_of

pytest_plugins = ["tests.contract.conftest"]

ACTOR = UUID("88888888-8888-4888-8888-888888888888")
BASELINE = date(2026, 10, 1)
HORIZON = date(2027, 12, 31)


def schedule_of(*events):
    schedule = plain_schedule_of(*events)
    schedule.anchors[0].derivation_rule = {"role": "SCHEDULE_ORIGIN"}
    return schedule


def shape(enrollment, versions):
    return (versions.index(enrollment.schedule_version_id) + 1,
            [(e.event_code, e.name, e.event_type, e.status, e.nominal_date, e.earliest_date,
              e.latest_date, e.activities) for e in enrollment.events])


class SqlStack(ImpactSqlStack):
    def link(self):
        linked = SqlBridge(self.session).validate_trial_link(
            organization_id=self.organization_id, external_trial_id="ext-trial",
            external_schedule_definition_id="ext-def", trial_id=self.ids["trial"],
            schedule_definition_id=self.ids["definition"])
        self.session.commit()
        return linked

    def bridge_enrol(self, external, **kwargs):
        try:
            result = SqlBridge(self.session).enroll_patient(
                organization_id=self.organization_id, actor_id=ACTOR,
                canonical_trial_id=self.ids["trial"],
                schedule_definition_id=self.ids["definition"], external_patient_id=external,
                patient_code=kwargs.pop("code", external), horizon=HORIZON,
                baseline_date=kwargs.pop("baseline", BASELINE), **kwargs)
        except (ValueError, KeyError) as error:
            self.session.rollback()
            return ("error", str(error), None)
        self.session.commit()
        return ("ok", shape(result, self.version_ids), result.patient_id)

    def patient_count(self):
        return len(list(self.session.scalars(select(db.Patient))))

    def anchors(self):
        return sorted((a.value_date, a.source_type, a.status)
                      for a in self.session.scalars(select(db.PatientAnchor)))

    def trial_external(self):
        return self.session.get(db.Trial, self.ids["trial"]).external_trial_id


class RepoStack(ImpactRepoStack):
    def link(self):
        return RepoBridge(self.uow).validate_trial_link(
            organization_id=self.organization_id, external_trial_id="ext-trial",
            external_schedule_definition_id="ext-def", trial_id=self.trial_id,
            schedule_definition_id=self.definition.id)

    def bridge_enrol(self, external, **kwargs):
        try:
            result = RepoBridge(self.uow).enroll_patient(
                organization_id=self.organization_id, actor_id=ACTOR,
                canonical_trial_id=self.trial_id, schedule_definition_id=self.definition.id,
                external_patient_id=external, patient_code=kwargs.pop("code", external),
                horizon=HORIZON, baseline_date=kwargs.pop("baseline", BASELINE), **kwargs)
        except (ValueError, KeyError) as error:
            return ("error", str(error), None)
        return ("ok", shape(result, self.version_ids), result.patient_id)

    def patient_count(self):
        return len(self.uow.read(
            lambda tx: tx.patients.list_for_organization(self.organization_id)))

    def anchors(self):
        patients = self.uow.read(lambda tx: tx.patients.list_for_organization(self.organization_id))
        rows = []
        for patient in patients:
            rows += self.uow.read(lambda tx, p=patient: tx.facts.list_anchors(p.id))
        return sorted((a.value_date, a.source_type, a.status) for a in rows)

    def trial_external(self):
        return self.uow.read(
            lambda tx: tx.authoring.get_trial(self.organization_id, self.trial_id)).external_trial_id


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


def strip(result):
    """The patient id differs per run; everything else must match."""
    return result[:2]


def test_linking_a_trial_records_the_external_ids_and_the_approved_version(sql_stack, repo_stack):
    def scenario(stack):
        stack.publish(schedule_of(from_baseline("VISIT_1", 7, sequence=1)))
        linked = stack.link()
        return (stack.version_ids.index(UUID(linked["schedule_version_id"])),
                stack.trial_external(), stack.audit_actions())

    both(sql_stack, repo_stack, scenario)


def test_enrolment_pins_the_approved_version_and_returns_the_dated_visits(sql_stack, repo_stack):
    def scenario(stack):
        stack.publish(schedule_of(from_baseline("VISIT_1", 7, sequence=1),
                                  from_baseline("VISIT_2", 14, sequence=2)))
        stack.link()
        outcome = stack.bridge_enrol("mongo-1")
        return strip(outcome), stack.patient_count(), stack.anchors(), stack.audit_actions()

    result = both(sql_stack, repo_stack, scenario)
    assert result[0][0] == "ok"
    assert date(2026, 10, 8) in {row[4] for row in result[0][1][1]}


def test_enrolling_the_same_external_patient_again_is_idempotent(sql_stack, repo_stack):
    def scenario(stack):
        stack.publish(schedule_of(from_baseline("VISIT_1", 7, sequence=1)))
        stack.link()
        first = stack.bridge_enrol("mongo-1")
        again = stack.bridge_enrol("mongo-1")
        return (strip(first), strip(again), first[2] == again[2], stack.patient_count(),
                stack.audit_actions())

    result = both(sql_stack, repo_stack, scenario)
    assert result[2] is True and result[3] == 1


def test_a_new_approved_version_does_not_move_an_existing_patient(sql_stack, repo_stack):
    def scenario(stack):
        stack.publish(schedule_of(from_baseline("VISIT_1", 7, sequence=1)))
        stack.link()
        stack.bridge_enrol("mongo-1")
        stack.publish(schedule_of(from_baseline("VISIT_1", 10, sequence=1)))
        return strip(stack.bridge_enrol("mongo-1"))

    outcome = both(sql_stack, repo_stack, scenario)
    assert outcome[0] == "error" and "impact preview/confirm" in outcome[1]


def test_a_different_baseline_for_a_linked_patient_is_refused(sql_stack, repo_stack):
    def scenario(stack):
        stack.publish(schedule_of(from_baseline("VISIT_1", 7, sequence=1)))
        stack.link()
        stack.bridge_enrol("mongo-1")
        return strip(stack.bridge_enrol("mongo-1", baseline=date(2026, 10, 5)))

    outcome = both(sql_stack, repo_stack, scenario)
    assert outcome[0] == "error" and "baseline differs" in outcome[1]


def test_a_stale_expected_version_is_refused_before_anything_is_written(sql_stack, repo_stack):
    def scenario(stack):
        stack.publish(schedule_of(from_baseline("VISIT_1", 7, sequence=1)))
        stack.link()
        outcome = stack.bridge_enrol("mongo-1", expected_version_id=uuid4())
        return strip(outcome), stack.patient_count()

    outcome = both(sql_stack, repo_stack, scenario)
    assert outcome[0][0] == "error" and outcome[1] == 0


def test_a_trial_without_an_approved_schedule_cannot_enrol(sql_stack, repo_stack):
    def scenario(stack):
        return strip(stack.bridge_enrol("mongo-1"))

    outcome = both(sql_stack, repo_stack, scenario)
    assert outcome[0] == "error"
