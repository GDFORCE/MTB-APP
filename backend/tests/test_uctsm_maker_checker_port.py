"""P3-04: maker-checker on the repository-port plane (memory AND real MongoDB).

The SQL plane is covered in ``test_uctsm_maker_checker.py``. Both planes enforce
the same rule at their own lowest approval boundary (``_approve``), which
``approve`` and ``approve_reviewed_table`` share.
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application.authoring import ReviewDecisionRecord
from app.application.errors import ImmutableRecordError
from app.application.schedule_review import ScheduleReviewService
from app.authorization.maker_checker import (
    MakerCheckerPolicy, MakerCheckerViolation, Scope,
)
from app.domain.schedule.models import Event
from test_mongo_parity_review import ORG, RepoStack, make_schedule

pytest_plugins = ["tests.contract.conftest"]

MAKER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CHECKER = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
THIRD = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def service(stack: RepoStack, policy: MakerCheckerPolicy | None = None) -> ScheduleReviewService:
    return ScheduleReviewService(stack.uow, maker_checker=policy)


def drafted(uow):
    stack = RepoStack(uow)
    version = stack.draft(make_schedule(), stack.add_protocol_version("1.0"))
    return stack, version


def retime(stack: RepoStack, version: UUID, offset: int) -> Event:
    event = stack.get_schedule(version).events[0].model_copy(deep=True)
    event.timing = event.timing.model_copy(update={
        "offset": event.timing.offset.model_copy(update={"value": offset})})
    return event


ON = MakerCheckerPolicy(enabled=True)


def test_material_correction_records_provenance_and_blocks_self_approval(uow):
    stack, version = drafted(uow)
    review = service(stack, ON)
    review.correct_event(version, retime(stack, version, 9), reviewer_id=MAKER, reason="source says 9")

    header = uow.read(lambda tx: tx.schedules.get_header(version))
    assert header.last_material_editor_id == MAKER
    assert header.material_editor_ids == [MAKER]

    review.submit_for_review(version, actor_id=MAKER)
    with pytest.raises(MakerCheckerViolation) as refused:
        review.approve_reviewed_table(version, reviewer_id=MAKER, reason="mine")
    assert refused.value.outcome.reason_code == "SELF_APPROVAL_OF_MATERIAL_EDIT"
    after = uow.read(lambda tx: tx.schedules.get_header(version))
    assert after.status != "APPROVED" and after.approved_by is None


def test_an_independent_approver_succeeds_and_the_audit_carries_the_stamp(uow):
    stack, version = drafted(uow)
    review = service(stack, ON)
    review.correct_event(version, retime(stack, version, 9), reviewer_id=MAKER, reason="source")
    review.submit_for_review(version, actor_id=MAKER)
    review.approve_reviewed_table(version, reviewer_id=CHECKER, reason="independently reviewed")

    def read(tx):
        header = tx.schedules.get_header(version)
        audit = [a for a in tx.audit.list_for_organization(ORG, limit=1000)
                 if a.action == "SCHEDULE_APPROVED"]
        return header, audit

    header, audit = uow.read(read)
    assert header.status == "APPROVED" and header.approved_by == CHECKER
    stamp = audit[-1].after["maker_checker"]
    assert stamp["maker_checker_enabled"] is True
    assert stamp["last_material_editor_id"] == str(MAKER)
    assert stamp["approver_id"] == str(CHECKER)
    assert stamp["reason_code"] == "INDEPENDENT_APPROVER"


def test_direct_approve_call_is_covered_too(uow):
    stack, version = drafted(uow)
    review = service(stack, ON)
    review.correct_event(version, retime(stack, version, 9), reviewer_id=MAKER, reason="source")
    review.submit_for_review(version, actor_id=MAKER)
    with pytest.raises(MakerCheckerViolation):
        review.approve(version, reviewer_id=MAKER, comment="direct")


def test_disabled_policy_changes_nothing(uow):
    stack, version = drafted(uow)
    review = service(stack, MakerCheckerPolicy(enabled=False))
    review.correct_event(version, retime(stack, version, 9), reviewer_id=MAKER, reason="source")
    review.approve_reviewed_table(version, reviewer_id=MAKER, reason="own work, policy off")
    assert uow.read(lambda tx: tx.schedules.get_header(version)).status == "APPROVED"


def test_label_only_correction_is_not_material(uow):
    stack, version = drafted(uow)
    review = service(stack, ON)
    event = stack.get_schedule(version).events[0].model_copy(deep=True)
    event.display_name = "Renamed visit"
    review.correct_event(version, event, reviewer_id=MAKER, reason="label")
    header = uow.read(lambda tx: tx.schedules.get_header(version))
    assert header.last_material_editor_id is None and header.material_editor_ids == []
    review.approve_reviewed_table(version, reviewer_id=MAKER, reason="no material edits")
    assert uow.read(lambda tx: tx.schedules.get_header(version)).status == "APPROVED"


def test_any_editor_scope_excludes_every_editor(uow):
    stack, version = drafted(uow)
    scoped = MakerCheckerPolicy(enabled=True, scope=Scope.ANY_EDITOR)
    review = service(stack, scoped)
    review.correct_event(version, retime(stack, version, 9), reviewer_id=MAKER, reason="a")
    second = stack.get_schedule(version).events[1].model_copy(deep=True)
    second.timing = second.timing.model_copy(update={
        "offset": second.timing.offset.model_copy(update={"value": 20})})
    review.correct_event(version, second, reviewer_id=CHECKER, reason="b")
    header = uow.read(lambda tx: tx.schedules.get_header(version))
    assert header.last_material_editor_id == CHECKER
    assert set(header.material_editor_ids) == {MAKER, CHECKER}
    review.submit_for_review(version, actor_id=THIRD)
    with pytest.raises(MakerCheckerViolation):
        review.approve_reviewed_table(version, reviewer_id=MAKER, reason="first editor")
    # LAST_EDITOR scope would have admitted MAKER; THIRD is independent in both
    review.approve_reviewed_table(version, reviewer_id=THIRD, reason="independent")


def test_legacy_decisions_without_provenance_exclude_only_their_reviewers(uow):
    stack, version = drafted(uow)

    def legacy(tx):
        tx.schedules.add_decision(ReviewDecisionRecord(
            schedule_version_id=version, entity_type="EVENT", decision="CORRECT",
            field_path="timing", reviewer_id=MAKER, reason="before provenance existed"))
        tx.schedules.add_decision(ReviewDecisionRecord(
            schedule_version_id=version, entity_type="EVENT", decision="CORRECT",
            field_path="display_name", reviewer_id=THIRD, reason="label only"))

    uow.run(legacy)
    review = service(stack, ON)
    review.submit_for_review(version, actor_id=CHECKER)
    with pytest.raises(MakerCheckerViolation) as refused:
        review.approve_reviewed_table(version, reviewer_id=MAKER, reason="mine")
    assert refused.value.outcome.reason_code == "LEGACY_DRAFT_APPROVER_IS_A_CORRECTOR"
    # a label-only decision never makes THIRD a presumed editor
    review.approve_reviewed_table(version, reviewer_id=THIRD, reason="independent")
    assert uow.read(lambda tx: tx.schedules.get_header(version)).status == "APPROVED"


def test_an_approved_version_cannot_be_given_provenance_afterwards(uow):
    stack, version = drafted(uow)
    service(stack, MakerCheckerPolicy(enabled=False)).approve_reviewed_table(
        version, reviewer_id=CHECKER, reason="ok")
    from datetime import datetime, timezone
    with pytest.raises(ImmutableRecordError):
        uow.run(lambda tx: tx.schedules.record_material_edit(
            version, editor_id=MAKER, at=datetime.now(timezone.utc)))
