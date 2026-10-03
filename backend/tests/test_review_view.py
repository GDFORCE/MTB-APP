"""The draft Visit Schedule review view (UI-02..UI-06, UI-09, UI-10).

Synthetic schedules only (tests/_review_fixtures.py).
"""

from __future__ import annotations

import csv
import io

import pytest

from _review_fixtures import API, build_client, row, seed_schedule, view


@pytest.fixture()
def api():
    client, factory, engine = build_client()
    with client:
        yield client, factory
    engine.dispose()


def test_clean_draft_counts_rows_not_issues_and_is_approvable(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    body = view(client, version_id)

    assert body["draft_version_id"] == version_id
    assert len(body["revision"]) == 32 and len(body["semantic_fingerprint"]) == 64
    assert body["summary"]["displayed_rows"] == 3
    assert body["summary"]["pending_rows"] == 0
    assert body["summary"]["ready_rows"] == 3
    assert body["summary"]["blocking_issues"] == 0
    assert body["can_approve"] is True, body["approval_blockers"]
    assert [item["visit_name"] for item in body["rows"]] == ["Screening", "Day 1", "Week 4"]


def test_counts_keep_rows_questions_and_findings_apart(api):
    client, factory = api
    body = view(client, seed_schedule(factory, with_issues=True))
    summary = body["summary"]

    assert summary["displayed_rows"] == 6
    # Two post-op rows wait on the shared question, Month 6 has its own footnote.
    assert summary["pending_rows"] == 3
    assert summary["ready_rows"] == 3
    assert summary["pending_rows"] + summary["ready_rows"] == summary["displayed_rows"]
    # Findings are counted as findings (two unknown-anchor findings + one footnote),
    # not as rows, and the shared question is counted once.
    assert summary["blocking_issues"] == 3
    assert summary["shared_questions"] == 1
    assert body["can_approve"] is False
    assert any("blocking finding" in reason for reason in body["approval_blockers"])


def test_window_distinguishes_stated_not_stated_needs_review_and_not_applicable(api):
    client, factory = api
    body = view(client, seed_schedule(factory, with_issues=True))

    assert row(body, "WEEK_4")["window_status"] == "STATED"
    assert row(body, "WEEK_4")["window_summary"] == "±3 days"
    # An absent window is never shown as a zero-day window.
    assert row(body, "DAY_1")["window_status"] == "NOT_STATED"
    assert row(body, "DAY_1")["window_summary"] == "Not stated"
    # A range is itself the allowed span.
    assert row(body, "SCREENING")["window_status"] == "NOT_APPLICABLE"
    # An unresolved window footnote is not treated as "no window".
    assert row(body, "MONTH_6")["window_status"] == "NEEDS_REVIEW"
    assert row(body, "MONTH_6")["window_summary"] == "Needs review"


def test_timing_keeps_units_and_reference_events(api):
    client, factory = api
    body = view(client, seed_schedule(factory, with_issues=True))

    assert row(body, "SCREENING")["timing_summary"] == "Between 1 and 28 days before Randomisation"
    assert row(body, "DAY_1")["timing_summary"] == "Day 1 from Randomisation"
    assert row(body, "WEEK_4")["timing_summary"] == "28 days after Randomisation"
    # Calendar months stay months; an undefined anchor says so.
    assert row(body, "POST_OP_2")["timing_summary"] == "2 months after SURGERY (anchor not defined)"


def test_expanded_row_exposes_every_constraint_including_those_not_in_the_summary(api):
    client, factory = api
    week4 = row(view(client, seed_schedule(factory, with_issues=False)), "WEEK_4")
    details = week4["details"]
    kinds = {item["kind"] for item in details["constraints"]}

    assert {"TIMING", "WINDOW", "APPLICABILITY", "FOOTNOTE", "VISIT_MODE"} <= kinds
    hidden = [item for item in details["constraints"] if not item["in_compact_summary"]]
    assert any(item["kind"] == "FOOTNOTE" and "pre-dose" in item["summary"] for item in hidden)
    assert week4["applicability_summary"] == "Arm: A"
    assert [item["name"] for item in details["activities"]] == ["Vital signs"]
    assert details["anchors"][0]["code"] == "BASELINE"
    assert details["evidence"][0]["page_number"] == 12
    assert details["evidence"][0]["quote"] == "Synthetic source sentence for the test."


def test_one_missing_anchor_is_one_shared_question_linked_to_every_affected_row(api):
    client, factory = api
    body = view(client, seed_schedule(factory, with_issues=True))
    [question] = body["shared_review_questions"]

    assert question["id"] == "missing-anchor:SURGERY"
    assert question["blocking"] is True
    assert len(question["issues"]) == 2
    affected = {item["visit_name"] for item in question["affected_rows"]}
    assert affected == {"Post-op check 1", "Post-op check 2"}
    for code in ("POST_OP_1", "POST_OP_2"):
        item = row(body, code)
        assert item["question_refs"] == ["missing-anchor:SURGERY"]
        assert item["review_status"] == "PENDING"
        # The shared finding is not duplicated into each row's own findings.
        assert item["issue_refs"] == []


def test_document_level_issues_are_separate_from_rows(api):
    client, factory = api
    body = view(client, seed_schedule(factory, with_issues=False))
    codes = [item["code"] for item in body["document_level_issues"]]

    # No coverage ledger yet: reported honestly, never as complete.
    assert "SOURCE_COVERAGE_NOT_RECORDED" in codes
    assert body["source_coverage_status"]["status"] == "NOT_RECORDED"
    assert all(not item["blocking"] for item in body["document_level_issues"])


def test_review_view_contains_no_patient_dates(api):
    client, factory = api
    body = view(client, seed_schedule(factory, with_issues=True))
    forbidden = {"patient_id", "due_date", "scheduled_date", "occurrence_date", "target_date",
                 "earliest", "latest", "completed_at", "actual_date"}

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                assert key not in forbidden, key
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(body)


def test_json_export_is_versioned_labelled_and_complete(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=True)
    response = client.get(f"{API}/schedule-versions/{version_id}/export?format=json")
    assert response.status_code == 200
    body = response.json()

    assert body["export_schema"] == "mtb.visit-schedule-export.v1"
    assert body["label"].startswith("DRAFT")
    assert body["provenance"]["document_hash"] == "a" * 64
    assert len(body["canonical_schedule"]["events"]) == 6
    assert body["shared_review_questions"][0]["id"] == "missing-anchor:SURGERY"


def test_csv_export_lists_every_constraint_evidence_and_question(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=True)
    body = view(client, version_id)
    response = client.get(f"{API}/schedule-versions/{version_id}/export?format=csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "draft" in response.headers["content-disposition"]
    records = list(csv.reader(io.StringIO(response.text)))
    detail_ids = {record[2] for record in records if len(record) > 2}

    assert records[1][1].startswith("DRAFT")
    for item in body["rows"]:
        for constraint in item["details"]["constraints"]:
            assert constraint["id"] in detail_ids, constraint
        for evidence in item["details"]["evidence"]:
            assert evidence["id"] in detail_ids
    assert "missing-anchor:SURGERY" in detail_ids
    assert any(record[:1] == ["DETAIL APPENDIX"] for record in records)


def test_unknown_tenant_is_not_found(api):
    client, factory = api
    response = client.get(f"{API}/schedule-versions/00000000-0000-0000-0000-00000000abcd/review-view")
    assert response.status_code == 404
