"""Protocol -> visit-schedule extraction.

Reads a clinical-trial protocol PDF and returns its Schedule of Assessments as a
structured list of visit templates that pre-fill the sponsor's visit-schedule
editor. Extraction is provider-abstracted behind ``ProtocolExtractor`` so the
default Claude backend can later be swapped for a self-hosted vision model
without touching the API endpoint or the frontend.

The default backend uses Anthropic's Claude with native PDF input and structured
outputs (a Pydantic schema constrains the response), so a single call performs
OCR, table understanding, and JSON shaping. No separate OCR pipeline is needed.

Schema note: the visit shape is a superset of the four fields the frontend editor
reads today (name, day_offset, window_days, activities). The added fields
(visit_type, day_end, window_before/after) are OPTIONAL and ignored by the
current editor, so extraction can capture the full fidelity the ground-truth
analysis expects (visit type, day-less ET/unscheduled visits, asymmetric windows,
multi-day visits) without disturbing the frozen Add-Trial flow.
"""
from __future__ import annotations

import base64
import os
from typing import List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

# Default model — Anthropic's most capable, with high-resolution vision that
# handles dense assessment tables. Override via PROTOCOL_EXTRACTION_MODEL.
DEFAULT_MODEL = "claude-opus-4-8"

# Guardrail: refuse absurdly large uploads before they ever reach the model.
MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MB (Claude's own hard limit is 32 MB)


class ExtractedVisit(BaseModel):
    """One scheduled visit / timepoint from the Schedule of Assessments.

    Field descriptions double as extraction instructions — the model reads them
    when producing the structured output.
    """
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
        "For cyclic schedules, Cycle C Day D with cycle length L days is (C-1)*L+(D-1); "
        "for crossover, offsets run continuously across periods and washout. Use null "
        "when the protocol genuinely does NOT specify a day for this visit (e.g. an "
        "Early-Termination, Unscheduled, or Final/Follow-up visit defined only by its "
        "activities) — keep the visit, just leave the day unknown.")
    day_end: Optional[int] = Field(
        default=None,
        description="For a visit that spans MULTIPLE consecutive days as a single entry "
        "(e.g. 'Day 14-17', a period's 'Check-in / Day 1 / Check-out'), the absolute end "
        "day (same Day 1 = 0 basis). null for single-day visits.")
    window_days: int = Field(
        default=3,
        description="Visit window as a single +/- number of days (e.g. '+/- 3 days' -> 3). "
        "For an asymmetric window use the larger side here and also set window_before / "
        "window_after. Default 3 when no window is stated.")
    window_before: Optional[int] = Field(
        default=None,
        description="Days the visit may occur EARLY, when the protocol gives an asymmetric "
        "window (e.g. a '+3 days only' window -> window_before=0, window_after=3; a "
        "'-3 days only' window -> window_before=3, window_after=0). null when the window "
        "is symmetric or unstated.")
    window_after: Optional[int] = Field(
        default=None,
        description="Days the visit may occur LATE for an asymmetric window (see "
        "window_before). null when symmetric or unstated.")
    activities: List[str] = Field(
        default_factory=list,
        description="Assessments / procedures marked (X or a footnote symbol) in this "
        "visit's column, using the protocol's own procedure names, deduplicated and "
        "concise (e.g. 'Vitals', 'ECG', 'PK sampling', 'Randomization').")


class ExtractedSchedule(BaseModel):
    visits: List[ExtractedVisit] = Field(default_factory=list)


class ExtractionError(Exception):
    """Extraction attempted but failed (bad response, upstream error)."""


class ExtractionNotConfigured(ExtractionError):
    """No credentials/backend configured — surfaced to the caller as 503."""


_SYSTEM_PROMPT = """You are a clinical-trial protocol analyst. From the attached \
protocol PDF, extract the Schedule of Assessments / Schedule of Activities / Schedule of \
Events — the visit-by-visit assessment matrix — as a FLAT, chronological list where every \
scheduled visit/timepoint is ONE entry. Encode any cycle / arm / period structure by (a) \
an ABSOLUTE day_offset from baseline on every entry that has a resolvable day and (b) a \
self-describing name.

DAY OFFSETS
- day_offset is the ABSOLUTE study day with Day 1 = 0. Screening/run-in before baseline \
is NEGATIVE. Week N -> N*7, Month N -> N*30 unless an explicit day is given. Cyclic: \
Cycle C Day D at cycle length L -> (C-1)*L+(D-1). Crossover: continuous offsets across all \
periods and washout. Calendar-date schedules: day_offset = (visit date - baseline/\
randomization date) in days.
- If a visit genuinely has NO specified day (many Early-Termination, Unscheduled, End-of-\
Study, or Final/Follow-up visits are defined only by their activities), set day_offset to \
null but STILL INCLUDE the visit. Never drop a real scheduled visit just because its day \
is unspecified.
- Multi-day visits: when one visit spans consecutive days (e.g. 'Day 14-17', or a period's \
Check-in + Day 1 + Check-out treated as a single visit), set day_offset to the start day \
and day_end to the end day.

WHICH VISITS TO INCLUDE
- Include Screening, Baseline, Randomization, every treatment/follow-up visit, End-of-\
Treatment, End-of-Study, and also Early-Termination / Unscheduled / Withdrawal / Discontinuation \
visits when the protocol lists them as columns with activities (day_offset null if no day).
- Telephonic visits (phone calls, or a column marked only with a telephone icon): include \
them as visits with visit_type 'Telephonic'.
- Do NOT invent visits or activities. Return an EMPTY list if the document has no \
assessment schedule at all (e.g. a GCP inspection checklist, a consent form, a generic \
one-slide study overview, or a plain field/data-collection list) — an empty result is the \
correct answer there.

VISIT TYPE & WINDOWS
- visit_type: fill from the protocol's 'Visit Type' row/column when present (including code \
schemes like SS = study site, V = virtual, T/C = telephone); otherwise infer the phase \
(Screening / Treatment / Follow-up / EOS / Early Termination / Unscheduled).
- window_days: the +/- window in days. If the window is asymmetric (e.g. '+3 days' only, or \
'-3 days' only), still set window_days to the larger side AND set window_before / window_after.

STRUCTURAL VARIETIES (handle all)
- CYCLIC / oncology (repeating cycles, e.g. '6 cycles x 21 days'): enumerate EVERY visit in \
EVERY defined cycle as its own entry; name each 'Cycle C Day D' (or 'Cycle C Week W'); \
compute absolute day_offset via (C-1)*L+(D-1). If the cycle length or within-cycle day is \
not given, still enumerate the named cycle visits with day_offset null.
- MULTI-ARM / multi-cohort: if the arms share ONE schedule (same visit timing, only the \
drug differs), emit each visit ONCE — do NOT duplicate per arm. If the arms have genuinely \
DIFFERENT visit schedules (the protocol prints a separate table per arm), emit a separate \
entry per arm and PREFIX the name with the arm (e.g. 'Arm B - Cycle 1 Day 1').
- CROSSOVER / multi-period: enumerate visits across all periods and washout with continuous \
absolute day_offsets; name them by the protocol's period labels ('Period 2 Day 1', \
'Period 2 Check-in').
- MULTI-PHASE (Core + Extension, Blinded + Open-label, Treatment + Follow-up phases): \
enumerate the visits of every phase in order.
- SURGICAL / admission schedules: include Screening, admission/procedure day, post-op days, \
discharge, and follow-up visits as given.

DOCUMENT FORMATS (handle all)
- Standard assessment TABLE (procedures as rows, visits as columns) — the common case.
- TEXT / prose schedules (visits described in sentences or bullets, not a grid).
- DIAGRAM / timeline figures (visits on an arrow/timeline) — read the visits from the figure.
- MULTIPLE schedules in one document: if there is a visit-level schedule AND an intra-visit \
hourly sampling table (e.g. PK at Hour 0/2/6...), extract the VISIT-LEVEL schedule. But if \
the study's ONLY schedule is intra-day (all timepoints are hours), extract those hourly \
timepoints as the visits. Include genuinely separate schedules (e.g. a core phase and an \
extension phase) together.

When unsure whether arms share a schedule, prefer emitting each distinct visit once with a \
clear name over duplicating or dropping visits. Extract only what the protocol contains."""


@runtime_checkable
class ProtocolExtractor(Protocol):
    async def extract(self, pdf_bytes: bytes) -> ExtractedSchedule:
        ...


class ClaudeProtocolExtractor:
    """Default backend: Anthropic Claude, native PDF input + structured output."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._model = model or os.getenv("PROTOCOL_EXTRACTION_MODEL") or DEFAULT_MODEL

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def extract(self, pdf_bytes: bytes) -> ExtractedSchedule:
        if not self._api_key:
            raise ExtractionNotConfigured(
                "ANTHROPIC_API_KEY is not set on the server")

        import anthropic  # imported lazily so the app boots without the dep/key

        client = anthropic.AsyncAnthropic(api_key=self._api_key)
        b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
        try:
            resp = await client.messages.parse(
                model=self._model,
                max_tokens=8000,
                system=_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "document",
                         "source": {"type": "base64",
                                    "media_type": "application/pdf",
                                    "data": b64}},
                        {"type": "text",
                         "text": "Extract the visit schedule as structured data."},
                    ],
                }],
                output_format=ExtractedSchedule,
            )
        except anthropic.APIError as e:  # network / rate / upstream
            raise ExtractionError(f"model request failed: {e}") from e

        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            raise ExtractionError("model did not return a parseable schedule")
        return parsed


def get_extractor() -> ProtocolExtractor:
    """Factory — swap the returned implementation to change backends."""
    return ClaudeProtocolExtractor()
