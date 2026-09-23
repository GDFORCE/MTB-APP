"""Input and output contract for protocol schedule extraction.

Whatever engine reads a protocol receives ``DocumentPage``s and must return one
``ExtractionResult`` per independent Schedule of Assessments. ExtractionService
persists those results as draft schedule versions for human review; nothing
here computes patient dates.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.schedule.models import UniversalSchedule, ValidationIssue


class DocumentPage(BaseModel):
    page_number: int
    text: str
    layout: dict[str, Any] = Field(default_factory=dict)
    # Whether this page has real embedded text, or needs the PDF's own page
    # image instead. Only pages_from_pdf sets this from a real reading.
    text_status: Literal["text", "sparse_text", "image_or_empty"] = "text"


class ExtractionResult(BaseModel):
    schedule: UniversalSchedule | None
    issues: list[ValidationIssue]
    extraction_trace: list[dict[str, Any]]
