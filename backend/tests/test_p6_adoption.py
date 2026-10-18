"""P6-03/04/05: simulation, adoption (immediate, remain, deferred) and return - on the
in-memory fake AND real MongoDB."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest

from app.application.errors import AmendmentBlockedError, IdempotencyConflictError
from app.domain.schedule.amendment import AdoptionMode, AdoptionPoint

from _p6_world import HORIZON, TODAY, World, immediately, remain

pytest_plugins = ["tests.contract.conftest"]


@pytest.fixture()
def world(uow):
    return World(uow, reviewed=True)


def counts(world, patient_id=None):
    patient = patient_id or world.patient_ids[0]
    return world.uow.read(lambda tx: {
        "occurrences": len(tx.facts.list_occurrences(patient)),
        "assignments": len(tx.impacts.list_assignments(patient)),
        "decisions": len(tx.amendments.list_decisions_for_patient(world.org, patient)),
        "outbox": len(tx.outbox.list_pending(limit=1000)),
        "evaluations_v1": tx.evaluations.count_evaluations(patient, world.v1),
        "evaluations_v2": tx.evaluations.count_evaluations(patient, world.v2),
        "patient": tx.patients.get(world.org, patient).current_schedule_version_id})


def visit(simulation, key, *, side="to_key"):
    return next(row for row in simulation["visits"] if row[side] == key)


def test_a_simulation_changes_nothing_clinical(world):
    world.record(world.patient_ids[0], "C1D1:0", date(2026, 1, 8), treatment=date(2026, 1, 8))
    before = counts(world)
    simulation = world.simulate()
    after = counts(world)
    assert simulation["can_confirm"] and not simulation["blockers"]
    assert after == before                      # no fact, assignment, projection, outbox or decision
    assert after["patient"] == world.v1
    kinds = {row["category"] for row in simulation["visits"]}
    assert "PROTECTED" in kinds and "ADDED" in kinds
    assert simulation["from"]["schedule_version_id"] == str(world.v1)
    assert simulation["to"]["schedule_version_id"] == str(world.v2)


def test_repeated_simulation_is_deterministic_and_binds_what_it_used(world):
    world.record(world.patient_ids[0], "C1D1:0", date(2026, 1, 8), treatment=date(2026, 1, 8))
    first, second = world.simulate(), world.simulate()
    assert first["simulation_id"] != second["simulation_id"]        # each preview is its own record
    for key in ("visits", "summary", "blockers", "carried_fact_count", "binding_hash", "mapping_revision"):
        assert first[key] == second[key], key
    stored = world.uow.read(lambda tx: tx.amendments.get_simulation(world.org, UUID(second["simulation_id"])))
    bound = stored.bindings
    assert bound["from_content_hash"] != bound["to_content_hash"]
    assert bound["mapping_revision"] == first["mapping_revision"] and bound["policy_version"]
    assert bound["evaluator_version"] and bound["schema_version"] == "uctsm.v1"
    assert bound["to_effective_from"] is None and bound["context_fingerprint"]
    # an older preview of the same patient is superseded by the newer one
    older = world.uow.read(lambda tx: tx.amendments.get_simulation(world.org, UUID(first["simulation_id"])))
    assert str(getattr(older.status, "value", older.status)) == "SUPERSEDED"


def test_simulation_is_mapping_aware_not_code_aware(world):
    """IMAGING_BASELINE -> IMAGING_BL is a reviewed rename: matched, not 'removed + added'."""
    simulation = world.simulate()
    renamed = visit(simulation, "IMAGING_BL:0")
    assert renamed["from_key"] == "IMAGING_BASELINE:0"
    assert renamed["category"] in {"UNCHANGED", "MOVED"}
    assert not any(row["category"] == "REMOVED" and row["from_key"] == "IMAGING_BASELINE:0"
                   for row in simulation["visits"])
    safety = visit(simulation, "SAFETY_CALL:0")
    assert safety["category"] == "ADDED" and safety["from_key"] is None
    c1d1 = visit(simulation, "C1D1:0")
    assert c1d1["category"] == "MOVED" and c1d1["old_target"] != c1d1["new_target"]


def test_immediate_adoption_moves_the_patient_atomically(world):
    pid = world.patient_ids[0]
    world.record(pid, "C1D1:0", date(2026, 1, 8), treatment=date(2026, 1, 8))
    original_ids = world.uow.read(lambda tx: {str(r.id) for r in tx.facts.list_occurrences(pid)})
    simulation = world.simulate()
    decision = world.confirm(simulation)
    assert decision["status"] == "APPLIED" and decision["open"] is False
    after = counts(world)
    assert after["patient"] == world.v2
    assert after["assignments"] == 2 and after["decisions"] == 1
    assert after["evaluations_v2"] == 1 and after["outbox"] >= 1
    rows = world.uow.read(lambda tx: tx.facts.list_occurrences(pid))
    originals = [r for r in rows if r.schedule_version_id == world.v1]
    copies = [r for r in rows if r.schedule_version_id == world.v2]
    assert {str(r.id) for r in originals} == original_ids          # V1 facts untouched, queryable
    assert len(copies) == len(originals) >= 1
    assert all(r.metadata["carried_from_occurrence_id"] in original_ids for r in copies)
    assert all(r.metadata["source"] == "amendment_mapping" for r in copies)
    # the calendar now shows the committed V2 evaluation
    current = {row.logical_key: row for row in world.events(pid)}
    assert current["C1D1:0"].schedule_version_id == world.v2
    assert current["C1D1:0"].status in {"COMPLETED", "RESOLVED"} or current["C1D1:0"].protected_history
    assert "SAFETY_CALL:0" in current and "IMAGING_BL:0" in current
    assert "IMAGING_BASELINE:0" not in current                      # not carried by code


def test_adoption_is_never_the_default(world):
    pid = world.patient_ids[0]
    assert counts(world)["patient"] == world.v1
    status = world.adoption.patient_status(organization_id=world.org, patient_id=pid)
    assert status["current_version"]["schedule_version_id"] == str(world.v1)
    assert [row["schedule_version_id"] for row in status["available"]] == [str(world.v2)]
    assert status["available"][0]["ready_for_adoption"] is True
    assert status["open_decision"] is None and status["decisions"] == []


def test_remain_is_recorded_and_changes_nothing(world):
    before = counts(world)
    decision = world.confirm(world.simulate(point=remain()), decision="REMAIN",
                             reason="mid-cycle; keep the current plan")
    assert decision["status"] == "REMAINING" and decision["open"] is False
    after = counts(world)
    assert after["patient"] == world.v1 and after["decisions"] == 1
    assert after["occurrences"] == before["occurrences"] and after["assignments"] == before["assignments"]
    with pytest.raises(AmendmentBlockedError):
        world.confirm(world.simulate(), decision="REMAIN")           # not that kind of simulation


def test_an_unreviewed_mapping_blocks_everything(uow):
    world = World(uow, reviewed=False)
    simulation = world.simulate()
    assert not simulation["can_confirm"]
    assert {b["code"] for b in simulation["blockers"]} == {"MAPPING_UNREVIEWED"}
    with pytest.raises(AmendmentBlockedError):
        world.confirm(simulation)
    assert counts(world)["patient"] == world.v1


def test_a_stale_simulation_is_refused_and_marked(world):
    pid = world.patient_ids[0]
    simulation = world.simulate()
    world.record(pid, "C1D1:0", date(2026, 1, 8), treatment=date(2026, 1, 8))   # a new fact
    with pytest.raises(AmendmentBlockedError) as raised:
        world.confirm(simulation)
    assert any(b["code"] == "STALE_INPUT" and b["path"] == "context_fingerprint"
               for b in raised.value.blockers)
    assert counts(world)["patient"] == world.v1
    status = world.uow.read(lambda tx: tx.amendments.get_simulation(
        world.org, UUID(simulation["simulation_id"])).status)
    assert str(getattr(status, "value", status)) == "SUPERSEDED"
    # a fresh simulation works
    assert world.confirm(world.simulate())["status"] == "APPLIED"


def test_an_expired_simulation_is_refused(world):
    from datetime import timedelta
    from app.application.records import utc_now
    simulation = world.simulate()

    sim_id = UUID(simulation["simulation_id"])
    past = utc_now() - timedelta(minutes=1)
    if hasattr(world.uow, "_database"):          # real MongoDB
        world.uow._database["amendment_simulations"].update_one(
            {"_id": sim_id}, {"$set": {"expires_at": past}})
    else:
        store = world.uow._store.amendment_simulations
        store[sim_id] = store[sim_id].model_copy(update={"expires_at": past})
    with pytest.raises(AmendmentBlockedError) as raised:
        world.confirm(simulation)
    assert raised.value.blockers[0]["code"] == "SIMULATION_EXPIRED"
    assert counts(world)["patient"] == world.v1


def test_a_mapping_change_after_the_preview_invalidates_it(world):
    simulation = world.simulate()
    view = world.authoring.pair_view(organization_id=world.org, pair_id=UUID(world.pair["pair_id"]))
    rename = next(m for m in view["mappings"] if m["mapping_type"] == "RENAMED")
    world.authoring.review_mapping(
        organization_id=world.org, actor_id=world.reviewer, pair_id=UUID(view["pair_id"]),
        expected_revision=view["mapping_set_revision"], action="REJECT",
        mapping_id=UUID(rename["id"]), reason="actually a different visit")
    with pytest.raises(AmendmentBlockedError) as raised:
        world.confirm(simulation)
    assert raised.value.blockers
    assert counts(world)["patient"] == world.v1


def test_idempotent_replay_and_key_conflict(world):
    simulation = world.simulate()
    first = world.confirm(simulation, idempotency_key="adopt-1")
    again = world.confirm(simulation, idempotency_key="adopt-1")
    assert again["id"] == first["id"] and again["replayed"] is True
    after = counts(world)
    assert after["decisions"] == 1 and after["assignments"] == 2 and after["evaluations_v2"] == 1


def test_the_same_key_for_a_different_decision_conflicts(world):
    point = AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 3, 1))
    world.confirm(world.simulate(point=point), idempotency_key="k-1")
    with pytest.raises(IdempotencyConflictError):
        world.confirm(world.simulate(point=remain()), decision="REMAIN", idempotency_key="k-1")


def test_cross_tenant_ids_are_not_found(world):
    simulation = world.simulate()
    from app.application.errors import RecordNotFoundError
    with pytest.raises(RecordNotFoundError):
        world.adoption.confirm(
            organization_id=uuid4(), actor_id=world.pi, simulation_id=UUID(simulation["simulation_id"]),
            decision="ADOPT", reason="not my tenant")
    with pytest.raises(RecordNotFoundError):
        world.adoption.simulate(
            organization_id=uuid4(), actor_id=world.pi, patient_id=world.patient_ids[0],
            direction="ADOPT", point=immediately(), to_schedule_version_id=world.v2)


# --------------------------------------------------------------------------- deferred
def deferred(world, point, pid=None, **kwargs):
    return world.confirm(world.simulate(patient_id=pid, point=point), **kwargs)


def test_a_date_point_waits_and_never_applies_early(world):
    point = AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 3, 1))
    decision = deferred(world, point)
    assert decision["status"] == "WAITING" and decision["open"] is True
    early = world.adoption.execute(
        organization_id=world.org, actor_id=world.pi, decision_id=UUID(decision["id"]),
        as_of=date(2027, 2, 28))
    assert early["status"] == "WAITING" and early["trigger_state"]["due"] is False
    assert counts(world)["patient"] == world.v1
    done = world.adoption.execute(
        organization_id=world.org, actor_id=world.pi, decision_id=UUID(decision["id"]),
        as_of=date(2027, 3, 1))
    assert done["status"] == "APPLIED" and done["effective_date"] == "2027-03-01"
    assert counts(world)["patient"] == world.v2


def test_an_event_point_waits_for_a_real_actual_date(world):
    pid = world.patient_ids[0]
    point = AdoptionPoint(mode=AdoptionMode.ADOPT_AT_EVENT, event_code="C1D1", occurrence_index=0)
    decision = deferred(world, point)
    waiting = world.adoption.execute(
        organization_id=world.org, actor_id=world.pi, decision_id=UUID(decision["id"]), as_of=TODAY)
    assert waiting["status"] == "WAITING" and waiting["effective_date"] is None   # nothing invented
    assert counts(world)["patient"] == world.v1


def test_deferred_decisions_validate_the_point_against_the_current_version(world):
    for point in (
            AdoptionPoint(mode=AdoptionMode.ADOPT_AT_EVENT, event_code="NOPE", occurrence_index=0),
            AdoptionPoint(mode=AdoptionMode.ADOPT_AT_EVENT, event_code="C1D1", occurrence_index=3),
            AdoptionPoint(mode=AdoptionMode.ADOPT_AT_EVENT, event_code="DOSE", occurrence_index=9),
            AdoptionPoint(mode=AdoptionMode.ADOPT_AT_CYCLE, repeat_block_code="NO_BLOCK", cycle_number=1),
            AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=TODAY)):
        simulation = world.simulate(point=point)
        assert simulation["blockers"], point
        assert not simulation["can_confirm"]


def test_one_open_decision_per_patient_and_replacement_is_explicit(world):
    first = deferred(world, AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 3, 1)))
    with pytest.raises(AmendmentBlockedError) as raised:
        deferred(world, AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 4, 1)))
    assert raised.value.blockers[0]["code"] == "OPEN_DECISION_EXISTS"
    with pytest.raises(AmendmentBlockedError):
        world.confirm(world.simulate(), idempotency_key="immediate-over-open")
    second = deferred(
        world, AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 4, 1)),
        replaces_decision_id=UUID(first["id"]))
    history = world.uow.read(lambda tx: {
        str(r.id): r.status.value for r in tx.amendments.list_decisions_for_patient(
            world.org, world.patient_ids[0])})
    assert history[first["id"]] == "CANCELLED" and history[second["id"]] == "WAITING"


def test_cancel_an_open_decision(world):
    first = deferred(world, AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 3, 1)))
    cancelled = world.adoption.cancel(
        organization_id=world.org, actor_id=world.pi, decision_id=UUID(first["id"]), reason="changed mind")
    assert cancelled["status"] == "CANCELLED" and cancelled["open"] is False
    with pytest.raises(AmendmentBlockedError):
        world.adoption.cancel(
            organization_id=world.org, actor_id=world.pi, decision_id=UUID(first["id"]), reason="again")
    assert world.confirm(world.simulate())["status"] == "APPLIED"      # the slot is free again


def test_a_due_decision_whose_impact_changed_needs_reconfirmation(world):
    """SAME_IMPACT: a fact recorded while it waited changes a visit's date, so the approved
    impact no longer applies and a person must confirm again."""
    pid = world.patient_ids[0]
    point = AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 3, 1))
    decision = deferred(world, point, future_policy="SAME_IMPACT")
    world.record(pid, "C1D1:0", date(2026, 2, 3), treatment=date(2026, 2, 3))   # re-dates the future
    with pytest.raises(AmendmentBlockedError) as raised:
        world.adoption.execute(
            organization_id=world.org, actor_id=world.pi, decision_id=UUID(decision["id"]),
            as_of=date(2027, 3, 1))
    assert {b["code"] for b in raised.value.blockers} & {"IMPACT_CHANGED", "INPUT_CHANGED"}
    stored = world.uow.read(lambda tx: tx.amendments.get_decision(world.org, UUID(decision["id"])))
    assert stored.status.value == "NEEDS_RECONFIRMATION" and stored.is_open is True
    assert counts(world)["patient"] == world.v1


def test_a_structural_policy_accepts_date_shifts_of_future_visits(world):
    pid = world.patient_ids[0]
    point = AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 3, 1))
    decision = deferred(world, point)                      # SAME_STRUCTURE (default)
    world.record(pid, "C1D1:0", date(2026, 2, 3), treatment=date(2026, 2, 3))
    done = world.adoption.execute(
        organization_id=world.org, actor_id=world.pi, decision_id=UUID(decision["id"]),
        as_of=date(2027, 3, 1))
    assert done["status"] == "APPLIED"


def test_a_lost_lease_cannot_commit_and_an_expired_one_is_reclaimed(world):
    from datetime import timedelta
    from app.application.records import utc_now
    point = AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 3, 1))
    decision = deferred(world, point)
    did = UUID(decision["id"])
    claim = world.adoption._run(lambda tx: world.adoption._claim(tx, world.org, did, date(2027, 3, 1)))
    assert claim["lease_token"]
    again = world.adoption._run(lambda tx: world.adoption._claim(tx, world.org, did, date(2027, 3, 1)))
    assert again["lease_token"] is None and again["view"]["status"] == "EXECUTING"   # held
    from app.application.errors import StaleRevisionError
    with pytest.raises(StaleRevisionError):
        world.adoption._run(lambda tx: world.adoption._execute_claimed(
            tx, world.org, world.pi, did, "not-the-token", date(2027, 3, 1), ()))

    def expire(tx):
        record = tx.amendments.get_decision(world.org, did)
        tx.amendments.save_decision(
            record.model_copy(update={"lease_expires_at": utc_now() - timedelta(minutes=1)}),
            expected_revision=record.revision)

    world.uow.run(expire)
    done = world.adoption.execute(
        organization_id=world.org, actor_id=world.pi, decision_id=did, as_of=date(2027, 3, 1))
    assert done["status"] == "APPLIED" and done["attempts"] == 2
    assert counts(world)["evaluations_v2"] == 1                          # applied exactly once


def test_scan_due_applies_only_what_is_due(world):
    deferred(world, AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 3, 1)))
    assert world.adoption.scan_due(organization_id=world.org, as_of=date(2027, 2, 1))[0]["status"] == "WAITING"
    assert world.adoption.scan_due(organization_id=world.org, as_of=date(2027, 3, 1))[0]["status"] == "APPLIED"
    assert world.adoption.scan_due(organization_id=world.org, as_of=date(2027, 3, 2)) == []


# ----------------------------------------------------------------------------- return
def adopt(world, pid=None):
    return world.confirm(world.simulate(patient_id=pid))


def test_return_is_a_new_authorised_evaluated_decision(world):
    pid = world.patient_ids[0]
    world.record(pid, "C1D1:0", date(2026, 1, 8), treatment=date(2026, 1, 8))
    adopt(world)
    before = counts(world)
    simulation = world.simulate(direction="RETURN")
    assert simulation["can_confirm"], simulation["blockers"]
    decision = world.confirm(simulation, decision="RETURN", reason="amendment withdrawn at this site")
    assert decision["status"] == "APPLIED" and decision["linked_decision_id"]
    after = counts(world)
    assert after["patient"] == world.v1 and after["decisions"] == 2
    assert after["evaluations_v1"] == before["evaluations_v1"] + 1       # a NEW evaluation
    # V2 records are never deleted
    assert after["occurrences"] >= before["occurrences"]
    current = {row.logical_key: row for row in world.events(pid)}
    assert "IMAGING_BASELINE:0" in current and "SAFETY_CALL:0" not in current


def test_return_preserves_a_fact_recorded_after_adoption(world):
    pid = world.patient_ids[0]
    adopt(world)
    world.record(pid, "DOSE:0", date(2026, 1, 15), treatment=date(2026, 1, 15))   # native V2 fact
    decision = world.confirm(world.simulate(direction="RETURN"), decision="RETURN", reason="withdrawn")
    assert decision["status"] == "APPLIED"
    v1_rows = world.uow.read(lambda tx: tx.facts.list_occurrences(pid, schedule_version_id=world.v1))
    returned = [r for r in v1_rows if r.metadata.get("carried_from_schedule_version_id") == str(world.v2)]
    assert [r.logical_key for r in returned] == ["DOSE:0"]
    v2_rows = world.uow.read(lambda tx: tx.facts.list_occurrences(pid, schedule_version_id=world.v2))
    assert any(r.logical_key == "DOSE:0" and "carried_from_occurrence_id" not in r.metadata
               for r in v2_rows)                                           # the V2 fact stays


def test_return_is_blocked_by_a_fact_on_a_visit_only_the_later_version_has(world):
    pid = world.patient_ids[0]
    adopt(world)
    world.record(pid, "SAFETY_CALL:0", date(2026, 3, 1))
    simulation = world.simulate(direction="RETURN")
    assert not simulation["can_confirm"]
    [problem] = [b for b in simulation["blockers"] if b["code"] == "FACT_UNMAPPED"]
    assert problem["path"] == "SAFETY_CALL:0" and "later version" in problem["detail"]
    with pytest.raises(AmendmentBlockedError):
        world.confirm(simulation, decision="RETURN", reason="withdrawn")
    assert counts(world)["patient"] == world.v2


def test_nothing_to_return_from_when_never_adopted(world):
    with pytest.raises(AmendmentBlockedError) as raised:
        world.simulate(direction="RETURN")
    assert raised.value.blockers[0]["code"] == "NOTHING_TO_RETURN_FROM"


def test_re_adoption_onto_a_version_with_history_is_refused(world):
    adopt(world)
    world.confirm(world.simulate(direction="RETURN"), decision="RETURN", reason="withdrawn")
    simulation = world.simulate()
    assert any(b["code"] == "TARGET_HAS_HISTORY" for b in simulation["blockers"])


# ------------------------------------------------------------------------- carry rules
def test_a_non_native_authority_plane_cannot_be_moved(world):
    from app.application.authority import PlaneAuthorityError

    def downgrade(tx):
        patient = tx.patients.get(world.org, world.patient_ids[0])
        tx.patients.save(patient.model_copy(update={"authority_plane": None}))

    world.uow.run(downgrade)
    with pytest.raises(PlaneAuthorityError):
        world.simulate()


def test_active_patient_commands_block_the_move(world):
    from app.application.patient_commands import PatientCommandService
    from app.domain.schedule.commands import CommandType
    pid = world.patient_ids[0]
    # a pause on the V1 treatment track is an operational exception the move cannot carry
    from app.application.command_ledger import append as ledger_append

    def add(tx):
        ledger_append(
            tx, organization_id=world.org, patient_id=pid, schedule_version_id=world.v1,
            command_type=CommandType.PAUSE_TRACK, payload={"type": "PAUSE_TRACK", "track_code": "TREATMENT"},
            effective_at=date(2026, 2, 1), actor_id=world.pi, reason="held", payload_hash="h" * 8,
            track_code="TREATMENT")

    world.uow.run(add)
    simulation = world.simulate()
    assert any(b["code"] == "ACTIVE_COMMANDS_NOT_CARRIED" for b in simulation["blockers"])
    assert not simulation["can_confirm"]


def test_both_patients_move_independently(world):
    first, second = world.patient_ids
    adopt(world, first)
    assert counts(world, first)["patient"] == world.v2
    assert counts(world, second)["patient"] == world.v1
    simulation = world.simulate(second, point=remain())
    assert simulation["summary"]
    assert world.confirm(simulation, decision="REMAIN")["status"] == "REMAINING"
