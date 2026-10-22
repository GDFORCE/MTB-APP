"""Content/version-bound validation artifacts; no store or request dependencies."""
from __future__ import annotations

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from .amendment import schedule_content_hash
from .models import UniversalSchedule, ValidationIssue
from .timing import StrictModel
from .validator import ScheduleValidator, topological_event_codes


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def artifact_key(schedule: UniversalSchedule, evaluator_version: str) -> str:
    # Extraction findings are deliberately outside the immutable content hash,
    # but they affect validation and MUST participate in a validation cache key.
    return digest({"content_hash": schedule_content_hash(schedule), "schema": schedule.schema_version,
        "validator": ScheduleValidator.version, "evaluator": evaluator_version,
        "findings": sorted((row.model_dump(mode="json", exclude={"id"})
            for row in schedule.validation_issues if row.details.get("origin") == "EXTRACTION"),
            key=lambda row: json.dumps(row, sort_keys=True))})


class CompiledSchedule(StrictModel):
    format_version: str = "uctsm-compiled.v1"
    cache_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    schedule_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    schema_version: str
    validator_version: str
    evaluator_version: str
    event_order: list[str]
    issues: list[ValidationIssue]
    artifact_hash: str

    def checksum(self):
        return digest(self.model_dump(mode="json", exclude={"artifact_hash"}))

    def verify_for(self, schedule: UniversalSchedule, evaluator_version: str):
        if (self.format_version != "uctsm-compiled.v1" or self.cache_key != artifact_key(schedule, evaluator_version)
                or self.schedule_hash != schedule_content_hash(schedule) or self.schema_version != schedule.schema_version
                or self.validator_version != ScheduleValidator.version or self.evaluator_version != evaluator_version
                or self.artifact_hash != self.checksum()
                or len(self.event_order) != len(schedule.events)
                or set(self.event_order) != {row.code for row in schedule.events}):
            raise ValueError("compiled schedule artifact is stale or damaged")
        if ScheduleValidator.blocking(self.issues):
            raise ValueError("approved schedule failed integrity validation")


def compile_schedule(schedule: UniversalSchedule, *, evaluator_version: str, issues=None) -> CompiledSchedule:
    findings = (ScheduleValidator().validate(schedule, evaluating_approved=True) if issues is None else issues)
    if ScheduleValidator.blocking(findings):
        raise ValueError("approved schedule failed integrity validation")
    key = artifact_key(schedule, evaluator_version)
    stable = [row.model_copy(update={"id": uuid5(NAMESPACE_URL,
        f"{key}/{index}/{row.issue_code}/{row.details.get('field_path', '')}")}, deep=True)
        for index, row in enumerate(findings)]
    compiled = CompiledSchedule(cache_key=key, schedule_hash=schedule_content_hash(schedule),
        schema_version=schedule.schema_version, validator_version=ScheduleValidator.version,
        evaluator_version=evaluator_version, event_order=topological_event_codes(schedule),
        issues=stable, artifact_hash="")
    compiled.artifact_hash = compiled.checksum()
    return compiled
