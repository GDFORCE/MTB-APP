"""Repeated visit names are matched by visit number, never by position."""
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application.legacy_migration import SOURCE_SYSTEM, LegacyMigrationJob, LegacyPatient
from app.application.parity import ParityService
from app.domain.schedule.parity import compare_schedules, to_comparable
from _p6_world import TODAY, World
from test_pdf_cohort_cutover import Source

pytest_plugins = ["tests.contract.conftest"]


def rows(*numbers, name="Weekly dose", start=date(2026, 1, 15)):
    return [{"name": name, "visit_number": number, "scheduled_date": start + timedelta(days=7 * index)}
            for index, number in enumerate(numbers)]


def verdict(legacy, engine, number_field="visit_number"):
    return compare_schedules(to_comparable(legacy, key_fields=("name",), number_field=number_field),
                             to_comparable(engine, key_fields=("name",), number_field=number_field))


def test_namesakes_with_distinct_whole_numbers_are_paired_by_number():
    report = verdict(rows(2, 3, 4), rows(2, 3, 4))
    assert report.verdict.value == "MATCH" and report.compared == 3


def test_a_number_that_disagrees_is_a_reported_difference_not_a_forced_pair():
    report = verdict(rows(2, 3, 4), rows(2, 3, 5))
    assert report.verdict.value != "MATCH"
    assert "AMBIGUOUS_MATCH" not in {item.kind for item in report.differences}


def test_missing_repeated_or_non_whole_numbers_keep_the_namesakes_ambiguous():
    assert "AMBIGUOUS_MATCH" in {i.kind for i in verdict(rows(None, None, None), rows(1, 2, 3)).differences}
    assert "AMBIGUOUS_MATCH" in {i.kind for i in verdict(rows(1, 1, 2), rows(1, 2, 3)).differences}
    assert "AMBIGUOUS_MATCH" in {i.kind for i in verdict(rows(1.5, 2, 3), rows(1, 2, 3)).differences}
    assert "AMBIGUOUS_MATCH" in {i.kind for i in verdict(rows(1, 2, 3), rows(1, 2, 3), number_field=None).differences}


def test_names_that_do_not_repeat_never_need_a_number():
    assert verdict([{"name": "Screening", "scheduled_date": date(2026, 1, 1)}],
                   [{"name": "Screening", "scheduled_date": date(2026, 1, 1)}]).verdict.value == "MATCH"


def test_a_recurring_schedule_can_now_be_migrated_and_reconciled_over_its_full_horizon(uow):
    world = World(uow)
    definition = uow.read(lambda tx: tx.schedules.get_header(world.v1)).schedule_definition_id
    args = dict(organization_id=world.org, actor_id=world.pi, canonical_trial_id=world.trial_id,
                schedule_definition_id=definition, external_trial_id="LEG", today=TODAY)
    item = LegacyPatient(legacy_id="LEG-1", subject_id="S1", baseline_date=TODAY)
    LegacyMigrationJob(uow, Source([item])).run(run_key="one", dry_run=False, **args)
    mapping = uow.read(lambda tx: tx.migrations.get_mapping(world.org, SOURCE_SYSTEM, "LEG-1"))

    def visits(tx):
        patient = tx.patients.get(world.org, mapping.patient_id)
        horizon = TODAY + timedelta(days=366)
        result = world.patients.evaluate_result(tx, patient, patient.current_schedule_version_id, horizon=horizon)
        schedule = tx.schedules.get(patient.current_schedule_version_id)
        engine = ParityService._engine_rows(patient, result.events, {e.id: e for e in schedule.events})
        return tuple({"name": r["name"], "visit_number": r["visit_number"], "scheduled_date": r.get("scheduled_date"),
                      "window_start": r.get("window_start"), "window_end": r.get("window_end")} for r in engine)
    legacy = uow.read(visits)
    assert sum(1 for row in legacy if row["name"] == "Weekly dose") > 1            # the case that used to be blocked
    LegacyMigrationJob(uow, Source([replace(item, visits=legacy)])).run(run_key="two", dry_run=False, **args)
    final = uow.read(lambda tx: tx.migrations.get_mapping(world.org, SOURCE_SYSTEM, "LEG-1"))
    assert final.status == "RECONCILED" and final.parity_verdict == "MATCH"
