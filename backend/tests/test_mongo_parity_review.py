"""Behavioural parity of the review / approval workflow: SQL service vs port."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import sys
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application.authoring import (
    ProtocolRecord, ProtocolVersionRecord, ScheduleDefinitionRecord, TrialRecord,
)
from app.application.schedule_review import ScheduleReviewService as RepoReview
from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.exceptions import ImmutableScheduleError
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Event, Evidence, Qualifier, ScheduleMetadata, ScheduleStatus,
    UniversalSchedule,
)
from app.domain.schedule.timing import AnchorReference, OffsetTiming, TemporalAmount
from app.services.schedule_service import ScheduleReviewService as SqlReview

pytest_plugins = ["tests.contract.conftest"]

ORG = UUID("33333333-3333-4333-8333-333333333333")
REVIEWER = UUID("44444444-4444-4444-8444-444444444444")


def make_schedule(*, with_footnote: bool = False) -> UniversalSchedule:
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=4, source_text="Day 30")
    events = []
    for index, (code, offset) in enumerate((("V1", 7), ("V2", 14)), 1):
        events.append(Event(
            code=code, protocol_label=code, display_name=code.title(), event_type="VISIT",
            sequence_number=index,
            timing=OffsetTiming(reference=AnchorReference(code="BASELINE"),
                                offset=TemporalAmount(value=offset, unit="DAY")),
            evidence_refs=[evidence.id]))
    if with_footnote:
        events[0].qualifiers = [Qualifier(
            marker="a", text="Only if clinically indicated", scope="VISIT", category="UNRESOLVED",
            target_codes=["V1"], resolved=False, evidence_refs=[evidence.id])]
    claims = [ClaimEvidence(
        evidence_id=evidence.id, claim_type=kind, claim_entity_type="EVENT",
        claim_entity_id=event.id, claim_path=path, confidence=0.9)
        for event in events for kind, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing"))]
    if with_footnote:
        claims.append(ClaimEvidence(
            evidence_id=evidence.id, claim_type="QUALIFIER", claim_entity_type="QUALIFIER",
            claim_entity_id=events[0].qualifiers[0].id, claim_path="text", confidence=0.9))
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=ScheduleStatus.VALIDATION_REQUIRED),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE",
                        status="RESOLVED")],
        events=events, evidence=[evidence], claim_evidence=claims)


@dataclass
class Observed:
    status: str
    decisions: list
    audit: list
    issues: list
    current_protocol_version_label: str | None
    approved_by: UUID | None
    effective_from: date | None


class SqlStack:
    def __init__(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.session = Session(engine)
        self.trial_id, self.protocol_id = uuid4(), uuid4()
        self.session.add_all([
            db.Trial(id=self.trial_id, organization_id=ORG, study_title="Study X"),
            db.Protocol(id=self.protocol_id, trial_id=self.trial_id, protocol_number="P-1")])
        self.session.flush()
        self.review = SqlReview(self.session)
        self.versions: list[UUID] = []

    def add_protocol_version(self, label: str) -> UUID:
        row = db.ProtocolVersion(
            protocol_id=self.protocol_id, version_label=label, document_name=f"{label}.pdf",
            document_uri=f"private://{label}", document_hash=label * 8)
        self.session.add(row)
        self.session.flush()
        protocol = self.session.get(db.Protocol, self.protocol_id)
        if protocol.current_version_id is None:
            protocol.current_version_id = row.id
        return row.id

    def draft(self, schedule: UniversalSchedule, protocol_version: UUID) -> UUID:
        definition = db.ScheduleDefinition(protocol_version_id=protocol_version, name=f"S{len(self.versions)}")
        self.session.add(definition)
        self.session.flush()
        schedule.schedule_metadata.protocol_version_id = protocol_version
        schedule.schedule_metadata.name = definition.name
        ScheduleRepository(self.session).persist_draft(schedule, schedule_definition_id=definition.id)
        self.session.flush()
        self.versions.append(schedule.schedule_version_id)
        return schedule.schedule_version_id

    def commit(self) -> None:
        self.session.commit()

    def get_schedule(self, version: UUID) -> UniversalSchedule:
        return ScheduleRepository(self.session).get(version)

    def observe(self, version: UUID) -> Observed:
        row = self.session.get(db.ScheduleVersion, version)
        decisions = sorted(
            (d.decision, d.entity_type, d.field_path, d.reason, d.comment)
            for d in self.session.scalars(select(db.ReviewDecision).where(
                db.ReviewDecision.schedule_version_id == version)))
        audit = sorted(
            a.action for a in self.session.scalars(select(db.AuditEvent).where(
                db.AuditEvent.organization_id == ORG)))
        issues = sorted(
            (i.issue_code, i.status) for i in self.session.scalars(select(db.ValidationIssue).where(
                db.ValidationIssue.schedule_version_id == version)))
        protocol = self.session.get(db.Protocol, self.protocol_id)
        current = self.session.get(db.ProtocolVersion, protocol.current_version_id)
        return Observed(row.status, decisions, audit, issues,
                        current.version_label if current else None, row.approved_by, row.effective_from)


class RepoStack:
    def __init__(self, uow):
        self.uow = uow
        self.trial = TrialRecord(organization_id=ORG, study_title="Study X")
        self.protocol = ProtocolRecord(
            organization_id=ORG, trial_id=self.trial.id, protocol_number="P-1")

        def seed(tx):
            tx.authoring.add_trial(self.trial)
            tx.authoring.add_protocol(self.protocol)

        uow.run(seed)
        self.review = RepoReview(uow)
        self.versions: list[UUID] = []
        self._definitions: dict[UUID, ScheduleDefinitionRecord] = {}

    def add_protocol_version(self, label: str) -> UUID:
        record = ProtocolVersionRecord(
            organization_id=ORG, trial_id=self.trial.id, protocol_id=self.protocol.id,
            version_label=label, document_name=f"{label}.pdf", document_uri=f"private://{label}",
            document_hash=label * 8)

        def work(tx):
            tx.authoring.add_protocol_version(record)
            if tx.authoring.get_protocol(self.protocol.id).current_version_id is None:
                tx.authoring.set_current_protocol_version(self.protocol.id, record.id)

        self.uow.run(work)
        return record.id

    def draft(self, schedule: UniversalSchedule, protocol_version: UUID) -> UUID:
        definition = ScheduleDefinitionRecord(
            organization_id=ORG, trial_id=self.trial.id, protocol_id=self.protocol.id,
            protocol_version_id=protocol_version, name=f"S{len(self.versions)}")
        schedule.schedule_metadata.protocol_version_id = protocol_version
        schedule.schedule_metadata.name = definition.name

        def work(tx):
            tx.authoring.add_definition(definition)
            tx.schedules.persist_draft(schedule, schedule_definition_id=definition.id)

        self.uow.run(work)
        self.versions.append(schedule.schedule_version_id)
        return schedule.schedule_version_id

    def commit(self) -> None:
        pass

    def get_schedule(self, version: UUID) -> UniversalSchedule:
        return self.uow.read(lambda tx: tx.schedules.get(version))

    def observe(self, version: UUID) -> Observed:
        def work(tx):
            header = tx.schedules.get_header(version)
            decisions = sorted(
                (d.decision, d.entity_type, d.field_path, d.reason, d.comment)
                for d in tx.schedules.list_decisions(version))
            audit = sorted(a.action for a in tx.audit.list_for_organization(ORG, limit=10000))
            issues = sorted((i.issue_code, i.status.value) for i in tx.schedules.list_issues(version))
            protocol = tx.authoring.get_protocol(self.protocol.id)
            current = tx.authoring.get_protocol_version(protocol.current_version_id) \
                if protocol.current_version_id else None
            return Observed(header.status, decisions, audit, issues,
                            current.version_label if current else None,
                            header.approved_by, header.effective_from)

        return self.uow.read(work)


@pytest.fixture
def sql_stack():
    stack = SqlStack()
    yield stack
    stack.session.close()


@pytest.fixture
def repo_stack(uow):
    return RepoStack(uow)


def attempt(call):
    """Normalise an outcome: ('ok', value) or ('error', type name, message)."""
    try:
        return ("ok", call())
    except Exception as error:  # noqa: BLE001 - the type and message ARE the contract
        return ("error", type(error).__name__, str(error.args[0]) if error.args else "")


def both(sql_stack, repo_stack, scenario):
    expected = scenario(sql_stack)
    actual = scenario(repo_stack)
    assert actual == expected
    return actual


def confirm_fields(stack, version, schedule):
    for event in schedule.events:
        for path in ("display_name", "timing"):
            stack.review.record_decision(
                version, reviewer_id=REVIEWER, decision="CONFIRM", entity_type="EVENT",
                entity_id=event.id, field_path=path)
    stack.commit()


def test_a_clean_schedule_is_validated_confirmed_and_approved_identically(sql_stack, repo_stack):
    def scenario(stack):
        label = stack.add_protocol_version("v1")
        version = stack.draft(make_schedule(), label)
        stack.commit()
        issues = stack.review.validate(version)
        stack.commit()
        stack.review.submit_for_review(version, actor_id=REVIEWER)
        stack.commit()
        confirm_fields(stack, version, stack.get_schedule(version))
        stack.review.approve(version, reviewer_id=REVIEWER, comment="ok", effective_from=date(2026, 5, 1))
        stack.commit()
        return len(issues), stack.observe(version)

    result = both(sql_stack, repo_stack, scenario)
    assert result[1].status == "APPROVED" and result[1].effective_from == date(2026, 5, 1)


def test_approval_without_the_required_confirmations_is_refused_with_the_same_message(sql_stack, repo_stack):
    def scenario(stack):
        version = stack.draft(make_schedule(), stack.add_protocol_version("v1"))
        stack.commit()
        stack.review.submit_for_review(version, actor_id=REVIEWER)
        stack.commit()
        outcome = attempt(lambda: stack.review.approve(version, reviewer_id=REVIEWER))
        return outcome, stack.observe(version).status

    outcome = both(sql_stack, repo_stack, scenario)
    assert outcome[0][0] == "error" and "outstanding field review" in outcome[0][2]


def test_only_an_in_review_schedule_can_be_approved_or_rejected(sql_stack, repo_stack):
    def scenario(stack):
        version = stack.draft(make_schedule(), stack.add_protocol_version("v1"))
        stack.commit()
        return (
            attempt(lambda: stack.review.approve(version, reviewer_id=REVIEWER)),
            attempt(lambda: stack.review.reject(version, reviewer_id=REVIEWER, reason="no")),
            attempt(lambda: stack.review.reject(uuid4(), reviewer_id=REVIEWER, reason="no")),
        )

    approve_error, reject_error, missing = both(sql_stack, repo_stack, scenario)
    assert approve_error == ("error", "ValueError", "only an in-review schedule can be approved")
    assert reject_error[2] == "only an in-review schedule can be rejected" == missing[2]


def test_reject_records_the_reason_and_the_decision(sql_stack, repo_stack):
    def scenario(stack):
        version = stack.draft(make_schedule(), stack.add_protocol_version("v1"))
        stack.commit()
        stack.review.submit_for_review(version, actor_id=REVIEWER)
        stack.commit()
        stack.review.reject(version, reviewer_id=REVIEWER, reason="timing is wrong")
        stack.commit()
        return stack.observe(version)

    observed = both(sql_stack, repo_stack, scenario)
    assert observed.status == "REJECTED"
    assert ("REJECT", None, None, "timing is wrong", None) in observed.decisions


def test_an_approved_version_is_immutable_to_every_review_action(sql_stack, repo_stack):
    def scenario(stack):
        version = stack.draft(make_schedule(), stack.add_protocol_version("v1"))
        stack.commit()
        stack.review.submit_for_review(version, actor_id=REVIEWER)
        stack.commit()
        schedule = stack.get_schedule(version)
        confirm_fields(stack, version, schedule)
        stack.review.approve(version, reviewer_id=REVIEWER)
        stack.commit()
        event = schedule.events[0]
        return [
            attempt(lambda: stack.review.correct_event(
                version, event, reviewer_id=REVIEWER, reason="late edit")),
            attempt(lambda: stack.review.validate(version)),
            attempt(lambda: stack.review.record_decision(
                version, reviewer_id=REVIEWER, decision="CONFIRM")),
            attempt(lambda: stack.review.bulk_confirm_all_fields(
                version, reviewer_id=REVIEWER, reason="again")),
        ]

    outcomes = both(sql_stack, repo_stack, scenario)
    assert all(item[0] == "error" and item[1] == "ImmutableScheduleError" for item in outcomes)


def test_correcting_an_event_revalidates_and_logs_before_and_after(sql_stack, repo_stack):
    def scenario(stack):
        version = stack.draft(make_schedule(), stack.add_protocol_version("v1"))
        stack.commit()
        event = stack.get_schedule(version).events[0].model_copy(deep=True)
        event.display_name = "Visit One (corrected)"
        issues = stack.review.correct_event(
            version, event, reviewer_id=REVIEWER, reason="typo in the source table")
        stack.commit()
        names = [e.display_name for e in stack.get_schedule(version).events]
        return sorted(i.issue_code for i in issues), names, stack.observe(version)

    result = both(sql_stack, repo_stack, scenario)
    assert "Visit One (corrected)" in result[1]
    assert result[2].status == "VALIDATION_REQUIRED"
    assert ("CORRECT", "EVENT", None, "typo in the source table", None) in result[2].decisions


def test_bulk_confirmation_says_it_was_bulk_and_counts_the_fields(sql_stack, repo_stack):
    def scenario(stack):
        version = stack.draft(make_schedule(), stack.add_protocol_version("v1"))
        stack.commit()
        count = stack.review.bulk_confirm_all_fields(version, reviewer_id=REVIEWER, reason="checked all")
        stack.commit()
        blank = attempt(lambda: stack.review.bulk_confirm_all_fields(
            version, reviewer_id=REVIEWER, reason="  "))
        return count, blank, stack.observe(version)

    count, blank, observed = both(sql_stack, repo_stack, scenario)
    assert count == 4 and blank[0] == "error"
    assert sum(1 for item in observed.decisions if item[0] == "CONFIRM") == 4
    assert "SCHEDULE_BULK_CONFIRMED" in observed.audit


def test_a_footnote_blocks_approval_until_it_is_resolved(sql_stack, repo_stack):
    def scenario(stack):
        version = stack.draft(make_schedule(with_footnote=True), stack.add_protocol_version("v1"))
        stack.commit()
        schedule = stack.get_schedule(version)
        qualifier = schedule.events[0].qualifiers[0]
        blocked = attempt(lambda: stack.review.approve_reviewed_table(
            version, reviewer_id=REVIEWER, reason="reviewed the table"))
        stack.commit()
        stack.review.resolve_qualifier(
            version, qualifier.id, reviewer_id=REVIEWER, decision="ACCEPT",
            reason="footnote applies to V1", category="CONDITION")
        stack.commit()
        stack.review.approve_reviewed_table(
            version, reviewer_id=REVIEWER, reason="reviewed the table")
        stack.commit()
        return blocked[0], stack.observe(version)

    blocked, observed = both(sql_stack, repo_stack, scenario)
    assert blocked == "error" and observed.status == "APPROVED"


def test_a_later_protocol_version_becomes_current_only_on_an_explicit_in_force_approval(sql_stack, repo_stack):
    def scenario(stack):
        first_label = stack.add_protocol_version("v1")
        first = stack.draft(make_schedule(), first_label)
        stack.commit()
        stack.review.approve_reviewed_table(first, reviewer_id=REVIEWER, reason="reviewed v1")
        stack.commit()
        after_first = stack.observe(first).current_protocol_version_label

        second_label = stack.add_protocol_version("v2")
        second = stack.draft(make_schedule(), second_label)
        stack.commit()
        before_second_approval = stack.observe(second).current_protocol_version_label
        stack.review.approve_reviewed_table(
            second, reviewer_id=REVIEWER, reason="reviewed v2", effective_from=date(2999, 1, 1))
        stack.commit()
        future = stack.observe(second).current_protocol_version_label

        third_label = stack.add_protocol_version("v3")
        third = stack.draft(make_schedule(), third_label)
        stack.commit()
        stack.review.approve_reviewed_table(third, reviewer_id=REVIEWER, reason="reviewed v3")
        stack.commit()
        return after_first, before_second_approval, future, stack.observe(third).current_protocol_version_label

    result = both(sql_stack, repo_stack, scenario)
    assert result[0] == "v1" and result[1] == "v1" and result[2] == "v1"  # a future date does not switch


def test_pending_review_lists_only_in_review_versions_for_the_organization(sql_stack, repo_stack):
    def scenario(stack):
        label = stack.add_protocol_version("v1")
        waiting = stack.draft(make_schedule(), label)
        draft_only = stack.draft(make_schedule(), label)
        stack.commit()
        stack.review.submit_for_review(waiting, actor_id=REVIEWER)
        stack.commit()
        rows = stack.review.list_pending_review(organization_id=ORG)
        none = stack.review.list_pending_review(organization_id=uuid4())
        normalised = [{k: v for k, v in row.items()
                       if k not in {"submitted_at", "trial_id", "schedule_definition_id",
                                    "schedule_version_id"}} for row in rows]
        return normalised, none, draft_only is not None

    rows, none, _ = both(sql_stack, repo_stack, scenario)
    assert len(rows) == 1 and rows[0]["trial_name"] == "Study X" and none == []
