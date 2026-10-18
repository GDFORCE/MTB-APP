"""P1-04/P1-05: storage-level concurrency, uniqueness and rollback - REAL MongoDB.

The in-memory fake serialises everything, so it can prove none of this. These
tests use the replica-set ``mongo_uow`` fixture only; with ``UCTSM_REQUIRE_MONGO=1``
an unavailable server FAILS them rather than skipping.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import sys
import threading
from uuid import uuid4

import pytest
from pymongo.errors import DuplicateKeyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application.errors import IdempotencyConflictError
from app.domain.schedule.timing import TemporalBasis
from test_uctsm_p1_occurrence_recalculation import (
    ORG, PERFORMED, TODAY, Env, after, from_baseline, schedule_of,
)

pytest_plugins = ["tests.contract.conftest"]

THREADS = 8


def make_env(mongo_uow) -> Env:
    env = Env(mongo_uow, schedule_of(
        from_baseline("V2", 9, 1), after("V3", "V2", 14, 2, TemporalBasis.ACTUAL_VISIT),
        from_baseline("IMAGING", 30, 3)))
    env.evaluate()
    return env


def race(count: int, work):
    """Run ``work(i)`` on ``count`` threads released together; (results, errors)."""
    gate = threading.Barrier(count)
    results: list = []
    errors: list = []

    def run(index: int):
        gate.wait()
        try:
            results.append(work(index))
        except Exception as error:  # noqa: BLE001 - the outcome under test
            errors.append(error)

    with ThreadPoolExecutor(count) as pool:
        list(pool.map(run, range(count)))
    return results, errors


def command(env: Env, visit: date, key: str | None, *, horizon: date | None = None):
    return env.service.record_actual_occurrence(
        env.patient_id, organization_id=ORG, logical_key="V2:0", kind=PERFORMED,
        visit_date=visit, idempotency_key=key, today=TODAY, horizon=horizon, reason="race")


def counts(env: Env, mongo_uow) -> dict[str, int]:
    database = mongo_uow._database
    return {
        "occurrences": database["patient_event_occurrences"].count_documents(
            {"patient_id": env.patient_id}),
        "evaluations": database["schedule_evaluations"].count_documents(
            {"patient_id": env.patient_id}),
        "outbox": database["outbox"].count_documents({"aggregate_id": env.patient_id}),
        "recorded_audits": database["audit_events"].count_documents(
            {"entity_type": "PATIENT_EVENT", "action": "PATIENT_OCCURRENCE_FACT_RECORDED"}),
        "receipts": database["command_receipts"].count_documents({"patient_id": env.patient_id}),
    }


def test_identical_concurrent_commands_produce_one_occurrence_one_evaluation_one_outbox(mongo_uow):
    env = make_env(mongo_uow)
    base = counts(env, mongo_uow)
    results, errors = race(THREADS, lambda _: command(env, date(2026, 10, 13), "same-key"))

    assert errors == []
    assert len(results) == THREADS
    assert len({item.occurrence_id for item in results}) == 1
    assert len({item.evaluation_id for item in results}) == 1
    assert sum(1 for item in results if not item.replayed) == 1     # exactly one real write
    after_counts = counts(env, mongo_uow)
    assert after_counts["occurrences"] - base["occurrences"] == 1
    assert after_counts["evaluations"] - base["evaluations"] == 1
    assert after_counts["outbox"] - base["outbox"] == 1
    assert after_counts["recorded_audits"] - base["recorded_audits"] == 1
    assert after_counts["receipts"] - base["receipts"] == 1
    assert env.calendar()["V3"].planned_start == date(2026, 10, 27)


def test_conflicting_concurrent_commands_under_one_key_let_exactly_one_win(mongo_uow):
    env = make_env(mongo_uow)
    base = counts(env, mongo_uow)
    visits = [date(2026, 10, 13), date(2026, 10, 14)]
    results, errors = race(THREADS, lambda i: command(env, visits[i % 2], "shared-key"))

    assert all(isinstance(error, IdempotencyConflictError) for error in errors)
    winner_date = env.calendar()["V2"].actual_visit_date
    assert winner_date in visits
    assert sum(1 for item in results if not item.replayed) == 1
    # every success carries the winner's result; every loser conflicted
    assert len({item.occurrence_id for item in results}) == 1
    assert len(results) + len(errors) == THREADS
    after_counts = counts(env, mongo_uow)
    assert after_counts["occurrences"] - base["occurrences"] == 1
    assert after_counts["evaluations"] - base["evaluations"] == 1
    assert after_counts["outbox"] - base["outbox"] == 1


def test_a_different_horizon_under_the_same_key_conflicts_in_storage_too(mongo_uow):
    env = make_env(mongo_uow)
    command(env, date(2026, 10, 13), "h-key", horizon=date(2027, 12, 31))
    base = counts(env, mongo_uow)
    with pytest.raises(IdempotencyConflictError):
        command(env, date(2026, 10, 13), "h-key", horizon=date(2028, 6, 30))
    assert counts(env, mongo_uow) == base


def test_concurrent_commands_with_different_keys_keep_one_current_pointer_and_no_duplicate_projections(
        mongo_uow):
    env = make_env(mongo_uow)
    database = mongo_uow._database
    visits = [date(2026, 10, 12 + i) for i in range(THREADS)]
    results, errors = race(THREADS, lambda i: command(env, visits[i], f"key-{i}"))

    assert errors == []
    assert len(results) == THREADS
    evaluations = list(database["schedule_evaluations"].find(
        {"patient_id": env.patient_id, "schedule_version_id": env.version_id}))
    sequences = sorted(item["sequence"] for item in evaluations)
    assert sequences == list(range(1, len(sequences) + 1))          # no gap, no duplicate
    pointer = database["patient_schedules"].find_one(
        {"patient_id": env.patient_id, "schedule_version_id": env.version_id})
    newest = max(evaluations, key=lambda item: item["sequence"])
    assert pointer["current_evaluation_id"] == newest["_id"]        # no lost pointer update
    for evaluation in evaluations:                                   # one projection per visit
        rows = list(database["patient_events"].find({"evaluation_id": evaluation["_id"]}))
        keys = [(item["event_code"], item["occurrence_index"]) for item in rows]
        assert len(keys) == len(set(keys)) == 3
    # the latest calendar is consistent with the newest effective fact
    entry = env.calendar()["V2"]
    assert entry.completed and entry.actual_visit_date in visits
    # append-only: every attempt is kept on file as its own fact
    assert database["patient_event_occurrences"].count_documents(
        {"patient_id": env.patient_id}) == THREADS


def test_the_unique_indexes_refuse_a_duplicate_in_storage(mongo_uow):
    env = make_env(mongo_uow)
    command(env, date(2026, 10, 13), "u-key")
    database = mongo_uow._database
    receipt = database["command_receipts"].find_one({"patient_id": env.patient_id})
    with pytest.raises(DuplicateKeyError):               # same org + patient + key
        database["command_receipts"].insert_one({**receipt, "_id": uuid4()})
    projection = database["patient_events"].find_one({"patient_id": env.patient_id})
    with pytest.raises(DuplicateKeyError):               # same evaluation + visit + index
        database["patient_events"].insert_one({**projection, "_id": uuid4()})
    outbox = database["outbox"].find_one({"aggregate_id": env.patient_id})
    with pytest.raises(DuplicateKeyError):               # same dedupe key
        database["outbox"].insert_one({**outbox, "_id": uuid4()})
    # ...while a second FACT for the same logical occurrence is allowed (append-only)
    fact = database["patient_event_occurrences"].find_one({"patient_id": env.patient_id})
    before = database["patient_event_occurrences"].count_documents({"patient_id": env.patient_id})
    database["patient_event_occurrences"].insert_one({**fact, "_id": uuid4()})
    assert database["patient_event_occurrences"].count_documents(
        {"patient_id": env.patient_id}) == before + 1


def test_an_outbox_failure_after_the_evaluation_rolls_back_every_write_in_storage(
        mongo_uow, monkeypatch):
    env = make_env(mongo_uow)
    base = counts(env, mongo_uow)
    outbox_type = type(mongo_uow.read(lambda tx: tx.outbox))

    def boom(self, record):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(outbox_type, "add", boom)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        command(env, date(2026, 10, 13), "fail-key")
    assert counts(env, mongo_uow) == base            # occurrence, evaluation, audit, receipt
    pointer = mongo_uow._database["patient_schedules"].find_one({"patient_id": env.patient_id})
    assert pointer["current_evaluation_id"] == mongo_uow._database[
        "schedule_evaluations"].find_one({"patient_id": env.patient_id, "sequence": 1})["_id"]
    assert env.calendar()["V3"].status == "BLOCKED"
