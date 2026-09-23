"""Prompts for the canonical extraction pipeline.

Kept apart from the provider so the rules the model is held to are readable on
their own, and so a change to wording is reviewable as a change to wording.

The governing instruction, repeated in every prompt, is that an unstated thing
must come back as unstated. A schedule that says "we could not determine the
timing of Week 4" is blocked by the validator and fixed by a human in minutes. A
schedule that quietly guesses Day 28 is approved, and a patient is seen on the
wrong day.
"""

from __future__ import annotations

import json
from pydantic import TypeAdapter
from app.domain.schedule.models import VISIT_MODE_WORDING
from app.domain.schedule.timing import TimingExpression
from app.domain.schedule.condition import ConditionExpression

SYSTEM = """\
You extract clinical trial schedule structure from protocol documents.

You are one stage of a pipeline. A deterministic layer assembles your output and
a human reviews it before any patient is scheduled. Your job is to report what
the protocol SAYS, with the evidence for it - not to produce a complete or
tidy-looking schedule.

Rules that override everything else:

1. NEVER invent a value the protocol does not state. If a visit's timing, window,
   applicability, or mode is not stated, omit it or mark it unresolved. Omission
   is a correct answer that a reviewer can fix. A plausible guess is a wrong
   answer that nobody catches.
2. NEVER resolve an ambiguity by choosing. If the protocol says two conflicting
   things, or a phrase could mean two things, report it as unresolved with both
   readings in `interpretation`.
3. Every claim MUST cite evidence_ids drawn from the evidence list you are given.
   A claim with no evidence is discarded downstream, so an uncited claim is
   wasted work.
4. Never produce patient dates. You produce RULES ("30 days after last dose"),
   never calculated calendar dates.
5. Use the protocol's own vocabulary for labels, and its own codes where it has
   them. Do not rename a visit to something tidier.

Some pages have little or no embedded text - scanned or image-only pages -
and are marked as such in the document text below. When the attached PDF
document is present, read those specific pages from its own page images
instead; do not treat a page's missing text as the protocol saying nothing
there.

Reply with JSON only. No prose before or after."""


DOCUMENT_STRUCTURE = """\
Identify the parts of this protocol that describe the schedule.

Return JSON:
{
  "sections": [{"title": str, "page": int, "kind": "SOA_TABLE"|"NARRATIVE"|"FOOTNOTES"|"OTHER"}],
  "tables": [{"title": str, "page": int, "description": str}]
}

Include every schedule-of-assessments table, every footnote block belonging to
one, and any narrative section that states timing, windows, or conditions. Do
not include sections that only describe eligibility or statistics."""


EVIDENCE = """\
List the specific passages that state schedule structure. These become the
citations every later claim must reference, so prefer many small precise
passages over a few large ones.

Return JSON:
{
  "evidence": [{
    "ref": str,                      # a short unique id you choose, e.g. "e1"
    "evidence_type": "TABLE_CELL"|"TABLE_ROW"|"TABLE_COLUMN"|"FOOTNOTE"|"SECTION",
    "page_number": int,
    "section_title": str|null,
    "table_title": str|null,
    "row_identifier": str|null,
    "column_identifier": str|null,
    "source_text": str               # the passage, quoted verbatim
  }]
}

source_text must be quoted from the document, not paraphrased. A reviewer uses
it to check your reading against the protocol."""


SCHEDULE_OPTIONS = """\
Decide whether this document prints more than one INDEPENDENT Schedule of
Assessments/Activities/Events - not a multi-arm or multi-phase protocol whose
several arms or phases still share ONE schedule table (that is still a single
schedule), but a document that prints genuinely separate schedule tables for
distinct substudies or sub-protocols, each with
its own visit list, duration, or population, where reading them together
would silently combine incompatible timelines.

Table count is NOT schedule count. Continuation pages, screening/treatment/
follow-up tables, arm-specific columns, and appendices for the SAME enrolled
participants on the SAME timeline belong to ONE schedule. Do not split on a
phase heading or a new table number alone. Split only when the protocol states
independently assigned participant schedules; quote that statement in description.

The overwhelming majority of protocols have exactly one schedule. Answer with
an empty list unless the document clearly prints more than one.

Return JSON:
{
  "options": [{
    "id": str,            # short unique slug you choose from the protocol's own naming, e.g. "ssa-p2"
    "label": str,         # human label using the protocol's own naming, e.g. "Substudy A - Phase 2 (SSA-P2)"
    "description": str,   # one short sentence: population, duration, or purpose
    "source_location": str  # where this schedule's own table lives, e.g. "Table 30, pages 156-159"
  }]
}

Leave "options" empty for a single schedule, even a multi-arm/multi-phase one."""


#: Shared schema blocks. Each category is a SEPARATE model call with no memory
#: of any other category's prompt text, so a category whose JSON schema only
#: names a type ("<condition expression>") without ever showing its shape
#: gives the model nothing to go on - every claim it then produces fails
#: model validation and is silently dropped, indistinguishable from "the
#: protocol doesn't have one of these". These are inlined wherever a prompt
#: needs them, rather than referenced by name, for exactly that reason.
_CONDITION_EXPRESSION_SCHEMA = json.dumps(
    TypeAdapter(ConditionExpression).json_schema(), ensure_ascii=False, separators=(",", ":"),
)

# Generated from the same discriminated union the assembler validates. The
# previous hand-written copy asked for three incompatible timing shapes.
_TIMING_EXPRESSION_SCHEMA = json.dumps(
    TypeAdapter(TimingExpression).json_schema(), ensure_ascii=False, separators=(",", ":"),
)


#: One instruction per pipeline node. The node names come from the graph, so a
#: category with no entry here simply produces no claims - which is honest, not
#: a failure.
CATEGORY_PROMPTS: dict[str, str] = {
    "protocol_metadata": """\
Extract the schedule's identity.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"name": str, "description": str|null, "schedule_type": str}
}]}

Return at most one claim. `name` is the schedule's own title, e.g. "Schedule of
Assessments" or "Part A Schedule".""",

    "epochs": """\
Extract study periods (Screening, Treatment, Follow-up) if the protocol names them.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"code": str, "protocol_label": str, "display_name": str,
                "description": str|null, "sequence_number": int|null}
}]}

Return nothing if the protocol does not divide the schedule into named periods.""",

    "arms_cohorts_populations": """\
Extract the patient groups the schedule distinguishes: arms, cohorts, parts,
substudies, populations, sequences, countries.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "dimension_type": "ARM"|"COHORT"|"POPULATION"|"PART"|"SUBSTUDY"|"SEQUENCE"|"COUNTRY",
    "code": str, "protocol_label": str, "display_name": str,
    "description": str|null,
    "parent_dimension_type": str|null, "parent_code": str|null
  }
}]}

If the protocol nests groups ("Part A, Cohort 1"), set parent_dimension_type and
parent_code together. Set both or neither - a half-stated hierarchy behaves as
if there were none.""",

    "anchors": """\
Extract the reference dates the schedule counts from: randomisation, first dose,
last dose, surgery, disease progression, and any other stated reference point.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "code": str, "display_name": str, "anchor_type": str,
    "source_event_code": str|null,      # if this date comes from a visit occurring
    "source_condition_code": str|null,  # if it comes from a clinical condition
    "is_schedule_origin": bool          # true only for the ONE anchor Day 1 /
                                         # Day 0 of the visit schedule is counted
                                         # from. Most protocols state this
                                         # explicitly ("Day 1 = date of first
                                         # dose"); if the protocol never states
                                         # which date the schedule counts days
                                         # from, set this false on every anchor
                                         # rather than guessing from a name like
                                         # "Baseline" - a protocol's origin may
                                         # be called Baseline, Randomisation or
                                         # First Dose, and only the protocol's
                                         # own words settle which one it is here.
  }
}]}

An anchor is a date a SITE SUPPLIES or that a recorded event produces. Do not
invent anchors the schedule does not count from.""",

    "events": """\
Extract every visit, contact, or assessment occasion in the schedule.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "code": str,                  # stable identifier, e.g. "C1D1", "WEEK_4"
    "protocol_label": str,        # exactly as the protocol column header reads
    "display_name": str,
    "event_type": str,            # SITE_VISIT, TELEPHONE_CONTACT, ASSESSMENT,
                                  # IMAGING, HOME_VISIT, INPATIENT_ADMISSION,
                                  # INPATIENT_DISCHARGE, UNSCHEDULED, OTHER
    "sequence_number": int|null
  }
}]}

One claim per DISTINCT occasion. A visit that repeats is ONE event with a
recurrence rule, not one event per occurrence - the recurrence stage handles
that. Include protocol-defined unscheduled visits.
When one printed column covers multiple distinct days (e.g. Days 1 and 15 of
Cycles 2-5), emit a separate event for each within-cycle day, each retaining the
printed source label and its own recurrence rule. Do not collapse several days
into one undatable event. Never expand an open-ended recurrence into guessed
finite rows. Apply the column's marked assessments to each stated occasion.""",

    "event_types": """\
For each visit already identified, state how the patient attends it.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "event_code": str,
    "visit_mode": str|null,             # the single stated mode, or null
    "allowed_visit_modes": [str],       # every mode permitted, if more than one
    "activation": "SCHEDULED"|"ON_DEMAND"
  }
}]}

Modes: """ + ", ".join(VISIT_MODE_WORDING) + """.

If the protocol does not state a mode, set visit_mode to null and leave
allowed_visit_modes empty. Do NOT default to CLINIC - telling a patient to
travel when the protocol did not say so is the specific harm this avoids.

If the protocol permits more than one ("clinic or telephone"), list both in
allowed_visit_modes and leave visit_mode null: the choice is per patient.

activation is ON_DEMAND only for a visit that occurs when clinically indicated
rather than on a schedule ("Unscheduled Visit - may occur at any time").""",

    "timing": """\
For each visit, state WHEN it occurs, as a rule.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str, "timing": <timing expression>}
}]}

A timing expression is one of:

""" + _TIMING_EXPRESSION_SCHEMA + """

Use UNRESOLVED whenever the protocol's timing is absent, ambiguous, or expressed
in a way none of the above captures. Say precisely what is unclear in `reason`.
An UNRESOLVED timing blocks approval until a human decides, which is the correct
outcome - a guessed day is not.

Day numbering: protocols usually have no Day 0. If the protocol says "Day 1 =
first dose", a PROTOCOL_DAY of 1 IS the anchor date.
For a stated cycle length and fixed first cycle/day, express the first occurrence
as an OFFSET from the schedule origin: (cycle - 1) * cycle_length + (day - 1).
For example Cycle 2 Day 15 of explicitly stated 28-day cycles is 42 days from
Cycle 1 Day 1. Retain the source label and cite the cycle length and day evidence.
The recurrence stage supplies the stated interval and cycle count separately.
If cycle length is absent, report that missing fact; never guess 21 or 28 days.
For a window, use NOMINAL_WITH_WINDOW with an OFFSET nominal. A clinical Day 1
is offset 0, Day 8 is offset 7, and Day -1 is offset -1; there is no Day 0.
A RANGE or WITHIN constraint is not a nominal appointment: preserve its bounds
instead of inventing a day inside them.""",

    "conditions": """\
Extract clinical conditions that gate whether a visit happens at all.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str, "condition": <condition expression>}
}]}

A condition expression is one of:
""" + _CONDITION_EXPRESSION_SCHEMA + """

Only for visits that are conditional on the patient's clinical state. A visit
that simply applies to one arm is NOT a condition - that is applicability.""",

    "applicability": """\
Extract visits, assessments, or conditional rules that apply only to a
specific arm, cohort, population, or other named patient group - never
guessed from a visit's name or position, only from an explicit protocol
statement.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str|null, "activity_code": str|null,
                "conditional_code": str|null,
                "dimension": str, "operator": "IN"|"NOT_IN", "values": [str]}
}]}

dimension: ARM, COHORT, POPULATION, or the protocol's own named group type
(SUBSTUDY, PART, SEQUENCE, COUNTRY, ...) in upper snake case - the same
dimension_type used in the arms/cohorts/populations stage. values: the
group codes this rule matches, exactly as extracted there.

Set exactly one of event_code or conditional_code - never both, never
neither:
- event_code: gates a visit, or one assessment within it (set activity_code
  too - e.g. a cardiac substudy's extra ECG at an otherwise-common visit).
- conditional_code: gates a whole "if X happens, then..." rule (from the
  conditional-rules stage) by its own code, for a rule that only applies to
  one group regardless of which visits its actions touch - e.g. "for the
  dose-escalation cohort only, hold treatment if Grade 3 toxicity occurs".

A visit, assessment, or rule that applies to every patient regardless of
group needs no applicability claim at all - do not extract a rule that is
always true.""",

    "dependencies": """\
Extract visits whose timing depends on another visit rather than on an anchor.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str, "source_event_code": str,
                "dependency_type": "TEMPORAL"|"TRIGGER"|"PRECONDITION"|"SEQUENCE"|"ANCHOR"}
}]}""",

    "dependency_modes": """\
For each dependent visit, state whether it counts from the PLANNED date of the
previous visit or from the date the previous visit ACTUALLY happened.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str,
                "dependency_mode": "NOMINAL"|"ACTUAL_PREVIOUS_EVENT"|"MANUAL"|"UNCLEAR"}
}]}

NOMINAL: the schedule grid does not move when a visit runs late.
ACTUAL_PREVIOUS_EVENT: later visits shift by however late the previous one was.
MANUAL: the protocol says a person decides.
UNCLEAR: the protocol does not say. Use this freely - it is the honest answer
and it stops the system from silently picking one behaviour over the other, which
would move real visit dates for real patients.""",

    "recurrence": """\
Extract visits that repeat.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str, "recurrence": {
    "interval": {"value": int, "unit": "DAY"|"WEEK"|"MONTH"|"YEAR"},
    "start_reference": {"kind":"ANCHOR","code": str},
    "start_offset": {"value": int, "unit": "DAY"|"WEEK"|"MONTH"|"YEAR"}|null,
    "termination": {"type":"COUNT"|"DATE"|"EVENT"|"CONDITION"|"HORIZON"|"RELATIVE",
                    "count": int|null, "date": str|null, "event_code": str|null,
                    "condition": <condition>|null,
                    "end_reference": {"kind":"ANCHOR","code":str}|null,
                    "end_offset": {"value":int,"unit":"DAY"|"WEEK"|"MONTH"|"YEAR"}|null,
                    "inclusive": bool|null},
    "include_start": bool
  }}
}]}

Use termination COUNT only when the protocol states a maximum number. If the
protocol gives no end ("continue every 21 days until progression" with no cap),
use EVENT or CONDITION if it names one, otherwise HORIZON. Never invent a
maximum: a protocol with no stated limit must keep no limit.

For an end relative to a protocol anchor ("through six months after baseline"),
use RELATIVE with end_reference, end_offset and inclusive=true; an explicitly
exclusive boundary ("before Week 12") uses inclusive=false. Those three fields
must be absent/null for other termination types. Preserve MONTH/YEAR units:
do not convert a calendar boundary to 30-day months or invent a visit count.
If the boundary's inclusion is unclear, retain an unresolved rule for review.
Changing fixed cadences use separate phase events with their own start_offset
and relative end. A variable interval such as every 3-4 months must remain
unresolved unless the protocol states a nominal target and allowed window.""",

    "repeat_blocks": """\
Extract groups of visits that repeat together as a cycle, and what happens when
such a block is paused and resumed.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"code": str, "protocol_label": str, "display_name": str,
                "event_codes": [str],
                "dependency_mode": "NOMINAL"|"ACTUAL_PREVIOUS_EVENT"|"MANUAL"|"UNCLEAR",
                "resume_mode": "NOMINAL"|"ACTUAL_RESUME"|"MANUAL"|"UNCLEAR"}
}]}

resume_mode NOMINAL: after a hold, the cycle grid is unchanged.
resume_mode ACTUAL_RESUME: the cadence restarts from the day dosing resumed.
Use UNCLEAR when the protocol does not say. Do not guess - the two produce
different visit dates.""",

    "activities": """\
Extract the assessments performed at each visit - every marked cell in the
schedule of assessments.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str, "code": str, "protocol_label": str,
                "display_name": str, "activity_type": str,
                "requiredness": "REQUIRED"|"OPTIONAL"|"CONDITIONAL"|"UNRESOLVED",
                "sequence_number": int|null}
}]}

Use UNRESOLVED when the protocol does not state whether this activity is
required - never REQUIRED as a default. An activity counted as required that
the protocol never actually called required inflates every "N of M required
assessments complete" count a site sees.

activity_type: ASSESSMENT, SAMPLE, TREATMENT, PROCEDURE, IMAGING, QUESTIONNAIRE,
ADMINISTRATIVE, OTHER.

If a cell carries a footnote marker, still report the activity here; the
qualifiers stage records what the marker means.""",

    "activity_timing": """\
Extract assessments with a stated time WITHIN the visit day - PK samples, ECGs
relative to dosing.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str, "activity_code": str, "timing": <timing expression>}
}]}

A timing expression is one of:

""" + _TIMING_EXPRESSION_SCHEMA + """

Reference another activity with {"kind":"ACTIVITY","activity_code": str} - for
example "2 hours post-dose" is an OFFSET from the DOSE activity, not from
midnight. Include the window if one is given ("+/- 10 minutes").

Only for assessments the protocol actually times. Most have no intra-day time.""",

    "activity_conditions": """\
Extract clinical conditions controlling whether an assessment is performed,
including patient-specific criteria, results of prior assessments, and
assessments performed only on SOME occurrences of a repeating visit -
"MRI every second cycle", "biopsy at even-numbered visits only", "PK sampling
on cycles 1 and 3 only".

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str, "activity_code": str,
                "condition": <condition expression>}
}]}

A condition expression is one of:
""" + _CONDITION_EXPRESSION_SCHEMA + """

occurrence_number is the visit's own repeat count (1 for the first time this
visit happens, 2 for the second, ...), always available as a FIELD. For
"every second cycle starting at cycle 2", use MEMBERSHIP - do not try to
express "every Nth" as a comparison; extract the specific occurrence numbers
the protocol states, or the ones a stated pattern implies (cycles 2, 4, 6...
up to the maximum cycle count the protocol gives elsewhere).

For a named protocol ARM/COHORT/POPULATION assignment, use an activity-level
applicability claim. For a clinical fact such as childbearing potential, a
prior result or whether testing was already performed, use a typed condition
on the activity. Preserve AND/OR/NOT and exceptions exactly; unknown facts must
remain unknown. Do not move an assessment restriction onto attendance at the
entire visit. Every conditional assessment needs its executable condition or
applicability rule, in addition to the source footnote. A footnote marked
resolved does not replace the rule. Unconditional assessments need no claim.""",

    "qualifiers": """\
Extract footnotes and table markers, and say what each one MEANS.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "marker": str|null,          # "a", "1", "*" - as printed
    "text": str,                 # the footnote, quoted
    "scope": "CELL"|"ROW"|"COLUMN"|"VISIT"|"ACTIVITY"|"VISIT_BLOCK"|"SCHEDULE_BLOCK"|"ARM"|"COHORT"|"SUBSTUDY"|"PERIOD"|"GLOBAL",
    # VISIT_BLOCK: a repeat/pause block spanning several visits. SCHEDULE_BLOCK: a rule governing the whole schedule.
    "category": "TIMING"|"WINDOW"|"CONDITION"|"APPLICABILITY"|"OPTIONALITY"|"EXCEPTION"|"REPEAT"|"STOP"|"SUBSTITUTION"|"SEQUENCE"|"DETAIL"|"UNRESOLVED",
    "target_codes": [str],       # the event or activity codes it applies to
    "resolved": bool
  }
}]}

Every marker you can see in the table must appear here, even if you cannot tell
what it means - in that case use category UNRESOLVED and resolved false. A
deterministic check compares your output against the markers found in the page
text and blocks approval for any you missed, so omitting one does not make it go
away.

target_codes must not be empty unless scope is GLOBAL.""",

    "conditional_actions": """\
Extract rules of the form "if X happens, then change the schedule".

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "code": str, "protocol_label": str, "display_name": str,
    "condition": <condition expression>,
    "actions": [{
      "action_type": "ADD_EVENT"|"REPEAT_EVENT"|"STOP_BLOCK"|"PAUSE_BLOCK"|"RESUME_BLOCK"|"EXTEND_CONFINEMENT"|"CANCEL_EVENT"|"MANUAL_REVIEW",
      "target_code": str,
      "parameters": {"interval": {"value": int, "unit": str}}   # for REPEAT_EVENT
    }]
  }
}]}

A condition expression is one of:
""" + _CONDITION_EXPRESSION_SCHEMA + """

Examples: "if ANC < 1000, repeat CBC weekly until recovery" is REPEAT_EVENT with
an interval. "On disease progression, discontinue treatment and begin survival
follow-up" is STOP_BLOCK plus ADD_EVENT.

These describe what the protocol says to do. They are never applied to a patient
without a human confirming.""",

    "conditional_resolution": """\
For each conditional rule, state what makes it STOP applying.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"code": str, "resolution_condition": <condition expression>}
}]}

A condition expression is one of:
""" + _CONDITION_EXPRESSION_SCHEMA + """

"Repeat weekly until ANC recovers to >= 1000" resolves on ANC >= 1000. Without a
resolution the repeat has no stopping point, so report it if the protocol gives
one and omit it if not.""",

    "confinement": """\
Extract inpatient stays: periods where the patient is admitted and remains in
the unit across several days.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "code": str, "protocol_label": str, "display_name": str,
    "admission_event_code": str, "discharge_event_code": str,
    "dose_event_codes": [str],
    "days": [{"day_label": str, "relative_day": int, "activity_codes": [str]}]
  }
}]}

A confinement is ONE stay, not one visit per day. relative_day uses the
protocol's own day numbering, so a stay running Day -1 to Day 3 has
relative_day -1, 1, 2, 3 with no Day 0.

Only where the protocol says the patient remains resident. A run of daily
outpatient visits is not a confinement.""",
}
