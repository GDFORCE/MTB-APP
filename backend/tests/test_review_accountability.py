"""The extraction accountability ledger in the Visit Schedule review
(RV-01, IN-03, IR-05, UI-03, UI-06, UI-09). Synthetic ledgers only."""

from __future__ import annotations

import csv
import io
from uuid import UUID

import pytest
from sqlalchemy import select

from _review_fixtures import API, USER, build_client, row, seed_schedule, view
from app.db import models as db


@pytest.fixture()
def api():
    client, factory, engine = build_client()
    with client:
        yield client, factory
    engine.dispose()


def ledger(*, coverage_status="COMPLETE", statements=None, gaps=None, references=None):
    statements = statements if statements is not None else []
    uncompiled = [s for s in statements if s["disposition"] in {"PENDING_INTERPRETATION", "UNSUPPORTED", "INVALID"}]
    return {
        "schema": "uctsm-extraction-accountability.v1",
        "versions": {"reader": "v3"},
        "document": {"hash": "a" * 64, "pages": 40},
        "summary": {"coverage_status": coverage_status, "coverage_gaps": len(gaps or []),
                    "statements": len(statements), "dispositions": {},
                    "uncompiled_statements": len(uncompiled),
                    "complete": coverage_status == "COMPLETE" and not uncompiled},
        "coverage": {"status": coverage_status, "counts": {"READ": 38, "NOT_SELECTED": 2},
                     "gaps": gaps or [], "units": [{"unit_id": "page-1", "page": 1, "state": "READ",
                                                    "rationale": "schedule table", "text_status": "OK"}],
                     "references": references or [], "usage": {}, "limits": {}},
        "statements": statements,
        "relations": [],
        "anchors": {"canonical": ["BASELINE"], "aliases": [], "unmatched": []},
    }


def statement(statement_id, disposition, *, event_code=None, quote="Synthetic quote.", verification="TEXT_MATCHED"):
    return {
        "statement_id": statement_id, "what": f"Statement {statement_id}", "disposition": disposition,
        "reason": f"reason for {statement_id}", "operator": None,
        "citations": [{"page": 7, "section": "6.1", "table": None, "quote": quote, "verification": verification}],
        "rule_refs": [{"entity_type": "EVENT", "code": event_code, "entity_id": None, "event_code": event_code}]
        if event_code else [],
        "relations": [], "unknown_fields": {}, "blocking_issue_codes": [],
    }


def attach(factory, version_id, accountability):
    with factory() as session:
        version = session.get(db.ScheduleVersion, UUID(version_id))
        definition = session.get(db.ScheduleDefinition, version.schedule_definition_id)
        run = db.ExtractionRun(
            organization_id=UUID(USER["organization_id"]), protocol_version_id=definition.protocol_version_id,
            status="COMPLETED", prompt_version="test", document_hash="a" * 64,
            trace={"options": [{"schedule_version_id": version_id, "accountability": accountability}]},
        )
        session.add(run)
        session.flush()
        version.extraction_run_id = run.id
        session.commit()


def add_extraction_issue(factory, version_id, **details):
    with factory() as session:
        session.add(db.ValidationIssue(
            schedule_version_id=UUID(version_id), validator_version="uctsm-validator.v1",
            issue_code=details.pop("code"), severity="ERROR", message=details.pop("message"),
            details={"origin": "EXTRACTION", **details}, blocking=True, status="OPEN",
        ))
        session.commit()


def test_without_a_ledger_the_non_blocking_note_remains(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    body = view(client, version_id)
    assert body["source_coverage"] is None
    assert "SOURCE_COVERAGE_NOT_RECORDED" in [item["code"] for item in body["document_level_issues"]]
    assert client.get(f"{API}/schedule-versions/{version_id}/accountability").status_code == 404


def test_complete_ledger_with_compiled_statements_allows_approval(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    attach(factory, version_id, ledger(statements=[
        statement("s-wk4", "COMPILED", event_code="WEEK_4"),
        statement("s-ae", "OUT_OF_SCOPE_NON_SCHEDULE"),
    ]))
    body = view(client, version_id)

    assert body["can_approve"] is True, body["approval_blockers"]
    assert "SOURCE_COVERAGE_NOT_RECORDED" not in [item["code"] for item in body["document_level_issues"]]
    panel = body["source_coverage"]
    assert panel["status"] == "COMPLETE" and panel["counts"]["READ"] == 38
    [unlinked] = panel["statements"]["not_linked_to_a_visit"]
    assert unlinked["statement_id"] == "s-ae"
    assert unlinked["disposition_label"] == "Not a schedule rule"
    [linked] = row(body, "WEEK_4")["details"]["statements"]
    assert linked["disposition"] == "COMPILED"
    assert linked["citations"][0]["verification"] == "TEXT_MATCHED"
    assert linked["citations"][0]["quote"] == "Synthetic quote."
    assert body["statement_summary"]["total"] == 2

    response = client.get(f"{API}/schedule-versions/{version_id}/accountability")
    assert response.status_code == 200
    assert response.json()["schema"] == "uctsm-extraction-accountability.v1"


def test_incomplete_coverage_blocks_approval_and_lists_gaps_and_missing_references(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    attach(factory, version_id, ledger(
        coverage_status="INCOMPLETE",
        gaps=[{"unit_id": "ref-appendix-B", "page": None, "state": "MISSING_REFERENCE",
               "rationale": "Appendix B is referenced on page 12 but is not in the uploaded document."}],
        references=[{"kind": "APPENDIX", "label": "B", "state": "MISSING", "required": True,
                     "from_pages": [12], "text": "see Appendix B"}],
    ))
    body = view(client, version_id)

    assert body["can_approve"] is False
    assert any("coverage is incomplete" in reason for reason in body["approval_blockers"])
    panel = body["source_coverage"]
    assert panel["gaps"][0]["rationale"].startswith("Appendix B is referenced")
    assert panel["missing_references"][0]["label"] == "B"
    assert "SOURCE_COVERAGE_INCOMPLETE" in [item["code"] for item in body["document_level_issues"]]
    blocked = client.post(f"{API}/schedule-versions/{version_id}/approve-version",
                          json={"expected_revision": body["revision"], "reason": "ok"})
    assert blocked.status_code == 409


def test_an_uncompiled_statement_blocks_and_marks_its_row_pending(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    attach(factory, version_id, ledger(statements=[
        statement("s-d1", "PENDING_INTERPRETATION", event_code="DAY_1", verification="UNVERIFIED"),
    ]))
    body = view(client, version_id)
    day1 = row(body, "DAY_1")

    assert body["can_approve"] is False
    assert day1["review_status"] == "PENDING"
    assert "A protocol statement about this visit is not compiled" in day1["pending_reasons"]
    assert day1["details"]["statements"][0]["citations"][0]["verification"] == "UNVERIFIED"
    assert row(body, "WEEK_4")["review_status"] == "READY"


def test_anchor_not_canonical_is_one_shared_question_per_question_id(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=True)  # POST_OP_* count from SURGERY
    attach(factory, version_id, ledger(statements=[
        statement("s-po1", "PENDING_INTERPRETATION", event_code="POST_OP_1"),
        statement("s-po2", "PENDING_INTERPRETATION", event_code="POST_OP_2"),
    ]))
    for fact in ("s-po1", "s-po2"):
        add_extraction_issue(
            factory, version_id, code="ANCHOR_NOT_CANONICAL",
            message="'Surgery' is used as a reference date but is not one of the protocol's reference dates.",
            anchor_name="Surgery", anchor_code="SURGERY", fact_ids=[fact], candidates=["BASELINE"],
            question_id="anchor-equivalence-surgery", shared_question=True)
    body = view(client, version_id)
    questions = [item["id"] for item in body["shared_review_questions"]]

    # The two ledger findings and the two unknown-anchor findings: one question.
    assert questions == ["anchor-equivalence-surgery"]
    [question] = body["shared_review_questions"]
    assert question["kind"] == "ANCHOR_EQUIVALENCE"
    assert {item["visit_name"] for item in question["affected_rows"]} == {"Post-op check 1", "Post-op check 2"}
    assert len(question["issues"]) == 4
    assert question["actions"] == ["REMAP_ANCHOR", "DEFINE_ANCHOR"]

    resolved = client.post(
        f"{API}/schedule-versions/{version_id}/review-questions/anchor-equivalence-surgery/resolve",
        json={"expected_revision": body["revision"], "rationale": "Protocol 6.2: surgery is its own date",
              "action": "DEFINE_ANCHOR", "display_name": "Surgery", "anchor_type": "PROCEDURE"})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["view"]["shared_review_questions"] == []
    with factory() as session:
        statuses = session.scalars(select(db.ValidationIssue.status).where(
            db.ValidationIssue.issue_code == "ANCHOR_NOT_CANONICAL")).all()
        assert statuses and set(statuses) == {"RESOLVED"}


def test_additional_restrictions_are_shown_block_and_are_exported(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    restriction = {"fact_id": "s-extra", "relation": "AND", "status": "NOT_COMPILED",
                   "required_operator": "CONSTRAINT_CONJUNCTION",
                   "timing": {"type": "NO_LATER_THAN"},
                   "citations": [{"page": 9, "quote": "and no later than 3 days after dosing",
                                  "verification": "TEXT_MATCHED"}]}
    with factory() as session:
        event = session.scalars(select(db.Event).where(db.Event.code == "WEEK_4")).one()
        event.metadata_json = {**(event.metadata_json or {}), "additional_restrictions": [restriction]}
        session.commit()
    attach(factory, version_id, ledger(statements=[statement("s-extra", "UNSUPPORTED", event_code="WEEK_4")]))
    body = view(client, version_id)
    week4 = row(body, "WEEK_4")

    [shown] = week4["details"]["additional_restrictions"]
    assert shown["status"] == "NOT_COMPILED" and shown["citations"][0]["page"] == 9
    assert "An additional restriction is not compiled" in week4["pending_reasons"]
    kinds = [item["kind"] for item in week4["details"]["constraints"]]
    assert "ADDITIONAL_RESTRICTION" in kinds and "ADDITIONAL_RULE" not in kinds
    assert body["can_approve"] is False

    text = client.get(f"{API}/schedule-versions/{version_id}/export?format=csv").text
    records = list(csv.reader(io.StringIO(text)))
    assert any(record[1:2] == ["ADDITIONAL_RESTRICTION"] for record in records)


def test_exports_carry_the_ledger_and_every_statement(api):
    client, factory = api
    version_id = seed_schedule(factory, with_issues=False)
    statements = [statement("s-wk4", "COMPILED", event_code="WEEK_4"),
                  statement("s-ae", "OUT_OF_SCOPE_NON_SCHEDULE"),
                  statement("s-dup", "DUPLICATE_OF", event_code="WEEK_4")]
    attach(factory, version_id, ledger(statements=statements))

    body = client.get(f"{API}/schedule-versions/{version_id}/export?format=json").json()
    assert body["accountability"]["schema"] == "uctsm-extraction-accountability.v1"
    assert len(body["accountability"]["statements"]) == 3
    assert body["source_coverage"]["status"] == "COMPLETE"

    records = list(csv.reader(io.StringIO(
        client.get(f"{API}/schedule-versions/{version_id}/export?format=csv").text)))
    flat = [record[0] for record in records if record]
    assert "SOURCE COVERAGE APPENDIX" in flat and "STATEMENTS APPENDIX" in flat
    start = flat.index("STATEMENTS APPENDIX")
    listed = {record[0] for record in records[start + 2:] if record}
    assert {"s-wk4", "s-ae", "s-dup"} <= listed
    assert "page-1" in flat
