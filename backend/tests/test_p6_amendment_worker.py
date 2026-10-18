"""P6-05: a deferred decision is applied by the worker at its approved point - exactly once,
only while the person who decided still holds the right, and never early."""

from __future__ import annotations

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.api.uctsm_amendment_worker import amendment_worker_loop, process_due_once
from app.domain.schedule.amendment import AdoptionMode, AdoptionPoint

from _p6_world import World

pytest_plugins = ["tests.contract.conftest"]


@pytest.fixture()
def world(uow):
    return World(uow, reviewed=True)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def decide(world, on=date(2027, 3, 1), patient=None):
    return world.confirm(world.simulate(
        patient_id=patient, point=AdoptionPoint(mode=AdoptionMode.ADOPT_ON_DATE, on_date=on)))


async def yes(decision, patient):
    return True


async def no(decision, patient):
    return False


def moved(world, patient=None):
    return world.patient(patient).current_schedule_version_id == world.v2


def test_nothing_happens_before_the_point_and_it_is_applied_exactly_once_after(world):
    decision = decide(world)
    assert run(process_due_once(world.uow, yes, as_of=date(2027, 2, 28)))[0]["status"] == "WAITING"
    assert not moved(world)
    first = run(process_due_once(world.uow, yes, as_of=date(2027, 3, 1)))
    assert first[0]["status"] == "APPLIED" and first[0]["effective_date"] == "2027-03-01"
    assert moved(world)
    assert run(process_due_once(world.uow, yes, as_of=date(2027, 3, 2))) == []     # nothing left to do
    stored = world.uow.read(lambda tx: tx.amendments.get_decision(world.org, UUID(decision["id"])))
    assert stored.status.value == "APPLIED" and stored.attempts == 1
    assert world.uow.read(lambda tx: tx.evaluations.count_evaluations(world.patient_ids[0], world.v2)) == 1


def test_a_decider_who_lost_the_right_blocks_the_decision_and_nothing_moves(world):
    decision = decide(world)
    outcome = run(process_due_once(world.uow, no, as_of=date(2027, 3, 1)))
    assert outcome[0]["status"] == "BLOCKED"
    assert [b["code"] for b in outcome[0]["blockers"]] == ["DECIDER_NOT_AUTHORISED"]
    assert not moved(world)
    stored = world.uow.read(lambda tx: tx.amendments.get_decision(world.org, UUID(decision["id"])))
    assert stored.status.value == "BLOCKED" and stored.is_open is False       # the patient's slot is free
    audit = world.uow.read(lambda tx: tx.audit.list_for_organization(world.org, action="PATIENT_AMENDMENT_BLOCKED"))
    assert len(audit) == 1
    # a person can decide again afterwards
    assert world.confirm(world.simulate())["status"] == "APPLIED"


def test_one_failing_decision_does_not_stop_the_rest_of_the_pass(world):
    first, second = world.patient_ids
    decide(world, patient=first)
    decide(world, patient=second)

    async def only_second(decision, patient):
        if decision.patient_id == first:
            raise RuntimeError("the authorisation lookup failed")
        return True

    outcomes = run(process_due_once(world.uow, only_second, as_of=date(2027, 3, 1)))
    assert sorted(row["status"] for row in outcomes) == ["APPLIED", "ERROR"]
    assert not moved(world, first) and moved(world, second)


def test_the_worker_is_off_when_its_interval_is_zero(world):
    called = []

    def provider():
        called.append(1)
        return world.uow

    assert run(amendment_worker_loop(provider, yes, interval_seconds=0)) is None
    assert called == []


def test_the_loop_survives_a_failing_pass_and_keeps_going(world):
    passes = []

    async def flaky(decision, patient):
        passes.append(1)
        raise RuntimeError("boom")

    decide(world)

    async def scenario():
        task = asyncio.create_task(amendment_worker_loop(lambda: world.uow, flaky, interval_seconds=1))
        await asyncio.sleep(1.6)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert len(passes) >= 2                      # it ran again after the first failure
