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
        description="Study day relative to baseline, where Day 1 = 0. Screening / "
        "run-in visits before baseline are NEGATIVE. Convert Week N to N*7 and "
        "Month N to N*30 unless the protocol states an explicit day.")
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
visit-by-visit assessment matrix. Return one entry per scheduled visit or \
timepoint, in chronological order.

Rules:
- day_offset: the study day relative to baseline where Day 1 = offset 0. \
Screening/run-in visits before baseline are negative. Convert Week N to N*7 and \
Month N to N*30 unless the protocol gives an explicit day number.
- window_days: parse the stated visit window (e.g. '+/- 3 days' -> 3); default to \
3 when none is stated.
- activities: list the assessments marked (X or a footnote symbol) in that visit's \
column, using the protocol's own procedure names, deduplicated.
- Include unscheduled / early-termination visits only when they carry a defined \
day offset; otherwise omit them.
- If the document contains no assessment schedule, return an empty visits list.

Extract only what is present. Do not invent visits or assessments."""


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
