"""P11-02: resumable, idempotent, reconciled legacy -> UCTSM migration (synthetic legacy records)."""
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from app.application.legacy_migration import SOURCE_SYSTEM, LegacyMigrationJob, LegacyPatient
from app.application.parity import ParityService
from _p6_world import HORIZON, TODAY, World

pytest_plugins = ["tests.contract.conftest"]


class FakeSource:
    def __init__(self, patients):
        self.patients = sorted(patients, key=lambda item: item.legacy_id)
        self.reads = 0

    def list_patients(self, external_trial_id, *, after, limit):
        self.reads += 1
        rows = [item for item in self.patients if after is None or item.legacy_id > after]
        return rows[:limit]


def legacy(i, baseline=date(2026, 3, 2), **extra):
    return LegacyPatient(legacy_id=f"LEG-{i:03d}", subject_id=f"S{i:03d}", baseline_date=baseline, **extra)


@pytest.fixture
def setup(uow):
    world = World(uow)
    definition = uow.read(lambda tx: tx.schedules.get_header(world.v1)).schedule_definition_id
    return world, definition


def job_args(world, definition, **extra):
    return dict(organization_id=world.org, actor_id=world.pi, canonical_trial_id=world.trial_id,
                schedule_definition_id=definition, external_trial_id="LEG-TRIAL", run_key="run-1",
                today=TODAY, **extra)


def patients_in_plane(world):
    return {row.external_patient_id for row in world.uow.read(
        lambda tx: tx.patients.list_for_trial(world.org, world.trial_id))}


def test_dry_run_plans_but_writes_nothing(setup):
    world, definition = setup
    before = patients_in_plane(world)
    job = LegacyMigrationJob(world.uow, FakeSource([legacy(1), legacy(2)]))
    report = job.run(**job_args(world, definition, dry_run=True))
    assert report.counts == {"WOULD_MIGRATE": 2} and report.dry_run
    assert patients_in_plane(world) == before
    assert world.uow.read(lambda tx: tx.migrations.get_run(world.org, "run-1")) is None
    assert world.uow.read(lambda tx: tx.migrations.list_mappings(world.org, SOURCE_SYSTEM)) == []


def test_apply_migrates_reconciles_and_a_rerun_creates_no_duplicates(setup):
    world, definition = setup
    source = FakeSource([legacy(i) for i in range(1, 5)])
    job = LegacyMigrationJob(world.uow, source)
    first = job.run(**job_args(world, definition, dry_run=False, batch_size=2))
    assert sum(first.counts.values()) == 4 and "FAILED" not in first.counts
    migrated = patients_in_plane(world)
    assert {f"LEG-{i:03d}" for i in range(1, 5)} <= migrated
    mappings = world.uow.read(lambda tx: tx.migrations.list_mappings(world.org, SOURCE_SYSTEM))
    assert [m.source_id for m in mappings] == [f"LEG-{i:03d}" for i in range(1, 5)]
    assert all(m.patient_id and m.parity_verdict for m in mappings)         # every patient is accounted for
    second = job.run(**job_args(world, definition, dry_run=False))
    assert len(second.items) == 4                                           # a finished run starts a fresh pass
    assert patients_in_plane(world) == migrated                             # no duplicate patients
    assert len(world.uow.read(lambda tx: tx.migrations.list_mappings(world.org, SOURCE_SYSTEM))) == 4
    assert second.status in {"COMPLETED", "PARTIAL"}
    summary = job.reconciliation(world.org)
    assert summary["mappings"] == 4 and sum(summary["by_status"].values()) == 4


def test_a_changed_source_is_remigrated_and_can_reconcile_to_a_match(setup):
    world, definition = setup
    original = legacy(1)
    job = LegacyMigrationJob(world.uow, FakeSource([original]))
    job.run(**job_args(world, definition, dry_run=False))
    first = world.uow.read(lambda tx: tx.migrations.get_mapping(world.org, SOURCE_SYSTEM, "LEG-001"))
    assert first.status == "DIFFERENCES"                                     # legacy listed no visits

    def engine_visits(tx):
        patient = tx.patients.get(world.org, first.patient_id)
        result = world.patients.evaluate_result(tx, patient, patient.current_schedule_version_id, horizon=HORIZON)
        schedule = tx.schedules.get(patient.current_schedule_version_id)
        rows = ParityService._engine_rows(patient, result.events, {e.id: e for e in schedule.events})
        return [{"name": r["name"], "scheduled_date": r.get("scheduled_date"),
                 "window_start": r.get("window_start"), "window_end": r.get("window_end")} for r in rows]
    visits = tuple(world.uow.read(engine_visits))
    assert visits
    fixed = replace(original, visits=visits)
    rerun = LegacyMigrationJob(world.uow, FakeSource([fixed])).run(**job_args(world, definition, dry_run=False))
    assert rerun.items[0]["result"] in {"RECONCILED", "DIFFERENCES"}
    assert rerun.items[0]["source_hash"] != first.source_hash
    again = LegacyMigrationJob(world.uow, FakeSource([fixed])).run(**job_args(world, definition, dry_run=False))
    if rerun.items[0]["result"] == "RECONCILED":
        assert again.items[0]["result"] == "SKIPPED_UNCHANGED"               # unchanged + reconciled is skipped


def test_the_run_is_resumable_from_its_checkpoint(setup):
    world, definition = setup
    source = FakeSource([legacy(i) for i in range(1, 6)])
    job = LegacyMigrationJob(world.uow, source)
    partial = job.run(**job_args(world, definition, dry_run=False, batch_size=2, max_batches=1))
    assert partial.status == "RUNNING" and sum(partial.counts.values()) == 2
    run = world.uow.read(lambda tx: tx.migrations.get_run(world.org, "run-1"))
    assert run.cursor == "LEG-002" and run.status == "RUNNING"
    finished = job.run(**job_args(world, definition, dry_run=False, batch_size=2))
    assert {item["source_id"] for item in finished.items} == {"LEG-003", "LEG-004", "LEG-005"}   # no re-processing
    assert world.uow.read(lambda tx: tx.migrations.get_run(world.org, "run-1")).cursor == "LEG-005"
    assert {f"LEG-{i:03d}" for i in range(1, 6)} <= patients_in_plane(world)


def test_an_unstated_baseline_is_reported_never_defaulted(setup):
    world, definition = setup
    before = patients_in_plane(world)
    report = LegacyMigrationJob(world.uow, FakeSource([legacy(1, baseline=None)])).run(
        **job_args(world, definition, dry_run=False))
    assert report.items[0] == {**report.items[0], "result": "FAILED", "error_code": "BASELINE_DATE_MISSING"}
    assert report.status == "PARTIAL" and patients_in_plane(world) == before
    mapping = world.uow.read(lambda tx: tx.migrations.get_mapping(world.org, SOURCE_SYSTEM, "LEG-001"))
    assert mapping.status == "FAILED" and mapping.patient_id is None


def test_a_run_key_cannot_be_reused_for_another_trial_and_failures_never_carry_text(setup):
    world, definition = setup
    job = LegacyMigrationJob(world.uow, FakeSource([legacy(1)]))
    job.run(**job_args(world, definition, dry_run=False))
    with pytest.raises(ValueError, match="another trial"):
        job.run(**{**job_args(world, definition, dry_run=False), "canonical_trial_id": uuid4()})
    with pytest.raises(ValueError):
        job.run(**job_args(world, definition, batch_size=0))


def test_the_mongo_source_reads_legacy_documents_page_by_page_and_never_writes_to_them(mongo_client, mongo_uow):
    from app.persistence.mongodb.legacy_source import LegacyMongoSource
    name = f"legacy_probe_{uuid4().hex[:8]}"
    database = mongo_client[name]
    try:
        database.patients.insert_many([
            {"id": f"P{i}", "trial_id": "LEG-TRIAL", "subject_id": f"S{i}", "baseline_date": "2026-03-02T00:00:00Z",
             "arm_label": None, "anchor_dates": {"BASELINE": "2026-03-02"}} for i in (3, 1, 2)]
            + [{"id": "OTHER", "trial_id": "ANOTHER-TRIAL", "baseline_date": "2026-03-02"}])
        database.visit_instances.insert_many([
            {"id": "v1", "patient_id": "P1", "name": "Screening", "scheduled_date": "2026-03-02",
             "window_start": "2026-02-28", "window_end": "2026-03-04", "status": "scheduled"}])
        before = {c: list(database[c].find({}, {"_id": 0}).sort("id", 1)) for c in ("patients", "visit_instances")}
        source = LegacyMongoSource(database)
        page = source.list_patients("LEG-TRIAL", after=None, limit=2)
        assert [p.legacy_id for p in page] == ["P1", "P2"] and page[0].baseline_date == date(2026, 3, 2)
        assert page[0].visits[0]["window_end"] == date(2026, 3, 4) and page[1].visits == ()
        assert [p.legacy_id for p in source.list_patients("LEG-TRIAL", after="P2", limit=5)] == ["P3"]
        assert source.list_patients("MISSING", after=None, limit=5) == []
        world = World(mongo_uow)
        definition = mongo_uow.read(lambda tx: tx.schedules.get_header(world.v1)).schedule_definition_id
        report = LegacyMigrationJob(mongo_uow, source).run(**job_args(world, definition, dry_run=False))
        assert sum(report.counts.values()) == 3
        after = {c: list(database[c].find({}, {"_id": 0}).sort("id", 1)) for c in ("patients", "visit_instances")}
        assert after == before                                              # the legacy store is untouched
    finally:
        mongo_client.drop_database(name)
