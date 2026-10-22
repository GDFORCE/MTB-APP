"""P14-03: atomic, retry-safe and concurrency-safe multi-document mutations on real MongoDB."""
import threading
from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest
from pymongo.errors import OperationFailure

from app.application.errors import IdempotencyConflictError, StaleRevisionError
from app.domain.schedule.models import OccurrenceFactKind
from _p6_world import HORIZON, TODAY, World

pytest_plugins = ["tests.contract.conftest"]


def counts(world, patient):
    def read(tx):
        version = tx.patients.get(world.org, patient).current_schedule_version_id
        return {"occurrences": len(tx.facts.list_occurrences(patient)),
                "evaluations": tx.evaluations.count_evaluations(patient, version),
                "audit": len(tx.audit.list_for_organization(world.org, limit=1000)),
                "pending": len(tx.outbox.list_pending(limit=1000))}
    return world.uow.read(read)


def record(world, patient, key=None, visit=date(2026, 1, 8), idempotency_key=None):
    return world.patients.record_actual_occurrence(
        patient, organization_id=world.org, logical_key=key or "C1D1:0", kind=OccurrenceFactKind.PERFORMED,
        visit_date=visit, treatment_date=visit, actor_id=world.pi, reason="synthetic",
        horizon=HORIZON, today=TODAY, idempotency_key=idempotency_key)


def test_a_failure_after_every_earlier_write_commits_nothing(mongo_uow):
    world = World(mongo_uow); patient = world.patient_ids[0]
    before = counts(world, patient)
    with patch.object(type(mongo_uow.read(lambda tx: tx.outbox)), "add", side_effect=RuntimeError("injected failure")):
        with pytest.raises(RuntimeError, match="injected failure"):
            record(world, patient, idempotency_key="boundary-1")
    assert counts(world, patient) == before                     # occurrence, evaluation, audit, outbox: none
    outcome = record(world, patient, idempotency_key="boundary-1")   # the same request succeeds afterwards
    after = counts(world, patient)
    assert after["occurrences"] == before["occurrences"] + 1 and outcome is not None


def test_concurrent_identical_requests_create_exactly_one_business_result(mongo_uow):
    world = World(mongo_uow); patient = world.patient_ids[0]
    before = counts(world, patient); outcomes, errors = [], []
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait()
        try:
            outcomes.append(record(world, patient, idempotency_key="race-1"))
        except Exception as error:                       # noqa: BLE001 - classified below
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert outcomes, errors
    # losers either replayed the winner's result or were told the state moved; none duplicated it
    assert all(isinstance(error, (StaleRevisionError, IdempotencyConflictError)) or
               "already" in str(error).lower() or "concurrent" in str(error).lower() for error in errors), errors
    after = counts(world, patient)
    assert after["occurrences"] == before["occurrences"] + 1
    assert after["evaluations"] == before["evaluations"] + 1
    assert after["pending"] <= before["pending"] + 1


def test_concurrent_different_commands_lose_cleanly_and_leave_a_contiguous_history(mongo_uow):
    world = World(mongo_uow); patient = world.patient_ids[0]
    keys = ["C1D1:0", "DOSE:0", "DOSE:1", "IMAGING_BASELINE:0"]
    results, barrier = [], threading.Barrier(len(keys))

    def worker(key, day):
        barrier.wait()
        try:
            record(world, patient, key=key, visit=day, idempotency_key=f"k-{key}")
            results.append("ok")
        except StaleRevisionError:
            results.append("stale")
        except Exception as error:                       # noqa: BLE001
            results.append(type(error).__name__)

    threads = [threading.Thread(target=worker, args=(key, date(2026, 1, 8 + i))) for i, key in enumerate(keys)]
    [t.start() for t in threads]; [t.join() for t in threads]
    committed = results.count("ok")
    assert committed >= 1 and set(results) <= {"ok", "stale"}, results
    history = mongo_uow.read(lambda tx: tx.evaluations.list_evaluations(
        patient, tx.patients.get(world.org, patient).current_schedule_version_id))
    assert [item.sequence for item in history] == list(range(1, len(history) + 1))   # no gap, no duplicate
    pointer_target = mongo_uow.read(lambda tx: tx.evaluations.get_pointer(
        patient, tx.patients.get(world.org, patient).current_schedule_version_id)).current_evaluation_id
    assert pointer_target == history[-1].id


def test_a_transient_error_during_a_business_command_is_retried_to_one_result(mongo_uow):
    world = World(mongo_uow); patient = world.patient_ids[0]
    before = counts(world, patient); fired = []
    real_add = type(mongo_uow.read(lambda tx: tx.audit)).append

    def flaky(self, row):
        real_add(self, row)
        if not fired:
            fired.append(1)
            error = OperationFailure("simulated write conflict")
            error._add_error_label("TransientTransactionError")
            raise error

    with patch.object(type(mongo_uow.read(lambda tx: tx.audit)), "append", flaky):
        record(world, patient, idempotency_key="transient-1")
    after = counts(world, patient)
    assert fired and after["occurrences"] == before["occurrences"] + 1
    assert after["evaluations"] == before["evaluations"] + 1


def test_unknown_commit_result_is_retried_without_a_duplicate(mongo_client, mongo_uow):
    """The driver retries commit on UnknownTransactionCommitResult; prove it with a real fail point."""
    try:
        mongo_client.admin.command({"configureFailPoint": "failCommand", "mode": {"times": 1},
            "data": {"failCommands": ["commitTransaction"], "errorCode": 91,
                     "errorLabels": ["UnknownTransactionCommitResult"]}})
    except OperationFailure as error:
        pytest.skip(f"fail points are not enabled on this mongod (enableTestCommands): {error.details.get('errmsg')}")
    try:
        world = World(mongo_uow); patient = world.patient_ids[0]
        before = counts(world, patient)
        record(world, patient, idempotency_key="unknown-commit-1")
        after = counts(world, patient)
        assert after["occurrences"] == before["occurrences"] + 1
    finally:
        mongo_client.admin.command({"configureFailPoint": "failCommand", "mode": "off"})
