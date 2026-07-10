"""Protocol -> visit-schedule extraction.

Reads a clinical-trial protocol PDF and returns its Schedule of Assessments as a
structured list of visit templates that pre-fill the sponsor's visit-schedule
editor. Extraction is provider-abstracted behind ``ProtocolExtractor`` so the
default Claude backend can later be swapped for a self-hosted vision model
without touching the API endpoint or the frontend.

The default backend uses Anthropic's Claude with native PDF input and structured
outputs (a Pydantic schema constrains the response), so a single call performs
OCR, table understanding, and JSON shaping. No separate OCR pipeline is needed.
"""
from __future__ import annotations

import base64
import os
from typing import List, Protocol, runtime_checkable

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
        description="Visit or timepoint name exactly as printed in the protocol's "
        "Schedule of Assessments (e.g. 'Screening', 'Baseline', 'Week 4', 'Day 14').")
    day_offset: int = Field(
        description="ABSOLUTE study day relative to baseline, where Day 1 = 0. "
        "Screening / run-in visits before baseline are NEGATIVE. Convert Week N to "
        "N*7 and Month N to N*30 unless the protocol states an explicit day. For "
        "cyclic schedules, Cycle C Day D with cycle length L days is "
        "(C-1)*L + (D-1); for crossover, offsets run continuously across periods "
        "and washout. Every entry's day_offset is the absolute day, not a "
        "within-cycle or within-period day.")
    window_days: int = Field(
        default=3,
        description="Visit window in +/- days parsed from the protocol "
        "(e.g. '±3 days' -> 3). Use 3 when no window is stated.")
    activities: List[str] = Field(
        default_factory=list,
        description="Assessments / procedures marked for this visit's column "
        "(e.g. 'Vitals', 'ECG', 'Blood draw', 'PK sampling'). Use the protocol's "
        "own procedure names, deduplicated and concise.")


class ExtractedSchedule(BaseModel):
    visits: List[ExtractedVisit] = Field(default_factory=list)


class ExtractionError(Exception):
    """Extraction attempted but failed (bad response, upstream error)."""


class ExtractionNotConfigured(ExtractionError):
    """No credentials/backend configured — surfaced to the caller as 503."""


_SYSTEM_PROMPT = """You are a clinical-trial protocol analyst. From the attached \
protocol PDF, extract the Schedule of Assessments / Schedule of Activities — the \
visit-by-visit assessment matrix — as a FLAT, chronological list where every \
scheduled visit/timepoint is one entry. The output is a flat schedule, so encode \
any cycle / arm / period structure by (a) putting an ABSOLUTE day_offset from \
baseline on every entry and (b) making the name self-describing.

Core rules:
- day_offset: absolute study day, Day 1 = 0. Screening/run-in before baseline is \
negative. Week N -> N*7, Month N -> N*30 unless an explicit day is given.
- window_days: parse the stated window (e.g. '+/- 3 days' -> 3); default 3.
- activities: the assessments marked (X or footnote symbol) in that visit's \
column, using the protocol's own procedure names, deduplicated.
- Include unscheduled / early-termination visits only when they carry a defined \
day offset; otherwise omit them.
- Return an empty list if the document has no assessment schedule. Extract only \
what is present — never invent visits or assessments.

Structural varieties (handle all of these):
- CYCLIC / oncology (treatment in repeating cycles, e.g. '6 cycles of 21 days'): \
enumerate EVERY visit in EVERY defined cycle as its own entry. Compute absolute \
day_offset as (Cycle-1)*CycleLength + (WithinCycleDay-1). Name each 'Cycle C Day \
D' (e.g. 'Cycle 2 Day 1'). Expand all cycles the protocol defines (use the stated \
number of cycles / max cycles).
- MULTI-ARM / multi-cohort: if the arms/cohorts share ONE Schedule of Assessments \
(they differ only in treatment, not in visit timing), emit each visit ONCE — do \
NOT duplicate per arm. If arms have DIFFERENT visit schedules, emit a separate \
entry per arm for each visit and PREFIX the name with the arm (e.g. 'Arm B — \
Week 4', 'Cohort 3 — Cycle 1 Day 1').
- CROSSOVER: enumerate visits across all periods and the washout with continuous \
absolute day_offsets; name them by the protocol's period labels (e.g. 'Period 2 \
Day 1').
- DOSE-ESCALATION: treat each cohort's cycle schedule like the cyclic case; \
prefix with the cohort when cohorts have distinct schedules.

When unsure whether arms share a schedule, prefer emitting each distinct visit \
once with a clear name over duplicating or dropping visits."""


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
