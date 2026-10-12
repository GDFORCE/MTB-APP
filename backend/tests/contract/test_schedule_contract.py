"""Contract for the schedule-version repository (fake and MongoDB)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.application.authoring import ReviewDecisionRecord
from app.application.errors import (
    DuplicateRecordError, ImmutableRecordError, RecordNotFoundError,
)
from app.domain.schedule.models import Dependency, ValidationIssue

from .support import ORG, build_schedule, seed_definition


@pytest.fixture
def stored(uow):
    definition = seed_definition(uow)
    schedule = build_schedule(definition)
    uow.run(lambda tx: tx.schedules.persist_draft(
        schedule, schedule_definition_id=definition.id, extraction_run_id=None))
    return uow, definition, schedule


def expected_after_persist(schedule):
    """Persisting drops dependencies on events that are not in the version."""
    expected = schedule.model_copy(deep=True)
    for event in expected.events:
        event.dependencies = [d for d in event.dependencies if d.source_event_code != "GHOST"]
    return expected


def test_round_trip_is_lossless_apart_from_dropped_dangling_dependencies(stored):
    uow, _, schedule = stored
    loaded = uow.read(lambda tx: tx.schedules.get(schedule.schedule_version_id))
    original = expected_after_persist(schedule)
    original.events.sort(key=lambda e: (e.sequence_number is not None, e.sequence_number or 0, e.code))
    assert loaded.model_dump(exclude={"created_at"}) == original.model_dump(exclude={"created_at"})


def test_events_read_back_ordered_by_sequence_then_code_with_unnumbered_first(stored):
    uow, _, schedule = stored
    loaded = uow.read(lambda tx: tx.schedules.get(schedule.schedule_version_id))
    assert [e.code for e in loaded.events] == ["AD_HOC", "DOSE", "FOLLOW_UP"]


def test_a_version_cannot_be_persisted_twice(stored):
    uow, definition, schedule = stored
    with pytest.raises(ValueError, match="already exists"):
        uow.run(lambda tx: tx.schedules.persist_draft(schedule, schedule_definition_id=definition.id))


def test_a_version_number_is_unique_per_definition(stored):
    uow, definition, _ = stored
    clash = build_schedule(definition, version_number=1)
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.schedules.persist_draft(clash, schedule_definition_id=definition.id))
    assert uow.read(lambda tx: tx.schedules.max_version_number(definition.id)) == 1


def test_a_self_dependency_is_refused(uow):
    definition = seed_definition(uow)
    schedule = build_schedule(definition)
    schedule.events[0].dependencies.append(
        Dependency(source_event_code=schedule.events[0].code, dependency_type="TEMPORAL"))
    with pytest.raises(ValueError, match="cannot depend on itself"):
        uow.run(lambda tx: tx.schedules.persist_draft(schedule, schedule_definition_id=definition.id))
    with pytest.raises(RecordNotFoundError):
        uow.read(lambda tx: tx.schedules.get(schedule.schedule_version_id))


def test_a_missing_protocol_version_is_refused(uow):
    definition = seed_definition(uow)
    schedule = build_schedule(definition)
    schedule.schedule_metadata.protocol_version_id = None
    with pytest.raises(ValueError, match="protocol_version_id"):
        uow.run(lambda tx: tx.schedules.persist_draft(schedule, schedule_definition_id=definition.id))


def test_replace_event_persists_the_edit_and_nothing_else(stored):
    uow, _, schedule = stored
    target = next(e for e in schedule.events if e.code == "FOLLOW_UP").model_copy(deep=True)
    target.display_name = "Follow-up (edited)"
    target.requires_review = True
    uow.run(lambda tx: tx.schedules.replace_event(schedule.schedule_version_id, target))
    loaded = uow.read(lambda tx: tx.schedules.get(schedule.schedule_version_id))
    edited = next(e for e in loaded.events if e.code == "FOLLOW_UP")
    assert edited.display_name == "Follow-up (edited)" and edited.requires_review is True
    assert next(e for e in loaded.events if e.code == "DOSE").display_name == "Dose"


def test_replace_event_on_an_unknown_event_is_not_found(stored):
    uow, _, schedule = stored
    stranger = schedule.events[0].model_copy(update={"id": uuid4()}, deep=True)
    with pytest.raises(RecordNotFoundError):
        uow.run(lambda tx: tx.schedules.replace_event(schedule.schedule_version_id, stranger))


def test_a_renamed_event_is_followed_by_its_dependents(stored):
    uow, _, schedule = stored
    dose = next(e for e in schedule.events if e.code == "DOSE").model_copy(deep=True)
    dose.code = "DOSE_1"
    uow.run(lambda tx: tx.schedules.replace_event(schedule.schedule_version_id, dose))
    loaded = uow.read(lambda tx: tx.schedules.get(schedule.schedule_version_id))
    follow_up = next(e for e in loaded.events if e.code == "FOLLOW_UP")
    assert [d.source_event_code for d in follow_up.dependencies] == ["DOSE_1"]


def test_an_event_code_cannot_collide_within_a_version(stored):
    uow, _, schedule = stored
    dose = next(e for e in schedule.events if e.code == "DOSE").model_copy(deep=True)
    dose.code = "FOLLOW_UP"
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.schedules.replace_event(schedule.schedule_version_id, dose))


def test_open_issues_are_replaced_but_resolved_ones_are_kept(stored):
    uow, _, schedule = stored
    version = schedule.schedule_version_id
    original = uow.read(lambda tx: tx.schedules.list_issues(version))
    assert len(original) == 1
    uow.run(lambda tx: tx.schedules.set_issue_status(
        version, original[0].id, "RESOLVED", resolved_by=uuid4(), note="checked"))
    fresh = [ValidationIssue(issue_code="UNRESOLVED_REFERENCE", severity="ERROR", message="x",
                             blocking=True)]
    uow.run(lambda tx: tx.schedules.replace_validation_issues(version, fresh))
    after = uow.read(lambda tx: tx.schedules.list_issues(version))
    assert [(i.issue_code, i.status.value) for i in after] == [
        ("AMBIGUOUS_TIMING", "RESOLVED"), ("UNRESOLVED_REFERENCE", "OPEN")]


def test_status_changes_are_guarded_and_bump_the_revision(stored):
    uow, _, schedule = stored
    version = schedule.schedule_version_id
    header = uow.run(lambda tx: tx.schedules.transition(
        version, to_status="IN_REVIEW", expected={"VALIDATION_REQUIRED", "DRAFT"}))
    assert header.status == "IN_REVIEW" and header.revision == 1
    with pytest.raises(ImmutableRecordError):
        uow.run(lambda tx: tx.schedules.transition(
            version, to_status="IN_REVIEW", expected={"VALIDATION_REQUIRED"}))
    assert uow.read(lambda tx: tx.schedules.get_header(version)).revision == 1


def test_approval_requires_review_and_freezes_the_version(stored):
    uow, _, schedule = stored
    version = schedule.schedule_version_id
    with pytest.raises(ImmutableRecordError):
        uow.run(lambda tx: tx.schedules.mark_approved(
            version, reviewer_id=uuid4(), approved_at=datetime.now(timezone.utc), effective_from=None))
    uow.run(lambda tx: tx.schedules.transition(version, to_status="IN_REVIEW"))
    reviewer, when = uuid4(), datetime(2026, 4, 1, 9, 30, tzinfo=timezone.utc)
    header = uow.run(lambda tx: tx.schedules.mark_approved(
        version, reviewer_id=reviewer, approved_at=when, effective_from=date(2026, 5, 1)))
    assert (header.status, header.approved_by, header.effective_from) == (
        "APPROVED", reviewer, date(2026, 5, 1))
    assert header.approved_at == when
    event = next(e for e in schedule.events if e.code == "DOSE").model_copy(deep=True)
    with pytest.raises(ImmutableRecordError):
        uow.run(lambda tx: tx.schedules.replace_event(version, event))
    with pytest.raises(ImmutableRecordError):
        uow.run(lambda tx: tx.schedules.replace_validation_issues(version, []))
    with pytest.raises(ImmutableRecordError):
        uow.run(lambda tx: tx.schedules.transition(version, to_status="REJECTED"))
    loaded = uow.read(lambda tx: tx.schedules.get(version))
    assert loaded.schedule_metadata.status.value == "APPROVED"
    assert loaded.schedule_metadata.effective_from == date(2026, 5, 1)


def test_decisions_are_appended_and_filterable(stored):
    uow, _, schedule = stored
    version, alice, bob = schedule.schedule_version_id, uuid4(), uuid4()

    def write(tx):
        tx.schedules.add_decision(ReviewDecisionRecord(
            schedule_version_id=version, decision="CONFIRM", reviewer_id=alice,
            entity_id=schedule.events[0].id, field_path="timing"))
        tx.schedules.add_decision(ReviewDecisionRecord(
            schedule_version_id=version, decision="CORRECT", reviewer_id=bob, reason="fix"))
        tx.schedules.add_decision(ReviewDecisionRecord(
            schedule_version_id=version, decision="REJECT", reviewer_id=alice))

    uow.run(write)
    assert len(uow.read(lambda tx: tx.schedules.list_decisions(version))) == 3
    mine = uow.read(lambda tx: tx.schedules.list_decisions(
        version, reviewer_id=alice, decisions={"CONFIRM", "APPROVE"}))
    assert [d.decision for d in mine] == ["CONFIRM"]


def test_headers_are_listed_per_organization(stored):
    uow, _, schedule = stored
    headers = uow.read(lambda tx: tx.schedules.list_headers(organization_id=ORG))
    assert [h.id for h in headers] == [schedule.schedule_version_id]
    assert uow.read(lambda tx: tx.schedules.list_headers(organization_id=uuid4())) == []
    assert uow.read(lambda tx: tx.schedules.find_by_extraction_run(uuid4())) is None


def test_a_failed_persist_leaves_no_partial_version(uow):
    definition = seed_definition(uow)
    schedule = build_schedule(definition)
    schedule.validation_issues = [schedule.validation_issues[0], schedule.validation_issues[0]]
    with pytest.raises(DuplicateRecordError):
        uow.run(lambda tx: tx.schedules.persist_draft(schedule, schedule_definition_id=definition.id))
    with pytest.raises(RecordNotFoundError):
        uow.read(lambda tx: tx.schedules.get(schedule.schedule_version_id))
    assert uow.read(lambda tx: tx.schedules.max_version_number(definition.id)) == 0
