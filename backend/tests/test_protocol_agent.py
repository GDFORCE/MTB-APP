"""The schedule agent audits, repairs, and stops within its configured bound."""
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from protocol_agent import (  # noqa: E402
    ScheduleAudit,
    ScheduleDocumentMap,
    ScheduleTimingEvidence,
    ScheduleVisitEvidence,
    run_schedule_extraction_agent,
)
from protocol_extraction import ExtractedSchedule  # noqa: E402


def _schedule(name: str, day: int) -> ExtractedSchedule:
    return ExtractedSchedule.model_validate({
        "schedule_kind": "linear",
        "visits": [{"name": name, "day_offset": day}],
        "source_notes": "Schedule table",
    })


def _audit(*, approved: bool, finding: str | None = None) -> ScheduleAudit:
    issues = [] if finding is None else [{
        "severity": "major",
        "category": "missing_visit",
        "finding": finding,
        "evidence": "Schedule of Assessments, page 12",
        "repair_instruction": "Add the omitted follow-up visit on Day 30.",
    }]
    passing = {
        "applicable": True,
        "accuracy": 0.98,
        "passed": True,
        "checked_items": ["Schedule of Assessments, page 12"],
        "summary": "matches",
    }
    failing = {
        "applicable": True,
        "accuracy": 0.70,
        "passed": False,
        "checked_items": ["Schedule of Assessments, page 12"],
        "summary": "missing follow-up",
    }
    return ScheduleAudit.model_validate({
        "approved": approved,
        "confidence": 0.96 if approved else 0.75,
        "visit_coverage": passing if approved else failing,
        "timing": passing,
        "windows": passing,
        "visit_types": passing,
        "procedure_mapping": passing,
        "overall_schedule": passing if approved else failing,
        "verified_items": ["visit columns", "timing", "footnotes"],
        "issues": issues,
        "summary": "complete" if approved else "repair required",
    })


def _decomposition_responses():
    return [
        ScheduleDocumentMap(
            has_schedule=True,
            schedule_kind="linear",
            schedule_locations=["Schedule of Assessments, page 12"],
            baseline_anchor="Day 1",
        ),
        ScheduleTimingEvidence(
            visit_timing=["Baseline is Day 1 (page 12)"],
        ),
        ScheduleVisitEvidence(
            visit_columns=["Baseline (page 12)"],
        ),
    ]


def test_agent_repairs_then_reaudits_until_verified():
    responses = _decomposition_responses() + [
        _schedule("Baseline", 0),
        _audit(approved=False, finding="Day 30 follow-up is missing."),
        _schedule("Follow-up", 30),
        _audit(approved=True),
    ]
    calls = []

    async def generate(pdf_bytes, prompt, schema, **kwargs):
        calls.append((pdf_bytes, prompt, schema, kwargs))
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-test", generate, max_refinements=2))

    assert [call[2].__name__ for call in calls] == [
        "ScheduleDocumentMap", "ScheduleTimingEvidence", "ScheduleVisitEvidence",
        "ExtractedSchedule", "ScheduleAudit", "ExtractedSchedule", "ScheduleAudit"]
    assert result.visits[0].name == "Follow-up"
    assert result.verification_status == "verified"
    assert result.verification_iterations == 1
    assert result.verification_issues == []
    assert result.verification_scores["timing"] == 0.98


def test_agent_stops_at_bound_and_surfaces_unresolved_issue():
    responses = _decomposition_responses() + [
        _schedule("Baseline", 0),
        _audit(approved=False, finding="Day 30 follow-up is missing."),
    ]

    async def generate(_pdf_bytes, _prompt, schema, **_kwargs):
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-test", generate, max_refinements=0))

    assert result.verification_status == "needs_review"
    assert result.verification_iterations == 0
    assert result.verification_issues == ["Day 30 follow-up is missing."]
    assert any("Day 30 follow-up is missing" in note for note in result.assumptions)


def test_high_procedure_accuracy_cannot_hide_bad_overall_schedule():
    passing = {
        "applicable": True,
        "accuracy": 0.95,
        "passed": True,
        "checked_items": ["Schedule table, page 12"],
        "summary": "matches",
    }
    weak_timing = {
        "applicable": True,
        "accuracy": 0.72,
        "passed": False,
        "checked_items": ["Treatment plan, page 8"],
        "summary": "cycle cadence is wrong",
    }
    audit = ScheduleAudit.model_validate({
        "approved": True,
        "confidence": 0.97,
        "visit_coverage": passing,
        "timing": weak_timing,
        "windows": passing,
        "visit_types": passing,
        "procedure_mapping": passing,
        "overall_schedule": weak_timing,
        "verified_items": ["visits", "timing", "windows", "procedures"],
        "issues": [],
        "summary": "Procedures match, but the schedule cadence does not.",
    })
    responses = _decomposition_responses() + [_schedule("Baseline", 0), audit]

    async def generate(_pdf_bytes, _prompt, schema, **_kwargs):
        response = responses.pop(0)
        assert isinstance(response, schema)
        return response

    result = asyncio.run(run_schedule_extraction_agent(
        b"%PDF-test", generate, max_refinements=0))

    assert audit.procedure_mapping.accuracy == 0.95
    assert not audit.accepted
    assert result.verification_status == "needs_review"
    assert result.verification_scores["timing"] == 0.72
    assert result.verification_scores["overall_schedule"] == 0.72
