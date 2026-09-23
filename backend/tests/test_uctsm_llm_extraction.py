"""The LLM-backed extraction provider, end to end without a network call.

The provider is driven by a scripted client, so these tests are about the
CONTRACT between the model and the pipeline rather than about model quality:

  * does a model answer become a canonical schedule with its traceability intact;
  * and, more importantly, what happens when the model is wrong.

The second question is the one that matters. A model that cites a passage that
does not exist, invents a visit code, or returns prose instead of JSON must
produce a visible extraction issue - never a schedule that looks finished and
quietly cannot be traced back to the protocol.
"""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.schedule.models import UniversalSchedule
from app.domain.schedule.validator import ScheduleValidator
from app.extraction.claude_provider import ClaudeExtractionProvider
from app.extraction.graph import DocumentPage, run_extraction
from app.extraction.llm_client import (
    ExtractionModelError, parse_json_list, parse_json_object,
)

PAGES = [DocumentPage(
    page_number=8,
    text=(
        "Table 2 Schedule of Assessments\n"
        "Activity        Screening   C1D1    Week 4\n"
        "ECG             X           Xa      X\n"
        "a. ECG must be performed pre-dose.\n"
    ),
)]


class ScriptedClient:
    """Answers each prompt from a canned script, keyed by a phrase in the prompt."""

    def __init__(self, script: dict[str, object], *, default: object = None):
        self.script = script
        self.default = default if default is not None else {"claims": []}
        self.prompts: list[str] = []
        #: One entry per call, aligned with `prompts` - lets a test check what
        #: scoping (e.g. a schedule-option suffix) the system prompt carried.
        self.systems: list[str] = []

    def complete(self, *, system: str, prompt: str, max_tokens: int = 0) -> str:
        self.prompts.append(prompt)
        self.systems.append(system)
        for marker, answer in self.script.items():
            if marker in prompt:
                return answer if isinstance(answer, str) else json.dumps(answer)
        return json.dumps(self.default)


EVIDENCE_ANSWER = {
    "evidence": [
        {
            "ref": "e1", "evidence_type": "TABLE_COLUMN", "page_number": 8,
            "table_title": "Schedule of Assessments", "column_identifier": "C1D1",
            "source_text": "C1D1",
        },
        {
            "ref": "e2", "evidence_type": "FOOTNOTE", "page_number": 8,
            "source_text": "a. ECG must be performed pre-dose.",
        },
    ]
}


def script(**overrides) -> dict[str, object]:
    """A minimal but complete model transcript for one visit."""
    base: dict[str, object] = {
        "Identify the parts of this protocol": {"sections": [], "tables": []},
        "List the specific passages": EVIDENCE_ANSWER,
        "Extract the schedule's identity": {"claims": [{
            "claim_id": "m1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {"name": "Schedule of Assessments", "schedule_type": "PRIMARY"},
        }]},
        "Extract the reference dates": {"claims": [{
            "claim_id": "a1", "evidence_ids": ["e1"], "confidence": 0.95,
            "candidate": {
                "code": "FIRST_DOSE", "display_name": "First Dose",
                "anchor_type": "FIRST_DOSE",
            },
        }]},
        "Extract every visit, contact": {"claims": [{
            "claim_id": "v1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "code": "C1D1", "protocol_label": "C1D1",
                "display_name": "Cycle 1 Day 1", "event_type": "SITE_VISIT",
            },
        }]},
        "state how the patient attends": {"claims": [{
            "claim_id": "t1", "evidence_ids": ["e1"], "confidence": 0.8,
            "candidate": {
                "event_code": "C1D1", "visit_mode": "CLINIC",
                "allowed_visit_modes": [], "activation": "SCHEDULED",
            },
        }]},
        "state WHEN it occurs": {"claims": [{
            "claim_id": "ti1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {"event_code": "C1D1", "timing": {
                "type": "PROTOCOL_DAY",
                "reference": {"kind": "ANCHOR", "code": "FIRST_DOSE"},
                "day": 1,
            }},
        }]},
        "Extract the assessments performed": {"claims": [{
            "claim_id": "ac1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "event_code": "C1D1", "code": "ECG", "protocol_label": "ECG",
                "display_name": "ECG", "activity_type": "ASSESSMENT",
                "requiredness": "REQUIRED",
            },
        }]},
        "Extract footnotes and table markers": {"claims": [{
            "claim_id": "q1", "evidence_ids": ["e2"], "confidence": 0.85,
            "candidate": {
                "marker": "a", "text": "ECG must be performed pre-dose.",
                "scope": "CELL", "category": "TIMING", "target_codes": ["C1D1"],
                "resolved": True,
            },
        }]},
    }
    base.update(overrides)
    return base


def extract(client) -> tuple[UniversalSchedule | None, list, list]:
    result = run_extraction(
        ClaudeExtractionProvider(client), document_hash="a" * 64, pages=PAGES,
    )
    return result.schedule, result.issues, result.extraction_trace


def codes(issues) -> set[str]:
    return {item.issue_code for item in issues}


# --- the happy path -------------------------------------------------------------

def test_a_model_transcript_becomes_a_canonical_schedule():
    schedule, _issues, _trace = extract(ScriptedClient(script()))

    assert schedule is not None
    event = next(item for item in schedule.events if item.code == "C1D1")
    assert event.display_name == "Cycle 1 Day 1"
    assert event.timing.type == "PROTOCOL_DAY"
    assert event.timing.day == 1
    assert [item.code for item in schedule.anchors] == ["FIRST_DOSE"]
    assert [item.display_name for item in event.activities] == ["ECG"]


def test_every_extracted_rule_can_be_traced_back_to_a_quoted_passage():
    """Traceability is generated mechanically, so it cannot be forgotten."""
    schedule, _issues, _trace = extract(ScriptedClient(script()))

    assert schedule is not None
    evidence_ids = {item.id for item in schedule.evidence}
    assert evidence_ids
    assert all(item.evidence_id in evidence_ids for item in schedule.claim_evidence)

    event = schedule.events[0]
    claim_types = {
        item.claim_type for item in schedule.claim_evidence
        if item.claim_entity_id == event.id
    }
    assert {"EVENT_NAME", "TIMING"} <= claim_types


def test_the_source_text_reaches_the_schedule_verbatim():
    schedule, _issues, _trace = extract(ScriptedClient(script()))

    assert schedule is not None
    footnote = next(
        item for item in schedule.evidence if item.evidence_type == "FOOTNOTE")
    assert footnote.source_text == "a. ECG must be performed pre-dose."
    assert footnote.page_number == 8


def test_the_model_is_asked_one_focused_question_per_category():
    client = ScriptedClient(script())
    extract(client)

    # Structure, evidence, and one per semantic category.
    assert len(client.prompts) >= 18
    assert any("state WHEN it occurs" in item for item in client.prompts)
    assert any("inpatient stays" in item for item in client.prompts)


def test_claims_can_only_cite_passages_the_model_was_given():
    client = ScriptedClient(script())
    extract(client)

    timing_prompt = next(
        item for item in client.prompts if "state WHEN it occurs" in item)
    assert "Cite only these passages" in timing_prompt
    assert "e1:" in timing_prompt


def test_every_category_that_names_a_shared_expression_type_also_shows_its_shape():
    """Each category is a SEPARATE model call with no memory of any other
    category's prompt. A category whose schema only names a type
    ("<condition expression>") without ever showing the model that type's
    actual shape gives it nothing to produce - every claim it then returns
    fails model validation and is silently dropped, indistinguishable from
    "the protocol doesn't have one of these" (found via
    test_two_conditions_on_the_same_visit_are_both_kept_not_overwritten,
    which the ORIGINAL "kind" vs "source" mismatch below made fail).
    """
    from app.extraction.prompts import CATEGORY_PROMPTS

    for category, text in CATEGORY_PROMPTS.items():
        if "<condition expression>" in text:
            assert "COMPARISON" in text and '"const":"FIELD"' in text, category
        if "<timing expression>" in text:
            assert "PROTOCOL_DAY" in text and "UNRESOLVED" in text, category
        # The domain model's Operand discriminator is "source", never "kind" -
        # this exact string is what silently broke every real condition claim.
        assert '"kind":"FIELD"' not in text and '"kind":"LITERAL"' not in text, category


# --- what happens when the model is wrong ---------------------------------------

def test_a_claim_citing_a_passage_that_does_not_exist_is_dropped_and_reported():
    """A dangling citation is worse than a missing rule: it looks checkable."""
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "state WHEN it occurs": {"claims": [{
            "claim_id": "ti1", "evidence_ids": ["e-does-not-exist"],
            "candidate": {"event_code": "C1D1", "timing": {
                "type": "PROTOCOL_DAY",
                "reference": {"kind": "ANCHOR", "code": "FIRST_DOSE"}, "day": 1,
            }},
        }]},
    })))

    assert "MISSING_EVIDENCE" in codes(issues)
    assert schedule is not None
    # The visit survives, but undated - never dated from an uncheckable claim.
    assert schedule.events[0].timing.type == "UNRESOLVED"


def test_a_visit_with_no_timing_is_unresolved_and_blocks_approval():
    """Doc 1: no anchor means no date. Blocking is the correct outcome."""
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "state WHEN it occurs": {"claims": []},
    })))

    assert schedule is not None
    assert schedule.events[0].timing.type == "UNRESOLVED"
    assert "AMBIGUOUS_TIMING" in codes(issues)
    assert any("Cycle 1 Day 1" in item.message for item in issues)
    assert ScheduleValidator.blocking(ScheduleValidator().validate(schedule))


def test_a_timing_the_model_expressed_wrongly_does_not_become_a_near_miss():
    """A malformed rule stays unresolved rather than being coerced into a date."""
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "state WHEN it occurs": {"claims": [{
            "claim_id": "ti1", "evidence_ids": ["e1"],
            "candidate": {"event_code": "C1D1", "timing": {
                "type": "PROTOCOL_DAY", "day": "the day of first dose",
            }},
        }]},
    })))

    assert schedule is not None
    assert schedule.events[0].timing.type == "UNRESOLVED"
    assert "UNSUPPORTED_PROTOCOL_CONSTRUCT" in codes(issues)


def test_a_claim_about_a_visit_that_was_never_extracted_is_reported():
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "state WHEN it occurs": {"claims": [{
            "claim_id": "ti1", "evidence_ids": ["e1"],
            "candidate": {"event_code": "WEEK_99", "timing": {
                "type": "PROTOCOL_DAY",
                "reference": {"kind": "ANCHOR", "code": "FIRST_DOSE"}, "day": 1,
            }},
        }]},
    })))

    assert "UNRESOLVED_REFERENCE" in codes(issues)
    assert any("WEEK_99" in json.dumps(item.details) for item in issues)
    assert schedule is not None


def test_a_claim_naming_a_visit_by_its_label_instead_of_its_code_still_resolves():
    """AD-2: each category is an independent model call with no memory of the
    events stage's own answer - observed with Gemini via
    eval/pipeline_diff.py, where every timing claim for one real protocol
    echoed the visit's display name instead of its code, silently failing
    every single one. Matched by label instead of dropped, with the
    substitution reported rather than silent.
    """
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "state WHEN it occurs": {"claims": [{
            "claim_id": "ti1", "evidence_ids": ["e1"],
            "candidate": {"event_code": "Cycle 1 Day 1", "timing": {
                "type": "PROTOCOL_DAY",
                "reference": {"kind": "ANCHOR", "code": "FIRST_DOSE"}, "day": 1,
            }},
        }]},
    })))

    assert schedule is not None
    assert schedule.events[0].timing.type == "PROTOCOL_DAY"
    assert schedule.events[0].timing.day == 1
    warning = next(item for item in issues if item.issue_code == "UNRESOLVED_REFERENCE")
    assert warning.blocking is False
    assert warning.severity == "WARNING"
    assert "C1D1" in json.dumps(warning.details)


def test_an_anchor_named_by_its_label_inside_a_timing_rule_still_resolves():
    """The same drift one level deeper: a TRIGGERED timing's own reference
    (or any other nested AnchorReference/EventReference - a NOMINAL_WITH_
    WINDOW's inner offset, a recurrence's start_reference) can name its
    target by label too. Observed for real: after the top-level event
    lookup was fixed, one real protocol's own origin anchor still failed
    this same way, one level down, inside its own timing rule.
    """
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "state WHEN it occurs": {"claims": [{
            "claim_id": "ti1", "evidence_ids": ["e1"],
            "candidate": {"event_code": "C1D1", "timing": {
                "type": "TRIGGERED",
                "trigger": {"kind": "ANCHOR", "code": "First Dose"},
                "timing_after_trigger": {"type": "OFFSET", "offset": {"value": 1, "unit": "DAY"}},
            }},
        }]},
    })))

    assert schedule is not None
    timing = schedule.events[0].timing
    assert timing.type == "TRIGGERED"
    assert timing.trigger.code == "FIRST_DOSE"


def test_an_abbreviation_that_loses_the_within_cycle_day_requires_review():
    """Cycle 1 is not enough information to identify Cycle 1 Day 1."""
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "state WHEN it occurs": {"claims": [{
            "claim_id": "ti1", "evidence_ids": ["e1"],
            "candidate": {"event_code": "Cycle 1", "timing": {
                "type": "PROTOCOL_DAY",
                "reference": {"kind": "ANCHOR", "code": "FIRST_DOSE"}, "day": 1,
            }},
        }]},
    })))

    assert schedule is not None
    assert schedule.events[0].timing.type == "UNRESOLVED"


def test_a_number_in_the_claimed_label_never_fuzzily_matches_a_different_number():
    """The trap a naive similarity score would fall into: "Week 4" and
    "Week 40" differ by a single character and would score highly under any
    generic string metric, but they are different clinical visits. A claim
    for one must never be silently attached to the other.
    """
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "Extract every visit, contact": {"claims": [
            {
                "claim_id": "v1", "evidence_ids": ["e1"], "confidence": 0.9,
                "candidate": {"code": "WEEK_4", "protocol_label": "Week 4",
                              "display_name": "Week 4", "event_type": "SITE_VISIT"},
            },
            {
                "claim_id": "v2", "evidence_ids": ["e1"], "confidence": 0.9,
                "candidate": {"code": "WEEK_40", "protocol_label": "Week 40",
                              "display_name": "Week 40", "event_type": "SITE_VISIT"},
            },
        ]},
        "state WHEN it occurs": {"claims": [
            {
                "claim_id": "ti1", "evidence_ids": ["e1"], "confidence": 0.9,
                "candidate": {"event_code": "Week 4 Visit", "timing": {
                    "type": "PROTOCOL_DAY",
                    "reference": {"kind": "ANCHOR", "code": "FIRST_DOSE"}, "day": 28,
                }},
            },
            {
                "claim_id": "ti2", "evidence_ids": ["e1"], "confidence": 0.9,
                "candidate": {"event_code": "Week 40 Visit", "timing": {
                    "type": "PROTOCOL_DAY",
                    "reference": {"kind": "ANCHOR", "code": "FIRST_DOSE"}, "day": 280,
                }},
            },
        ]},
    })))

    assert schedule is not None
    week_4 = next(item for item in schedule.events if item.code == "WEEK_4")
    week_40 = next(item for item in schedule.events if item.code == "WEEK_40")
    assert week_4.timing.day == 28
    assert week_40.timing.day == 280
    assert not any(
        item.issue_code == "UNRESOLVED_REFERENCE" and "Week 4 Visit" in json.dumps(item.details)
        and item.details.get("event_code") == "WEEK_40"
        for item in issues
    )


def test_a_claim_equally_close_to_two_visits_refuses_to_guess_between_them():
    """AD-5: an ambiguous match is refused, not coin-flipped - the same
    discipline this codebase already applies to a genuinely ambiguous
    schedule origin anchor.
    """
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "Extract every visit, contact": {"claims": [
            {
                "claim_id": "v1", "evidence_ids": ["e1"], "confidence": 0.9,
                "candidate": {"code": "ARM_A_BASELINE", "protocol_label": "Arm A Baseline",
                              "display_name": "Baseline Randomization A", "event_type": "SITE_VISIT"},
            },
            {
                "claim_id": "v2", "evidence_ids": ["e1"], "confidence": 0.9,
                "candidate": {"code": "ARM_B_BASELINE", "protocol_label": "Arm B Baseline",
                              "display_name": "Baseline Randomization B", "event_type": "SITE_VISIT"},
            },
        ]},
        "state WHEN it occurs": {"claims": [{
            "claim_id": "ti1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {"event_code": "Baseline Randomization", "timing": {
                "type": "PROTOCOL_DAY",
                "reference": {"kind": "ANCHOR", "code": "FIRST_DOSE"}, "day": 1,
            }},
        }]},
    })))

    assert schedule is not None
    for event in schedule.events:
        if event.code in ("ARM_A_BASELINE", "ARM_B_BASELINE"):
            assert event.timing.type == "UNRESOLVED"
    assert any(
        item.issue_code == "UNRESOLVED_REFERENCE"
        and "Baseline Randomization" in json.dumps(item.details)
        and item.blocking
        for item in issues
    )


def test_prose_instead_of_json_becomes_an_issue_not_a_silent_empty_answer():
    """An empty answer reads as 'the protocol does not say this'. It is not."""
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "state WHEN it occurs": "I could not find the timing for this visit.",
    })))

    assert "UNSUPPORTED_PROTOCOL_CONSTRUCT" in codes(issues)
    assert schedule is not None
    assert schedule.events[0].timing.type == "UNRESOLVED"


def test_a_model_that_fails_entirely_produces_issues_not_a_plausible_schedule():
    """An unreachable model must not look like a protocol with nothing in it."""
    class BrokenClient:
        def complete(self, *, system, prompt, max_tokens=0):
            raise ExtractionModelError("provider is unavailable")

    schedule, issues, _trace = extract(BrokenClient())

    assert issues
    assert any("provider is unavailable" in item.message for item in issues)
    # Nothing was extracted, so nothing can be approved.
    assert schedule is None or not schedule.events


def test_an_unusable_evidence_entry_is_reported_rather_than_stored():
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "List the specific passages": {"evidence": [
            {"ref": "e1", "evidence_type": "TABLE_COLUMN", "page_number": 0,
             "source_text": "C1D1"},
        ]},
    })))

    assert "MISSING_EVIDENCE" in codes(issues)
    assert schedule is not None
    assert schedule.evidence == []


# --- AD-7: applicability, extracted rather than left as dead model surface -------

def test_an_arm_scoped_visit_becomes_an_applicability_rule():
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "arms, cohorts, parts": {"claims": [{
            "claim_id": "d1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "dimension_type": "ARM", "code": "SURGICAL",
                "protocol_label": "Surgical", "display_name": "Surgical arm",
            },
        }]},
        "conditional rules that apply only to a": {"claims": [{
            "claim_id": "ap1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "event_code": "C1D1", "activity_code": None,
                "dimension": "ARM", "operator": "IN", "values": ["SURGICAL"],
            },
        }]},
    })))

    assert schedule is not None
    event = schedule.events[0]
    assert len(event.applicability) == 1
    rule = event.applicability[0]
    assert rule.dimension == "ARM" and rule.operator == "IN" and rule.values == ["SURGICAL"]
    # It must land on the EVENT, not accidentally on the activity underneath it.
    assert event.activities[0].applicability == []


def test_an_activity_scoped_applicability_rule_gates_one_assessment_not_the_visit():
    """A cardiac substudy's extra ECG at an otherwise shared visit - the visit
    itself stays common to every arm, only the one assessment is gated.
    """
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "arms, cohorts, parts": {"claims": [{
            "claim_id": "d1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "dimension_type": "SUBSTUDY", "code": "CARDIAC",
                "protocol_label": "Cardiac substudy", "display_name": "Cardiac substudy",
            },
        }]},
        "conditional rules that apply only to a": {"claims": [{
            "claim_id": "ap1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "event_code": "C1D1", "activity_code": "ECG",
                "dimension": "SUBSTUDY", "operator": "IN", "values": ["CARDIAC"],
            },
        }]},
    })))

    assert schedule is not None
    event = schedule.events[0]
    assert event.applicability == []
    assert len(event.activities[0].applicability) == 1
    rule = event.activities[0].applicability[0]
    assert rule.dimension == "SUBSTUDY" and rule.values == ["CARDIAC"]


def test_an_applicability_claim_for_an_unrecorded_activity_is_reported():
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "conditional rules that apply only to a": {"claims": [{
            "claim_id": "ap1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "event_code": "C1D1", "activity_code": "MRI",
                "dimension": "ARM", "operator": "IN", "values": ["SURGICAL"],
            },
        }]},
    })))

    assert schedule is not None
    assert "UNRESOLVED_REFERENCE" in codes(issues)
    assert any("MRI" in item.message for item in issues)


def test_two_conditions_on_the_same_visit_are_both_kept_not_overwritten():
    """Before the fix, a second CONDITION claim on one event silently replaced
    the first instead of adding to it - the model's last answer would win
    even though both conditions genuinely gate the same visit.
    """
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "clinical conditions that gate": {"claims": [
            {
                "claim_id": "c1", "evidence_ids": ["e1"], "confidence": 0.9,
                "candidate": {"event_code": "C1D1", "condition": {
                    "type": "COMPARISON", "operator": "EQUALS",
                    "left": {"source": "FIELD", "field": "disease_status"},
                    "right": {"source": "LITERAL", "value": "PROGRESSION"},
                }},
            },
            {
                "claim_id": "c2", "evidence_ids": ["e1"], "confidence": 0.9,
                "candidate": {"event_code": "C1D1", "condition": {
                    "type": "COMPARISON", "operator": "EQUALS",
                    "left": {"source": "FIELD", "field": "consented"},
                    "right": {"source": "LITERAL", "value": True},
                }},
            },
        ]},
    })))

    assert schedule is not None
    assert len(schedule.events[0].conditions) == 2


def test_an_applicability_rule_can_target_a_conditional_rule_by_code():
    """A conditional pathway isn't tied to one visit - 'this dose-hold rule
    only applies to the dose-escalation cohort' gates the whole rule, no
    matter which events its actions touch. conditional.py:176 already reads
    ConditionalDefinition.applicability; nothing ever populated it.
    """
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "arms, cohorts, parts": {"claims": [{
            "claim_id": "d1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "dimension_type": "COHORT", "code": "DOSE_ESCALATION",
                "protocol_label": "Dose escalation", "display_name": "Dose escalation cohort",
            },
        }]},
        "if X happens, then change the schedule": {"claims": [{
            "claim_id": "ca1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "code": "DOSE_HOLD", "protocol_label": "Dose hold",
                "display_name": "Dose hold for toxicity",
                "condition": {
                    "type": "COMPARISON", "operator": "GTE",
                    "left": {"source": "FIELD", "field": "toxicity_grade"},
                    "right": {"source": "LITERAL", "value": 3},
                },
                "actions": [{"action_type": "PAUSE_BLOCK", "target_code": "C1D1"}],
            },
        }]},
        "conditional rules that apply only to a": {"claims": [{
            "claim_id": "ap1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "event_code": None, "conditional_code": "DOSE_HOLD",
                "dimension": "COHORT", "operator": "IN", "values": ["DOSE_ESCALATION"],
            },
        }]},
    })))

    assert schedule is not None
    assert len(schedule.conditional_definitions) == 1
    definition = schedule.conditional_definitions[0]
    assert len(definition.applicability) == 1
    rule = definition.applicability[0]
    assert rule.dimension == "COHORT" and rule.values == ["DOSE_ESCALATION"]


def test_an_applicability_claim_for_an_unrecorded_conditional_rule_is_reported():
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "conditional rules that apply only to a": {"claims": [{
            "claim_id": "ap1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "event_code": None, "conditional_code": "NOT_EXTRACTED",
                "dimension": "COHORT", "operator": "IN", "values": ["DOSE_ESCALATION"],
            },
        }]},
    })))

    assert schedule is not None
    assert "UNRESOLVED_REFERENCE" in codes(issues)
    assert any("NOT_EXTRACTED" in item.message for item in issues)


# --- cycle-gated activities: which OCCURRENCE, not which patient group -----------

def test_an_every_other_cycle_assessment_becomes_an_activity_condition():
    """'MRI every second cycle' - the visit itself repeats for everyone;
    only the MRI's own presence is gated by occurrence number.
    """
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "MRI every second cycle": {"claims": [{
            "claim_id": "ax1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "event_code": "C1D1", "activity_code": "ECG", "condition": {
                    "type": "MEMBERSHIP", "operator": "IN",
                    "value": {"source": "FIELD", "field": "occurrence_number"},
                    "values": [1, 3, 5],
                },
            },
        }]},
    })))

    assert schedule is not None
    activity = schedule.events[0].activities[0]
    assert len(activity.conditions) == 1
    condition = activity.conditions[0]
    assert condition.type == "MEMBERSHIP"
    assert condition.values == [1, 3, 5]
    # It must land on the ACTIVITY, not on the whole event - the visit still
    # happens on every occurrence, only this one assessment is gated.
    assert schedule.events[0].conditions == []


def test_an_activity_condition_for_an_unrecorded_activity_is_reported():
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "MRI every second cycle": {"claims": [{
            "claim_id": "ax1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "event_code": "C1D1", "activity_code": "MRI", "condition": {
                    "type": "MEMBERSHIP", "operator": "IN",
                    "value": {"source": "FIELD", "field": "occurrence_number"},
                    "values": [2, 4],
                },
            },
        }]},
    })))

    assert schedule is not None
    assert "UNRESOLVED_REFERENCE" in codes(issues)
    assert any("MRI" in item.message for item in issues)


def test_the_activity_conditions_prompt_documents_membership_not_just_comparison():
    """A model asked to express 'every second cycle' with only COMPARISON
    available has nothing correct to write - MEMBERSHIP against
    occurrence_number is the only way to state a specific occurrence list.
    """
    from app.extraction.prompts import CATEGORY_PROMPTS

    prompt = CATEGORY_PROMPTS["activity_conditions"]
    assert "MEMBERSHIP" in prompt
    assert "occurrence_number" in prompt


# --- unstated things stay unstated -----------------------------------------------

def test_an_unstated_visit_mode_is_not_defaulted_to_a_clinic_visit():
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "state how the patient attends": {"claims": [{
            "claim_id": "t1", "evidence_ids": ["e1"],
            "candidate": {"event_code": "C1D1", "visit_mode": None,
                          "allowed_visit_modes": [], "activation": "SCHEDULED"},
        }]},
    })))

    assert schedule is not None
    assert schedule.events[0].visit_mode is None
    assert schedule.events[0].allowed_visit_modes == []


def test_a_hybrid_mode_keeps_both_options_and_selects_neither():
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "state how the patient attends": {"claims": [{
            "claim_id": "t1", "evidence_ids": ["e1"],
            "candidate": {"event_code": "C1D1", "visit_mode": None,
                          "allowed_visit_modes": ["CLINIC", "TELEPHONE"],
                          "activation": "SCHEDULED"},
        }]},
    })))

    assert schedule is not None
    assert schedule.events[0].allowed_visit_modes == ["CLINIC", "TELEPHONE"]
    assert schedule.events[0].visit_mode is None


def test_an_unstated_dependency_mode_stays_absent_rather_than_defaulting():
    schedule, _issues, _trace = extract(ScriptedClient(script()))

    assert schedule is not None
    assert schedule.events[0].dependency_mode is None


def test_an_omitted_event_type_is_not_defaulted_to_a_site_visit():
    """AD-5: before this, an omitted event_type silently became SITE_VISIT - an
    active claim ("come to the hospital") the protocol never made. OTHER is
    the closed taxonomy's escape member, which correctly lands the event as
    needing review instead of looking like an ordinary confirmed visit.
    """
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "Extract every visit, contact": {"claims": [{
            "claim_id": "v1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "code": "C1D1", "protocol_label": "C1D1",
                "display_name": "Cycle 1 Day 1",
            },
        }]},
    })))

    assert schedule is not None
    assert schedule.events[0].event_type == "OTHER"


def test_an_omitted_requiredness_is_not_defaulted_to_required():
    """AD-5: before this, an omitted requiredness silently became REQUIRED,
    which would count an assessment the protocol never confirmed as required
    toward the dashboard's completion denominator.
    """
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "Extract the assessments performed": {"claims": [{
            "claim_id": "ac1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "event_code": "C1D1", "code": "ECG", "protocol_label": "ECG",
                "display_name": "ECG", "activity_type": "ASSESSMENT",
            },
        }]},
    })))

    assert schedule is not None
    activity = schedule.events[0].activities[0]
    assert activity.requiredness == "UNRESOLVED"


def test_a_requiredness_the_model_calls_unclear_is_not_dropped_as_invalid():
    """The prompt offers UNCLEAR as an honest answer; the model vocabulary
    (UNCLEAR) is a spelling of the domain vocabulary (UNRESOLVED), not a
    different fact - it must not fail model validation and silently drop the
    whole activity claim.
    """
    schedule, issues, _trace = extract(ScriptedClient(script(**{
        "Extract the assessments performed": {"claims": [{
            "claim_id": "ac1", "evidence_ids": ["e1"], "confidence": 0.9,
            "candidate": {
                "event_code": "C1D1", "code": "ECG", "protocol_label": "ECG",
                "display_name": "ECG", "activity_type": "ASSESSMENT",
                "requiredness": "UNCLEAR",
            },
        }]},
    })))

    assert schedule is not None
    assert schedule.events[0].activities, "the activity must not be silently dropped"
    assert schedule.events[0].activities[0].requiredness == "UNRESOLVED"
    assert "UNSUPPORTED_PROTOCOL_CONSTRUCT" not in codes(issues)


def test_an_open_ended_repeat_keeps_no_invented_maximum():
    schedule, _issues, _trace = extract(ScriptedClient(script(**{
        "Extract visits that repeat": {"claims": [{
            "claim_id": "r1", "evidence_ids": ["e1"],
            "candidate": {"event_code": "C1D1", "recurrence": {
                "interval": {"value": 21, "unit": "DAY"},
                "start_reference": {"kind": "ANCHOR", "code": "FIRST_DOSE"},
                "termination": {"type": "HORIZON"},
            }},
        }]},
    })))

    assert schedule is not None
    recurrence = schedule.events[0].recurrence
    assert recurrence is not None
    assert recurrence.termination.type == "HORIZON"
    assert recurrence.termination.count is None


# --- the JSON reader --------------------------------------------------------------

def test_a_fenced_reply_is_read_normally():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_a_reply_with_a_preface_keeps_the_object():
    assert parse_json_object('Here you go:\n{"a": 1}') == {"a": 1}


def test_an_empty_reply_raises_rather_than_returning_nothing():
    with pytest.raises(ExtractionModelError, match="empty"):
        parse_json_object("")


def test_a_bare_list_is_accepted_where_a_keyed_list_was_asked_for():
    assert parse_json_list('[{"a": 1}]', "claims") == [{"a": 1}]


# --- the runner's document handling ----------------------------------------------

def test_plain_text_becomes_one_page_per_form_feed():
    from app.extraction.runner import pages_from_text

    pages = pages_from_text("page one\fpage two")

    assert [item.page_number for item in pages] == [1, 2]
    assert pages[1].text == "page two"


def test_the_document_the_model_sees_carries_page_markers_it_can_cite():
    from app.extraction.runner import document_text

    rendered = document_text(pages_from_text_helper())

    assert "--- page 1 ---" in rendered
    assert "--- page 2 ---" in rendered


def pages_from_text_helper():
    from app.extraction.runner import pages_from_text

    return pages_from_text("alpha\fbeta")


def test_an_oversized_document_is_rejected_before_any_pages_can_be_omitted():
    """Silently dropping pages would make a partial extraction look complete."""
    from app.extraction import runner

    original = runner.MAX_DOCUMENT_CHARS
    runner.MAX_DOCUMENT_CHARS = 60
    try:
        with pytest.raises(ExtractionModelError, match="No pages were silently omitted"):
            runner.document_text(runner.pages_from_text("a" * 100 + "\f" + "b" * 100))
    finally:
        runner.MAX_DOCUMENT_CHARS = original



# --- multiple independent schedules -----------------------------------------------

TWO_OPTIONS_ANSWER = {"options": [
    {"id": "ssa-p2", "label": "Substudy A - Phase 2", "description": "Treatment-naive induction",
     "source_location": "Table 12, pages 40-44"},
    {"id": "ss3-m", "label": "Maintenance (SS3-M)", "description": "Long-term extension",
     "source_location": "Appendix 1"},
]}


def test_a_single_schedule_document_costs_one_pipeline_run():
    """The overwhelming majority case: no options detected, one ungated run -
    byte-for-byte the same shape run_extraction already produces on its own.
    """
    from app.extraction.runner import run_extraction_for_all_options

    client = ScriptedClient(script())
    results = run_extraction_for_all_options(client, PAGES, document_hash="a" * 64)

    assert len(results) == 1
    option, result = results[0]
    assert option is None
    assert result.schedule is not None
    assert result.schedule.events[0].code == "C1D1"


def test_two_independent_schedules_each_produce_their_own_traceable_schedule():
    from app.extraction.runner import run_extraction_for_all_options

    client = ScriptedClient(script(**{
        "Decide whether this document prints more than one": TWO_OPTIONS_ANSWER,
    }))
    results = run_extraction_for_all_options(client, PAGES, document_hash="a" * 64)

    assert [option["id"] for option, _result in results] == ["ssa-p2", "ss3-m"]
    for _option, result in results:
        assert result.schedule is not None
        assert result.schedule.events[0].code == "C1D1"

    # Every category call for one option was scoped to that option, and not
    # to the other - the model has already read the whole document, so this
    # scoping is the only thing telling it which schedule to answer about.
    scoped_to_a = [s for s in client.systems if "Substudy A - Phase 2" in s]
    scoped_to_m = [s for s in client.systems if "Maintenance (SS3-M)" in s]
    assert scoped_to_a and scoped_to_m
    assert not any("Maintenance (SS3-M)" in s for s in scoped_to_a)
    assert not any("Substudy A - Phase 2" in s for s in scoped_to_m)


def test_a_failed_schedule_option_detection_falls_back_to_one_schedule():
    """A model failure while classifying must not sink the whole extraction -
    it degrades to the safe, single-schedule default, same as a genuinely
    single-schedule protocol would produce.
    """
    from app.extraction.runner import run_extraction_for_all_options

    client = ScriptedClient(script(**{
        "Decide whether this document prints more than one": "not valid json",
    }))
    results = run_extraction_for_all_options(client, PAGES, document_hash="a" * 64)

    assert len(results) == 1
    assert results[0][0] is None
    assert results[0][1].schedule is not None


def test_extraction_reports_that_it_cannot_run_without_a_key(monkeypatch):
    from app.extraction.runner import provider_configured

    # Pin the provider: which key "not configured" checks depends on it, and
    # a developer's real .env (loaded by server.py on import, even in tests)
    # may already have UCTSM_EXTRACTION_PROVIDER set to something else.
    monkeypatch.setenv("UCTSM_EXTRACTION_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert provider_configured() is False

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert provider_configured() is True
