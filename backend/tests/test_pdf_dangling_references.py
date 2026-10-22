"""P7-01: a dangling reference survives storage unchanged, is reported with a stable code, and blocks approval."""
from uuid import uuid4

import pytest

from app.application.pilot import build_pilot_schedule
from app.application.schedule_review import ScheduleReviewService
from app.domain.schedule.models import Dependency
from app.domain.schedule.timing import AnchorReference, EventReference, OffsetTiming, TemporalAmount, TimeUnit
from app.domain.schedule.validator import ScheduleValidator
from test_p6_approval_hash import Draft

pytest_plugins = ["tests.contract.conftest"]


def event(schedule, code):
    return next(row for row in schedule.events if row.code == code)


def ghost_dependency(schedule):
    event(schedule, "LAB_AFTER_TX").dependencies = [Dependency(source_event_code="GHOST", dependency_type="TEMPORAL")]


def ghost_event_timing(schedule):
    row = event(schedule, "LAB_AFTER_TX")
    row.timing = OffsetTiming(reference=EventReference(event_code="GHOST", temporal_basis=row.timing.reference.temporal_basis),
                              offset=TemporalAmount(value=7, unit=TimeUnit.DAY))


def ghost_anchor_timing(schedule):
    row = event(schedule, "IMAGING_BASELINE")
    row.timing = OffsetTiming(reference=AnchorReference(code="GHOST"), offset=TemporalAmount(value=30, unit=TimeUnit.DAY))


def ghost_track(schedule):
    event(schedule, "IMAGING_BASELINE").track_id = uuid4()


def ghost_process_member(schedule):
    schedule.processes[0].event_codes = [*schedule.processes[0].event_codes, "GHOST"]


def ghost_event_evidence(schedule):
    event(schedule, "C1D1").evidence_refs = [*event(schedule, "C1D1").evidence_refs, uuid4()]


def ghost_claim_evidence(schedule):
    schedule.claim_evidence[0].evidence_id = uuid4()


def ghost_recurrence_start(schedule):
    event(schedule, "DOSE").recurrence.start_reference = AnchorReference(code="GHOST")


def ghost_track_anchor(schedule):
    schedule.tracks[0].anchor_code = "GHOST"


def duplicate_event_code(schedule):
    twin = event(schedule, "C1D1").model_copy(deep=True)
    twin.id = uuid4()
    schedule.events.append(twin)


CASES = [ghost_dependency, ghost_event_timing, ghost_anchor_timing, ghost_track, ghost_process_member,
         ghost_event_evidence, ghost_claim_evidence, ghost_recurrence_start, ghost_track_anchor, duplicate_event_code]


def mutated(mutation):
    def build(protocol_id, name):
        schedule = build_pilot_schedule(protocol_id, name)
        mutation(schedule)
        return schedule
    return build


def blocking_codes(schedule):
    return {row.issue_code for row in ScheduleValidator().validate(schedule) if row.blocking}


#: Refused at WRITE time by both stores (fail closed, stated clearly); the validator still names them if seen in memory.
REFUSED = {ghost_track: "TRACK_REFERENCE_UNKNOWN", duplicate_event_code: "DUPLICATE_EVENT"}
STORED = [fn for fn in CASES if fn not in REFUSED]


@pytest.mark.parametrize("mutation", STORED, ids=lambda fn: fn.__name__)
def test_a_dangling_reference_is_stored_as_written_found_by_the_validator_and_blocks_approval(uow, mutation):
    draft = Draft(uow, builder=mutated(mutation))
    stored, expected = draft.schedule(), mutated(mutation)(uuid4(), "expected")
    # nothing was silently dropped or repaired on the way in
    assert {row.code for row in stored.events} == {row.code for row in expected.events}
    assert len(stored.claim_evidence) == len(expected.claim_evidence)
    assert len(stored.tracks) == len(expected.tracks) and len(stored.processes) == len(expected.processes)
    assert blocking_codes(stored), f"{mutation.__name__}: the validator reported nothing blocking"
    with pytest.raises(ValueError):
        ScheduleReviewService(uow).approve_reviewed_table(draft.version, reviewer_id=uuid4(), reason="Reviewed")
    assert draft.header().status != "APPROVED" and draft.header().content_hash is None


@pytest.mark.parametrize("mutation", list(REFUSED), ids=lambda fn: fn.__name__)
def test_a_reference_the_store_cannot_hold_is_refused_clearly_and_nothing_is_kept(uow, mutation):
    with pytest.raises((ValueError, Exception)) as refused:
        Draft(uow, builder=mutated(mutation))
    assert type(refused.value).__name__ in {"ValueError", "DuplicateRecordError"}
    assert REFUSED[mutation] in blocking_codes(mutated(mutation)(uuid4(), "in memory"))       # the validator agrees


def test_the_unmutated_fixture_has_no_blocking_finding_so_the_cases_above_are_the_cause(uow):
    draft = Draft(uow)
    assert blocking_codes(draft.schedule()) == set()
    ScheduleReviewService(uow).approve_reviewed_table(draft.version, reviewer_id=uuid4(), reason="Reviewed")
    assert draft.header().status == "APPROVED"
