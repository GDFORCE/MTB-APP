"""P1-05: actual-relative and baseline-relative recalculation, end to end.

Runs the Mongo-backed patient service on the in-memory fake AND a real replica
set. Fixed dates and an injected clock throughout: nothing depends on today.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import sys
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application.authoring import (
    ProtocolRecord, ProtocolVersionRecord, ScheduleDefinitionRecord, TrialRecord,
)
from app.application.errors import IdempotencyConflictError
from app.application.patient_schedule import PatientScheduleService
from app.application.patients import PatientAnchorRecord, PatientRecord
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Dependency, DependencyMode, Event, Evidence, OccurrenceFactKind,
    RecurrenceRule, RecurrenceTermination, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorReference, EventReference, OffsetTiming, PositiveTemporalAmount, TemporalAmount,
    TemporalBasis,
)

pytest_plugins = ["tests.contract.conftest"]

BASELINE = date(2026, 10, 1)
TODAY = date(2026, 12, 1)            # injected clock: every recorded date is before it
ORG = UUID("22222222-2222-4222-8222-222222222222")
PERFORMED = OccurrenceFactKind.PERFORMED


def days(value: int, unit: str = "DAY") -> TemporalAmount:
    return TemporalAmount(value=value, unit=unit)


def from_baseline(code: str, offset: int, seq: int, *, event_type="VISIT", amount=None) -> Event:
    return Event(
        code=code, protocol_label=code, display_name=code.title(), event_type=event_type,
        sequence_number=seq, timing=OffsetTiming(
            reference=AnchorReference(code="BASELINE"), offset=amount or days(offset)))


def after(code: str, source: str, offset: int, seq: int, basis: TemporalBasis, *,
          fallback: TemporalBasis | None = None, mode: DependencyMode | None = None) -> Event:
    if mode is None:
        mode = (DependencyMode.ACTUAL_PREVIOUS_EVENT
                if basis in {TemporalBasis.ACTUAL_VISIT, TemporalBasis.ACTUAL_TREATMENT}
                else DependencyMode.NOMINAL)
    return Event(
        code=code, protocol_label=code, display_name=code.title(), event_type="VISIT",
        sequence_number=seq,
        timing=OffsetTiming(
            reference=EventReference(event_code=source, temporal_basis=basis, fallback_basis=fallback),
            offset=days(offset)),
        dependencies=[Dependency(source_event_code=source, dependency_type="TEMPORAL")],
        dependency_mode=mode)


def schedule_of(*events: Event) -> UniversalSchedule:
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=2, source_text="schedule")
    claims: list[ClaimEvidence] = []
    for event in events:
        event.evidence_refs = [evidence.id]
        paths = [("EVENT_NAME", "display_name"), ("TIMING", "timing")]
        if event.recurrence is not None:
            paths.append(("RECURRENCE", "recurrence"))
        if event.dependencies:
            paths += [("DEPENDENCY", "dependencies"), ("DEPENDENCY_MODE", "dependency_mode")]
        claims += [ClaimEvidence(
            evidence_id=evidence.id, claim_type=kind, claim_entity_type="EVENT",
            claim_entity_id=event.id, claim_path=path, confidence=0.9) for kind, path in paths]
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=ScheduleStatus.DRAFT),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE",
                        status="RESOLVED")],
        events=list(events), evidence=[evidence], claim_evidence=claims)


class Env:
    def __init__(self, uow, schedule: UniversalSchedule, *, baseline: date = BASELINE,
                 native_authority: str | None = "MONGO"):
        self.uow = uow
        trial = TrialRecord(organization_id=ORG, external_trial_id=f"t-{uuid4().hex[:6]}")
        protocol = ProtocolRecord(organization_id=ORG, trial_id=trial.id, protocol_number="P-1")
        version = ProtocolVersionRecord(
            organization_id=ORG, trial_id=trial.id, protocol_id=protocol.id, version_label="1",
            document_name="p.pdf", document_uri="private://p.pdf", document_hash="a" * 64)
        definition = ScheduleDefinitionRecord(
            organization_id=ORG, trial_id=trial.id, protocol_id=protocol.id,
            protocol_version_id=version.id, name="Primary")
        schedule.schedule_metadata.protocol_version_id = version.id
        self.version_id = schedule.schedule_version_id
        self.anchor_id = next(a.id for a in schedule.anchors if a.code == "BASELINE")
        patient = PatientRecord(
            organization_id=ORG, trial_id=trial.id, patient_code="P-001",
            external_patient_id="legacy-patient-001",
            current_schedule_version_id=self.version_id,
            authority_plane=native_authority)          # explicit: unclassified is refused
        self.patient_id = patient.id
        self.trial_id = trial.id
        self.external_patient_id = "legacy-patient-001"
        self.clock = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)

        def seed(tx):
            tx.authoring.add_trial(trial)
            tx.authoring.add_protocol(protocol)
            tx.authoring.add_protocol_version(version)
            tx.authoring.add_definition(definition)
            tx.schedules.persist_draft(schedule, schedule_definition_id=definition.id)
            tx.schedules.transition(self.version_id, to_status="IN_REVIEW")
            tx.schedules.mark_approved(
                self.version_id, reviewer_id=uuid4(), approved_at=datetime.now(timezone.utc),
                effective_from=None)
            tx.patients.add(patient)

        uow.run(seed)
        self.definition_id = definition.id
        self.protocol_version_id = version.id
        self.service = PatientScheduleService(uow)
        self.anchor(baseline)

    def approve_second(self, schedule: UniversalSchedule) -> UUID:
        """Approve a second version of the same definition WITHOUT pinning anyone."""
        schedule.schedule_metadata.protocol_version_id = self.protocol_version_id
        schedule.schedule_metadata.version_number = 2
        new_id = schedule.schedule_version_id

        def work(tx):
            tx.schedules.persist_draft(schedule, schedule_definition_id=self.definition_id)
            tx.schedules.transition(new_id, to_status="IN_REVIEW")
            tx.schedules.mark_approved(
                new_id, reviewer_id=uuid4(), approved_at=datetime.now(timezone.utc),
                effective_from=None)

        self.uow.run(work)
        return new_id

    def repin_to(self, schedule: UniversalSchedule) -> UUID:
        """Approve a second version and pin the patient to it DIRECTLY - no impact
        confirm, so there is no assignment lineage and no approved mapping."""
        new_id = self.approve_second(schedule)
        self.uow.run(lambda tx: tx.patients.pin_schedule_version(ORG, self.patient_id, new_id))
        self.version_id = new_id
        self.anchor_id = next(a.id for a in schedule.anchors if a.code == "BASELINE")
        return new_id

    def anchor(self, value: date) -> None:
        from datetime import timedelta
        self.clock += timedelta(minutes=1)
        record = PatientAnchorRecord(
            organization_id=ORG, patient_id=self.patient_id, anchor_definition_id=self.anchor_id,
            anchor_code="BASELINE", value_date=value, status="CONFIRMED",
            source_type="OPERATIONAL_ENROLMENT", recorded_at=self.clock)
        self.uow.run(lambda tx: tx.facts.add_anchor(record))

    def evaluate(self, key: str | None = None):
        evaluation, _ = self.service.evaluate(
            self.patient_id, organization_id=ORG, horizon=date(2027, 12, 31),
            schedule_version_id=self.version_id, idempotency_key=key)
        return evaluation

    def record(self, code: str, visit: date | None, *, kind=PERFORMED, key=None, treatment=None,
               supersedes=None, index: int = 0):
        return self.service.record_actual_occurrence(
            self.patient_id, organization_id=ORG, logical_key=f"{code}:{index}", kind=kind,
            visit_date=visit, treatment_date=treatment, supersedes_occurrence_id=supersedes,
            idempotency_key=key, today=TODAY, reason="test")

    def calendar(self) -> dict[str, object]:
        entries = self.service.current_calendar(self.patient_id, organization_id=ORG)
        return {item.event_code: item for item in entries}

    def evaluations(self):
        return self.uow.read(lambda tx: tx.evaluations.list_evaluations(self.patient_id, self.version_id))


def target(env: Env, code: str) -> date | None:
    return env.calendar()[code].planned_start


# ------------------------------------------------------------- 1-3 bases
def test_headline_actual_relative_moves_and_baseline_imaging_does_not(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), from_baseline("IMAGING", 30, 2),
        after("V3", "V2", 14, 3, TemporalBasis.ACTUAL_VISIT)))
    env.evaluate()
    assert target(env, "V2") == date(2026, 10, 10)
    assert env.calendar()["V3"].status == "BLOCKED" and target(env, "V3") is None
    imaging_before = target(env, "IMAGING")

    outcome = env.record("V2", date(2026, 10, 13))
    assert outcome.reevaluated and outcome.reason == "recalculated"
    assert target(env, "V3") == date(2026, 10, 27)                 # 13 Oct + 14
    assert target(env, "IMAGING") == imaging_before == date(2026, 10, 31)
    changed = {item.event_code: item for item in outcome.changes if item.changed}
    assert set(changed) == {"V2", "V3"}
    assert changed["V3"].new_target == date(2026, 10, 27) and changed["V3"].old_target is None
    assert "actual visit" in changed["V3"].reason
    unchanged = {item.event_code: item for item in outcome.changes if not item.changed}
    assert unchanged["IMAGING"].reason == "does not depend on V2"


def test_the_same_rule_on_current_planned_stays_on_the_planned_relative_date(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.CURRENT_PLANNED)))
    env.evaluate()
    assert target(env, "V3") == date(2026, 10, 24)                 # 10 Oct + 14
    outcome = env.record("V2", date(2026, 10, 13))
    assert target(env, "V3") == date(2026, 10, 24)                 # the late actual is ignored
    assert all(not item.changed or item.event_code == "V2" for item in outcome.changes)


def test_original_planned_is_untouched_by_a_later_planned_date_move(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ORIGINAL_PLANNED)))
    env.evaluate()
    assert target(env, "V3") == date(2026, 10, 24)
    env.anchor(date(2026, 10, 6))                                  # V2 now planned 15 Oct
    env.evaluate()
    assert target(env, "V2") == date(2026, 10, 15)
    assert target(env, "V3") == date(2026, 10, 24)                 # still the ORIGINAL + 14
    assert env.calendar()["V2"].original_planned == date(2026, 10, 10)


# ----------------------------------------------------- 4-6 waiting / fallback
def test_a_missing_required_actual_waits_with_an_explanation_and_no_date(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    evaluation = env.evaluate()
    waiting = env.calendar()["V3"]
    assert waiting.planned_start is None and waiting.status == "BLOCKED"
    event = next(e for e in uow.read(lambda tx: tx.evaluations.list_events(evaluation.id))
                 if e.event_code == "V3")
    assert event.dependency_result["waiting_for_actual"] == ["V2"]
    assert event.nominal_start_date is None and event.earliest_date is None


def test_actual_or_planned_falls_back_visibly_then_recalculates_on_the_actual(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1),
        after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_OR_PLANNED,
              fallback=TemporalBasis.CURRENT_PLANNED, mode=DependencyMode.NOMINAL)))
    evaluation = env.evaluate()
    assert target(env, "V3") == date(2026, 10, 24)
    v3 = next(e for e in uow.read(lambda tx: tx.evaluations.list_events(evaluation.id))
              if e.event_code == "V3")
    used = v3.generation_reason["temporal_basis"][0]
    assert used["fallback_used"] is True and used["source"] == "FALLBACK_CURRENT_PLANNED"

    env.record("V2", date(2026, 10, 13))
    assert target(env, "V3") == date(2026, 10, 27)


def test_attending_without_treatment_blocks_treatment_rules_but_not_visit_rules(uow):
    env = Env(uow, schedule_of(
        from_baseline("DOSE", 9, 1, event_type="TREATMENT"),
        after("AFTER_VISIT", "DOSE", 7, 2, TemporalBasis.ACTUAL_VISIT),
        after("AFTER_TREATMENT", "DOSE", 14, 3, TemporalBasis.ACTUAL_TREATMENT)))
    env.evaluate()
    env.record("DOSE", date(2026, 10, 13), treatment=None)         # attended, treatment withheld
    assert target(env, "AFTER_VISIT") == date(2026, 10, 20)
    assert env.calendar()["AFTER_TREATMENT"].planned_start is None
    assert env.calendar()["AFTER_TREATMENT"].status == "BLOCKED"

    corrected = env.record("DOSE", date(2026, 10, 13), treatment=date(2026, 10, 14),
                           supersedes=env.calendar()["DOSE"] and _last_occurrence(env))
    assert corrected.reevaluated
    assert target(env, "AFTER_TREATMENT") == date(2026, 10, 28)    # 14 Oct + 14
    assert env.calendar()["DOSE"].actual_treatment_date == date(2026, 10, 14)


def _last_occurrence(env: Env) -> UUID:
    rows = env.uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id))
    return rows[-1].id


# --------------------------------------------------- 7-8 history is preserved
def test_a_completed_visit_stays_completed_with_its_actual_after_many_re_evaluations(uow):
    env = Env(uow, schedule_of(from_baseline("V1", 7, 1), from_baseline("V2", 14, 2)))
    env.evaluate()
    env.record("V1", date(2026, 10, 9))
    for _ in range(3):
        env.evaluate()
    env.anchor(date(2026, 10, 5))
    env.evaluate()
    entry = env.calendar()["V1"]
    assert entry.completed and entry.status == "COMPLETED"
    assert entry.actual_visit_date == date(2026, 10, 9)
    assert entry.planned_start == date(2026, 10, 8)                # protected, not recalculated
    assert env.calendar()["V2"].planned_start == date(2026, 10, 19)  # moved with the baseline


def test_every_previous_evaluation_remains_queryable_and_unchanged(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    first = env.evaluate()
    snapshot = uow.read(lambda tx: [
        (e.event_code, e.status, e.nominal_start_date) for e in tx.evaluations.list_events(first.id)])
    env.record("V2", date(2026, 10, 13))
    env.record("V2", date(2026, 10, 15), supersedes=_last_occurrence(env))
    history = env.evaluations()
    assert len(history) == 3 and [e.sequence for e in history] == [1, 2, 3]
    assert history[0].id == first.id
    assert uow.read(lambda tx: [
        (e.event_code, e.status, e.nominal_start_date)
        for e in tx.evaluations.list_events(first.id)]) == snapshot


# ------------------------------------------------------- 9-12 command safety
def test_the_same_key_twice_creates_one_occurrence_and_one_evaluation(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    env.evaluate()
    first = env.record("V2", date(2026, 10, 13), key="req-1")
    second = env.record("V2", date(2026, 10, 13), key="req-1")
    assert second.replayed and not first.replayed
    assert second.occurrence_id == first.occurrence_id and second.evaluation_id == first.evaluation_id
    assert [c.model_dump() for c in second.changes] == [c.model_dump() for c in first.changes]
    assert len(uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id))) == 1
    assert len(env.evaluations()) == 2                              # the initial one + one


def test_the_same_key_with_a_different_payload_is_refused_and_writes_nothing(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    env.evaluate()
    env.record("V2", date(2026, 10, 13), key="req-1")
    with pytest.raises(IdempotencyConflictError):
        env.record("V2", date(2026, 10, 14), key="req-1")
    assert len(uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id))) == 1
    assert len(env.evaluations()) == 2
    assert target(env, "V3") == date(2026, 10, 27)


def test_an_occurrence_nothing_depends_on_is_saved_without_a_new_evaluation(uow):
    env = Env(uow, schedule_of(from_baseline("V1", 7, 1), from_baseline("V2", 14, 2)))
    env.evaluate()
    outcome = env.record("V1", date(2026, 10, 8))
    assert outcome.reevaluated is False and outcome.reason == "no dependent rules"
    assert outcome.changes == [] and outcome.evaluation_id is None
    assert len(env.evaluations()) == 1
    assert len(uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id))) == 1
    assert env.calendar()["V1"].completed                          # still shown as done
    assert uow.read(lambda tx: tx.outbox.list_pending()) == []


def test_a_failure_part_way_through_commits_nothing(uow, monkeypatch):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    env.evaluate()
    outbox_type = type(uow.read(lambda tx: tx.outbox))

    def boom(self, record):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(outbox_type, "add", boom)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        env.record("V2", date(2026, 10, 13), key="req-9")
    assert uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id)) == []
    assert len(env.evaluations()) == 1
    assert env.calendar()["V3"].status == "BLOCKED"
    assert uow.read(lambda tx: tx.commands.find_receipt(ORG, env.patient_id, "req-9")) is None


def test_a_relevant_occurrence_writes_exactly_one_outbox_record(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    env.evaluate()
    outcome = env.record("V2", date(2026, 10, 13), key="req-1")
    env.record("V2", date(2026, 10, 13), key="req-1")                # replay
    pending = uow.read(lambda tx: tx.outbox.list_pending())
    assert len(pending) == 1
    assert pending[0].event_type == "PATIENT_SCHEDULE_CHANGED"
    assert pending[0].payload["evaluation_id"] == str(outcome.evaluation_id)
    assert set(pending[0].payload["changed"]) == {"V2:0", "V3:0"}


# ------------------------------------------------- P1-02 version-scoped identity
def test_a_reused_event_code_in_a_new_version_inherits_no_actual_or_completion(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    env.evaluate()
    env.record("V2", date(2026, 10, 13))
    assert target(env, "V3") == date(2026, 10, 27)

    env.repin_to(schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    env.anchor(BASELINE)                                   # a baseline for the new version
    env.evaluate()
    calendar = env.calendar()
    assert not calendar["V2"].completed and calendar["V2"].status != "COMPLETED"
    assert calendar["V2"].actual_visit_date is None
    assert calendar["V2"].planned_start == date(2026, 10, 10)
    assert calendar["V3"].planned_start is None            # waits: v1's actual is not v2's
    assert calendar["V3"].status == "BLOCKED"

    # Recording in the new version is scoped to it and does not touch version 1.
    env.record("V2", date(2026, 10, 15))
    assert env.calendar()["V3"].planned_start == date(2026, 10, 29)


def test_an_approved_reassignment_is_the_mapping_that_carries_recorded_visits(uow):
    """The explicit counterpart of the isolation test above: a confirmed impact
    proposal (the approved assignment) carries version 1's recorded V2 into
    version 2, so V3 (counted from V2's ACTUAL) is dated in the preview AND after
    confirm, and a correction stays possible."""
    from app.application.impact import PatientScheduleImpactService

    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    env.evaluate()
    env.record("V2", date(2026, 10, 13))
    v1_record = _last_occurrence(env)
    v1_id = env.version_id
    v2_id = env.approve_second(schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 21, 2, TemporalBasis.ACTUAL_VISIT)))

    impact = PatientScheduleImpactService(uow)
    proposal = impact.preview(
        env.patient_id, organization_id=ORG, target_schedule_version_id=v2_id,
        horizon=date(2027, 12, 31), actor_id=uuid4(), reason="Amendment 2")
    by_key = {item["logical_key"]: item for item in proposal.impact["events"]}
    assert by_key["V3:0"]["after"]["date"] == "2026-11-03"   # 13 Oct + 21: carried actual

    impact.confirm(proposal.id, organization_id=ORG, actor_id=uuid4())
    env.version_id = v2_id
    calendar = env.calendar()
    assert calendar["V2"].completed and calendar["V2"].actual_visit_date == date(2026, 10, 13)
    assert target(env, "V3") == date(2026, 11, 3)

    # the carried record can still be corrected: the lineage names version 1
    env.record("V2", date(2026, 10, 15), supersedes=v1_record)
    assert target(env, "V3") == date(2026, 11, 5)
    assert v1_id != v2_id


def repeating(code: str, interval: int, count: int, seq: int) -> Event:
    return Event(
        code=code, protocol_label=code, display_name=code.title(), event_type="VISIT",
        sequence_number=seq,
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"), offset=days(interval)),
        recurrence=RecurrenceRule(
            interval=PositiveTemporalAmount(value=interval, unit="DAY"),
            start_reference=AnchorReference(code="BASELINE"), start_offset=days(interval),
            termination=RecurrenceTermination(type="COUNT", count=count)))


def by_key(env: Env) -> dict[str, object]:
    return {item.logical_key: item for item in
            env.service.current_calendar(env.patient_id, organization_id=ORG)}


def test_a_missed_first_occurrence_does_not_shift_the_second_occurrences_actual(uow):
    env = Env(uow, schedule_of(repeating("CYC", 7, 3, 1), from_baseline("IMAGING", 30, 2)))
    env.evaluate()
    planned = {k: v.planned_start for k, v in by_key(env).items()}
    assert planned["CYC:0"] == date(2026, 10, 8) and planned["CYC:1"] == date(2026, 10, 15)

    env.record("CYC", None, kind=OccurrenceFactKind.MISSED, index=0)
    env.record("CYC", date(2026, 10, 17), index=1)          # attended late
    entries = by_key(env)
    assert entries["CYC:0"].status == "MISSED" and entries["CYC:0"].actual_visit_date is None
    assert entries["CYC:1"].completed and entries["CYC:1"].actual_visit_date == date(2026, 10, 17)
    assert entries["CYC:2"].actual_visit_date is None and not entries["CYC:2"].completed
    # completed / missed history keeps its planned date; the rest stays fixed
    assert entries["CYC:0"].planned_start == planned["CYC:0"]
    assert entries["CYC:1"].planned_start == planned["CYC:1"]
    assert entries["IMAGING:0"].planned_start == date(2026, 10, 31)


def test_out_of_order_actuals_stay_with_their_own_occurrence_index(uow):
    env = Env(uow, schedule_of(repeating("CYC", 7, 3, 1)))
    env.evaluate()
    env.record("CYC", date(2026, 10, 12), index=2)           # third visit first
    env.record("CYC", date(2026, 10, 20), index=1)           # later date, earlier index
    entries = by_key(env)
    assert entries["CYC:2"].actual_visit_date == date(2026, 10, 12)
    assert entries["CYC:1"].actual_visit_date == date(2026, 10, 20)
    assert entries["CYC:0"].actual_visit_date is None
    # correcting index 1 leaves index 2 alone and keeps the first record on file
    first = [r for r in uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id))
             if r.occurrence_index == 1][0]
    env.record("CYC", date(2026, 10, 19), index=1, supersedes=first.id)
    entries = by_key(env)
    assert entries["CYC:1"].actual_visit_date == date(2026, 10, 19)
    assert entries["CYC:2"].actual_visit_date == date(2026, 10, 12)
    assert len(uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id))) == 3


def test_an_event_that_depends_on_its_own_previous_occurrence_is_relevant_to_itself():
    """references_event audit: a repeated visit whose only self-reference is a
    dependency on its own earlier occurrence still has dependants, so recording
    one occurrence must re-evaluate (otherwise the next one keeps a stale date)."""
    from app.domain.schedule.relevance import references_event

    cyc = after("CYC", "CYC", 7, 1, TemporalBasis.ACTUAL_VISIT)
    cyc.timing = OffsetTiming(
        reference=AnchorReference(code="BASELINE"), offset=days(7))   # no self reference
    assert references_event(schedule_of(cyc), "CYC") is True
    alone = from_baseline("CYC", 7, 1)
    assert references_event(schedule_of(alone), "CYC") is False


def test_a_correction_cannot_target_a_record_of_another_version(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    env.evaluate()
    env.record("V2", date(2026, 10, 13))
    v1_record = _last_occurrence(env)

    env.repin_to(schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    env.anchor(BASELINE)
    env.evaluate()
    before = len(uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id)))
    with pytest.raises(KeyError):
        env.record("V2", date(2026, 10, 15), supersedes=v1_record)
    assert len(uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id))) == before


def test_a_different_calculation_horizon_under_the_same_key_conflicts(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    env.evaluate()

    def record(horizon):
        return env.service.record_actual_occurrence(
            env.patient_id, organization_id=ORG, logical_key="V2:0", kind=PERFORMED,
            visit_date=date(2026, 10, 13), idempotency_key="req-h", today=TODAY, horizon=horizon)

    first = record(date(2027, 12, 31))
    assert record(date(2027, 12, 31)).replayed
    with pytest.raises(IdempotencyConflictError):
        record(date(2028, 6, 30))
    assert len(uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id))) == 1
    assert first.evaluation_id is not None


def test_a_replay_after_the_patient_moved_versions_is_refused_not_replayed(uow):
    from app.application.errors import ScheduleVersionChangedError
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    env.evaluate()
    first = env.record("V2", date(2026, 10, 13), key="req-v")
    assert first.schedule_version_id == env.version_id

    env.repin_to(schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    before = len(uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id)))
    with pytest.raises(ScheduleVersionChangedError):
        env.record("V2", date(2026, 10, 13), key="req-v")        # same key, same body
    assert len(uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id))) == before


def test_a_request_prepared_against_another_version_is_refused_without_writes(uow):
    from app.application.errors import ScheduleVersionChangedError
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    env.evaluate()
    with pytest.raises(ScheduleVersionChangedError):
        env.service.record_actual_occurrence(
            env.patient_id, organization_id=ORG, logical_key="V2:0", kind=PERFORMED,
            visit_date=date(2026, 10, 13), today=TODAY, expected_schedule_version_id=uuid4())
    assert uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id)) == []
    ok = env.service.record_actual_occurrence(
        env.patient_id, organization_id=ORG, logical_key="V2:0", kind=PERFORMED,
        visit_date=date(2026, 10, 13), today=TODAY, expected_schedule_version_id=env.version_id)
    assert ok.reevaluated


def test_another_organization_cannot_record_against_the_patient(uow):
    from app.application.errors import RecordNotFoundError
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT)))
    env.evaluate()
    with pytest.raises(RecordNotFoundError):
        env.service.record_actual_occurrence(
            env.patient_id, organization_id=uuid4(), logical_key="V2:0", kind=PERFORMED,
            visit_date=date(2026, 10, 13), today=TODAY)
    assert uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id)) == []


# ------------------------------------------------------- 13-14 chains, dates
def test_a_dependency_chain_moves_downstream_and_leaves_parallel_tracks_alone(uow):
    env = Env(uow, schedule_of(
        from_baseline("V2", 9, 1),
        after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT),
        after("V4", "V3", 7, 3, TemporalBasis.CURRENT_PLANNED, mode=DependencyMode.NOMINAL),
        from_baseline("IMAGING", 30, 4),
        from_baseline("LAB", 21, 5)))
    env.evaluate()
    env.record("V2", date(2026, 10, 13))
    assert target(env, "V3") == date(2026, 10, 27)
    assert target(env, "V4") == date(2026, 11, 3)                   # V3 planned + 7
    assert target(env, "IMAGING") == date(2026, 10, 31)
    assert target(env, "LAB") == date(2026, 10, 22)
    outcome = env.record("V2", date(2026, 10, 16), supersedes=_last_occurrence(env))
    assert target(env, "V3") == date(2026, 10, 30)
    assert target(env, "V4") == date(2026, 11, 6)
    changed = {c.event_code for c in outcome.changes if c.changed}
    assert changed == {"V3", "V4"}      # V2's own planned dates are protected history
    v2 = next(c for c in outcome.changes if c.event_code == "V2")
    assert v2.reason == "recorded outcome; its planned dates are kept"
    assert env.calendar()["V2"].actual_visit_date == date(2026, 10, 16)


def test_month_end_arithmetic_and_a_late_evening_actual_stay_on_the_same_clinical_date(uow):
    env = Env(uow, schedule_of(
        from_baseline("V1", 30, 1),
        Event(code="V2", protocol_label="V2", display_name="V2", event_type="VISIT",
              sequence_number=2,
              timing=OffsetTiming(
                  reference=EventReference(event_code="V1", temporal_basis=TemporalBasis.ACTUAL_VISIT),
                  offset=days(1, "MONTH")),
              dependencies=[Dependency(source_event_code="V1", dependency_type="TEMPORAL")],
              dependency_mode=DependencyMode.ACTUAL_PREVIOUS_EVENT)))
    env.evaluate()
    env.record("V1", date(2026, 1, 31))                             # a late-evening visit, 31 Jan
    assert env.calendar()["V1"].actual_visit_date == date(2026, 1, 31)
    assert target(env, "V2") == date(2026, 2, 28)                   # month-end clamp
    stored = uow.read(lambda tx: [r.actual_date for r in tx.facts.list_occurrences(env.patient_id)])
    assert stored == [date(2026, 1, 31)]


def test_clinical_dates_are_stored_as_calendar_dates_not_instants(mongo_uow):
    env = Env(mongo_uow, schedule_of(from_baseline("V1", 7, 1), from_baseline("V2", 14, 2)))
    env.evaluate()
    env.record("V1", date(2026, 10, 31))
    raw = mongo_uow._database["patient_event_occurrences"].find_one({"patient_id": env.patient_id})
    assert raw["actual_date"] == "2026-10-31"
    assert raw["recorded_at"].tzinfo is not None
    anchor = mongo_uow._database["patient_anchors"].find_one({"patient_id": env.patient_id})
    assert anchor["value_date"] == "2026-10-01"


# ----------------------------------------------------------- validation rules
@pytest.mark.parametrize("visit,message", [
    (date(2026, 12, 2), "cannot be in the future"),
    (date(2025, 6, 1), "days before this patient's earliest"),
])
def test_an_implausible_actual_date_is_refused_before_anything_is_written(uow, visit, message):
    env = Env(uow, schedule_of(from_baseline("V1", 7, 1)))
    env.evaluate()
    with pytest.raises(ValueError, match=message):
        env.record("V1", visit)
    assert uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id)) == []


def test_a_visit_that_is_not_in_the_patients_schedule_is_refused(uow):
    env = Env(uow, schedule_of(from_baseline("V1", 7, 1)))
    env.evaluate()
    with pytest.raises(KeyError):
        env.record("NO_SUCH_VISIT", date(2026, 10, 8))
    assert uow.read(lambda tx: tx.facts.list_occurrences(env.patient_id)) == []
