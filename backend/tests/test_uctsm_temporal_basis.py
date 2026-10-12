"""P1-01: explicit temporal basis on event references.

A reference says which date of the referenced visit it counts from. Stored
schedules that predate the field are upgraded at READ time to the basis the
engine already used for them, flagged LEGACY_EQUIVALENT, so no date moves and
nothing stored is rewritten.
"""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import models as db
from app.db.base import Base
from app.db.repositories import ScheduleRepository
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Dependency, DependencyMode, Event, Evidence,
    PatientContext, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)
from app.domain.schedule.temporal_basis import (
    effective_temporal_basis, upgrade_event_payload, upgrade_schedule_payload,
)
from app.domain.schedule.timing import (
    AnchorReference, BasisSource, EventReference, OffsetTiming, TemporalAmount,
    TemporalBasis,
)
from app.domain.schedule.validator import ScheduleValidator

BASELINE = date(2026, 1, 1)


def ref(basis=TemporalBasis.CURRENT_PLANNED, **extra) -> EventReference:
    return EventReference(event_code="DOSE", temporal_basis=basis, **extra)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def test_a_reference_without_a_basis_is_not_a_rule():
    with pytest.raises(ValidationError):
        EventReference(event_code="DOSE")  # type: ignore[call-arg]


def test_actual_or_planned_must_name_a_planned_fallback():
    with pytest.raises(ValidationError):
        ref(TemporalBasis.ACTUAL_OR_PLANNED)
    with pytest.raises(ValidationError):
        ref(TemporalBasis.ACTUAL_OR_PLANNED, fallback_basis=TemporalBasis.ACTUAL_VISIT)
    ok = ref(TemporalBasis.ACTUAL_OR_PLANNED, fallback_basis=TemporalBasis.ORIGINAL_PLANNED)
    assert ok.fallback_basis == TemporalBasis.ORIGINAL_PLANNED


@pytest.mark.parametrize("basis", [
    TemporalBasis.ORIGINAL_PLANNED, TemporalBasis.CURRENT_PLANNED, TemporalBasis.ACTUAL_VISIT,
    TemporalBasis.ACTUAL_TREATMENT, TemporalBasis.MANUAL_ANCHOR,
])
def test_a_fallback_is_only_valid_with_actual_or_planned(basis):
    with pytest.raises(ValidationError):
        ref(basis, fallback_basis=TemporalBasis.CURRENT_PLANNED)


def test_the_basis_survives_a_json_round_trip():
    original = OffsetTiming(
        reference=ref(TemporalBasis.ACTUAL_OR_PLANNED, fallback_basis=TemporalBasis.CURRENT_PLANNED,
                      basis_source=BasisSource.EXPLICIT),
        offset=TemporalAmount(value=14, unit="DAY"))
    assert OffsetTiming.model_validate(original.model_dump(mode="json")) == original


# ---------------------------------------------------------------------------
# Read-time legacy upgrade
# ---------------------------------------------------------------------------


def legacy_event(mode, *, dependency_codes=("DOSE",), extra_reference=None) -> dict:
    return {
        "code": "FOLLOW_UP", "dependency_mode": mode,
        "dependencies": [{"source_event_code": code, "dependency_type": "TEMPORAL"}
                         for code in dependency_codes],
        "timing": {"type": "OFFSET", "reference": {"kind": "EVENT", "event_code": "DOSE",
                                                   "occurrence": "CURRENT"},
                   "offset": {"value": 7, "unit": "DAY"}},
        "recurrence": extra_reference,
        "activities": [],
    }


@pytest.mark.parametrize("mode,expected", [
    ("NOMINAL", TemporalBasis.CURRENT_PLANNED),
    ("ACTUAL_PREVIOUS_EVENT", TemporalBasis.ACTUAL_VISIT),
    ("MANUAL", TemporalBasis.MANUAL_ANCHOR),
    ("UNCLEAR", TemporalBasis.CURRENT_PLANNED),
    (None, TemporalBasis.CURRENT_PLANNED),
])
def test_legacy_references_get_the_basis_the_engine_already_used(mode, expected):
    upgraded = upgrade_event_payload(legacy_event(mode))
    reference = upgraded["timing"]["reference"]
    assert reference["temporal_basis"] == expected.value
    assert reference["basis_source"] == "LEGACY_EQUIVALENT"


def test_only_listed_dependencies_follow_the_dependency_mode():
    # The engine swaps in actual dates for the dependencies an event LISTS; any
    # other reference still counts from the planned date.
    upgraded = upgrade_event_payload(legacy_event("ACTUAL_PREVIOUS_EVENT", dependency_codes=()))
    assert upgraded["timing"]["reference"]["temporal_basis"] == "CURRENT_PLANNED"


def test_upgrade_is_idempotent_leaves_stated_bases_alone_and_never_mutates_input():
    payload = legacy_event("ACTUAL_PREVIOUS_EVENT")
    snapshot = copy.deepcopy(payload)
    once = upgrade_event_payload(payload)
    assert payload == snapshot
    assert upgrade_event_payload(once) == once

    stated = legacy_event("ACTUAL_PREVIOUS_EVENT")
    stated["timing"]["reference"].update(temporal_basis="CURRENT_PLANNED", basis_source="EXPLICIT")
    assert upgrade_event_payload(stated)["timing"]["reference"] == {
        "kind": "EVENT", "event_code": "DOSE", "occurrence": "CURRENT",
        "temporal_basis": "CURRENT_PLANNED", "basis_source": "EXPLICIT"}


def test_repeat_rule_and_activity_references_are_upgraded_too():
    payload = legacy_event("NOMINAL", extra_reference={
        "type": "INTERVAL", "start_reference": {"kind": "EVENT", "event_code": "DOSE"}})
    payload["activities"] = [{"timing": {"type": "OFFSET", "reference": {
        "kind": "EVENT", "event_code": "DOSE"}, "offset": {"value": 1, "unit": "DAY"}}}]
    upgraded = upgrade_event_payload(payload)
    assert upgraded["recurrence"]["start_reference"]["temporal_basis"] == "CURRENT_PLANNED"
    assert upgraded["activities"][0]["timing"]["reference"]["basis_source"] == "LEGACY_EQUIVALENT"


def test_whole_schedule_payload_upgrade_touches_every_event_and_nothing_else():
    payload = {"name": "x", "events": [legacy_event("MANUAL"), legacy_event("NOMINAL")]}
    upgraded = upgrade_schedule_payload(payload)
    assert [e["timing"]["reference"]["temporal_basis"] for e in upgraded["events"]] == [
        "MANUAL_ANCHOR", "CURRENT_PLANNED"]
    assert upgraded["name"] == "x"


def test_an_unconfirmed_basis_follows_the_stated_mode_and_is_none_while_unstated():
    placeholder = ref(basis_source=BasisSource.UNCONFIRMED)
    assert effective_temporal_basis(
        placeholder, owner_mode=DependencyMode.ACTUAL_PREVIOUS_EVENT, is_dependency=True,
    ) == TemporalBasis.ACTUAL_VISIT
    assert effective_temporal_basis(
        placeholder, owner_mode=DependencyMode.UNCLEAR, is_dependency=True) is None
    assert effective_temporal_basis(
        placeholder, owner_mode=None, is_dependency=True) is None
    assert effective_temporal_basis(
        ref(TemporalBasis.ACTUAL_VISIT), owner_mode=DependencyMode.NOMINAL, is_dependency=True,
    ) == TemporalBasis.ACTUAL_VISIT


# ---------------------------------------------------------------------------
# Approval validation
# ---------------------------------------------------------------------------


def build(reference, *, mode=DependencyMode.NOMINAL, dependencies=True,
          source_type="TREATMENT", status=ScheduleStatus.APPROVED) -> UniversalSchedule:
    evidence = Evidence(evidence_type="TABLE_CELL", page_number=3, source_text="Day 14")
    source = Event(
        code="DOSE", protocol_label="Dose", display_name="Dose", event_type=source_type,
        timing=OffsetTiming(reference=AnchorReference(code="BASELINE"),
                            offset=TemporalAmount(value=0, unit="DAY")),
        evidence_refs=[evidence.id])
    follow_up = Event(
        code="FOLLOW_UP", protocol_label="Follow-up", display_name="Follow-up", event_type="VISIT",
        timing=OffsetTiming(reference=reference, offset=TemporalAmount(value=7, unit="DAY")),
        dependencies=([Dependency(source_event_code="DOSE", dependency_type="TEMPORAL")]
                      if dependencies else []),
        dependency_mode=mode, evidence_refs=[evidence.id])
    claims = [
        ClaimEvidence(evidence_id=evidence.id, claim_type=claim, claim_entity_type="EVENT",
                      claim_entity_id=event.id, claim_path=path, confidence=.9)
        for event in (source, follow_up)
        for claim, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing"))
    ]
    claims.append(ClaimEvidence(
        evidence_id=evidence.id, claim_type="DEPENDENCY", claim_entity_type="EVENT",
        claim_entity_id=follow_up.id, claim_path="dependencies", confidence=.9))
    claims.append(ClaimEvidence(
        evidence_id=evidence.id, claim_type="DEPENDENCY_MODE", claim_entity_type="EVENT",
        claim_entity_id=follow_up.id, claim_path="dependency_mode", confidence=.9))
    return UniversalSchedule(
        schedule_metadata=ScheduleMetadata(name="Primary", status=status),
        anchors=[Anchor(code="BASELINE", display_name="Baseline", anchor_type="BASELINE",
                        status="RESOLVED")],
        events=[source, follow_up], evidence=[evidence], claim_evidence=claims)


def codes(schedule, *, blocking_only=False) -> set[str]:
    issues = ScheduleValidator().validate(schedule)
    return {i.issue_code for i in issues if i.blocking or not blocking_only
            if i.issue_code.startswith("TEMPORAL_BASIS")}


@pytest.mark.parametrize("basis,mode,dependencies,source_type,expected", [
    (TemporalBasis.CURRENT_PLANNED, DependencyMode.NOMINAL, True, "TREATMENT", set()),
    (TemporalBasis.ACTUAL_VISIT, DependencyMode.ACTUAL_PREVIOUS_EVENT, True, "TREATMENT", set()),
    (TemporalBasis.MANUAL_ANCHOR, DependencyMode.MANUAL, True, "TREATMENT", set()),
    # basis contradicts the stated mode
    (TemporalBasis.ACTUAL_VISIT, DependencyMode.NOMINAL, True, "TREATMENT",
     {"TEMPORAL_BASIS_MODE_MISMATCH"}),
    (TemporalBasis.CURRENT_PLANNED, DependencyMode.ACTUAL_PREVIOUS_EVENT, True, "TREATMENT",
     {"TEMPORAL_BASIS_MODE_MISMATCH"}),
    # manual date with no manual mode
    (TemporalBasis.MANUAL_ANCHOR, DependencyMode.NOMINAL, True, "TREATMENT",
     {"TEMPORAL_BASIS_MANUAL_REQUIRES_MANUAL_MODE"}),
    # actual treatment date of a non-treatment event
    (TemporalBasis.ACTUAL_TREATMENT, DependencyMode.ACTUAL_PREVIOUS_EVENT, True, "VISIT",
     {"TEMPORAL_BASIS_TREATMENT_ON_NON_TREATMENT"}),
    # P1-02: these are executed now, so they are approvable when coherent
    (TemporalBasis.ORIGINAL_PLANNED, DependencyMode.NOMINAL, True, "TREATMENT", set()),
    (TemporalBasis.ACTUAL_TREATMENT, DependencyMode.ACTUAL_PREVIOUS_EVENT, True, "TREATMENT", set()),
    # a planned basis under a mode that counts from the actual previous event
    (TemporalBasis.ORIGINAL_PLANNED, DependencyMode.ACTUAL_PREVIOUS_EVENT, True, "TREATMENT",
     {"TEMPORAL_BASIS_MODE_MISMATCH"}),
])
def test_approval_blocks_a_basis_the_schedule_cannot_honestly_carry_out(
        basis, mode, dependencies, source_type, expected):
    schedule = build(ref(basis), mode=mode, dependencies=dependencies, source_type=source_type)
    assert codes(schedule, blocking_only=True) == expected


def test_actual_or_planned_is_approvable_under_a_nominal_mode_only():
    nominal = build(ref(TemporalBasis.ACTUAL_OR_PLANNED, fallback_basis=TemporalBasis.CURRENT_PLANNED),
                    mode=DependencyMode.NOMINAL)
    assert codes(nominal, blocking_only=True) == set()
    blocked_until_actual = build(
        ref(TemporalBasis.ACTUAL_OR_PLANNED, fallback_basis=TemporalBasis.CURRENT_PLANNED),
        mode=DependencyMode.ACTUAL_PREVIOUS_EVENT)
    assert codes(blocked_until_actual, blocking_only=True) == {"TEMPORAL_BASIS_MODE_MISMATCH"}


def test_unconfirmed_basis_blocks_only_while_the_mode_is_unstated():
    placeholder = ref(basis_source=BasisSource.UNCONFIRMED)
    unstated = build(placeholder, mode=DependencyMode.UNCLEAR)
    assert "TEMPORAL_BASIS_UNCONFIRMED" in codes(unstated, blocking_only=True)
    stated = build(placeholder, mode=DependencyMode.ACTUAL_PREVIOUS_EVENT)
    assert codes(stated, blocking_only=True) == set()


def test_legacy_equivalent_is_flagged_for_review_but_does_not_block():
    schedule = build(ref(basis_source=BasisSource.LEGACY_EQUIVALENT))
    issues = [i for i in ScheduleValidator().validate(schedule)
              if i.issue_code == "TEMPORAL_BASIS_LEGACY_EQUIVALENT"]
    assert len(issues) == 1 and issues[0].blocking is False
    assert not ScheduleValidator.blocking(ScheduleValidator().validate(schedule))


def test_a_basis_on_a_reference_that_is_not_a_listed_dependency_is_honoured_not_flagged():
    schedule = build(ref(TemporalBasis.ACTUAL_VISIT), mode=DependencyMode.NOMINAL,
                     dependencies=False)
    assert codes(schedule) == set()


# ---------------------------------------------------------------------------
# Behaviour is preserved
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode,legacy_expected", [
    (DependencyMode.NOMINAL, date(2026, 1, 8)),
    (DependencyMode.ACTUAL_PREVIOUS_EVENT, date(2026, 1, 12)),
])
def test_a_legacy_upgraded_schedule_dates_exactly_as_the_explicit_one(mode, legacy_expected):
    explicit_basis = (TemporalBasis.ACTUAL_VISIT
                      if mode == DependencyMode.ACTUAL_PREVIOUS_EVENT
                      else TemporalBasis.CURRENT_PLANNED)
    explicit = build(ref(explicit_basis), mode=mode)

    stored = explicit.model_dump(mode="json")
    for event in stored["events"]:
        for node in (event["timing"].get("reference"),):
            if node and node.get("kind") == "EVENT":
                node.pop("temporal_basis"), node.pop("basis_source"), node.pop("fallback_basis")
    upgraded = UniversalSchedule.model_validate(upgrade_schedule_payload(stored))
    follow_up = next(e for e in upgraded.events if e.code == "FOLLOW_UP")
    assert follow_up.timing.reference.basis_source == BasisSource.LEGACY_EQUIVALENT

    def date_of(schedule):
        context = PatientContext(
            patient_id=uuid4(), schedule_version_id=schedule.schedule_version_id,
            anchors={"BASELINE": BASELINE},
            actual_event_values={"DOSE": [date(2026, 1, 5)]})
        result = ScheduleEvaluator().evaluate(schedule, context, horizon=date(2026, 12, 31))
        return next(i for i in result.events if i.event_code == "FOLLOW_UP").timing.nominal_start

    assert date_of(explicit) == legacy_expected
    assert date_of(upgraded) == legacy_expected


# ---------------------------------------------------------------------------
# Persistence: upgraded on read, never rewritten in storage
# ---------------------------------------------------------------------------


def test_stored_events_without_a_basis_are_upgraded_on_read_and_left_untouched():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    organization_id, trial_id, protocol_id = uuid4(), uuid4(), uuid4()
    version_id, definition_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add_all([
            db.Trial(id=trial_id, organization_id=organization_id),
            db.Protocol(id=protocol_id, trial_id=trial_id, protocol_number="ABC-1"),
            db.ProtocolVersion(id=version_id, protocol_id=protocol_id, version_label="1",
                               document_name="p.pdf", document_uri="private://p.pdf",
                               document_hash="a" * 64),
            db.ScheduleDefinition(id=definition_id, protocol_version_id=version_id,
                                  name="Primary"),
        ])
        session.flush()
        schedule = build(ref(TemporalBasis.ACTUAL_VISIT),
                         mode=DependencyMode.ACTUAL_PREVIOUS_EVENT,
                         status=ScheduleStatus.DRAFT)
        schedule.schedule_metadata.protocol_version_id = version_id
        ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition_id)
        session.commit()

        row = session.query(db.Event).filter_by(code="FOLLOW_UP").one()
        stored = dict(row.timing)
        stored["reference"] = {k: v for k, v in stored["reference"].items()
                               if k not in {"temporal_basis", "basis_source", "fallback_basis"}}
        row.timing = stored
        session.commit()

        loaded = ScheduleRepository(session).get(schedule.schedule_version_id)
        follow_up = next(e for e in loaded.events if e.code == "FOLLOW_UP")
        assert follow_up.timing.reference.temporal_basis == TemporalBasis.ACTUAL_VISIT
        assert follow_up.timing.reference.basis_source == BasisSource.LEGACY_EQUIVALENT

        session.expire_all()
        raw = session.query(db.Event).filter_by(code="FOLLOW_UP").one().timing
        assert "temporal_basis" not in raw["reference"]
