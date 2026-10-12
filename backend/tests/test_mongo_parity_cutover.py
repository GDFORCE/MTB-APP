"""Behavioural parity of the parity gate and the read-mode cutover: SQL vs port."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application import parity as repo_parity
from app.db import models as db
from app.services import parity_service as sql_parity
from test_mongo_parity_impact import (
    RepoStack as ImpactRepoStack, SqlStack as ImpactSqlStack,
)
from test_mongo_parity_patient_plane import from_baseline, schedule_of as plain_schedule_of

pytest_plugins = ["tests.contract.conftest"]

ACTOR = UUID("99999999-9999-4999-8999-999999999999")
BASELINE = date(2026, 10, 1)
HORIZON = date(2027, 12, 31)

MATCHING = [
    {"name": "Visit_1", "scheduled_date": "2026-10-08"},
    {"name": "Visit_2", "scheduled_date": "2026-10-15"},
]
ONE_DAY_OUT = [
    {"name": "Visit_1", "scheduled_date": "2026-10-08"},
    {"name": "Visit_2", "scheduled_date": "2026-10-16"},
]


def schedule_of(*events):
    schedule = plain_schedule_of(*events)
    schedule.anchors[0].derivation_rule = {"role": "SCHEDULE_ORIGIN"}
    return schedule


def run_shape(run):
    return (run.verdict, run.compared, run.matched, len(run.differences),
            run.accepted_at is not None, run.accepted_reason)


class SqlStack(ImpactSqlStack):
    def parity(self):
        return sql_parity.ParityService(self.session)

    def seed(self, count=1):
        self.publish(schedule_of(from_baseline("VISIT_1", 7, sequence=1),
                                 from_baseline("VISIT_2", 14, sequence=2)))
        self.pin_first()
        return [self.enrol(f"P{i:03d}", {"BASELINE": BASELINE}) for i in range(1, count + 1)]

    def check(self, patient_id, legacy):
        report, run = self.parity().compare_patient(
            patient_id, organization_id=self.organization_id, legacy_visits=legacy,
            horizon=HORIZON, actor_id=ACTOR)
        self.session.commit()
        return report, run

    def accept(self, run, reason="reviewed"):
        try:
            result = self.parity().accept_run(
                run.id, organization_id=self.organization_id, actor_id=ACTOR, reason=reason)
        except (ValueError, KeyError) as error:
            self.session.rollback()
            return ("error", str(error))
        self.session.commit()
        return ("ok", run_shape(result))

    def readiness(self):
        status = self.parity().readiness(self.ids["trial"], organization_id=self.organization_id)
        return self._normal(status)

    def cutover(self, mode="ENGINE", reason="Parity proven"):
        try:
            result = self.parity().set_read_mode(
                self.ids["trial"], organization_id=self.organization_id, mode=mode,
                actor_id=ACTOR, reason=reason)
        except (ValueError, KeyError) as error:
            self.session.rollback()
            return ("error", str(error))
        self.session.commit()
        return ("ok", result["read_mode"], result["previous"])

    def read_mode(self):
        self.session.expire_all()
        return self.session.get(db.Trial, self.ids["trial"]).schedule_read_mode

    def unpin(self, patient_id):
        self.session.get(db.Patient, patient_id).current_schedule_version_id = None
        self.session.commit()

    def repin(self, patient_id, version_id):
        self.session.get(db.Patient, patient_id).current_schedule_version_id = version_id
        self.session.commit()

    @staticmethod
    def _normal(status):
        return {key: ([{k: v for k, v in item.items()
                        if k not in {"parity_run_id", "accepted_at", "accepted_by"}}
                       for item in value] if isinstance(value, list) and value
                      and isinstance(value[0], dict) else value)
                for key, value in status.items() if key != "trial_id"}

    def codes(self, patient_ids):
        return sorted(self.session.get(db.Patient, p).patient_code for p in patient_ids)


class RepoStack(ImpactRepoStack):
    def parity(self):
        return repo_parity.ParityService(self.uow)

    def seed(self, count=1):
        self.publish(schedule_of(from_baseline("VISIT_1", 7, sequence=1),
                                 from_baseline("VISIT_2", 14, sequence=2)))
        self.pin_first()
        return [self.enrol(f"P{i:03d}", {"BASELINE": BASELINE}) for i in range(1, count + 1)]

    def check(self, patient_id, legacy):
        return self.parity().compare_patient(
            patient_id, organization_id=self.organization_id, legacy_visits=legacy,
            horizon=HORIZON, actor_id=ACTOR)

    def accept(self, run, reason="reviewed"):
        try:
            result = self.parity().accept_run(
                run.id, organization_id=self.organization_id, actor_id=ACTOR, reason=reason)
        except (ValueError, KeyError) as error:
            return ("error", str(error))
        return ("ok", run_shape(result))

    def readiness(self):
        return SqlStack._normal(self.parity().readiness(
            self.trial_id, organization_id=self.organization_id))

    def cutover(self, mode="ENGINE", reason="Parity proven"):
        try:
            result = self.parity().set_read_mode(
                self.trial_id, organization_id=self.organization_id, mode=mode,
                actor_id=ACTOR, reason=reason)
        except (ValueError, KeyError) as error:
            return ("error", str(error))
        return ("ok", result["read_mode"], result["previous"])

    def read_mode(self):
        return self.uow.read(
            lambda tx: tx.authoring.get_trial(self.organization_id, self.trial_id)
        ).schedule_read_mode

    def unpin(self, patient_id):
        self.repin(patient_id, None)

    def repin(self, patient_id, version_id):
        def work(tx):
            patient = tx.patients.get(self.organization_id, patient_id)
            tx.patients.save(patient.model_copy(update={"current_schedule_version_id": version_id}))

        self.uow.run(work)

    def codes(self, patient_ids):
        return sorted(self.uow.read(
            lambda tx: tx.patients.get(self.organization_id, p)).patient_code for p in patient_ids)


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


def verdict(outcome):
    report, run = outcome
    return (report.verdict.value, report.compared, report.matched,
            run_shape(run) if run is not None else None)


def test_cutover_is_refused_before_any_patient_has_been_compared(sql_stack, repo_stack):
    def scenario(stack):
        stack.seed()
        return stack.cutover(), stack.read_mode(), stack.readiness()

    refused, mode, _ = both(sql_stack, repo_stack, scenario)
    assert refused[0] == "error" and "have not been compared" in refused[1] and mode == "LEGACY"


def test_a_matching_comparison_lets_the_trial_cut_over_and_back(sql_stack, repo_stack):
    def scenario(stack):
        (patient,) = stack.seed()
        first = verdict(stack.check(patient, MATCHING))
        ready = stack.readiness()
        moved = stack.cutover()
        engine = stack.read_mode()
        reversed_ = stack.cutover(mode="LEGACY", reason="Site reported a wrong date")
        return first, ready, moved, engine, reversed_, stack.read_mode(), stack.audit_actions()

    result = both(sql_stack, repo_stack, scenario)
    assert result[0][0] == "MATCH" and result[3] == "ENGINE" and result[5] == "LEGACY"


def test_a_difference_blocks_the_gate_until_it_is_accepted_with_a_reason(sql_stack, repo_stack):
    def scenario(stack):
        (patient,) = stack.seed()
        report, run = stack.check(patient, ONE_DAY_OUT)
        blocked = stack.cutover()
        no_reason = stack.accept(run, reason="   ")
        accepted = stack.accept(run)
        return (report.passed, blocked, no_reason, accepted, stack.readiness(), stack.cutover(),
                stack.read_mode(), stack.audit_actions())

    result = both(sql_stack, repo_stack, scenario)
    assert result[0] is False and result[1][0] == "error" and result[3][0] == "ok"
    assert result[6] == "ENGINE"


def test_a_matching_run_cannot_be_accepted(sql_stack, repo_stack):
    def scenario(stack):
        (patient,) = stack.seed()
        _, run = stack.check(patient, MATCHING)
        return stack.accept(run)

    outcome = both(sql_stack, repo_stack, scenario)
    assert outcome[0] == "error" and "only a run with differences" in outcome[1]


def test_accepting_one_patients_run_does_not_clear_another(sql_stack, repo_stack):
    def scenario(stack):
        first, second = stack.seed(count=2)
        _, run = stack.check(first, ONE_DAY_OUT)
        stack.check(second, [{"name": "Visit_1", "scheduled_date": "2026-10-09"}])
        stack.accept(run)
        return stack.readiness()

    status = both(sql_stack, repo_stack, scenario)
    assert status["ready"] is False
    assert [i["patient_code"] for i in status["accepted"]] == ["P001"]
    assert [i["patient_code"] for i in status["with_differences"]] == ["P002"]


def test_an_unchecked_second_patient_blocks_the_whole_trial(sql_stack, repo_stack):
    def scenario(stack):
        first, _second = stack.seed(count=2)
        stack.check(first, MATCHING)
        return stack.readiness(), stack.cutover()

    status, refused = both(sql_stack, repo_stack, scenario)
    assert status["ready"] is False and "P002" in status["unchecked"]
    assert refused[0] == "error"


def test_an_amendment_makes_an_earlier_run_stop_counting(sql_stack, repo_stack):
    def scenario(stack):
        (patient,) = stack.seed()
        stack.check(patient, MATCHING)
        before = stack.readiness()["ready"]
        stack.publish(schedule_of(from_baseline("VISIT_1", 7, sequence=1),
                                  from_baseline("VISIT_2", 14, sequence=2)))
        stack.repin(patient, stack.version_ids[-1])
        return before, stack.readiness()

    before, after = both(sql_stack, repo_stack, scenario)
    assert before is True and after["ready"] is False and "P001" in after["unchecked"]


def test_a_patient_with_no_approved_version_is_not_comparable(sql_stack, repo_stack):
    def scenario(stack):
        (patient,) = stack.seed()
        stack.unpin(patient)
        return verdict(stack.check(patient, MATCHING))

    outcome = both(sql_stack, repo_stack, scenario)
    assert outcome[0] == "NOT_COMPARABLE"


def test_an_unknown_mode_and_a_missing_reason_are_refused(sql_stack, repo_stack):
    def scenario(stack):
        stack.seed()
        return stack.cutover(mode="MAYBE"), stack.cutover(reason="  ")

    both(sql_stack, repo_stack, scenario)
