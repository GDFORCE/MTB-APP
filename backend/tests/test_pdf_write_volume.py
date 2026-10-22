"""P9-04: a large evaluation is persisted exactly once, in bulk, inside one transaction; a retry adds nothing."""
import os
from datetime import date
from uuid import uuid4

import pytest
from pymongo import MongoClient, monitoring

from app.application.patient_schedule import PatientScheduleService
from app.application.pilot import build_pilot_schedule
from app.domain.schedule.models import RecurrenceTermination
from app.domain.schedule.timing import PositiveTemporalAmount, TimeUnit
from app.persistence.mongodb.migrate import run_migrations
from app.persistence.mongodb.unit_of_work import MongoUnitOfWork
from app.quality.models import SequenceScenario, Step
from app.quality.sequence import _world

pytest_plugins = ["tests.contract.conftest"]
DAYS = 1500


class Capture(monitoring.CommandListener):
    def __init__(self):
        self.inserts = []

    def started(self, event):
        if event.command_name == "insert":
            self.inserts.append((event.command["insert"], len(event.command["documents"])))

    def succeeded(self, event): pass
    def failed(self, event): pass


def big_schedule():
    schedule = build_pilot_schedule(uuid4(), "SYNTHETIC write volume")
    dose = next(event for event in schedule.events if event.code == "DOSE")
    dose.recurrence.interval = PositiveTemporalAmount(value=1, unit=TimeUnit.DAY)
    dose.recurrence.termination = RecurrenceTermination(type="COUNT", count=DAYS)
    return schedule


def test_a_large_evaluation_is_one_transaction_bulk_inserted_and_a_retry_is_idempotent(mongo_client):
    url = os.getenv("UCTSM_TEST_MONGO_URL")
    capture = Capture()
    client = MongoClient(url, event_listeners=[capture], uuidRepresentation="standard", tz_aware=True)
    name = f"uctsm_volume_{uuid4().hex[:8]}"
    try:
        database = client[name]
        run_migrations(database)
        uow = MongoUnitOfWork(client, database)
        sequence = SequenceScenario(id="volume", as_of=date(2026, 1, 1), horizon=date(2031, 12, 31),
                                    schedule=big_schedule(), anchors={"BASELINE": date(2026, 1, 1)},
                                    steps=[Step(logical_key="C1D1:0", visit_date=date(2026, 1, 8), recorded_on=date(2026, 1, 9),
                                                expect_reevaluated=True)])
        _, org, _, patient_id = _world(sequence, uow)
        service = PatientScheduleService(uow)
        capture.inserts.clear()
        evaluation, events = service.evaluate(patient_id, organization_id=org, horizon=sequence.horizon,
                                              today=sequence.as_of, idempotency_key="volume-1")
        assert len(events) >= DAYS
        projections = [count for collection, count in capture.inserts if collection == "patient_events"]
        assert sum(projections) == len(events)
        assert len(projections) <= 2                                   # bulk batches, never one insert per visit
        assert database.patient_events.count_documents({"evaluation_id": evaluation.id}) == len(events)
        capture.inserts.clear()
        again, again_events = service.evaluate(patient_id, organization_id=org, horizon=sequence.horizon,
                                               today=sequence.as_of, idempotency_key="volume-1")
        assert again.id == evaluation.id and len(again_events) == len(events)          # same key: replayed
        assert [row for row in capture.inserts if row[0] in {"patient_events", "schedule_evaluations"}] == []
        assert database.schedule_evaluations.count_documents({"patient_id": patient_id}) == 1
    finally:
        client.drop_database(name)
        client.close()
