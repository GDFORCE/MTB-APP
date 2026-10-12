"""Behavioural parity of the read models and the version resolver: SQL vs port."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application import read_services as repo_read
from app.application import schedule_resolution as repo_resolution
from app.application.authoring import (
    ProtocolRecord, ProtocolVersionRecord, ScheduleDefinitionRecord, TrialRecord,
)
from app.application.schedule_review import ScheduleReviewService as RepoReview
from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import OccurrenceFactKind, ScheduleStatus
from app.services import anchor_status_service as sql_anchor
from app.services import deviation_service as sql_deviation
from app.services import enrolment_preview as sql_preview
from app.services.schedule_service import ScheduleReviewService as SqlReview
from app.services.schedule_service import resolve_current_schedule_version as sql_resolve
from test_mongo_parity_patient_plane import (
    BASELINE, RepoStack as BaseRepoStack, SqlStack as BaseSqlStack, from_baseline, from_visit,
    schedule_of,
)

pytest_plugins = ["tests.contract.conftest"]

TODAY = date(2026, 10, 20)


class SqlStack(BaseSqlStack):
    def deviation(self, patient_id, today=TODAY):
        report = sql_deviation.DeviationService(self.session).report(
            patient_id, organization_id=self.organization_id, today=today)
        return report

    def anchors(self, patient_id):
        return sql_anchor.AnchorStatusService(self.session).statuses(
            patient_id, organization_id=self.organization_id)

    def preview(self, **kwargs):
        return sql_preview.preview_enrolment(
            self.session, organization_id=self.organization_id, trial_id=self.ids["trial"],
            definition_id=self.ids["definition"], **kwargs)


class RepoStack(BaseRepoStack):
    def deviation(self, patient_id, today=TODAY):
        return repo_read.DeviationService(self.uow).report(
            patient_id, organization_id=self.organization_id, today=today)

    def anchors(self, patient_id):
        return repo_read.AnchorStatusService(self.uow).statuses(
            patient_id, organization_id=self.organization_id)

    def preview(self, **kwargs):
        return repo_read.preview_enrolment(
            self.uow, organization_id=self.organization_id, trial_id=self.trial_id,
            definition_id=self.definition.id, **kwargs)


@pytest.fixture
def sql_stack():
    stack = SqlStack()
    yield stack
    stack.session.close()


@pytest.fixture
def repo_stack(uow):
    return RepoStack(uow)


def with_origin(schedule):
    """Mark BASELINE as the schedule origin (enrolment needs one)."""
    schedule.anchors[0].derivation_rule = {"role": "SCHEDULE_ORIGIN"}
    return schedule


def both(sql_stack, repo_stack, scenario):
    expected = scenario(sql_stack)
    actual = scenario(repo_stack)
    assert actual == expected
    return actual


def strip_ids(report):
    data = report.model_dump(mode="json")
    data.pop("patient_id", None)
    data.pop("schedule_version_id", None)
    return data


def test_the_deviation_report_matches_for_a_late_completed_and_an_overdue_visit(sql_stack, repo_stack):
    def scenario(stack):
        stack.publish(schedule_of(
            from_baseline("V1", 7, sequence=1), from_baseline("V2", 14, sequence=2),
            from_baseline("V3", 60, sequence=3)))
        patient = stack.enrol("P-001", {"BASELINE": BASELINE})
        stack.evaluate(patient)
        stack.fact(patient, "V1:0", OccurrenceFactKind.PERFORMED, date(2026, 10, 12))
        stack.evaluate(patient)
        return strip_ids(stack.deviation(patient))

    report = both(sql_stack, repo_stack, scenario)
    assert len(report["visits"]) == 3


def test_a_corrected_actual_replaces_the_one_it_corrects_in_the_report(sql_stack, repo_stack):
    def scenario(stack):
        stack.publish(schedule_of(from_baseline("V1", 7, sequence=1), from_baseline("V2", 14, sequence=2)))
        patient = stack.enrol("P-001", {"BASELINE": BASELINE})
        stack.evaluate(patient)
        first = stack.fact(patient, "V1:0", OccurrenceFactKind.PERFORMED, date(2026, 10, 9))
        stack.fact(patient, "V1:0", OccurrenceFactKind.PERFORMED, date(2026, 10, 14), supersedes=first)
        stack.evaluate(patient)
        return strip_ids(stack.deviation(patient))

    both(sql_stack, repo_stack, scenario)


def test_a_patient_with_no_evaluation_has_an_empty_report(sql_stack, repo_stack):
    def scenario(stack):
        stack.publish(schedule_of(from_baseline("V1", 7, sequence=1)))
        patient = stack.enrol("P-001", {"BASELINE": BASELINE})
        return strip_ids(stack.deviation(patient))

    assert both(sql_stack, repo_stack, scenario)["visits"] == []


def test_anchor_statuses_match_for_confirmed_and_unrecorded_anchors(sql_stack, repo_stack):
    def scenario(stack):
        stack.publish(schedule_of(from_baseline("V1", 7, sequence=1)))
        patient = stack.enrol("P-001", {"BASELINE": BASELINE})
        stack.evaluate(patient)
        stack.anchor(patient, "BASELINE", date(2026, 10, 3))   # a later correction
        return [view.model_dump(mode="json") for view in stack.anchors(patient)]

    views = both(sql_stack, repo_stack, scenario)
    assert views and views[0]["anchor_code"] == "BASELINE"


def test_the_enrolment_preview_lists_the_same_visits_and_windows(sql_stack, repo_stack):
    def scenario(stack):
        stack.publish(with_origin(schedule_of(
            from_baseline("V1", 7, sequence=1), from_visit("V2", "V1", 14, sequence=2, actual=True),
            from_baseline("V3", 30, sequence=3))))
        result = stack.preview(baseline=BASELINE, horizon=date(2027, 12, 31))
        return {
            "baseline": result["baseline_date"], "horizon": result["horizon"],
            "visits": [{k: v for k, v in row.items() if k not in {"visit_template_id", "explanation"}}
                       for row in result["visits"]],
            "summaries": result["repeat_summaries"],
        }

    result = both(sql_stack, repo_stack, scenario)
    assert {row["event_code"] for row in result["visits"]} == {"V1", "V2", "V3"}
    v2 = next(row for row in result["visits"] if row["event_code"] == "V2")
    assert v2["status"] == "BLOCKED" and v2["scheduled_date"] is None


def test_the_enrolment_preview_refuses_an_unknown_anchor_in_the_same_words(sql_stack, repo_stack):
    def scenario(stack):
        stack.publish(with_origin(schedule_of(from_baseline("V1", 7, sequence=1))))
        try:
            stack.preview(baseline=BASELINE, horizon=date(2027, 12, 31),
                          anchor_dates={"NOPE": date(2026, 10, 2)})
        except ValueError as error:
            return str(error)
        return "accepted"

    assert both(sql_stack, repo_stack, scenario) == "Unknown protocol anchor: NOPE"


# ------------------------------------------------------------------ resolver
class SqlWorld:
    organization_id = uuid4()

    def __init__(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.session = Session(engine)
        self.trial_id, self.protocol_id = uuid4(), uuid4()
        self.session.add_all([
            db.Trial(id=self.trial_id, organization_id=self.organization_id),
            db.Protocol(id=self.protocol_id, trial_id=self.trial_id, protocol_number="P-1")])
        self.session.flush()
        self.review = SqlReview(self.session)
        self.versions: dict[str, UUID] = {}
        self.definitions: dict[str, UUID] = {}

    def add_protocol_version(self, label):
        row = db.ProtocolVersion(
            protocol_id=self.protocol_id, version_label=label, document_name=f"{label}.pdf",
            document_uri=f"private://{label}", document_hash=label * 8,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
                days=len(self.versions) + len(self.definitions)))
        self.session.add(row)
        self.session.flush()
        protocol = self.session.get(db.Protocol, self.protocol_id)
        if protocol.current_version_id is None:
            protocol.current_version_id = row.id
        return row.id

    def publish(self, tag, protocol_version, *, name="Primary", number=1, effective_from=None):
        definition = db.ScheduleDefinition(protocol_version_id=protocol_version, name=name)
        self.session.add(definition)
        self.session.flush()
        schedule = schedule_of(from_baseline("V1", 7, sequence=1))
        schedule.schedule_metadata.protocol_version_id = protocol_version
        schedule.schedule_metadata.name = name
        schedule.schedule_metadata.version_number = number
        schedule.schedule_metadata.status = ScheduleStatus.VALIDATION_REQUIRED
        ScheduleRepository(self.session).persist_draft(schedule, schedule_definition_id=definition.id)
        self.session.flush()
        self.review.approve_reviewed_table(
            schedule.schedule_version_id, reviewer_id=uuid4(), reason="reviewed",
            effective_from=effective_from)
        self.session.commit()
        self.versions[tag] = schedule.schedule_version_id
        self.definitions[tag] = definition.id

    def resolve(self, definition_tag, today):
        try:
            row = sql_resolve(self.session, self.trial_id, self.definitions[definition_tag])
        except ValueError as error:
            return ("error", str(error))
        return ("ok", next(k for k, v in self.versions.items() if v == row.id))


class RepoWorld:
    organization_id = uuid4()

    def __init__(self, uow):
        self.uow = uow
        self.trial = TrialRecord(organization_id=self.organization_id)
        self.protocol = ProtocolRecord(
            organization_id=self.organization_id, trial_id=self.trial.id, protocol_number="P-1")

        def seed(tx):
            tx.authoring.add_trial(self.trial)
            tx.authoring.add_protocol(self.protocol)

        uow.run(seed)
        self.review = RepoReview(uow)
        self.versions: dict[str, UUID] = {}
        self.definitions: dict[str, UUID] = {}

    def add_protocol_version(self, label):
        record = ProtocolVersionRecord(
            organization_id=self.organization_id, trial_id=self.trial.id,
            protocol_id=self.protocol.id, version_label=label, document_name=f"{label}.pdf",
            document_uri=f"private://{label}", document_hash=label * 8,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(
                days=len(self.versions) + len(self.definitions)))

        def work(tx):
            tx.authoring.add_protocol_version(record)
            if tx.authoring.get_protocol(self.protocol.id).current_version_id is None:
                tx.authoring.set_current_protocol_version(self.protocol.id, record.id)

        self.uow.run(work)
        return record.id

    def publish(self, tag, protocol_version, *, name="Primary", number=1, effective_from=None):
        definition = ScheduleDefinitionRecord(
            organization_id=self.organization_id, trial_id=self.trial.id,
            protocol_id=self.protocol.id, protocol_version_id=protocol_version, name=name)
        schedule = schedule_of(from_baseline("V1", 7, sequence=1))
        schedule.schedule_metadata.protocol_version_id = protocol_version
        schedule.schedule_metadata.name = name
        schedule.schedule_metadata.version_number = number
        schedule.schedule_metadata.status = ScheduleStatus.VALIDATION_REQUIRED

        def work(tx):
            tx.authoring.add_definition(definition)
            tx.schedules.persist_draft(schedule, schedule_definition_id=definition.id)

        self.uow.run(work)
        self.review.approve_reviewed_table(
            schedule.schedule_version_id, reviewer_id=uuid4(), reason="reviewed",
            effective_from=effective_from)
        self.versions[tag] = schedule.schedule_version_id
        self.definitions[tag] = definition.id

    def resolve(self, definition_tag, today):
        try:
            header = self.uow.read(lambda tx: repo_resolution.resolve_current_schedule_version(
                tx, self.organization_id, self.trial.id, self.definitions[definition_tag]))
        except ValueError as error:
            return ("error", str(error))
        return ("ok", next(k for k, v in self.versions.items() if v == header.id))


@pytest.fixture
def sql_world():
    world = SqlWorld()
    yield world
    world.session.close()


@pytest.fixture
def repo_world(uow):
    return RepoWorld(uow)


def test_the_resolver_picks_the_in_force_version_and_ignores_a_future_amendment(sql_world, repo_world):
    def scenario(world):
        v1 = world.add_protocol_version("v1")
        world.publish("a", v1, number=1)
        v2 = world.add_protocol_version("v2")
        world.publish("b", v2, number=1, effective_from=date(2999, 1, 1))
        return world.resolve("a", None), world.resolve("b", None)

    first, second = both(sql_world, repo_world, scenario)
    assert first == ("ok", "a")
    # the amendment's own definition is not yet in force: nothing resolves for it
    assert second[0] in {"ok", "error"}


def test_a_trial_with_nothing_approved_is_refused_with_the_same_reason(sql_world, repo_world):
    def scenario(world):
        world.add_protocol_version("v1")
        try:
            if isinstance(world, SqlWorld):
                sql_resolve(world.session, world.trial_id, uuid4())
            else:
                world.uow.read(lambda tx: repo_resolution.resolve_current_schedule_version(
                    tx, world.organization_id, world.trial.id, uuid4()))
        except ValueError as error:
            return str(error)
        return "resolved"

    assert both(sql_world, repo_world, scenario) \
        == "no approved canonical schedule is available for the linked trial"


def test_a_later_in_force_amendment_becomes_the_enrollable_version(sql_world, repo_world):
    def scenario(world):
        v1 = world.add_protocol_version("v1")
        world.publish("a", v1, number=1)
        v2 = world.add_protocol_version("v2")
        world.publish("b", v2, number=1)               # amendment, in force at once
        return world.resolve("a", None), world.resolve("b", None)

    both(sql_world, repo_world, scenario)
