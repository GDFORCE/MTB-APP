"""Shared building blocks of the amendment workflow (P6): verified version loading, the
mapping gate, the patient's material-context fingerprint and the simulation bindings.

Everything here is read-only over the repository ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from uuid import UUID

from app.domain.schedule.amendment import (
    EventMapping, HashStatus, MappingReviewStatus, MappingType, canonical_json,
    header_integrity_hash, mapping_problems, mapping_revision, propose_event_mappings,
    sha256_text, verify_content_hash,
)
from app.domain.schedule.models import UniversalSchedule

from .amendments import AmendmentMappingSetRecord, MappingDecisionRecord
from .authoring import ScheduleVersionHeader
from .errors import AmendmentBlockedError, AmendmentIntegrityError, RecordNotFoundError
from .ports import UnitOfWork


@dataclass(frozen=True)
class VerifiedVersion:
    header: ScheduleVersionHeader
    schedule: UniversalSchedule
    #: VERIFIED, or UNVERIFIED_LEGACY for an approved version stored before hashes existed
    hash_status: HashStatus

    @property
    def id(self) -> UUID:
        return self.header.id

    def identity(self) -> dict[str, Any]:
        header = self.header
        return {
            "schedule_version_id": str(header.id), "version_number": header.version_number,
            "status": header.status, "protocol_version_id": str(header.protocol_version_id),
            "approved_at": header.approved_at.isoformat() if header.approved_at else None,
            "effective_from": header.effective_from.isoformat() if header.effective_from else None,
            "based_on_schedule_version_id": (
                str(header.based_on_schedule_version_id)
                if header.based_on_schedule_version_id else None),
            "content_hash": header.content_hash, "content_hash_version": header.content_hash_version,
            "verification": self.hash_status.value}


def load_version(tx: UnitOfWork, organization_id: UUID, schedule_version_id: UUID) -> VerifiedVersion:
    """An approved version of THIS organization whose content still matches the hash it
    was approved under. A mismatch raises: nothing is compared, simulated or adopted from
    a version that changed. A version approved before hashes existed is returned as
    ``UNVERIFIED_LEGACY`` - never as verified, never rewritten."""
    header = tx.schedules.get_header(schedule_version_id)
    if header.organization_id != organization_id:
        raise RecordNotFoundError("schedule version not found")
    schedule = tx.schedules.get(schedule_version_id)
    status = verify_content_hash(schedule, header.content_hash, header.content_hash_version)
    if status in {HashStatus.MISMATCH, HashStatus.UNSUPPORTED_VERSION}:
        raise AmendmentIntegrityError(
            f"schedule version {schedule_version_id} no longer matches the content it was "
            f"approved under ({status.value}); nothing is adopted or compared from it")
    if status == HashStatus.VERIFIED and header.header_integrity_hash:
        expected = header_integrity_hash(
            schedule_version_id=header.id, organization_id=header.organization_id,
            trial_id=header.trial_id, protocol_id=header.protocol_id,
            protocol_version_id=header.protocol_version_id,
            schedule_definition_id=header.schedule_definition_id,
            version_number=header.version_number,
            based_on_schedule_version_id=header.based_on_schedule_version_id,
            effective_from=header.effective_from, approved_by=header.approved_by,
            approved_at=header.approved_at, content_hash=header.content_hash or "",
            content_hash_version=header.content_hash_version or "")
        if expected != header.header_integrity_hash:
            raise AmendmentIntegrityError(
                f"the approval header of schedule version {schedule_version_id} (lineage, "
                "effective date, approver) no longer matches what was approved")
    return VerifiedVersion(header, schedule, status)


def require_approved(version: VerifiedVersion, label: str) -> None:
    if version.header.status != "APPROVED":
        raise AmendmentBlockedError(
            f"the {label} schedule version is {version.header.status}, not APPROVED",
            [{"code": "VERSION_NOT_APPROVED", "path": label, "detail": version.header.status}])


# ------------------------------------------------------------------------- mapping state
def rows_of(record: AmendmentMappingSetRecord) -> list[EventMapping]:
    return [EventMapping.model_validate(item) for item in record.mappings]


def rows_to_documents(rows: Iterable[EventMapping]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in rows]


def baseline_signature(left: UniversalSchedule, right: UniversalSchedule) -> set[tuple]:
    """The automatic matches the SERVER derives from the two version snapshots. A stored
    row claiming AUTO_CONFIRMED that is not among them was not server-derived."""
    return {
        (item.mapping_type.value, tuple(sorted(item.source_codes)), tuple(sorted(item.target_codes)))
        for item in propose_event_mappings(left, right)
        if item.review_status == MappingReviewStatus.AUTO_CONFIRMED}


def mapping_gate(
    left: UniversalSchedule, right: UniversalSchedule, record: AmendmentMappingSetRecord,
    decisions: list[MappingDecisionRecord],
) -> list[dict[str, str]]:
    """THE complete gate: structural coverage, review readiness, and provenance.

    * every AUTO_CONFIRMED row is re-derivable from the two snapshots (a client cannot
      supply one);
    * every CONFIRMED / REJECTED row has an authenticated review decision by the same
      reviewer;
    * the stored fingerprint is the fingerprint of the stored rows."""
    rows = rows_of(record)
    problems = mapping_problems(left, right, rows)
    auto = baseline_signature(left, right)
    by_mapping = {}
    for decision in decisions:
        if decision.mapping_id is not None:
            by_mapping.setdefault(decision.mapping_id, []).append(decision)
    for row in rows:
        signature = (row.mapping_type.value, tuple(sorted(row.source_codes)),
                     tuple(sorted(row.target_codes)))
        if row.review_status == MappingReviewStatus.AUTO_CONFIRMED and signature not in auto:
            problems.append({
                "code": "MAPPING_AUTO_NOT_SERVER_DERIVED", "path": f"mapping[{row.id}]",
                "detail": "an automatic confirmation that the two versions do not support"})
        if row.review_status in {MappingReviewStatus.CONFIRMED, MappingReviewStatus.REJECTED}:
            ok = any(item.reviewer_id == row.reviewed_by and item.reviewer_id is not None
                     for item in by_mapping.get(row.id, []))
            if not ok:
                problems.append({
                    "code": "MAPPING_REVIEW_NOT_RECORDED", "path": f"mapping[{row.id}]",
                    "detail": "no authenticated review decision backs this reviewed mapping"})
    if mapping_revision(rows) != record.mapping_revision:
        problems.append({"code": "MAPPING_REVISION_MISMATCH", "path": "mapping_set",
                         "detail": "the stored fingerprint does not match the stored mappings"})
    return problems


def baseline_rows(left: UniversalSchedule, right: UniversalSchedule) -> list[EventMapping]:
    return propose_event_mappings(left, right)


def mapping_summary(rows: Iterable[EventMapping]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in rows:
        key = f"{item.mapping_type.value}:{item.review_status.value}"
        counts[key] = counts.get(key, 0) + 1
    return counts


# ----------------------------------------------------------------- patient context
def context_fingerprint(tx: UnitOfWork, organization_id: UUID, patient_id: UUID) -> str:
    """Digest of the patient's MATERIAL inputs: every anchor, occurrence fact, condition,
    state, activity entry, unscheduled visit and ledger command (id + the one mutable
    field each can carry). Facts are append-only, so a recorded, corrected or superseded
    fact changes the digest - which is what makes a stale preview detectable."""
    anchors = [(str(item.id), item.status) for item in tx.facts.list_anchors(patient_id)]
    occurrences = sorted(
        (str(item.id), item.schedule_version_id.hex) for item in tx.facts.list_occurrences(patient_id))
    conditions = sorted(
        (str(item.id), str(item.superseded_by_id or ""))
        for item in tx.facts.list_current_conditions(patient_id))
    states = sorted(str(item.id) for item in tx.facts.list_states(patient_id))
    entries = sorted(
        (str(item.id), str(item.superseded_by_id or ""))
        for item in tx.facts.list_current_activity_entries(patient_id))
    unscheduled = sorted(str(item.id) for item in tx.facts.list_unscheduled_visits(patient_id))
    ledger = sorted(str(item.id) for item in tx.ledger.list_for_patient(organization_id, patient_id))
    return sha256_text(canonical_json({
        "anchors": sorted(anchors), "occurrences": occurrences, "conditions": conditions,
        "states": states, "entries": entries, "unscheduled": unscheduled, "ledger": ledger}))


def mapped_assignment_types() -> frozenset[str]:
    from .patient_schedule import MAPPED_ASSIGNMENT_TYPES
    return MAPPED_ASSIGNMENT_TYPES


def is_structural(row: EventMapping) -> bool:
    return row.mapping_type in {MappingType.EQUIVALENT, MappingType.RENAMED}
