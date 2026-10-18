"""P6-01: the content hash is precise.

Every mutation below is applied to a deep copy of ONE schedule, so a changed hash can
only be caused by that field - not by fresh identifiers or metadata.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.application.pilot import build_pilot_schedule
from app.domain.schedule.amendment import (
    CONTENT_HASH_VERSION, HashStatus, header_integrity_hash, schedule_content_hash,
    verify_content_hash,
)
from app.domain.schedule.models import (
    HoldResumePolicy, ScheduleStatus, StudyDimension, TrackRebasePolicy, ValidationIssue,
)
from app.domain.schedule.timing import TemporalBasis

PROTOCOL_VERSION = uuid4()


def base():
    schedule = build_pilot_schedule(PROTOCOL_VERSION, "Hash fixture")
    schedule.schedule_metadata.status = ScheduleStatus.APPROVED
    return schedule


def event(schedule, code):
    return next(item for item in schedule.events if item.code == code)


def track(schedule, code):
    return next(item for item in schedule.tracks if item.code == code)


def set_offset(schedule):
    event(schedule, "C1D1").timing.offset.value += 1


def set_basis(schedule):
    event(schedule, "LAB_AFTER_TX").timing.reference.temporal_basis = TemporalBasis.ACTUAL_VISIT


def widen_window(schedule):
    event(schedule, "IMAGING_BASELINE").window.before.value += 1


def drop_dependency(schedule):
    event(schedule, "LAB_AFTER_TX").dependencies = []


def dependency_mode(schedule):
    event(schedule, "LAB_AFTER_TX").dependency_mode = None


def recurrence_count(schedule):
    event(schedule, "DOSE").recurrence.termination.count += 1


def recurrence_interval(schedule):
    event(schedule, "DOSE").recurrence.interval.value += 1


def rebase_policy(schedule):
    track(schedule, "IMAGING").rebase_policy = TrackRebasePolicy.PREVIOUS_ACTUAL_OCCURRENCE


def track_membership(schedule):
    event(schedule, "IMAGING_BASELINE").track_id = track(schedule, "LABS").id


def link_relation(schedule):
    schedule.track_links[0].relation = "FOLLOWS_PLANNED"


def drop_link(schedule):
    schedule.track_links = []


def anchor_rule(schedule):
    schedule.anchors[0].derivation_rule = {"role": "OTHER"}


def add_population(schedule):
    schedule.populations.append(
        StudyDimension(code="ADULT", protocol_label="Adults", display_name="Adults"))


def process_policy(schedule):
    schedule.processes[0].hold_resume_policy = HoldResumePolicy.NOMINAL


def evidence_text(schedule):
    schedule.evidence[0].source_text = "A different rule"


def claim_confidence(schedule):
    schedule.claim_evidence[0].confidence = 0.5


def remove_event(schedule):
    schedule.events = [item for item in schedule.events if item.code != "DOSE"]


def rename_visit(schedule):
    event(schedule, "C1D1").display_name = "Cycle 1 Day 1 (renamed)"


def version_number(schedule):
    schedule.schedule_metadata.version_number = 2


def protocol_lineage(schedule):
    schedule.schedule_metadata.protocol_version_id = uuid4()


MATERIAL = [
    set_offset, set_basis, widen_window, drop_dependency, dependency_mode, recurrence_count,
    recurrence_interval, rebase_policy, track_membership, link_relation, drop_link, anchor_rule,
    add_population, process_policy, evidence_text, claim_confidence, remove_event, rename_visit,
    version_number, protocol_lineage,
]


@pytest.mark.parametrize("mutate", MATERIAL, ids=lambda fn: fn.__name__)
def test_each_material_change_alone_changes_the_hash(mutate):
    original = base()
    baseline = schedule_content_hash(original)
    changed = original.model_copy(deep=True)
    assert schedule_content_hash(changed) == baseline          # the copy itself is identical
    mutate(changed)
    assert schedule_content_hash(changed) != baseline, f"{mutate.__name__} was not detected"


def test_the_hash_is_stable_and_a_pure_function_of_content():
    schedule = base()
    first = schedule_content_hash(schedule)
    for _ in range(3):
        assert schedule_content_hash(schedule.model_copy(deep=True)) == first
    assert len(first) == 64 and int(first, 16) >= 0


def test_collection_order_and_dict_key_order_do_not_matter():
    schedule = base()
    baseline = schedule_content_hash(schedule)
    shuffled = schedule.model_copy(deep=True)
    shuffled.events.reverse()
    shuffled.tracks.reverse()
    shuffled.claim_evidence.reverse()
    assert schedule_content_hash(shuffled) == baseline
    shuffled.anchors[0].derivation_rule = dict(reversed(list(shuffled.anchors[0].derivation_rule.items())))
    assert schedule_content_hash(shuffled) == baseline


def test_lifecycle_only_changes_do_not_change_the_content_hash():
    schedule = base()
    baseline = schedule_content_hash(schedule)
    changed = schedule.model_copy(deep=True)
    changed.schedule_metadata.status = ScheduleStatus.SUPERSEDED
    changed.schedule_metadata.effective_from = date(2027, 3, 1)
    changed.validation_issues.append(ValidationIssue(
        severity="WARNING", issue_code="X", message="note", entity_type="EVENT", entity_id=uuid4()))
    assert schedule_content_hash(changed) == baseline


def test_effective_from_is_protected_by_the_header_integrity_hash_instead():
    common = dict(
        schedule_version_id=uuid4(), organization_id=uuid4(), trial_id=uuid4(), protocol_id=uuid4(),
        protocol_version_id=uuid4(), schedule_definition_id=uuid4(), version_number=2,
        based_on_schedule_version_id=uuid4(), approved_by=uuid4(), approved_at=None,
        content_hash="a" * 64, content_hash_version=CONTENT_HASH_VERSION)
    one = header_integrity_hash(effective_from=date(2027, 1, 1), **common)
    assert one == header_integrity_hash(effective_from=date(2027, 1, 1), **common)
    assert one != header_integrity_hash(effective_from=date(2027, 1, 2), **common)
    assert one != header_integrity_hash(effective_from=None, **common)
    assert one != header_integrity_hash(
        effective_from=date(2027, 1, 1), **{**common, "based_on_schedule_version_id": uuid4()})
    assert one != header_integrity_hash(
        effective_from=date(2027, 1, 1), **{**common, "content_hash": "b" * 64})


def test_verification_never_declares_a_hashless_or_foreign_version_verified():
    schedule = base()
    stored = schedule_content_hash(schedule)
    assert verify_content_hash(schedule, stored, CONTENT_HASH_VERSION) == HashStatus.VERIFIED
    assert verify_content_hash(schedule, None, None) == HashStatus.UNVERIFIED_LEGACY
    assert verify_content_hash(schedule, "", CONTENT_HASH_VERSION) == HashStatus.UNVERIFIED_LEGACY
    assert verify_content_hash(schedule, stored, "schedule-content.v0") == HashStatus.UNSUPPORTED_VERSION
    assert verify_content_hash(schedule, stored, None) == HashStatus.UNSUPPORTED_VERSION
    tampered = schedule.model_copy(deep=True)
    set_offset(tampered)
    assert verify_content_hash(tampered, stored, CONTENT_HASH_VERSION) == HashStatus.MISMATCH
