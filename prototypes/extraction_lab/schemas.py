"""Schemas for the existing reader's metadata, facts and repair questions."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import bootstrap  # noqa: F401
from app.extraction.facts import (
    BlockFact, DayNumberingFact, DimensionFact, ScheduleFact, ScheduleOption, TrialMetadata,
)
from app.extraction.reader import METADATA_PROMPT


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetadataReply(Envelope):
    trial: TrialMetadata
    schedule_options: list[ScheduleOption] = Field(default_factory=list)
    day_numbering: DayNumberingFact = Field(default_factory=DayNumberingFact)
    arms: list[DimensionFact] = Field(default_factory=list)
    parts: list[DimensionFact] = Field(default_factory=list)
    cohorts: list[DimensionFact] = Field(default_factory=list)
    populations: list[DimensionFact] = Field(default_factory=list)
    blocks: list[BlockFact] = Field(default_factory=list)
    visit_names: list[str] = Field(default_factory=list)
    anchor_names: list[str] = Field(default_factory=list)


class TableCell(Envelope):
    table: str | None = None
    page: int = Field(gt=0)
    row: str
    column: str
    mark: str
    footnotes: list[str] = Field(default_factory=list)


class Footnote(Envelope):
    table: str | None = None
    page: int = Field(gt=0)
    label: str
    text: str
    effect: Literal["RULE", "NONE"]
    effect_reason: str


class FactsReply(Envelope):
    facts: list[ScheduleFact]
    table_cells: list[TableCell] = Field(default_factory=list)
    footnotes: list[Footnote] = Field(default_factory=list)


class RepairFact(ScheduleFact):
    repair_id: str = Field(min_length=1)


class RepairReply(Envelope):
    facts: list[RepairFact]


def _clean(node):
    """Close known objects, retain refs/nullability, omit schema-only defaults.

    Any-valued clinical thresholds accept scalar/list values. The prototype
    never asks the model to supply deterministic provenance/verification.
    Custom Pydantic validation still runs in the real reader afterward.
    """
    if isinstance(node, list):
        return [_clean(item) for item in node]
    if not isinstance(node, dict):
        return node
    cleaned = {}
    for key, item in node.items():
        if key in {"default", "title"}:
            continue
        cleaned[key] = ({name: _clean(child) for name, child in item.items()}
                        if key in {"properties", "$defs"} else _clean(item))
    if cleaned.get("type") == "object":
        if "properties" in cleaned:
            cleaned["additionalProperties"] = False
        elif cleaned.get("additionalProperties") is True:
            cleaned["additionalProperties"] = {
                "type": ["string", "number", "boolean", "null"]}
    if not cleaned or set(cleaned) <= {"description"}:
        cleaned = {"anyOf": [
            {"type": ["string", "number", "boolean", "null"]},
            {"type": "array", "items": {"type": ["string", "number", "boolean", "null"]}},
        ], **cleaned}
    return cleaned


@lru_cache(maxsize=3)
def response_schema(stage: str) -> dict:
    model = {"metadata": MetadataReply, "facts": FactsReply, "repair": RepairReply}[stage]
    schema = model.model_json_schema(by_alias=True)
    for name, definition in schema.get("$defs", {}).items():
        for field in ({"verification"} if name == "Citation" else
                      {"provenance"} if name in {"ScheduleFact", "RepairFact"} else set()):
            definition.get("properties", {}).pop(field, None)
    return _clean(schema)


def stage_for_prompt(prompt: str) -> str:
    """Recognize the real reader's exact question families; never fall back.

    Parse-repair attempts append text to the same question, preserving its
    prefix. A future reader prompt change must be supported explicitly here.
    """
    if prompt.startswith(METADATA_PROMPT):
        return "metadata"
    if prompt.startswith("Extract every schedule fact stated on pages "):
        return "facts"
    if prompt.startswith("Some schedule statements you reported could not be read by the schedule model."):
        return "repair"
    raise ValueError("This reader question has no registered response schema; no API request was sent.")


def schema_signature() -> str:
    encoded = json.dumps({stage: response_schema(stage) for stage in ("metadata", "facts", "repair")},
                         sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:12]
