"""P9-05: encrypted hot/cold archive of superseded evaluation projections."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.application.archive_records import (
    ArchiveIntegrityError, ArchiveRefusedError, EvaluationArchivedError)
from app.application.errors import RecordNotFoundError
from app.application.evaluation_archive import ArchivePolicy, EvaluationArchiveService, FileArchiveStore
from _p6_world import HORIZON, TODAY, World

pytest_plugins = ["tests.contract.conftest"]
LATER = datetime.now(timezone.utc) + timedelta(days=400)


def two_evaluations(world):
    patient = world.patient_ids[0]
    org = world.org
    first, _ = world.patients.evaluate(patient, organization_id=org, horizon=HORIZON,
                                       idempotency_key="archive-1", today=TODAY)
    second, _ = world.patients.evaluate(patient, organization_id=org, horizon=HORIZON,
                                        idempotency_key="archive-2", today=TODAY)
    return patient, first, second


@pytest.fixture
def setup(uow, tmp_path):
    world = World(uow)
    patient, first, second = two_evaluations(world)
    key = AESGCM.generate_key(bit_length=256)
    service = EvaluationArchiveService(uow, FileArchiveStore(tmp_path), key)
    return world, patient, first, second, service, key, tmp_path


def events_of(uow, evaluation_id):
    return uow.read(lambda tx: tx.evaluations.list_events(evaluation_id))


def test_archive_moves_only_superseded_projections_and_restores_them_exactly(setup):
    world, patient, first, second, service, key, root = setup
    uow, actor = world.uow, uuid4()
    before = events_of(uow, first.id)
    assert before and first.id != second.id
    row = service.archive(world.org, first.id, actor_id=actor, policy=ArchivePolicy(30), now=LATER)
    assert row.state == "ARCHIVED" and row.event_count == len(before)
    blob = next(root.rglob("*.enc")).read_bytes()
    assert b"event_code" not in blob and b"nominal" not in blob              # encrypted at rest
    # hot lineage is intact; only the projections are cold, and reading them says so
    assert uow.read(lambda tx: tx.evaluations.get_evaluation(first.id)).id == first.id
    with pytest.raises(EvaluationArchivedError):
        events_of(uow, first.id)
    assert events_of(uow, second.id)                                          # current untouched
    assert service.verify(world.org, first.id)["verified"] is True
    service.restore(world.org, first.id, actor_id=actor)
    assert [item.model_dump() for item in events_of(uow, first.id)] == [item.model_dump() for item in before]
    actions = [a.action for a in uow.read(lambda tx: tx.audit.list_for_entity(world.org, "EVALUATION", first.id))]
    assert actions == ["EVALUATION_ARCHIVED", "EVALUATION_ARCHIVE_RESTORED"]


def test_current_young_held_and_foreign_evaluations_are_refused(setup):
    world, patient, first, second, service, key, root = setup
    actor = uuid4()
    with pytest.raises(ArchiveRefusedError, match="current"):
        service.archive(world.org, second.id, actor_id=actor, policy=ArchivePolicy(30), now=LATER)
    with pytest.raises(ArchiveRefusedError, match="younger"):
        service.archive(world.org, first.id, actor_id=actor, policy=ArchivePolicy(30))
    with pytest.raises(RecordNotFoundError):
        service.archive(uuid4(), first.id, actor_id=actor, policy=ArchivePolicy(30), now=LATER)
    service.place_hold(world.org, first.id, actor_id=actor, reason="Legal hold, synthetic")
    with pytest.raises(ArchiveRefusedError, match="hold"):
        service.archive(world.org, first.id, actor_id=actor, policy=ArchivePolicy(30), now=LATER)
    service.release_hold(world.org, first.id, actor_id=actor, reason="Hold lifted")
    assert service.archive(world.org, first.id, actor_id=actor, policy=ArchivePolicy(30), now=LATER).state == "ARCHIVED"
    with pytest.raises(ArchiveRefusedError, match="already"):
        service.archive(world.org, first.id, actor_id=actor, policy=ArchivePolicy(30), now=LATER)
    with pytest.raises(ValueError):
        ArchivePolicy(0)


def test_damaged_missing_or_swapped_payloads_fail_closed_and_keep_hot_data(setup):
    world, patient, first, second, service, key, root = setup
    uow, actor = world.uow, uuid4()
    service.archive(world.org, first.id, actor_id=actor, policy=ArchivePolicy(30), now=LATER)
    path = next(root.rglob("*.enc"))
    good = path.read_bytes()
    path.write_bytes(good[:-1] + bytes([good[-1] ^ 1]))
    with pytest.raises(ArchiveIntegrityError):
        service.verify(world.org, first.id)
    with pytest.raises(ArchiveIntegrityError):
        service.restore(world.org, first.id, actor_id=actor)
    path.unlink()
    with pytest.raises(ArchiveIntegrityError, match="missing"):
        service.restore(world.org, first.id, actor_id=actor)
    wrong = EvaluationArchiveService(uow, FileArchiveStore(root), AESGCM.generate_key(bit_length=256))
    path.write_bytes(good)
    with pytest.raises(ArchiveIntegrityError):
        wrong.verify(world.org, first.id)
    assert service.verify(world.org, first.id)["verified"]


def test_a_failed_cold_write_never_removes_hot_projections(setup):
    world, patient, first, second, service, key, root = setup

    class Broken(FileArchiveStore):
        def put(self, key, data): super().put(key, data[:-3] + b"xyz")
    broken = EvaluationArchiveService(world.uow, Broken(root), key)
    with pytest.raises(ArchiveIntegrityError):
        broken.archive(world.org, first.id, actor_id=uuid4(), policy=ArchivePolicy(30), now=LATER)
    assert events_of(world.uow, first.id)
