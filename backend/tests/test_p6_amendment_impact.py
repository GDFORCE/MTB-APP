"""P6-03: the mapping-aware per-visit impact and the signatures a deferred decision compares."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.application.patients import PatientEventRecord
from app.domain.schedule.amendment import Translation
from app.domain.schedule.amendment_impact import (
    binding_differences, binding_hash, build_visit_impacts, impact_signature, impact_summary,
    signature_difference,
)

ORG, PATIENT, VERSION = uuid4(), uuid4(), uuid4()


def old(code, index=0, *, on=date(2026, 1, 8), status="RESOLVED", protected=False, earliest=None, latest=None):
    return PatientEventRecord(
        organization_id=ORG, patient_id=PATIENT, evaluation_id=uuid4(), schedule_version_id=VERSION,
        event_definition_id=uuid4(), event_code=code, occurrence_index=index,
        logical_key=f"{code}:{index}", status=status, nominal_start_date=on, earliest_date=earliest,
        latest_date=latest, protected_history=protected)


def new(code, index=0, *, on=date(2026, 1, 8), status="RESOLVED", earliest=None, latest=None,
        protected=False):
    """A row of the FINAL reconciled projection (what the commit persists)."""
    return old(code, index, on=on, status=status, earliest=earliest, latest=latest, protected=protected)


def result(*rows):
    return list(rows)


def identity(code, index):
    return Translation.TARGET, (code, index), None


def by_key(impacts):
    return {(row["from_key"], row["to_key"]): row for row in impacts}


def test_every_category_is_reported_for_the_right_reason():
    prior = [old("SAME"), old("MOVES"), old("DONE", protected=True), old("GONE")]
    outcome = result(new("SAME"), new("MOVES", on=date(2026, 1, 11)), new("DONE", on=date(2026, 1, 8), protected=True),
                     new("FRESH", on=date(2026, 2, 15)))

    def translate(code, index):
        return (Translation.NONE, None, None) if code == "GONE" else identity(code, index)

    impacts, blockers = build_visit_impacts(prior, outcome, translate)
    rows = by_key(impacts)
    assert blockers == []
    assert rows[("SAME:0", "SAME:0")]["category"] == "UNCHANGED"
    moved = rows[("MOVES:0", "MOVES:0")]
    assert moved["category"] == "MOVED" and (moved["old_target"], moved["new_target"]) == ("2026-01-08", "2026-01-11")
    protected = rows[("DONE:0", "DONE:0")]
    assert protected["category"] == "PROTECTED" and protected["new_target"] == "2026-01-08"   # as it happened
    assert "recorded history is kept" in protected["reason"]
    assert rows[("GONE:0", None)]["category"] == "REMOVED"
    assert rows[(None, "FRESH:0")]["category"] == "ADDED"
    assert impact_summary(impacts) == {
        "UNCHANGED": 1, "MOVED": 1, "STATUS_CHANGED": 0, "PROTECTED": 1, "ADDED": 1, "REMOVED": 1,
        "WAITING": 0, "BLOCKED": 0}


def test_a_renamed_visit_is_matched_by_the_mapping_not_by_its_code():
    prior = [old("WEEK_4", on=date(2026, 2, 1))]
    outcome = result(new("VISIT_04", on=date(2026, 2, 3)))

    def translate(code, index):
        return Translation.TARGET, ("VISIT_04", index), None

    impacts, blockers = build_visit_impacts(prior, outcome, translate)
    assert blockers == [] and len(impacts) == 1
    assert (impacts[0]["from_key"], impacts[0]["to_key"], impacts[0]["category"]) == (
        "WEEK_4:0", "VISIT_04:0", "MOVED")


def test_an_undecided_destination_is_a_blocker_never_a_silent_removal():
    impacts, blockers = build_visit_impacts(
        [old("A")], result(new("A")),
        lambda code, index: (Translation.UNRESOLVED, None, "the mapping has not been reviewed"))
    assert [b["code"] for b in blockers] == ["VISIT_MAPPING_UNRESOLVED"]
    assert blockers[0]["detail"] == "the mapping has not been reviewed"
    # the earlier occurrence is not lost: it is BLOCKED, and the unmatched later one is added
    assert {row["category"] for row in impacts} == {"BLOCKED", "ADDED"}


def test_a_visit_that_was_waiting_stays_waiting_and_is_not_a_new_problem():
    prior = [old("LAB", on=None, status="BLOCKED")]
    outcome = result(new("LAB", on=None, status="BLOCKED"))
    impacts, _ = build_visit_impacts(prior, outcome, identity)
    assert impacts[0]["category"] == "UNCHANGED"
    # ... but one the new version newly holds is reported
    impacts, _ = build_visit_impacts([old("LAB")], result(new("LAB", on=None, status="BLOCKED")),
                                     identity)
    assert impacts[0]["category"] == "BLOCKED" and "BLOCKED" in impacts[0]["reason"]
    impacts, _ = build_visit_impacts(
        [old("LAB")], result(new("LAB", on=None, status="WAITING_FOR_ANCHOR")), identity)
    assert impacts[0]["category"] == "WAITING"


def test_a_changed_window_is_a_move_and_a_status_change_is_reported():
    prior = [old("A", earliest=date(2026, 1, 5), latest=date(2026, 1, 11)), old("B")]
    outcome = result(new("A", earliest=date(2026, 1, 6), latest=date(2026, 1, 12)),
                     new("B", status="PAUSED"))
    impacts, _ = build_visit_impacts(prior, outcome, identity)
    rows = by_key(impacts)
    assert rows[("A:0", "A:0")]["category"] == "MOVED" and rows[("A:0", "A:0")]["reason"] == "the allowed window changed"
    assert rows[("B:0", "B:0")]["category"] == "BLOCKED"          # a held visit is not a quiet status change


def test_the_structural_signature_ignores_dates_and_progress_but_not_which_visits_exist():
    before = [old("A"), old("B", on=None, status="BLOCKED")]
    first, _ = build_visit_impacts(before, result(new("A", on=date(2026, 2, 1)), new("B", on=None,
                                   status="BLOCKED")), identity)
    later, _ = build_visit_impacts(before, result(new("A", on=date(2026, 2, 9)), new("B", on=date(2026, 3, 1))),
                                   identity)
    assert signature_difference(impact_signature(first, strict=False), impact_signature(later, strict=False)) == []
    assert signature_difference(impact_signature(first, strict=True), impact_signature(later, strict=True)) != []
    added, _ = build_visit_impacts(before, result(new("A"), new("B", on=None, status="BLOCKED"),
                                                  new("C")), identity)
    assert signature_difference(
        impact_signature(first, strict=False), impact_signature(added, strict=False)) == ["->C:0"]


def test_bindings_are_compared_by_canonical_value_and_hashed_deterministically():
    a = {"x": 1, "y": {"b": 2, "a": 1}, "z": None}
    b = {"z": None, "y": {"a": 1, "b": 2}, "x": 1}
    assert binding_hash(a) == binding_hash(b)
    assert binding_differences(a, b) == []
    assert binding_differences(a, {**b, "x": 2, "w": 1}) == ["w", "x"]
    assert binding_hash(a) != binding_hash({**a, "x": 2})


def test_a_visit_before_the_adoption_point_shows_the_date_persistence_keeps():
    """F2: the preview reads the final reconciled row, so a raw earlier-version re-date that
    reconciliation overrides (protected before the effective point) is never displayed."""
    prior = [old("C1D1", on=date(2026, 1, 8))]
    final = result(new("C1D1", on=date(2026, 1, 8), protected=True))
    impacts, _ = build_visit_impacts(prior, final, identity)
    assert impacts[0]["category"] == "PROTECTED" and impacts[0]["new_target"] == "2026-01-08"
