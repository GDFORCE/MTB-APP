"""Composable constraints (WP2) in the Visit Schedule review: B's formatter,
unsupported operators that cannot be acknowledged, infeasible sets, and the
patient feasibility fields. Synthetic schedules only."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select

from _review_fixtures import API, USER, build_client, row, view
from app.api.uctsm import _feasibility_fields
from app.db import models as db
from app.db.repositories import ScheduleRepository
from app.domain.schedule.models import (
    Anchor, ClaimEvidence, Event, Evidence, ScheduleMetadata, ScheduleStatus, UniversalSchedule,
)


@pytest.fixture()
def api():
    client, factory, engine = build_client()
    with client:
        yield client, factory
    engine.dispose()


def anchor_value(code, days=None):
    value = {"type": "ANCHOR", "code": code}
    if days is None:
        return value
    return {"type": "SHIFT", "reference": value, "amount": {"value": days, "unit": "DAY"}}


def persist(factory, events: list[dict], extra: dict | None = None) -> str:
    with factory() as session:
        trial = db.Trial(organization_id=UUID(USER["organization_id"]), protocol_id="SYN-C")
        session.add(trial); session.flush()
        protocol = db.Protocol(trial_id=trial.id, protocol_number="SYN-C")
        session.add(protocol); session.flush()
        protocol_version = db.ProtocolVersion(
            protocol_id=protocol.id, version_label="1", document_name="c.pdf",
            document_uri="private://c.pdf", document_hash="c" * 64, uploaded_by=UUID(USER["id"]))
        session.add(protocol_version); session.flush()
        protocol.current_version_id = protocol_version.id
        definition = db.ScheduleDefinition(protocol_version_id=protocol_version.id, name="Constraint schedule")
        session.add(definition); session.flush()
        evidence = Evidence(evidence_type="PROTOCOL_TEXT", page_number=3, source_text="Synthetic.")
        anchors = [
            Anchor(code="BASELINE", display_name="Randomisation", anchor_type="RANDOMISATION",
                   status="RESOLVED", derivation_rule={"role": "SCHEDULE_ORIGIN"}, evidence_refs=[evidence.id]),
            Anchor(code="RT_END", display_name="RT end", anchor_type="TREATMENT_END",
                   status="RESOLVED", evidence_refs=[evidence.id]),
        ]
        built, claims = [], []
        for index, raw in enumerate(events, 1):
            event = Event.model_validate({
                "protocol_label": raw["code"], "display_name": raw["code"].replace("_", " ").title(),
                "event_type": "VISIT", "sequence_number": index, "evidence_refs": [evidence.id], **raw})
            built.append(event)
            for claim in ("EVENT_NAME", "TIMING", "CONSTRAINT"):
                claims.append(ClaimEvidence(evidence_id=evidence.id, claim_type=claim,
                                            claim_entity_type="EVENT", claim_entity_id=event.id, confidence=1))
        schedule = UniversalSchedule(
            schedule_metadata=ScheduleMetadata(name="Constraint schedule", protocol_version_id=protocol_version.id,
                                               status=ScheduleStatus.VALIDATION_REQUIRED),
            anchors=anchors, events=built, evidence=[evidence], claim_evidence=claims, **(extra or {}))
        ScheduleRepository(session).persist_draft(schedule, schedule_definition_id=definition.id)
        session.commit()
        return str(schedule.schedule_version_id)


CONSTRAINED = {
    "code": "FOLLOW_UP",
    "timing": {"type": "CONSTRAINED", "constraint": {"type": "ALL", "id": "fu", "items": [
        {"type": "LOWER_BOUND", "id": "fu-lower", "inclusive": True, "value": anchor_value("BASELINE", 28)},
        {"type": "UPPER_BOUND", "id": "fu-upper", "inclusive": True, "value": anchor_value("RT_END", 14)},
    ]}},
}


def test_constrained_timing_uses_the_constraint_formatter_and_names_every_reference(api):
    client, factory = api
    body = view(client, persist(factory, [CONSTRAINED]))
    follow = row(body, "FOLLOW_UP")

    # Every bound is in the one-line summary, with the protocol's own names.
    assert follow["timing_summary"] == "On or after Randomisation + 28 days and on or before RT end + 14 days"
    kinds = [item["kind"] for item in follow["details"]["constraints"]]
    assert {"CONSTRAINT_ALL", "CONSTRAINT_LOWER_BOUND", "CONSTRAINT_UPPER_BOUND"} <= set(kinds)
    assert "ADDITIONAL_RULE" not in kinds
    [tree] = follow["details"]["constraint_trees"]
    assert [item["id"] for item in tree["tree"]["items"]] == ["fu-lower", "fu-upper"]
    assert follow["window_status"] != "STATED"
    assert follow["unsupported"] is False


def test_event_constraints_are_listed_and_named_in_the_compact_row(api):
    client, factory = api
    body = view(client, persist(factory, [{
        "code": "SCAN",
        "timing": {"type": "OFFSET", "reference": {"kind": "ANCHOR", "code": "BASELINE"},
                   "offset": {"value": 56, "unit": "DAY"}},
        "constraints": {"type": "UPPER_BOUND", "id": "scan-rt", "inclusive": True,
                        "value": anchor_value("RT_END", 7)},
    }]))
    scan = row(body, "SCAN")
    assert "; also on or before RT end + 7 days" in scan["timing_summary"]
    assert any(item["constraint_id"] == "scan-rt" for item in scan["details"]["constraints"]
               if item["kind"].startswith("CONSTRAINT_"))


def test_unsupported_operator_blocks_every_affected_row_and_cannot_be_acknowledged(api):
    client, factory = api
    version_id = persist(factory, [
        {"code": "DOSE", "timing": {"type": "CONSTRAINED", "constraint": {
            "type": "UNSUPPORTED", "id": "dose-bsa", "family": "CONSTRAINT",
            "source_operator": "WITHIN_N_BUSINESS_DAYS", "source_text": "within 3 business days"}}},
        {"code": "POST_DOSE", "timing": {"type": "OFFSET",
                                          "reference": {"kind": "EVENT", "event_code": "DOSE", "occurrence": "FIRST"},
                                          "offset": {"value": 7, "unit": "DAY"}}},
    ])
    body = view(client, version_id)
    dose = row(body, "DOSE")

    assert dose["unsupported"] is True and dose["review_status"] == "PENDING"
    assert any(item["kind"] == "CONSTRAINT_UNSUPPORTED" and not item["supported"]
               for item in dose["details"]["constraints"])
    questions = [item for item in body["shared_review_questions"] if item["kind"] == "UNSUPPORTED_OPERATOR"]
    assert questions and all(item["actions"] == [] and item["acknowledgeable"] is False for item in questions)
    affected = {name["visit_name"] for q in questions for name in q["affected_rows"]}
    assert "Dose" in affected
    assert all(issue["acknowledgeable"] is False for q in questions for issue in q["issues"])
    # The dependent visit is blocked too, through the same single question.
    assert len(questions) == 1
    assert {item["code"] for item in questions[0]["issues"]} == {
        "UNSUPPORTED_OPERATOR", "DEPENDS_ON_UNSUPPORTED_OPERATOR"}
    assert affected == {"Dose", "Post Dose"}
    assert row(body, "POST_DOSE")["review_status"] == "PENDING"
    assert row(body, "POST_DOSE")["unsupported"] is True
    assert body["can_approve"] is False

    attempt = client.post(
        f"{API}/schedule-versions/{version_id}/review-questions/{questions[0]['id']}/resolve",
        json={"expected_revision": body["revision"], "rationale": "acknowledged", "action": "CONFIRM_ANCHOR"})
    assert attempt.status_code == 422


def test_infeasible_constraints_are_a_blocking_row_finding_with_the_conflicting_rules(api):
    client, factory = api
    body = view(client, persist(factory, [{
        "code": "IMPOSSIBLE",
        "timing": {"type": "CONSTRAINED", "constraint": {"type": "ALL", "id": "imp", "items": [
            {"type": "LOWER_BOUND", "id": "imp-late", "inclusive": True, "value": anchor_value("BASELINE", 10)},
            {"type": "UPPER_BOUND", "id": "imp-early", "inclusive": True, "value": anchor_value("BASELINE", 5)},
        ]}},
    }]))
    impossible = row(body, "IMPOSSIBLE")
    finding = next(item for item in impossible["issues"] if item["code"] == "CONSTRAINT_INFEASIBLE")
    assert finding["blocking"] is True and finding["conflicting_constraints"]
    flagged = {item["constraint_id"] for item in impossible["details"]["constraints"] if item.get("conflicting")}
    assert flagged & {"imp-late", "imp-early"}
    assert impossible["review_status"] == "PENDING"


def test_a_duplicate_constraint_is_informational_only(api):
    client, factory = api
    bound = {"type": "UPPER_BOUND", "inclusive": True, "value": anchor_value("RT_END", 7)}
    body = view(client, persist(factory, [{
        "code": "DUP",
        "timing": {"type": "CONSTRAINED", "constraint": {"type": "ALL", "items": [dict(bound), dict(bound)]}},
    }]))
    duplicates = [item for item in row(body, "DUP")["issues"] if item["code"] == "DUPLICATE_CONSTRAINT"]
    assert duplicates and all(not item["blocking"] for item in duplicates)


def test_visit_mode_edits_persist_through_the_repository(api):
    client, factory = api
    version_id = persist(factory, [{"code": "CALL", "timing": {
        "type": "OFFSET", "reference": {"kind": "ANCHOR", "code": "BASELINE"}, "offset": {"value": 3, "unit": "DAY"}}}])
    body = view(client, version_id)
    response = client.patch(f"{API}/schedule-versions/{version_id}/events/{row(body, 'CALL')['id']}", json={
        "expected_revision": body["revision"], "rationale": "Protocol 6.3: by telephone",
        "changes": {"visit_mode": "TELEPHONE"}})
    assert response.status_code == 200, response.text
    with factory() as session:
        stored = session.scalars(select(db.Event).where(db.Event.code == "CALL")).one()
        assert stored.visit_mode == "TELEPHONE"


def test_patient_feasibility_fields_keep_disjoint_intervals_apart():
    explanation = {"feasibility": {
        "status": "FEASIBLE",
        "intervals": [
            {"lower": {"value": "2026-11-01", "inclusive": True, "sources": ["a"], "provisional": False},
             "upper": {"value": "2026-11-05", "inclusive": True, "sources": ["a"], "provisional": False}},
            {"lower": {"value": "2026-11-20", "inclusive": True, "sources": ["b"], "provisional": False},
             "upper": {"value": None, "inclusive": True, "sources": [], "provisional": False}},
        ],
        "missing_inputs": [], "conflicting_constraints": []}}
    fields = _feasibility_fields(explanation)
    assert fields["feasibility"] == "FEASIBLE"
    assert len(fields["feasible_intervals"]) == 2
    assert _feasibility_fields({})["feasibility"] is None


def test_v5_constraints_show_their_source_statement_and_calendar_gaps_block(api):
    from test_review_accountability import attach, ledger, statement

    client, factory = api
    version_id = persist(factory, [{
        "code": "SCAN",
        "timing": {"type": "OFFSET", "reference": {"kind": "ANCHOR", "code": "BASELINE"},
                   "offset": {"value": 56, "unit": "DAY"}},
        "constraints": {"type": "ALL", "id": "scan-all", "items": [
            {"type": "UPPER_BOUND", "id": "scan-rt", "inclusive": True, "value": anchor_value("RT_END", 7)},
            {"type": "UNSUPPORTED", "id": "scan-bd", "family": "CALENDAR",
             "source_operator": "BUSINESS_DAY_OFFSET", "source_text": "within 2 business days"},
        ]},
        "metadata": {"constraint_sources": [
            {"fact_id": "f-rt", "relation": "AND", "constraint_ids": ["scan-rt"]},
            {"fact_id": "f-bd", "relation": "AND", "constraint_ids": ["scan-bd"]},
        ]},
    }])
    # f-rt has no rule_refs: it is linked only through constraint_sources.
    attach(factory, version_id, ledger(statements=[
        statement("f-rt", "COMPILED", quote="no later than 7 days after RT"),
        statement("f-bd", "UNSUPPORTED", quote="within 2 business days"),
    ]))
    body = view(client, version_id)
    scan = row(body, "SCAN")
    by_id = {item.get("constraint_id"): item for item in scan["details"]["constraints"]
             if item["kind"].startswith("CONSTRAINT_")}

    [rt_source] = by_id["scan-rt"]["source_statements"]
    assert rt_source["statement_id"] == "f-rt" and rt_source["relation"] == "AND"
    assert rt_source["citations"][0]["quote"] == "no later than 7 days after RT"
    assert by_id["scan-bd"]["supported"] is False
    assert by_id["scan-bd"]["source_statements"][0]["disposition"] == "UNSUPPORTED"
    assert {item["statement_id"] for item in scan["details"]["statements"]} == {"f-rt", "f-bd"}
    assert body["source_coverage"]["statements"]["not_linked_to_a_visit"] == []
    assert scan["unsupported"] is True and scan["review_status"] == "PENDING"
    assert any(q["kind"] == "UNSUPPORTED_OPERATOR" for q in body["shared_review_questions"])
    kinds = [item["kind"] for item in scan["details"]["constraints"]]
    assert "ADDITIONAL_RULE" not in kinds and "ADDITIONAL_RESTRICTION" not in kinds
