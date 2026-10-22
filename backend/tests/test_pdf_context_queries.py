"""P9-03: every patient-context read is index-bounded, sorted by index and issued once.

Volume belongs to other patients (a large collection); the explain plan is what is asserted."""
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from bson.binary import UuidRepresentation
from bson.codec_options import CodecOptions
from pymongo import MongoClient, monitoring

from app.persistence.mongodb.migrate import run_migrations
from app.persistence.mongodb.settings import MongoSettings, require_local
from app.persistence.mongodb.unit_of_work import MongoUnitOfWork

pytest_plugins = ["tests.contract.conftest"]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Capture(monitoring.CommandListener):
    def __init__(self): self.finds = []
    def started(self, event):
        if event.command_name == "find":
            self.finds.append(dict(event.command))
    def succeeded(self, event): pass
    def failed(self, event): pass


def plans(node):
    yield node.get("stage")
    for key in ("inputStage", "queryPlan"):
        if key in node: yield from plans(node[key])
    for child in node.get("inputStages", []): yield from plans(child)


@pytest.fixture
def database(mongo_client):
    url = os.getenv("UCTSM_TEST_MONGO_URL")
    require_local(MongoSettings(url=url, database="unused"))
    capture = Capture()
    client = MongoClient(url, event_listeners=[capture], uuidRepresentation="standard")
    name = f"uctsm_ctx_{uuid4().hex[:10]}"
    db = client[name]; run_migrations(db)
    yield db, client, capture
    client.drop_database(name); client.close()


def seed(db, patient, others):
    spec = {
        "patient_anchors": {"recorded_at": NOW, "status": "CONFIRMED"},
        "patient_states": {"effective_at": NOW, "created_at": NOW},
        "patient_conditions": {"superseded_by_id": None, "recorded_at": NOW},
        "patient_activity_records": {"superseded_by_id": None, "recorded_at": NOW},
        "patient_unscheduled_visits": {"occurred_on": "2026-01-01"},
        "patient_event_occurrences": {"recorded_at": NOW, "schedule_version_id": SV},
        "patient_schedule_assignments": {"assigned_at": NOW},
    }
    for name, fields in spec.items():
        docs = [{"_id": uuid4(), "patient_id": others[i % len(others)], **fields}
                for i in range(1200)]
        db[name].insert_many(docs, bypass_document_validation=True)
    evaluation = uuid4()
    db.patient_events.insert_many([{"_id": uuid4(), "evaluation_id": uuid4(),
        "nominal_start_date": f"2026-01-{i % 28 + 1:02d}", "occurrence_index": i, "event_code": f"E{i % 9}"}
        for i in range(1200)], bypass_document_validation=True)
    db.patient_confinement_episodes.insert_many([{"_id": uuid4(), "evaluation_id": uuid4(),
        "episode_code": f"C{i}"} for i in range(1200)], bypass_document_validation=True)
    db.schedule_evaluations.insert_many([{"_id": uuid4(), "patient_id": others[0],
        "schedule_version_id": SV, "sequence": i, "evaluated_at": NOW} for i in range(1200)],
        bypass_document_validation=True)
    return evaluation


SV = uuid4()


def test_context_reads_are_single_indexed_and_need_no_in_memory_sort(database):
    db, client, capture = database
    patient, others = uuid4(), [uuid4() for _ in range(25)]
    evaluation = seed(db, patient, others)
    uow = MongoUnitOfWork(client, db)

    def reads(tx):
        tx.facts.list_anchors(patient, statuses={"CONFIRMED", "PROVISIONAL"})
        tx.facts.list_states(patient); tx.facts.list_current_conditions(patient)
        tx.facts.list_current_activity_entries(patient); tx.facts.list_unscheduled_visits(patient)
        tx.facts.list_occurrences(patient); tx.facts.list_occurrences(patient, schedule_version_id=SV)
        tx.impacts.list_assignments(patient)
        tx.evaluations.list_events(evaluation); tx.evaluations.list_confinements(evaluation)
        tx.evaluations.list_evaluations(patient, SV)
    capture.finds.clear()
    uow.run(reads)
    finds = [c for c in capture.finds if "patient_" in c["find"] or c["find"] == "schedule_evaluations"
             and c.get("filter")]
    assert len(finds) == 11, [c["find"] for c in finds]            # one query per read: no N+1
    for command in finds:
        explain = db.command({"explain": {k: v for k, v in command.items()
            if k in {"find", "filter", "sort", "limit"}}, "verbosity": "queryPlanner"},
            codec_options=CodecOptions(uuid_representation=UuidRepresentation.STANDARD))
        stages = set(plans(explain["queryPlanner"]["winningPlan"]))
        assert "COLLSCAN" not in stages, (command["find"], stages)
        assert "SORT" not in stages, (command["find"], stages)
        assert "IXSCAN" in stages, (command["find"], stages)
