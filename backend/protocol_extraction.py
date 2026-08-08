"""Protocol -> visit-schedule extraction.

Reads a clinical-trial protocol (full protocol, synopsis, EC deck, or a bare
Schedule-of-Assessments page) and returns its visit schedule as a flat list of
visit templates that pre-fill the sponsor's visit-schedule editor. Extraction is
provider-abstracted behind ``ProtocolExtractor`` so the default Gemini backend
can later be swapped for a self-hosted vision model without touching the API
endpoint or the frontend.

DESIGN: declare structure, don't enumerate
------------------------------------------
Real protocols collapse repetition. The Schedule of Assessments prints columns
like ``Cycle 2 & Next Cycles`` or ``every 8th week thereafter``, and the numbers
needed to expand them (cycle length, cycle count, intra-cycle spacing) live in
prose on *other pages* — in the PICN protocol the table is on p42 while the
cycle length is on p15 and the expansion rule on p24.

Asking a model to emit an already-flattened list therefore asks it to do
multi-page arithmetic in its head, silently, with no way to check the result.
Instead the model emits the *structure* it read — repeating blocks with a cycle
length and a member layout, relative anchors, conditional activities — and
:func:`expand_schedule` does the arithmetic in Python, where it is deterministic
and unit-testable without an API key.

The model-facing schema is therefore richer than the frontend contract, and
``extract()`` returns an already-expanded ``ExtractedSchedule.visits`` so callers
(``POST /api/trials/{id}/extract-schedule``) keep consuming the same flat shape
they always did.

Every expansion that required an assumption (an open-ended "until progression"
tail, an unresolvable relative anchor) is recorded on ``assumptions`` /
``warnings`` so the sponsor reviews it before saving. Extraction is always a
draft — nothing is written to the trial without human confirmation.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)

# Google's newest stable multimodal model supports native PDF input and
# schema-constrained output for cross-page protocol reconstruction.
DEFAULT_MODEL = "gemini-3.6-flash"
LEGACY_CLAUDE_MODEL = "claude-opus-5"
DEFAULT_PROVIDER = "gemini"

# Guardrail: refuse absurdly large uploads before they ever reach the model.
# Gemini accepts inline PDFs up to 50 MB; keep the app's stricter limit.
MAX_PDF_BYTES = 25 * 1024 * 1024

# Output cap. The declarative schema keeps responses small (a 6-cycle protocol
# is ~8 rows + one repeating block, not 25 enumerated rows), so this is generous
# even with adaptive thinking, and stays well under the SDK's non-streaming
# timeout guard.
MAX_OUTPUT_TOKENS = 16000

# How far to expand a repetition the protocol leaves open-ended ("continue until
# progression", "every 8th week thereafter"). Bounded so one vague protocol
# cannot materialize thousands of visits per patient; always recorded as an
# assumption for the reviewer.
OPEN_ENDED_CYCLE_CAP = 12

# Sanity ceiling on a single expansion, independent of the cap above.
MAX_EXPANDED_VISITS = 400


# ─────────────────────────── model-facing schema ───────────────────────────
# Field descriptions double as extraction instructions — the model reads them
# when producing structured output, so they carry real weight.

class ConditionalActivity(BaseModel):
    """An assessment that happens only in SOME repetitions of a cycle.

    e.g. "imaging (CT/MRI) will be performed after cycles 2, 4 and 6" — the
    visit recurs every cycle but this assessment does not.
    """
    name: str = Field(description="The assessment / procedure name.")
    cycles: List[int] = Field(
        default_factory=list,
        description="The 1-based cycle numbers this assessment applies to, e.g. [2, 4, 6].")


class RepeatMember(BaseModel):
    """One visit inside a repeating cycle."""
    name_template: str = Field(
        description="Visit name with '{cycle}' where the cycle number belongs, e.g. "
        "'Cycle {cycle} Day 1', 'Cycle {cycle} Intra-cycle Visit 2'.")
    day_within_cycle: int = Field(
        description="0-based day offset from the START of the cycle. The cycle's "
        "first/dosing day is 0; a visit 7 days later is 7.")
    visit_type: Optional[str] = Field(default=None, description="See ExtractedVisit.visit_type.")
    window_days: int = Field(default=3, description="Visit window as +/- days.")
    activities: List[str] = Field(
        default_factory=list,
        description="Assessments performed at this visit in EVERY cycle.")
    conditional_activities: List[ConditionalActivity] = Field(
        default_factory=list,
        description="Assessments performed only in specific cycles.")

    @field_validator("window_days", mode="before")
    @classmethod
    def default_unknown_window(cls, value):
        """Free-form model JSON often spells an unstated window as null.

        An unknown visit window has always meant the application default (+/-3
        days). Treat explicit JSON null the same as an omitted field instead of
        rejecting an otherwise valid extracted schedule.
        """
        return 3 if value is None else value


class RepeatingBlock(BaseModel):
    """A cycle the protocol prints once and tells you to repeat.

    This is the single most important field in the schema. Use it whenever the
    Schedule of Assessments collapses repetition — a column headed 'Cycle 2 &
    Next Cycles', 'each subsequent cycle', 'Cycles 3-6', or prose like 'every
    3 weeks for 6 cycles'. Do NOT enumerate those cycles as individual visits;
    describe the block and the server will expand it exactly.
    """
    from_cycle: int = Field(description="First cycle number this block covers (1-based).")
    to_cycle: Optional[int] = Field(
        default=None,
        description="Last cycle number covered. Use null ONLY when the protocol is "
        "genuinely open-ended ('until disease progression', 'every 8th week "
        "thereafter') — the server will expand a bounded number and flag it.")
    cycle_length_days: int = Field(
        description="Length of one cycle in days. Read from the treatment plan when "
        "the schedule table does not state it (e.g. 'every 3 weekly' -> 21, "
        "'q4w' -> 28, '28-day cycle' -> 28).")
    first_cycle_start_day: int = Field(
        description="ABSOLUTE study day (Day 1 = 0) on which cycle `from_cycle` STARTS. "
        "e.g. if cycle 1 starts at baseline and cycles are 21 days, then a block "
        "beginning at cycle 2 has first_cycle_start_day = 21.")
    members: List[RepeatMember] = Field(
        default_factory=list,
        description="The visits that occur within each cycle of this block.")


class ExtractedVisit(BaseModel):
    """One scheduled visit / timepoint from the Schedule of Assessments."""
    name: str = Field(
        description="Self-describing visit name. Use the protocol's own label and make "
        "structure explicit: 'Visit 1 - Screening', 'Cycle 2 Day 1', 'Period 2 Day 1', "
        "'Arm B - Week 4', 'Week 12', 'Early Termination', 'Unscheduled', 'Follow-up'.")
    visit_type: Optional[str] = Field(
        default=None,
        description="Category of the visit when stated or clearly inferable: 'Screening', "
        "'Baseline', 'Randomization', 'Treatment', 'Follow-up', 'End of Treatment', "
        "'End of Study', 'Early Termination', 'Unscheduled', or 'Telephonic' (a phone/"
        "telephone-icon contact). Use the protocol's own visit-type codes when given "
        "(e.g. 'SS' study-site, 'V' virtual, 'T/C' telephone). null if not determinable.")
    day_offset: Optional[int] = Field(
        default=None,
        description="ABSOLUTE study day relative to baseline, where Day 1 = 0. "
        "Screening / run-in visits before baseline are NEGATIVE. Convert Week N to "
        "(N*7) and Month N to (N*30) unless the protocol states an explicit day; for a "
        "calendar-date schedule, use the day count from the baseline/randomization date. "
        "Leave null when the visit's timing is expressed RELATIVE to another visit (use "
        "relative_to instead) or when the protocol genuinely does not specify a day "
        "(Early Termination, Unscheduled) — keep the visit either way.")
    day_end: Optional[int] = Field(
        default=None,
        description="For a visit that spans MULTIPLE consecutive days as a single entry "
        "(e.g. 'Day 14-17', a period's 'Check-in / Day 1 / Check-out'), the absolute end "
        "day (same Day 1 = 0 basis). null for single-day visits.")
    hour_offset: Optional[float] = Field(
        default=None,
        description="For INTRA-DAY timepoints (PK sampling, hourly assessments), hours "
        "from dosing/Hour 0. May be negative for pre-dose. Set this IN ADDITION to "
        "day_offset. Only use when the protocol's schedule is genuinely hour-level.")
    hour_end: Optional[float] = Field(
        default=None,
        description="End of an hour RANGE, e.g. 'Hour -4 to Hour 0' -> hour_offset=-4, "
        "hour_end=0. null for a single timepoint.")
    window_days: int = Field(
        default=3,
        description="Visit window as a single +/- number of days (e.g. '+/- 3 days' -> 3). "
        "For an asymmetric window use the larger side here and also set window_before / "
        "window_after. Default 3 when no window is stated.")
    window_before: Optional[int] = Field(
        default=None,
        description="Days the visit may occur EARLY, when the protocol gives an asymmetric "
        "window (e.g. a '+3 days only' window -> window_before=0, window_after=3). null "
        "when the window is symmetric or unstated.")
    window_after: Optional[int] = Field(
        default=None,
        description="Days the visit may occur LATE for an asymmetric window. null when "
        "symmetric or unstated.")
    relative_to: Optional[str] = Field(
        default=None,
        description="When the protocol times this visit against ANOTHER visit rather than "
        "against baseline ('within 3 days after intra-cycle visit 3', '28 days after the "
        "last dose'), put that other visit's exact `name` here and the gap in "
        "relative_offset_days. The server resolves it to an absolute day.")
    relative_offset_days: Optional[int] = Field(
        default=None,
        description="Days after the `relative_to` visit (negative for before).")
    arm: Optional[str] = Field(
        default=None,
        description="Arm / cohort label when the protocol prints genuinely DIFFERENT "
        "schedules per arm. null when all arms share one schedule.")
    period: Optional[str] = Field(
        default=None,
        description="Period / phase label for crossover or multi-phase studies "
        "('Period 1', 'Washout 1', 'Extension'). null when not applicable.")
    activities: List[str] = Field(
        default_factory=list,
        description="Assessments / procedures marked (X or a footnote symbol) in this "
        "visit's column, using the protocol's own procedure names, deduplicated and "
        "concise (e.g. 'Vitals', 'ECG', 'PK sampling', 'Randomization').")

    @field_validator("window_days", mode="before")
    @classmethod
    def default_unknown_window(cls, value):
        """Accept a model's explicit null as the documented +/-3 day default."""
        return 3 if value is None else value


class ExtractedSchedule(BaseModel):
    """The model's reading of the protocol, before server-side expansion."""
    schedule_kind: Optional[str] = Field(
        default=None,
        description="One of: 'linear' (fixed visit list), 'cyclic' (repeating cycles), "
        "'crossover' (periods + washout), 'multi_arm' (different schedule per arm), "
        "'intra_day' (hour-level timepoints only), 'none' (document has no schedule).")
    visits: List[ExtractedVisit] = Field(
        default_factory=list,
        description="Explicitly-scheduled visits: screening, baseline, the visits of any "
        "cycle printed in full, end-of-treatment, follow-up, Early Termination, "
        "Unscheduled. Do NOT enumerate cycles covered by a repeating_block.")
    repeating_blocks: List[RepeatingBlock] = Field(
        default_factory=list,
        description="Cycles the protocol collapsed instead of printing. See RepeatingBlock.")
    total_cycles: Optional[int] = Field(
        default=None,
        description="Planned maximum number of treatment cycles, when stated anywhere in "
        "the document (e.g. 'maximum 6 cycles').")
    assumptions: List[str] = Field(
        default_factory=list,
        description="Any inference you had to make that a reviewer should verify — a cycle "
        "length read from a different section, an open-ended tail you bounded, an "
        "ambiguous arm structure. One short sentence each. Be honest and specific.")
    source_notes: Optional[str] = Field(
        default=None,
        description="Where in the document the schedule came from (e.g. 'Appendix I, p42; "
        "cycle length from section 2.5, p15'). Helps the reviewer check your work.")


class ExtractedTrialDetails(BaseModel):
    """Creation-form fields read from a protocol before a trial exists."""
    ctri_number: str = ""
    title: str = ""
    phase: str = ""
    indications: List[str] = Field(default_factory=list)
    drug: str = ""
    duration: str = ""
    target_enrollment: Optional[int] = None
    total_visits: Optional[int] = None
    status: str = "active"


class ExtractionError(Exception):
    """Extraction attempted but failed (bad response, upstream error)."""


class ExtractionNotConfigured(ExtractionError):
    """No credentials/backend configured — surfaced to the caller as 503."""


class ExtractionUnavailable(ExtractionError):
    """Provider reachable but refusing work (billing, quota, rate limit).

    Separated from ExtractionError so the API can tell the sponsor *why* the
    button did nothing instead of a generic 'could not extract'.
    """


# ──────────────────────────── expansion (pure) ────────────────────────────
# Deterministic, no network, no API key — this is where all schedule arithmetic
# lives so it can be unit-tested against real protocols offline.

def _fill_template(template: str, cycle: int) -> str:
    """Substitute the cycle number into a member name template.

    Uses explicit replacement rather than str.format so a stray brace in a
    model-authored template can never raise.
    """
    out = template
    for token in ("{cycle}", "{c}", "{n}", "{CYCLE}"):
        out = out.replace(token, str(cycle))
    if str(cycle) not in out:
        # Template forgot the placeholder — disambiguate so cycles don't collide.
        out = f"{out} (Cycle {cycle})"
    return out


def _expand_blocks(schedule: ExtractedSchedule,
                   assumptions: List[str],
                   warnings: List[str]) -> List[ExtractedVisit]:
    """Turn each RepeatingBlock into concrete per-cycle visits."""
    out: List[ExtractedVisit] = []
    for block in schedule.repeating_blocks:
        if block.cycle_length_days <= 0:
            warnings.append(
                f"Ignored a repeating block starting at cycle {block.from_cycle}: "
                f"cycle length {block.cycle_length_days} is not a positive number of days.")
            continue
        if not block.members:
            warnings.append(
                f"Ignored a repeating block starting at cycle {block.from_cycle}: "
                "it listed no visits.")
            continue

        to_cycle = block.to_cycle
        if to_cycle is None:
            to_cycle = block.from_cycle + OPEN_ENDED_CYCLE_CAP - 1
            if schedule.total_cycles and schedule.total_cycles >= block.from_cycle:
                to_cycle = schedule.total_cycles
                assumptions.append(
                    f"Cycles {block.from_cycle}-{to_cycle} were expanded using the "
                    f"protocol's stated maximum of {schedule.total_cycles} cycles.")
            else:
                assumptions.append(
                    f"The protocol leaves the schedule open-ended from cycle "
                    f"{block.from_cycle}; expanded {OPEN_ENDED_CYCLE_CAP} cycles "
                    f"(to cycle {to_cycle}). Confirm the real number before saving.")
        if to_cycle < block.from_cycle:
            warnings.append(
                f"Ignored a repeating block: last cycle ({to_cycle}) is before the "
                f"first ({block.from_cycle}).")
            continue

        for cycle in range(block.from_cycle, to_cycle + 1):
            cycle_start = (block.first_cycle_start_day
                           + (cycle - block.from_cycle) * block.cycle_length_days)
            for member in block.members:
                acts = list(member.activities)
                for cond in member.conditional_activities:
                    if cycle in (cond.cycles or []):
                        acts.append(cond.name)
                out.append(ExtractedVisit(
                    name=_fill_template(member.name_template, cycle),
                    visit_type=member.visit_type,
                    day_offset=cycle_start + member.day_within_cycle,
                    window_days=member.window_days,
                    activities=acts,
                ))
    return out


def _resolve_relative(visits: List[ExtractedVisit], warnings: List[str]) -> None:
    """Resolve visits timed against another visit into absolute day offsets.

    Runs to a fixed point so a chain (A -> B -> C) resolves, and stops rather
    than looping when a cycle is present.
    """
    by_name = {v.name.strip().lower(): v for v in visits if v.name}
    pending = [v for v in visits
               if v.day_offset is None and v.relative_to and v.relative_offset_days is not None]
    for _ in range(len(pending) + 1):
        progressed = False
        for v in list(pending):
            target = by_name.get((v.relative_to or "").strip().lower())
            if target is not None and target.day_offset is not None:
                v.day_offset = target.day_offset + int(v.relative_offset_days or 0)
                pending.remove(v)
                progressed = True
        if not pending or not progressed:
            break
    for v in pending:
        warnings.append(
            f"'{v.name}' is scheduled relative to '{v.relative_to}', which has no "
            "resolvable date — set its day manually.")


def expand_schedule(schedule: ExtractedSchedule) -> ExtractedSchedule:
    """Expand declared structure into the flat visit list the app consumes.

    Pure and deterministic: same input always yields the same visits. Returns a
    NEW ExtractedSchedule; the input is not mutated.
    """
    assumptions: List[str] = list(schedule.assumptions)
    warnings: List[str] = []

    visits: List[ExtractedVisit] = [v.model_copy(deep=True) for v in schedule.visits]
    visits.extend(_expand_blocks(schedule, assumptions, warnings))
    _resolve_relative(visits, warnings)

    # Drop exact duplicates — a model that both enumerated cycle 2 AND described
    # it in a repeating block should not double-book the patient.
    seen: set = set()
    deduped: List[ExtractedVisit] = []
    for v in visits:
        key = (v.name.strip().lower(), v.day_offset, v.hour_offset)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(v)

    if len(deduped) > MAX_EXPANDED_VISITS:
        warnings.append(
            f"Schedule expanded to {len(deduped)} visits; kept the first "
            f"{MAX_EXPANDED_VISITS}. Check the cycle count before saving.")
        deduped = deduped[:MAX_EXPANDED_VISITS]

    # Chronological, with undated visits (ET / Unscheduled) last but preserved.
    deduped.sort(key=lambda v: (
        v.day_offset is None,
        v.day_offset if v.day_offset is not None else 0,
        v.hour_offset if v.hour_offset is not None else 0,
    ))

    return schedule.model_copy(update={
        "visits": deduped,
        "repeating_blocks": [],     # consumed
        "assumptions": assumptions + warnings,
    })


# ──────────────────────────────── prompt ────────────────────────────────

_SYSTEM_PROMPT = """You are a clinical-trial protocol analyst. Read the attached \
document and extract its visit schedule — the Schedule of Assessments / Schedule of \
Activities / Schedule of Events / study flow chart.

You are producing a DRAFT that a sponsor will review before it is saved. Accuracy and \
honesty matter more than completeness: record what the document says, use `assumptions` \
for anything you inferred, and never invent a visit or an assessment.

## THE MOST IMPORTANT RULE: declare repetition, do not enumerate it

Real protocols collapse repeating cycles. A schedule table may print a column headed \
"Cycle 2 & Next Cycles", "each subsequent cycle", "Cycles 3-6", or prose may say "every \
3 weeks for a maximum of 6 cycles". When that happens, emit ONE `repeating_blocks` entry \
describing the cycle — do NOT write out each cycle as its own visit. The server expands \
blocks arithmetically, which is more reliable than you doing it in your head.

Enumerate a cycle in `visits` ONLY when the protocol prints that cycle in full and it \
differs from the repeating pattern (commonly Cycle 1, which often has extra baseline \
assessments).

## The numbers you need are usually NOT in the schedule table

This is the single most common reason extractions are wrong. A Schedule of Assessments \
appendix routinely omits the cycle length, the number of cycles, and the intra-cycle \
spacing — those live in the treatment-plan / dosing / study-design sections, often dozens \
of pages away. SEARCH THE WHOLE DOCUMENT for them before concluding a cycle is \
unspecified. Typical phrasings: "infused every 3 weekly for maximum 6 cycles" (-> \
cycle_length_days 21, total_cycles 6), "q4w", "28-day cycles", "subject will be scheduled \
for the next visit 7 days after this visit" (-> intra-cycle spacing 7). Cite where you \
found them in `source_notes`.

## Day offsets

- `day_offset` is the ABSOLUTE study day with Day 1 = 0. Screening/run-in before baseline \
is NEGATIVE. Week N -> N*7, Month N -> N*30 unless an explicit day is given.
- Inside a `repeating_blocks` member, use `day_within_cycle` (0-based from the cycle's \
first day) and set the block's `first_cycle_start_day` to the absolute day that cycle \
starts. Do not pre-compute per-cycle absolute days.
- If a visit is timed against ANOTHER visit ("within 3 days after intra-cycle visit 3", \
"28 days after the last dose"), leave `day_offset` null and set `relative_to` (the other \
visit's exact name) plus `relative_offset_days`. The server resolves it.
- If a visit genuinely has NO timing (many Early-Termination, Unscheduled, Withdrawal \
visits), leave `day_offset` null and STILL INCLUDE the visit. Never drop a real visit just \
because its day is unspecified.
- Multi-day visits (e.g. "Day 14-17", a period's Check-in + Day 1 + Check-out treated as \
one visit): set `day_offset` to the start and `day_end` to the end.

## Structural varieties (handle all)

- CYCLIC / oncology: use `repeating_blocks` (see above). If the cadence CHANGES partway \
("every 6th week for Cycles 1-6 and every 8th week thereafter"), emit TWO blocks with \
different `cycle_length_days` and ranges.
- CONDITIONAL assessments: when a recurring visit performs an assessment only in some \
cycles ("imaging after cycles 2, 4 and 6"), put it in that member's \
`conditional_activities` with the cycle numbers — not in `activities`.
- CROSSOVER / multi-period: enumerate visits across all periods and washouts with \
continuous absolute day offsets; set `period` on each ("Period 1", "Washout 1", \
"Period 2") and name them by the protocol's own labels.
- MULTI-ARM: if the arms share ONE schedule (same timing, only the drug differs), emit \
each visit ONCE and leave `arm` null. Only when the protocol prints a genuinely different \
schedule per arm, set `arm` and repeat the visits per arm.
- MULTI-PHASE (Core + Extension, Blinded + Open-label): enumerate every phase in order, \
using `period` to label them.
- INTRA-DAY / PK: if the study's schedule is hour-level ("Hour -4 to Hour 0", "Hour 26"), \
set `hour_offset` (and `hour_end` for a range) alongside `day_offset`, and set \
`schedule_kind` to 'intra_day'. If the document has BOTH a visit-level schedule and an \
intra-visit hourly sampling table, extract the VISIT-level schedule.
- SURGICAL / admission: screening, admission/procedure day, post-op days, discharge, \
follow-up.

## Visit type and windows

- `visit_type`: use the protocol's own 'Visit Type' row when present (including codes like \
SS = study site, V = virtual, T/C = telephone); otherwise infer the phase.
- Telephonic visits (phone contacts, or a column marked only with a telephone icon) are \
real visits — include them with visit_type 'Telephonic'.
- `window_days`: the +/- window. If asymmetric ("+3 days only"), set `window_days` to the \
larger side AND set `window_before` / `window_after`.

## Documents that have no schedule

Return an EMPTY `visits` list with `schedule_kind` 'none' when the document genuinely has \
no visit schedule — a GCP inspection checklist, a consent form, an investigator CV, a \
one-slide study overview, or a plain data-collection list. An empty result is the correct \
and expected answer there. Never manufacture a plausible-looking schedule to fill the gap.

## Scanned documents

Many of these files are scanned images with no text layer. Read them from the page images. \
If a table is too degraded to read reliably, extract what you can and say so in \
`assumptions` rather than guessing at values."""


_DETAILS_PROMPT = """Read the attached clinical-trial protocol and return only the
trial-level metadata requested by the schema. Preserve the official study title,
CTRI registration number, phase, disease/indications, investigational drug,
planned duration, planned sample size/target enrollment, and the number of
distinct protocol visits. Use empty strings/nulls when the document does not
state a value; never invent one. Normalize status to active, completed, or
terminated, defaulting to active when no status is stated."""


@runtime_checkable
class ProtocolExtractor(Protocol):
    async def extract(self, pdf_bytes: bytes) -> ExtractedSchedule:
        ...


def _classify_api_error(exc: Exception) -> ExtractionError:
    """Map provider failures to the right error class.

    A billing/quota failure is not a parsing failure — the sponsor needs to be
    told the service is unavailable, not that their protocol was unreadable.
    """
    msg = str(exc)
    low = msg.lower()
    if any(s in low for s in ("credit balance", "billing", "quota", "insufficient funds",
                              "payment", "plans & billing")):
        return ExtractionUnavailable(
            "the AI provider account has no available credit")
    if "rate limit" in low or "429" in low:
        return ExtractionUnavailable("the AI provider is rate limiting requests")
    if any(s in low for s in ("overloaded", "529", "503", "502")):
        return ExtractionUnavailable("the AI provider is temporarily overloaded")
    return ExtractionError(f"model request failed: {msg}")


class ClaudeProtocolExtractor:
    """Default backend: Anthropic Claude, native PDF input + structured output."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._model = (
            model
            or os.getenv("CLAUDE_PROTOCOL_EXTRACTION_MODEL")
            or LEGACY_CLAUDE_MODEL
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _client(self):
        if not self._api_key:
            raise ExtractionNotConfigured("ANTHROPIC_API_KEY is not set on the server")
        import anthropic  # imported lazily so the app boots without the dep/key
        return anthropic, anthropic.AsyncAnthropic(api_key=self._api_key)

    @staticmethod
    def _document_block(pdf_bytes: bytes) -> dict:
        """A cached document block.

        Sponsors re-run extraction while iterating on a schedule; caching the
        protocol makes every retry after the first ~10x cheaper on input.
        """
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
            },
            "cache_control": {"type": "ephemeral"},
        }

    @staticmethod
    def _json_text(response) -> str:
        """Extract the JSON text from a normal Messages API response."""
        text = ''.join(
            getattr(block, 'text', '') for block in (getattr(response, 'content', None) or [])
            if getattr(block, 'type', '') == 'text'
        ).strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1] if '\n' in text else text
            if text.rstrip().endswith('```'):
                text = text.rstrip()[:-3].rstrip()
        # Models occasionally add a one-line preface despite the instruction.
        # Keep the outer JSON object rather than rejecting usable extraction.
        first, last = text.find('{'), text.rfind('}')
        if first >= 0 and last > first:
            text = text[first:last + 1]
        return text

    async def _extract_without_grammar(self, client, pdf_bytes: bytes, repair: bool = False) -> ExtractedSchedule:
        """Fallback for provider grammar-compilation timeouts.

        The structured parser is preferred, but complex Pydantic schemas can
        exceed Anthropic's grammar compiler limit for a full PDF. Asking for
        JSON and validating it locally preserves the same strict application
        schema without discarding a usable protocol extraction.
        """
        response = await client.messages.create(
            model=self._model,
            max_tokens=MAX_OUTPUT_TOKENS,
            thinking={'type': 'adaptive'},
            system=_SYSTEM_PROMPT + (
                '\n\nReturn ONLY a valid JSON object with these top-level keys: '
                'schedule_kind, visits, repeating_blocks, assumptions, source_notes. '
                'Each visit must include name, visit_type, day_offset, day_end, '
                'hour_offset, hour_end, window_days, window_before, window_after, '
                'relative_to, relative_offset_days, arm, period, activities. '
                'Use null only for unknown nullable fields and [] for unknown lists. '
                'window_days must always be a non-negative integer; use 3 when the '
                'protocol does not state a visit window.'
            ),
            messages=[{
                'role': 'user',
                'content': [
                    self._document_block(pdf_bytes),
                    {'type': 'text', 'text': (
                        'Extract this protocol schedule as the requested JSON. '
                        'Return JSON only—no explanation, markdown, or code fences.'
                        if not repair else
                        'Return a corrected, strictly valid JSON schedule now. JSON only; no markdown or explanation.'
                    )},
                ],
            }],
        )
        raw = self._json_text(response)
        try:
            payload = json.loads(raw)
            # Normalise the few harmless shape variations a free-form JSON
            # response can make before Pydantic applies the strict schema.
            if isinstance(payload, dict):
                if isinstance(payload.get('source_notes'), list):
                    payload['source_notes'] = ' '.join(
                        str(note).strip() for note in payload['source_notes'] if str(note).strip())
                if isinstance(payload.get('assumptions'), str):
                    payload['assumptions'] = [payload['assumptions']]
            return ExtractedSchedule.model_validate(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            if not repair:
                log.warning('Anthropic schedule JSON failed validation; requesting one corrected response: %s', exc)
                return await self._extract_without_grammar(client, pdf_bytes, repair=True)
            raise ExtractionError('the AI response was not valid schedule JSON') from exc

    async def extract(self, pdf_bytes: bytes) -> ExtractedSchedule:
        """Read a protocol and return its EXPANDED visit schedule."""
        anthropic, client = self._client()
        # Structured-output grammar compilation can time out for the nested
        # clinical schedule schema. Use Anthropic's normal Messages API and
        # enforce the same schema locally with Pydantic.
        try:
            parsed = await self._extract_without_grammar(client, pdf_bytes)
        except anthropic.APIError as e:
            raise _classify_api_error(e) from e
        expanded = expand_schedule(parsed)
        log.info(
            'protocol extraction (JSON): kind=%s raw_visits=%d -> expanded=%d assumptions=%d',
            parsed.schedule_kind, len(parsed.visits), len(expanded.visits),
            len(expanded.assumptions),
        )
        return expanded

        try:
            resp = await client.messages.parse(
                model=self._model,
                max_tokens=MAX_OUTPUT_TOKENS,
                thinking={"type": "adaptive"},
                system=_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        self._document_block(pdf_bytes),
                        {"type": "text",
                         "text": "Extract this protocol's visit schedule. Search the whole "
                                 "document for cycle length and cycle count before "
                                 "concluding a repeating block is unspecified."},
                    ],
                }],
                output_format=ExtractedSchedule,
            )
        except anthropic.APIError as e:
            if 'grammar compilation timed out' in str(e).lower():
                log.warning('Anthropic structured-output grammar timed out; using JSON fallback')
                return expand_schedule(await self._extract_without_grammar(client, pdf_bytes))
            raise _classify_api_error(e) from e

        if resp.stop_reason == "refusal":
            raise ExtractionError("the model declined to process this document")

        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            if resp.stop_reason == "max_tokens":
                raise ExtractionError(
                    "the schedule was too large to return in one response — "
                    "try uploading only the Schedule of Assessments pages")
            raise ExtractionError("model did not return a parseable schedule")

        expanded = expand_schedule(parsed)
        log.info(
            "protocol extraction: kind=%s raw_visits=%d blocks=%d -> expanded=%d assumptions=%d",
            parsed.schedule_kind, len(parsed.visits), len(parsed.repeating_blocks),
            len(expanded.visits), len(expanded.assumptions),
        )
        return expanded

    async def extract_details(self, pdf_bytes: bytes) -> ExtractedTrialDetails:
        """Extract the trial-level metadata needed by the pre-creation form."""
        anthropic, client = self._client()
        try:
            resp = await client.messages.parse(
                model=self._model,
                max_tokens=4000,
                system=_DETAILS_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        self._document_block(pdf_bytes),
                        {"type": "text", "text": "Extract the protocol's trial details."},
                    ],
                }],
                output_format=ExtractedTrialDetails,
            )
        except anthropic.APIError as e:
            raise _classify_api_error(e) from e
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            raise ExtractionError("model did not return parseable trial details")
        return parsed


class GeminiProtocolExtractor:
    """Google Gemini backend with native PDF input and structured output."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        self._model = (
            model
            or os.getenv("GEMINI_PROTOCOL_EXTRACTION_MODEL")
            or os.getenv("PROTOCOL_EXTRACTION_MODEL")
            or DEFAULT_MODEL
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _client(self):
        if not self._api_key:
            raise ExtractionNotConfigured("GEMINI_API_KEY is not set on the server")
        from google import genai  # lazy import keeps non-AI routes independent
        from google.genai import errors, types
        return errors, types, genai.Client(api_key=self._api_key)

    async def _generate(
        self,
        pdf_bytes: bytes,
        prompt: str,
        schema,
        *,
        system_instruction: str,
        max_tokens: int,
    ):
        errors, types, client = self._client()
        async_client = client.aio
        try:
            response = await async_client.models.generate_content(
                model=self._model,
                contents=[
                    types.Part.from_bytes(
                        data=pdf_bytes,
                        mime_type="application/pdf",
                    ),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    max_output_tokens=max_tokens,
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
        except errors.APIError as exc:
            raise _classify_api_error(exc) from exc
        finally:
            await async_client.aclose()
            client.close()

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, schema):
            return parsed
        if parsed is not None:
            return schema.model_validate(parsed)
        raw = (getattr(response, "text", None) or "").strip()
        if not raw:
            raise ExtractionError("model did not return a parseable structured response")
        try:
            return schema.model_validate_json(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ExtractionError("the AI response was not valid structured JSON") from exc

    async def extract(self, pdf_bytes: bytes) -> ExtractedSchedule:
        parsed = await self._generate(
            pdf_bytes,
            "Extract this protocol's visit schedule. Search the whole document "
            "for cycle length and cycle count before concluding a repeating "
            "block is unspecified.",
            ExtractedSchedule,
            system_instruction=_SYSTEM_PROMPT,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        expanded = expand_schedule(parsed)
        log.info(
            "Gemini protocol extraction: kind=%s raw_visits=%d blocks=%d "
            "expanded=%d assumptions=%d",
            parsed.schedule_kind,
            len(parsed.visits),
            len(parsed.repeating_blocks),
            len(expanded.visits),
            len(expanded.assumptions),
        )
        return expanded

    async def extract_details(self, pdf_bytes: bytes) -> ExtractedTrialDetails:
        return await self._generate(
            pdf_bytes,
            "Extract the protocol's trial-level metadata.",
            ExtractedTrialDetails,
            system_instruction=_DETAILS_PROMPT,
            max_tokens=4000,
        )


def get_extractor() -> ProtocolExtractor:
    """Factory — swap the returned implementation to change backends."""
    provider = os.getenv(
        "PROTOCOL_EXTRACTION_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    if provider in ("gemini", "google"):
        return GeminiProtocolExtractor()
    if provider in ("claude", "anthropic"):
        return ClaudeProtocolExtractor()
    raise ExtractionNotConfigured(
        "PROTOCOL_EXTRACTION_PROVIDER must be 'gemini' or 'claude'")


def get_details_extractor():
    """Factory kept separate so focused tests/providers can replace it alone."""
    return get_extractor()
