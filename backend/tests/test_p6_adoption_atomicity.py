"""P6-05: atomicity and concurrency. Failure injection runs on both stores; the racing
tests need real transactions and so run against MongoDB only."""

from __future__ import annotations

import threading
from datetime import date
from uuid import UUID

import pytest

from app.application.amendment_adoption import AdoptionService
from app.application.errors import AmendmentBlockedError
from app.application.patient_schedule import PatientScheduleService
from app.domain.schedule.amendment import AdoptionMode, AdoptionPoint

from _p6_world import World, remain

pytest_plugins = ["tests.contract.conftest"]


def snapshot(world, pid=None):
    pid = pid or world.patient_ids[0]
    return world.uow.read(lambda tx: {
        "occurrences": sorted(str(r.id) for r in tx.facts.list_occurrences(pid)),
        "assignments": len(tx.impacts.list_assignments(pid)),
        "decisions": len(tx.amendments.list_decisions_for_patient(world.org, pid)),
        "outbox": len(tx.outbox.list_pending(limit=1000)),
        "receipts": len(world.uow._store.receipts) if hasattr(world.uow, "_store")
        else world.uow._database["command_receipts"].count_documents({}),
        "patient": tx.patients.get(world.org, pid).current_schedule_version_id,
        "evaluations": tx.evaluations.count_evaluations(pid, world.v2),
        "pointer_v1": tx.evaluations.get_pointer(pid, world.v1).status,
        "audit": len(tx.audit.list_for_organization(world.org, limit=10000))})


@pytest.mark.parametrize("fail_at", ["evaluate", "audit"])
def test_an_injected_failure_leaves_no_partial_adoption(uow, monkeypatch, fail_at):
    world = World(uow, reviewed=True)
    world.record(world.patient_ids[0], "C1D1:0", date(2026, 1, 8), treatment=date(2026, 1, 8))
    simulation = world.simulate()
    before = snapshot(world)
    if fail_at == "evaluate":
        monkeypatch.setattr(PatientScheduleService, "evaluate_in",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    else:
        original = AdoptionService._audit

        def explode(tx, record, action, *rest):
            if action == "PATIENT_SCHEDULE_AMENDED":
                raise RuntimeError("boom")
            return original(tx, record, action, *rest)

        monkeypatch.setattr(AdoptionService, "_audit", staticmethod(explode))
    with pytest.raises(RuntimeError, match="boom"):
        world.confirm(simulation)
    monkeypatch.undo()
    after = snapshot(world)
    assert after == before                              # nothing: facts, assignment, pointer, audit, outbox
    status = world.uow.read(lambda tx: tx.amendments.get_simulation(
        world.org, UUID(simulation["simulation_id"])).status)
    assert str(getattr(status, "value", status)) == "ACTIVE"      # still confirmable
    assert world.confirm(simulation)["status"] == "APPLIED"      # and it works afterwards


def test_a_return_that_fails_midway_leaves_the_patient_on_the_later_version(uow, monkeypatch):
    world = World(uow, reviewed=True)
    world.confirm(world.simulate())
    simulation = world.simulate(direction="RETURN")
    before = snapshot(world)
    monkeypatch.setattr(PatientScheduleService, "evaluate_in",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        world.confirm(simulation, decision="RETURN", reason="withdrawn")
    monkeypatch.undo()
    assert snapshot(world) == before and before["patient"] == world.v2


# ------------------------------------------------------------------ real concurrency
def race(*jobs):
    results: list = [None] * len(jobs)
    gate = threading.Barrier(len(jobs))

    def run(index, job):
        gate.wait()
        try:
            results[index] = ("ok", job())
        except Exception as error:                       # noqa: BLE001
            results[index] = ("error", error)

    threads = [threading.Thread(target=run, args=(i, job)) for i, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


def test_two_immediate_adoptions_of_one_patient_apply_exactly_once(mongo_uow):
    world = World(mongo_uow, reviewed=True)
    first, second = world.simulate(), world.simulate()      # the second supersedes the first
    results = race(lambda: world.confirm(first, idempotency_key="a"),
                   lambda: world.confirm(second, idempotency_key="b"))
    applied = [r for r in results if r[0] == "ok" and r[1].get("status") == "APPLIED"]
    assert len(applied) == 1, results
    refused = [r for r in results if r[0] == "error"]
    assert all(isinstance(r[1], AmendmentBlockedError) for r in refused), results
    state = snapshot(world)
    assert state["patient"] == world.v2 and state["assignments"] == 2 and state["evaluations"] == 1
    assert state["decisions"] == 1


def test_the_same_confirmation_sent_twice_is_one_decision(mongo_uow):
    world = World(mongo_uow, reviewed=True)
    simulation = world.simulate()
    results = race(*[lambda: world.confirm(simulation, idempotency_key="same") for _ in range(4)])
    ok = [r[1] for r in results if r[0] == "ok"]
    assert ok and len({row["id"] for row in ok}) == 1, results
    errors = [r for r in results if r[0] == "error"]
    assert all(isinstance(r[1], AmendmentBlockedError) for r in errors), results
    state = snapshot(world)
    assert state["decisions"] == 1 and state["assignments"] == 2 and state["evaluations"] == 1


def test_a_deferred_decision_and_an_immediate_one_never_both_stand(mongo_uow):
    world = World(mongo_uow, reviewed=True)
    deferred_sim = world.simulate(point=AdoptionPoint(
        mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 3, 1)))
    immediate_sim = world.simulate()                          # supersedes the deferred preview
    results = race(lambda: world.confirm(deferred_sim, idempotency_key="d"),
                   lambda: world.confirm(immediate_sim, idempotency_key="i"))
    pid = world.patient_ids[0]
    state = snapshot(world)
    open_rows = world.uow.read(lambda tx: tx.amendments.find_open_decision(world.org, pid))
    assert not (state["patient"] == world.v2 and open_rows is not None), (state, results)
    assert sum(1 for r in results if r[0] == "ok") >= 1


def test_two_workers_cannot_both_apply_one_deferred_decision(mongo_uow):
    world = World(mongo_uow, reviewed=True)
    decision = world.confirm(world.simulate(point=AdoptionPoint(
        mode=AdoptionMode.ADOPT_ON_DATE, on_date=date(2027, 3, 1))))
    did = UUID(decision["id"])
    run = lambda: world.adoption.execute(          # noqa: E731
        organization_id=world.org, actor_id=world.pi, decision_id=did, as_of=date(2027, 3, 1))
    results = race(run, run, run)
    assert any(r[0] == "ok" and r[1].get("status") == "APPLIED" for r in results), results
    state = snapshot(world)
    assert state["patient"] == world.v2 and state["evaluations"] == 1 and state["assignments"] == 2
    stored = world.uow.read(lambda tx: tx.amendments.get_decision(world.org, did))
    assert stored.status.value == "APPLIED" and stored.is_open is False


def test_remain_and_adopt_for_two_patients_do_not_interfere(mongo_uow):
    world = World(mongo_uow, reviewed=True)
    a, b = world.patient_ids
    sim_a, sim_b = world.simulate(a), world.simulate(b, point=remain())
    results = race(lambda: world.confirm(sim_a, idempotency_key="a"),
                   lambda: world.confirm(sim_b, decision="REMAIN", idempotency_key="b"))
    assert all(r[0] == "ok" for r in results), results
    assert snapshot(world, a)["patient"] == world.v2 and snapshot(world, b)["patient"] == world.v1
