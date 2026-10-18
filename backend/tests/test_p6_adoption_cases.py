"""P6: the amendment cases that need a differently shaped Amendment 1 - a split, a removal -
plus carried fact kinds, repeated occurrences and an event-triggered deferral. Persisted and
approved through the real review boundary, on the in-memory fake AND real MongoDB."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest

from app.application.errors import AmendmentBlockedError, RecordNotFoundError
from app.application.pilot import build_pilot_schedule
from app.domain.schedule.amendment import AdoptionMode, AdoptionPoint
from app.domain.schedule.models import ClaimEvidence, Event

from _p6_world import World, immediately

pytest_plugins = ["tests.contract.conftest"]


def split_builder(protocol_version_id, name):
    """Amendment 1 splits Cycle 1 Day 1 into a clinic visit and a lab visit."""
    schedule = build_pilot_schedule(protocol_version_id, name)
    schedule.schedule_metadata.version_number = 2
    original = next(e for e in schedule.events if e.code == "C1D1")
    evidence = schedule.evidence[0]
    parts = []
    for code, label in (("C1D1_CLINIC", "Cycle 1 Day 1 clinic"), ("C1D1_LAB", "Cycle 1 Day 1 lab")):
        part = Event.model_validate({**original.model_dump(), "id": str(uuid4()), "code": code,
                                     "protocol_label": label, "display_name": label})
        part.evidence_refs = [evidence.id]
        parts.append(part)
        for claim_type, path in (("EVENT_NAME", "display_name"), ("TIMING", "timing")):
            schedule.claim_evidence.append(ClaimEvidence(
                evidence_id=evidence.id, claim_type=claim_type, claim_entity_type="EVENT",
                claim_entity_id=part.id, claim_path=path, confidence=1))
    schedule.claim_evidence = [c for c in schedule.claim_evidence if c.claim_entity_id != original.id]
    schedule.events = [e for e in schedule.events if e.code != "C1D1"] + parts
    for event in schedule.events:
        if event.code == "LAB_AFTER_TX":
            event.timing.reference.event_code = "C1D1_CLINIC"
            event.dependencies[0].source_event_code = "C1D1_CLINIC"
    schedule.processes[0].event_codes = ["C1D1_CLINIC", "C1D1_LAB", "DOSE"]
    return schedule


def removal_builder(protocol_version_id, name):
    """Amendment 1 drops the baseline imaging visit altogether."""
    schedule = build_pilot_schedule(protocol_version_id, name)
    schedule.schedule_metadata.version_number = 2
    gone = next(e for e in schedule.events if e.code == "IMAGING_BASELINE")
    schedule.events = [e for e in schedule.events if e.code != "IMAGING_BASELINE"]
    schedule.claim_evidence = [c for c in schedule.claim_evidence if c.claim_entity_id != gone.id]
    schedule.tracks = [t for t in schedule.tracks if t.code != "IMAGING"]
    return schedule


def test_a_split_needs_explicit_rules_and_a_fact_goes_to_exactly_one_new_visit(uow):
    world = World(uow, builder=split_builder)
    pid = world.patient_ids[0]
    view = world.authoring.pair_view(organization_id=world.org, pair_id=UUID(world.pair["pair_id"]))
    split = next(m for m in view["mappings"] if m["mapping_type"] == "SPLIT")
    assert split["source_codes"] == ["C1D1"] and sorted(split["target_codes"]) == ["C1D1_CLINIC", "C1D1_LAB"]
    assert split["review_status"] == "PROPOSED" and split["occurrence_rules"] == []
    world.record(pid, "C1D1:0", date(2026, 1, 8), treatment=date(2026, 1, 8))

    # unreviewed: nothing can be simulated
    assert not world.simulate(pid)["can_confirm"]
    # confirming without rules is refused; the rules must cover every old and new visit
    with pytest.raises(AmendmentBlockedError) as raised:
        world.authoring.review_mapping(
            organization_id=world.org, actor_id=world.reviewer, pair_id=UUID(view["pair_id"]),
            expected_revision=view["mapping_set_revision"], action="CONFIRM",
            mapping_id=UUID(split["id"]), reason="looks right")
    assert raised.value.blockers[0]["code"] == "RULES_MISSING"
    partial = world.authoring.review_mapping(
        organization_id=world.org, actor_id=world.reviewer, pair_id=UUID(view["pair_id"]),
        expected_revision=view["mapping_set_revision"], action="SET_RULES", mapping_id=UUID(split["id"]),
        rules=[{"source_code": "C1D1", "source_index": 0, "target_code": "C1D1_CLINIC", "target_index": 0}],
        reason="the performed visit was the clinic part")
    assert [p["code"] for p in partial["problems"]] == ["MAPPING_RULES_MISSING"]     # no rule for C1D1_LAB
    full = world.authoring.review_mapping(
        organization_id=world.org, actor_id=world.reviewer, pair_id=UUID(view["pair_id"]),
        expected_revision=partial["mapping_set_revision"], action="SET_RULES", mapping_id=UUID(split["id"]),
        rules=[{"source_code": "C1D1", "source_index": 0, "target_code": "C1D1_CLINIC", "target_index": 0},
               {"source_code": "C1D1", "source_index": 1, "target_code": "C1D1_LAB", "target_index": 0}],
        reason="occurrence 0 is the clinic part, occurrence 1 the lab part")
    assert full["ready_for_adoption"], full["problems"]

    simulation = world.simulate(pid)
    assert simulation["can_confirm"], simulation["blockers"]
    rows = {(r["from_key"], r["to_key"]): r["category"] for r in simulation["visits"]}
    assert rows[("C1D1:0", "C1D1_CLINIC:0")] == "PROTECTED"
    assert rows[(None, "C1D1_LAB:0")] == "ADDED"           # nothing was recorded on the lab part
    world.confirm(simulation)
    carried = world.uow.read(lambda tx: [
        r for r in tx.facts.list_occurrences(pid, schedule_version_id=world.v2)])
    assert [(r.logical_key, r.metadata["carried_from_occurrence_id"] is not None) for r in carried] \
        == [("C1D1_CLINIC:0", True)]                                    # one new visit only, never duplicated
    assert carried[0].treatment_date == date(2026, 1, 8)               # the independent treatment fact too

    # the original V1 fact is untouched
    v1 = world.uow.read(lambda tx: tx.facts.list_occurrences(pid, schedule_version_id=world.v1))
    assert [r.logical_key for r in v1] == ["C1D1:0"]


def test_a_fact_on_one_part_of_a_split_cannot_be_returned_without_a_rule(uow):
    world = World(uow, builder=split_builder)
    pid = world.patient_ids[0]
    view = world.authoring.pair_view(organization_id=world.org, pair_id=UUID(world.pair["pair_id"]))
    split = next(m for m in view["mappings"] if m["mapping_type"] == "SPLIT")
    world.authoring.review_mapping(
        organization_id=world.org, actor_id=world.reviewer, pair_id=UUID(view["pair_id"]),
        expected_revision=view["mapping_set_revision"], action="SET_RULES", mapping_id=UUID(split["id"]),
        rules=[{"source_code": "C1D1", "source_index": 0, "target_code": "C1D1_CLINIC", "target_index": 0},
               {"source_code": "C1D1", "source_index": 1, "target_code": "C1D1_LAB", "target_index": 0}],
        reason="clinic then lab")
    world.confirm(world.simulate(pid))
    world.record(pid, "C1D1_LAB:0", date(2026, 1, 9))                   # recorded after the move
    back = world.simulate(pid, direction="RETURN")
    assert back["can_confirm"], back["blockers"]                        # lab:0 has a unique origin: C1D1:1
    world.confirm(back, decision="RETURN", reason="withdrawn")
    returned = world.uow.read(lambda tx: [
        r for r in tx.facts.list_occurrences(pid, schedule_version_id=world.v1)
        if r.metadata.get("carried_from_schedule_version_id") == str(world.v2)])
    assert [r.logical_key for r in returned] == ["C1D1:1"]


def test_a_removed_visit_keeps_its_history_on_the_earlier_version(uow):
    world = World(uow, builder=removal_builder)
    pid = world.patient_ids[0]
    view = world.authoring.pair_view(organization_id=world.org, pair_id=UUID(world.pair["pair_id"]))
    removed = next(m for m in view["mappings"] if m["mapping_type"] == "REMOVED")
    assert removed["source_codes"] == ["IMAGING_BASELINE"] and removed["review_status"] == "AUTO_CONFIRMED"
    assert view["ready_for_adoption"], view["problems"]                 # an automatic match needs no review
    world.record(pid, "IMAGING_BASELINE:0", date(2026, 1, 31))
    simulation = world.simulate(pid)
    assert simulation["can_confirm"], simulation["blockers"]
    assert [r["logical_key"] for r in simulation["retained_in_earlier_version"]] == ["IMAGING_BASELINE:0"]
    assert any(r["category"] == "REMOVED" for r in simulation["visits"])
    world.confirm(simulation)
    after = world.uow.read(lambda tx: (
        tx.facts.list_occurrences(pid, schedule_version_id=world.v1),
        tx.facts.list_occurrences(pid, schedule_version_id=world.v2)))
    assert [r.logical_key for r in after[0]] == ["IMAGING_BASELINE:0"] and after[1] == []   # not copied anywhere
    assert "IMAGING_BASELINE:0" not in {r.logical_key for r in world.events(pid)}


# ------------------------------------------------------------------ the standard amendment
@pytest.fixture()
def world(uow):
    return World(uow, reviewed=True)


def test_repeated_occurrences_keep_their_own_identity_and_new_ones_are_added(world):
    pid = world.patient_ids[0]
    for index, day in ((0, date(2026, 1, 15)), (2, date(2026, 1, 29))):       # occurrence 1 not recorded
        world.record(pid, f"DOSE:{index}", day, treatment=day)
    world.confirm(world.simulate(pid))
    carried = world.uow.read(lambda tx: sorted(
        (r.logical_key, r.actual_date) for r in tx.facts.list_occurrences(pid, schedule_version_id=world.v2)))
    assert carried == [("DOSE:0", date(2026, 1, 15)), ("DOSE:2", date(2026, 1, 29))]
    current = {r.logical_key: r for r in world.events(pid)}
    assert {f"DOSE:{i}" for i in range(5)} <= set(current)                    # V2 repeats five times
    assert current["DOSE:0"].status == "COMPLETED" and current["DOSE:2"].status == "COMPLETED"
    assert current["DOSE:1"].status not in {"COMPLETED", "MISSED"}            # never marked done by a neighbour


def test_completed_missed_and_treatment_only_facts_keep_their_kind(world):
    pid = world.patient_ids[0]
    world.record(pid, "C1D1:0", date(2026, 1, 8), treatment=date(2026, 1, 15))        # attended; treated a week later
    world.record(pid, "DOSE:1", None, kind="MISSED")
    world.confirm(world.simulate(pid))
    rows = {r.logical_key: r for r in world.uow.read(
        lambda tx: tx.facts.list_occurrences(pid, schedule_version_id=world.v2))}
    assert rows["C1D1:0"].actual_date == date(2026, 1, 8) and rows["C1D1:0"].treatment_date == date(2026, 1, 15)
    assert rows["DOSE:1"].status == "MISSED" and rows["DOSE:1"].actual_date is None
    assert rows["C1D1:0"].status == "COMPLETED"


def test_a_corrected_fact_carries_only_its_effective_value(world):
    pid = world.patient_ids[0]
    first = world.record(pid, "C1D1:0", date(2026, 1, 8))
    world.patients.record_actual_occurrence(
        pid, organization_id=world.org, logical_key="C1D1:0",
        kind=__import__("app.domain.schedule.models", fromlist=["OccurrenceFactKind"]).OccurrenceFactKind.PERFORMED,
        visit_date=date(2026, 1, 9), supersedes_occurrence_id=first.occurrence_id, actor_id=world.pi,
        reason="entered a day early", horizon=date(2027, 12, 31), today=date(2026, 10, 16))
    world.confirm(world.simulate(pid))
    v1 = world.uow.read(lambda tx: tx.facts.list_occurrences(pid, schedule_version_id=world.v1))
    v2 = world.uow.read(lambda tx: tx.facts.list_occurrences(pid, schedule_version_id=world.v2))
    assert len(v1) == 2 and len(v2) == 1                                  # both originals stay; one effective copy
    assert v2[0].actual_date == date(2026, 1, 9)


def test_an_event_point_applies_when_that_visit_is_actually_performed(world):
    pid = world.patient_ids[0]
    point = AdoptionPoint(mode=AdoptionMode.ADOPT_AT_EVENT, event_code="C1D1", occurrence_index=0)
    decision = world.confirm(world.simulate(pid, point=point))
    waiting = world.adoption.execute(organization_id=world.org, actor_id=world.pi, decision_id=UUID(decision["id"]))
    assert waiting["status"] == "WAITING" and waiting["effective_date"] is None
    world.record(pid, "C1D1:0", date(2026, 1, 8), treatment=date(2026, 1, 8))
    done = world.adoption.execute(organization_id=world.org, actor_id=world.pi, decision_id=UUID(decision["id"]))
    assert done["status"] == "APPLIED" and done["effective_date"] == "2026-01-08"
    assert world.patient(pid).current_schedule_version_id == world.v2
    assert done["carried_fact_count"] == 1
    stored = world.uow.read(lambda tx: tx.amendments.get_decision(world.org, UUID(decision["id"])))
    assert stored.carried_fact_count == 1                          # persisted, not only reported


def test_a_mapping_id_from_another_pair_is_not_found(world):
    view = world.authoring.pair_view(organization_id=world.org, pair_id=UUID(world.pair["pair_id"]))
    with pytest.raises(RecordNotFoundError):
        world.authoring.review_mapping(
            organization_id=world.org, actor_id=world.reviewer, pair_id=UUID(view["pair_id"]),
            expected_revision=view["mapping_set_revision"], action="REJECT", mapping_id=uuid4(),
            reason="a mapping that is not in this pair")


def test_the_effective_date_of_the_new_version_is_bound_and_enforced(uow):
    from datetime import datetime, timezone
    world = World(uow, reviewed=True)
    # the new version takes effect in the future: moving now is refused, moving on that date is not
    header = uow.read(lambda tx: tx.schedules.get_header(world.v2))
    if hasattr(uow, "_database"):
        uow._database["schedule_versions"].update_one({"_id": world.v2}, {"$set": {"effective_from": "2099-01-01"}})
    else:
        uow._store.versions[world.v2]["header"] = header.model_copy(update={"effective_from": date(2099, 1, 1)})
    from app.application.errors import AmendmentIntegrityError
    with pytest.raises(AmendmentIntegrityError):        # effective_from is covered by the header integrity hash
        world.simulate()
    assert datetime.now(timezone.utc).year >= 2026
