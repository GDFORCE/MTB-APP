"""P11-03/04/05: shadow -> pilot -> rollback of an explicit cohort, and the legacy-write guard."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.application.cohort_cutover import MIGRATED, CohortCutoverService, CutoverRefusedError
from app.application.legacy_migration import SOURCE_SYSTEM, LegacyMigrationJob, LegacyPatient
from app.application.parity import ParityService
from app.authorization.policy import Grant, Principal
from app.domain.schedule.models import OccurrenceFactKind
from _p6_world import HORIZON, TODAY, World

pytest_plugins = ["tests.contract.conftest"]
TODAY_DT = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
#: What `recovery_cli snapshot` reports, taken one hour before the (fixed) clock.
SNAP = {"status": "ENCRYPTED_LOGICAL_SNAPSHOT", "manifest_hash": "a" * 64, "encrypted_file_hash": "b" * 64,
        "created_at": "2026-10-16T11:00:00+00:00", "collections": 40, "documents": 10}
#: A short horizon keeps the recurring weekly dose to one visit so visit names are unique;
#: parity (correctly) refuses to pair repeated names, so a long horizon could never MATCH.
BASELINE = TODAY                      # the job's horizon is max(baseline, today) + horizon_days
SHORT = 16


class Source:
    def __init__(self, patients):
        self.patients = sorted(patients, key=lambda p: p.legacy_id)

    def list_patients(self, external_trial_id, *, after, limit):
        return [p for p in self.patients if after is None or p.legacy_id > after][:limit]


def principal(org, *, grant=True, role="pi"):
    return Principal(user_id=str(uuid4()), organization_id=str(org), role=role,
                     grants=(Grant("cohort.cutover"),) if grant else ())


@pytest.fixture
def reconciled(uow):
    """Two mirror patients migrated and RECONCILED (parity MATCH), plus the service."""
    world = World(uow)
    definition = uow.read(lambda tx: tx.schedules.get_header(world.v1)).schedule_definition_id
    args = dict(organization_id=world.org, actor_id=world.pi, canonical_trial_id=world.trial_id,
                schedule_definition_id=definition, external_trial_id="LEG", today=TODAY, horizon_days=SHORT)
    legacy = [LegacyPatient(legacy_id=f"LEG-{i}", subject_id=f"S{i}", baseline_date=BASELINE) for i in (1, 2)]
    LegacyMigrationJob(uow, Source(legacy)).run(run_key="pass-1", dry_run=False, **args)

    def visits(tx, patient_id):
        patient = tx.patients.get(world.org, patient_id)
        result = world.patients.evaluate_result(tx, patient, patient.current_schedule_version_id, horizon=BASELINE + timedelta(days=SHORT))
        schedule = tx.schedules.get(patient.current_schedule_version_id)
        rows = ParityService._engine_rows(patient, result.events, {e.id: e for e in schedule.events})
        return tuple({"name": r["name"], "scheduled_date": r.get("scheduled_date"),
                      "window_start": r.get("window_start"), "window_end": r.get("window_end")} for r in rows)
    fixed = []
    for item in legacy:
        mapping = uow.read(lambda tx, key=item.legacy_id: tx.migrations.get_mapping(world.org, SOURCE_SYSTEM, key))
        fixed.append(replace(item, visits=uow.read(lambda tx, pid=mapping.patient_id: visits(tx, pid))))
    LegacyMigrationJob(uow, Source(fixed)).run(run_key="pass-2", dry_run=False, **args)
    return world, CohortCutoverService(uow, clock=lambda: TODAY_DT)


def authority(world):
    return {row.external_patient_id: row.authority_plane for row in world.uow.read(
        lambda tx: tx.patients.list_for_trial(world.org, world.trial_id))}


def test_the_fixture_really_produced_reconciled_matching_mirrors(reconciled):
    world, _ = reconciled
    rows = world.uow.read(lambda tx: tx.migrations.list_mappings(world.org, SOURCE_SYSTEM))
    assert [row.status for row in rows] == ["RECONCILED", "RECONCILED"]
    assert all(row.parity_verdict == "MATCH" for row in rows)


def test_shadow_cohort_needs_the_grant_reconciled_members_and_the_right_trial(reconciled):
    world, service = reconciled
    with pytest.raises(CutoverRefusedError) as denied:
        service.create_shadow(principal(world.org, grant=False), world.org, world.trial_id, "no-grant", ["LEG-1"])
    assert denied.value.code == "CAPABILITY_MISSING"
    maker = principal(world.org)
    with pytest.raises(CutoverRefusedError) as missing:
        service.create_shadow(maker, world.org, world.trial_id, "bad", ["LEG-1", "NOT-MIGRATED"])
    assert missing.value.details["members"] == {"NOT-MIGRATED": "MAPPING_MISSING"}
    with pytest.raises(CutoverRefusedError) as wrong:
        service.create_shadow(maker, world.org, uuid4(), "other-trial", ["LEG-1"])
    assert wrong.value.details["members"] == {"LEG-1": "PATIENT_IN_OTHER_TRIAL"}
    cohort = service.create_shadow(maker, world.org, world.trial_id, "pilot-a", ["LEG-1", "LEG-2", "LEG-1"])
    assert cohort.state == "SHADOW" and [m.external_patient_id for m in cohort.members] == ["LEG-1", "LEG-2"]
    assert service.readiness(world.org, cohort.id)["ready"] is True


def test_promotion_needs_a_different_person_and_changes_authority_only_for_members(reconciled):
    world, service = reconciled
    maker = principal(world.org)
    cohort = service.create_shadow(maker, world.org, world.trial_id, "pilot-a", ["LEG-1"])
    with pytest.raises(CutoverRefusedError) as same:
        service.promote(maker, world.org, cohort.id, reason="self approval", snapshot=SNAP)
    assert same.value.code == "MAKER_CHECKER_SAME_PERSON"
    with pytest.raises(CutoverRefusedError):
        service.promote(principal(world.org, grant=False), world.org, cohort.id, reason="no grant", snapshot=SNAP)
    pilot = service.promote(principal(world.org), world.org, cohort.id, reason="parity matched, owner approved", snapshot=SNAP)
    assert pilot.state == "PILOT" and pilot.promoted_at == TODAY_DT
    states = authority(world)
    assert states["LEG-1"] == "MONGO" and states["LEG-2"] in (None, "UNCLASSIFIED")        # only the member
    with pytest.raises(CutoverRefusedError) as again:
        service.promote(principal(world.org), world.org, cohort.id, reason="twice", snapshot=SNAP)
    assert again.value.code == "COHORT_NOT_IN_SHADOW"
    actions = [a.action for a in world.uow.read(lambda tx: tx.audit.list_for_entity(world.org, "TRIAL", world.trial_id))]
    assert "COHORT_SHADOW_CREATED" in actions and "COHORT_PROMOTED_TO_PILOT" in actions


def test_a_member_that_stops_matching_blocks_promotion_with_exact_reasons(reconciled):
    world, service = reconciled
    cohort = service.create_shadow(principal(world.org), world.org, world.trial_id, "pilot-a", ["LEG-1", "LEG-2"])
    mapping = world.uow.read(lambda tx: tx.migrations.get_mapping(world.org, SOURCE_SYSTEM, "LEG-2"))
    world.uow.run(lambda tx: tx.migrations.save_mapping(mapping.model_copy(update={"status": "DIFFERENCES"})))
    with pytest.raises(CutoverRefusedError) as refused:
        service.promote(principal(world.org), world.org, cohort.id, reason="try", snapshot=SNAP)
    assert refused.value.code == "COHORT_NOT_READY"
    assert refused.value.details["patients"] == [{"external_patient_id": "LEG-2", "ready": False,
                                                  "reasons": ["MAPPING_NOT_RECONCILED"]}]
    assert authority(world)["LEG-1"] in (None, "UNCLASSIFIED")                          # all or nothing


def test_the_legacy_write_guard_blocks_only_pilot_members_and_lifts_on_rollback(reconciled):
    world, service = reconciled
    cohort = service.create_shadow(principal(world.org), world.org, world.trial_id, "pilot-a", ["LEG-1"])
    assert service.legacy_write_block("LEG-1") is None                  # shadow does not block anything
    service.promote(principal(world.org), world.org, cohort.id, reason="approved", snapshot=SNAP)
    blocked = service.legacy_write_block("LEG-1")
    assert blocked["code"] == MIGRATED and "UCTSM" in blocked["detail"]
    assert service.legacy_write_block("LEG-2") is None and service.legacy_write_block("UNKNOWN") is None
    service.rollback(principal(world.org), world.org, cohort.id, reason="rehearsal")
    assert service.legacy_write_block("LEG-1") is None


def test_rollback_restores_authority_exactly_and_refuses_to_lose_plane_changes(reconciled):
    world, service = reconciled
    cohort = service.create_shadow(principal(world.org), world.org, world.trial_id, "pilot-a", ["LEG-1"])
    with pytest.raises(CutoverRefusedError) as early:
        service.rollback(principal(world.org), world.org, cohort.id, reason="not yet promoted")
    assert early.value.code == "COHORT_NOT_IN_PILOT"
    pilot = service.promote(principal(world.org), world.org, cohort.id, reason="approved", snapshot=SNAP)
    patient_id = pilot.members[0].patient_id
    assert world.uow.read(lambda tx: tx.patients.get(world.org, patient_id)).authority_plane == "MONGO"
    # a clinical change happens in the plane during the pilot
    world.patients.record_actual_occurrence(
        patient_id, organization_id=world.org, logical_key="C1D1:0", kind=OccurrenceFactKind.PERFORMED,
        visit_date=BASELINE, treatment_date=BASELINE, actor_id=world.pi, reason="pilot work",
        horizon=HORIZON, today=TODAY + timedelta(days=100))
    activity = service.plane_activity_since_promotion(world.org, cohort.id)
    assert activity["commands"] + activity["occurrences"] >= 1
    with pytest.raises(CutoverRefusedError) as lost:
        service.rollback(principal(world.org), world.org, cohort.id, reason="rehearsal")
    assert lost.value.code == "PLANE_CHANGES_WOULD_BE_LOST"
    rolled = service.rollback(principal(world.org), world.org, cohort.id, reason="rehearsal, loss accepted",
                              accept_plane_changes_loss=True)
    assert rolled.state == "ROLLED_BACK"
    assert world.uow.read(lambda tx: tx.patients.get(world.org, patient_id)).authority_plane == \
        pilot.members[0].previous_authority
    last = world.uow.read(lambda tx: tx.audit.list_for_entity(world.org, "TRIAL", world.trial_id))[-1]
    assert last.action == "COHORT_ROLLED_BACK" and last.after["accepted_loss"] is True


def test_promotion_needs_a_fresh_completed_encrypted_snapshot_and_records_its_hash(reconciled):
    world, service = reconciled
    cohort = service.create_shadow(principal(world.org), world.org, world.trial_id, "pilot-snap", ["LEG-1"])
    approver = principal(world.org)

    def attempt(snapshot, **extra):
        with pytest.raises(CutoverRefusedError) as refused:
            service.promote(approver, world.org, cohort.id, reason="approved", snapshot=snapshot, **extra)
        return refused.value.code
    assert attempt(None) == "SNAPSHOT_REQUIRED"
    assert attempt({**SNAP, "status": "FAILED"}) == "SNAPSHOT_INVALID"
    assert attempt({**SNAP, "manifest_hash": "short"}) == "SNAPSHOT_INVALID"
    assert attempt({**SNAP, "encrypted_file_hash": ""}) == "SNAPSHOT_INVALID"
    assert attempt({**SNAP, "created_at": "yesterday"}) == "SNAPSHOT_INVALID"
    assert attempt({**SNAP, "created_at": "2026-10-16T11:00:00"}) == "SNAPSHOT_INVALID"          # no zone
    assert attempt({**SNAP, "created_at": "2026-10-14T11:00:00+00:00"}) == "SNAPSHOT_STALE"        # two days old
    assert attempt({**SNAP, "created_at": "2026-10-16T13:00:00+00:00"}) == "SNAPSHOT_STALE"        # from the future
    assert attempt(SNAP, max_snapshot_age_hours=0) == "SNAPSHOT_STALE"
    assert authority(world)["LEG-1"] in (None, "UNCLASSIFIED")                                      # nothing changed
    service.promote(approver, world.org, cohort.id, reason="approved", snapshot=SNAP)
    promoted = [a for a in world.uow.read(lambda tx: tx.audit.list_for_entity(world.org, "TRIAL", world.trial_id))
                if a.action == "COHORT_PROMOTED_TO_PILOT"][0]
    assert promoted.after["snapshot"] == {"manifest_hash": "a" * 64, "encrypted_file_hash": "b" * 64,
                                          "created_at": "2026-10-16T11:00:00+00:00"}
