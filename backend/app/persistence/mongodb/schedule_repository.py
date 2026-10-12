"""Schedule versions: header, events, evidence, claims, issues, decisions.

Layout (see the port plan, section 2):
* ``schedule_versions``  - header + small bounded structures (epochs, dimensions,
  anchors, repeat blocks, ...) in ONE document, so a version's lifecycle and its
  content-level immutability are decided by a single conditional write;
* ``schedule_events``    - one document per event, with its activities,
  applicability, recurrence and dependencies embedded;
* ``schedule_evidence`` / ``schedule_claims`` / ``schedule_validation_issues`` -
  separate because they are large and grow with the protocol;
* ``schedule_review_decisions`` - append-only.

Frozen versions (APPROVED / SUPERSEDED) are protected by guarded writes: every
mutating method filters on the status it expects, so a write racing an approval
loses instead of silently editing an approved rule.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.client_session import ClientSession
from pymongo.database import Database
from pymongo.errors import BulkWriteError, DuplicateKeyError

from app.application import schedule_documents as parts
from app.application.authoring import (
    FROZEN_STATUSES,
    ReviewDecisionRecord,
    ScheduleDefinitionRecord,
    ScheduleVersionHeader,
)
from app.application.errors import (
    DuplicateRecordError,
    ImmutableRecordError,
    RecordNotFoundError,
)
from app.domain.schedule.models import Event, UniversalSchedule, ValidationIssue

from .codec import aware, encode
from .documents import DOC_VERSION, from_document, to_document

VERSIONS = "schedule_versions"
EVENTS = "schedule_events"
EVIDENCE = "schedule_evidence"
CLAIMS = "schedule_claims"
ISSUES = "schedule_validation_issues"
DECISIONS = "schedule_review_decisions"
DEFINITIONS = "schedule_definitions"

_HEADER_FIELDS = set(ScheduleVersionHeader.model_fields)
_DUPLICATE = 11000


def _is_duplicate(error: Exception) -> bool:
    if isinstance(error, DuplicateKeyError):
        return True
    if isinstance(error, BulkWriteError):
        return any(item.get("code") == _DUPLICATE for item in error.details.get("writeErrors", []))
    return False


def _header_from(doc: dict[str, Any]) -> ScheduleVersionHeader:
    data = {key: value for key, value in doc.items() if key in _HEADER_FIELDS or key == "_id"}
    return from_document(ScheduleVersionHeader, data)


class MongoScheduleVersionRepository:
    def __init__(self, database: Database, session: ClientSession | None = None):
        self._db = database
        self._session = session

    # -- reads --------------------------------------------------------------
    def _header_doc(self, schedule_version_id: UUID) -> dict[str, Any]:
        doc = self._db[VERSIONS].find_one({"_id": schedule_version_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("schedule version not found")
        return doc

    def get_header(self, schedule_version_id: UUID) -> ScheduleVersionHeader:
        return _header_from(self._header_doc(schedule_version_id))

    def get(self, schedule_version_id: UUID) -> UniversalSchedule:
        doc = self._header_doc(schedule_version_id)
        header = _header_from(doc)
        definition = self._db[DEFINITIONS].find_one(
            {"_id": header.schedule_definition_id}, session=self._session)
        if definition is None:
            raise RecordNotFoundError("schedule definition not found")
        definition_record = from_document(ScheduleDefinitionRecord, definition)

        def children(collection: str, *, order: str | None = None) -> list[dict[str, Any]]:
            cursor = self._db[collection].find(
                {"schedule_version_id": schedule_version_id}, session=self._session)
            if order:
                cursor = cursor.sort([(order, ASCENDING)])
            return list(cursor)

        return parts.assemble(
            header=header, definition=definition_record, content=doc["content"],
            events=children(EVENTS), evidence=children(EVIDENCE, order="seq"),
            claims=children(CLAIMS, order="seq"), issues=children(ISSUES, order="seq"),
        )

    def find_by_extraction_run(self, run_id: UUID) -> ScheduleVersionHeader | None:
        doc = self._db[VERSIONS].find_one({"extraction_run_id": run_id}, session=self._session)
        return _header_from(doc) if doc else None

    def max_version_number(self, schedule_definition_id: UUID) -> int:
        doc = self._db[VERSIONS].find_one(
            {"schedule_definition_id": schedule_definition_id}, {"version_number": 1},
            sort=[("version_number", DESCENDING)], session=self._session)
        return int(doc["version_number"]) if doc else 0

    def list_headers(
        self, *, organization_id: UUID, status: str | None = None,
        trial_id: UUID | None = None, schedule_definition_id: UUID | None = None,
    ) -> list[ScheduleVersionHeader]:
        query: dict[str, Any] = {"organization_id": organization_id}
        if status is not None:
            query["status"] = status
        if trial_id is not None:
            query["trial_id"] = trial_id
        if schedule_definition_id is not None:
            query["schedule_definition_id"] = schedule_definition_id
        cursor = self._db[VERSIONS].find(query, session=self._session).sort(
            [("updated_at", ASCENDING), ("_id", ASCENDING)])
        return [_header_from(doc) for doc in cursor]

    # -- persist ------------------------------------------------------------
    def persist_draft(
        self, schedule: UniversalSchedule, *, schedule_definition_id: UUID,
        extraction_run_id: UUID | None = None,
    ) -> None:
        definition_doc = self._db[DEFINITIONS].find_one(
            {"_id": schedule_definition_id}, session=self._session)
        if definition_doc is None:
            raise RecordNotFoundError("schedule definition not found")
        definition = from_document(ScheduleDefinitionRecord, definition_doc)
        if self._db[VERSIONS].find_one(
                {"_id": schedule.schedule_version_id}, {"_id": 1}, session=self._session):
            raise ValueError("schedule version already exists")
        built = parts.build_parts(
            schedule, definition=definition, extraction_run_id=extraction_run_id)
        header: ScheduleVersionHeader = built["header"]
        version_id = header.id
        scope = {
            "schedule_version_id": version_id, "organization_id": header.organization_id,
            "doc_version": DOC_VERSION,
        }
        version_doc = to_document(header)
        version_doc["content"] = built["content"]
        try:
            self._db[VERSIONS].insert_one(version_doc, session=self._session)
            if built["events"]:
                self._db[EVENTS].insert_many([
                    {"_id": item["id"], **scope, "code": item["code"],
                     "sequence_number": item["sequence_number"], "event": item["event"]}
                    for item in built["events"]], ordered=True, session=self._session)
            if built["evidence"]:
                self._db[EVIDENCE].insert_many([
                    {"_id": item["id"], **scope, "seq": index,
                     "protocol_version_id": item["protocol_version_id"], "evidence": item["evidence"]}
                    for index, item in enumerate(built["evidence"])],
                    ordered=True, session=self._session)
            if built["claims"]:
                self._db[CLAIMS].insert_many([
                    {"_id": item["id"], **scope, "seq": index, "claim": item["claim"]}
                    for index, item in enumerate(built["claims"])],
                    ordered=True, session=self._session)
        except (DuplicateKeyError, BulkWriteError) as error:
            if not _is_duplicate(error):
                raise
            raise DuplicateRecordError(
                f"duplicate record while persisting schedule version {version_id}: {error}",
                key=str(version_id)) from error
        self._insert_issues(version_id, header.organization_id, built["issues"], start=0)

    def _insert_issues(
        self, version_id: UUID, organization_id: UUID, issues: list[dict[str, Any]], *, start: int,
    ) -> None:
        if not issues:
            return
        try:
            self._db[ISSUES].insert_many([
                {"_id": item["id"], "schedule_version_id": version_id,
                 "organization_id": organization_id, "doc_version": DOC_VERSION,
                 "seq": start + index, "validator_version": item["validator_version"],
                 "status": item["status"], "resolved_by": item["resolved_by"],
                 "resolved_at": item["resolved_at"], "resolution_note": item["resolution_note"],
                 "issue": item["issue"]}
                for index, item in enumerate(issues)], ordered=True, session=self._session)
        except (DuplicateKeyError, BulkWriteError) as error:
            if not _is_duplicate(error):
                raise
            raise DuplicateRecordError(
                f"duplicate validation issue id: {error}", key=str(version_id)) from error

    # -- content edits ------------------------------------------------------
    def _require_editable(self, schedule_version_id: UUID) -> dict[str, Any]:
        doc = self._header_doc(schedule_version_id)
        if doc["status"] in FROZEN_STATUSES:
            raise ImmutableRecordError("approved schedule versions cannot be edited")
        return doc

    def replace_event(self, schedule_version_id: UUID, event: Event) -> None:
        header = self._require_editable(schedule_version_id)
        stored = self._db[EVENTS].find_one(
            {"_id": event.id, "schedule_version_id": schedule_version_id}, session=self._session)
        if stored is None:
            raise RecordNotFoundError("event definition not found")
        codes = {
            doc["code"] for doc in self._db[EVENTS].find(
                {"schedule_version_id": schedule_version_id}, {"code": 1}, session=self._session)
        } | {event.code}
        document = parts._event_document(event, valid_codes=codes)
        old_code = stored["code"]
        try:
            self._db[EVENTS].replace_one(
                {"_id": event.id},
                {"_id": event.id, "schedule_version_id": schedule_version_id,
                 "organization_id": header["organization_id"], "doc_version": DOC_VERSION,
                 "code": document["code"], "sequence_number": document["sequence_number"],
                 "event": document["event"]},
                session=self._session)
        except DuplicateKeyError as error:
            raise DuplicateRecordError(
                f"event code {event.code!r} already exists in this schedule version",
                key=event.code) from error
        if old_code != event.code:
            # SQL held dependencies by event id, so a renamed source was followed
            # automatically; embedded dependencies must be rewritten to match.
            for other in self._db[EVENTS].find(
                    {"schedule_version_id": schedule_version_id, "_id": {"$ne": event.id}},
                    session=self._session):
                payload = other["event"]
                if parts.rename_dependency_source(payload, old_code, event.code):
                    self._db[EVENTS].update_one(
                        {"_id": other["_id"]}, {"$set": {"event": payload}}, session=self._session)

    # -- issues ---------------------------------------------------------------
    def replace_validation_issues(
        self, schedule_version_id: UUID, issues: list[ValidationIssue],
    ) -> None:
        header = self._require_editable(schedule_version_id)
        self._db[ISSUES].delete_many(
            {"schedule_version_id": schedule_version_id,
             "validator_version": parts.VALIDATOR_VERSION, "status": "OPEN"},
            session=self._session)
        last = self._db[ISSUES].find_one(
            {"schedule_version_id": schedule_version_id}, {"seq": 1},
            sort=[("seq", DESCENDING)], session=self._session)
        start = (last["seq"] + 1) if last else 0
        self._insert_issues(
            schedule_version_id, header["organization_id"],
            [parts.issue_document(item) for item in issues], start=start)

    def list_issues(self, schedule_version_id: UUID) -> list[ValidationIssue]:
        self._header_doc(schedule_version_id)
        cursor = self._db[ISSUES].find(
            {"schedule_version_id": schedule_version_id}, session=self._session
        ).sort([("seq", ASCENDING)])
        return [ValidationIssue.model_validate(doc["issue"]) for doc in cursor]

    def set_issue_status(
        self, schedule_version_id: UUID, issue_id: UUID, status: str, *,
        resolved_by: UUID | None = None, note: str | None = None,
    ) -> None:
        self._require_editable(schedule_version_id)
        doc = self._db[ISSUES].find_one(
            {"_id": issue_id, "schedule_version_id": schedule_version_id}, session=self._session)
        if doc is None:
            raise RecordNotFoundError("validation issue not found")
        payload = dict(doc["issue"])
        payload["status"] = status
        self._db[ISSUES].update_one(
            {"_id": issue_id},
            {"$set": {"status": status, "issue": payload, "resolved_by": resolved_by,
                      "resolved_at": datetime.now(timezone.utc) if resolved_by else None,
                      "resolution_note": note}},
            session=self._session)

    # -- lifecycle --------------------------------------------------------------
    def _guarded(
        self, schedule_version_id: UUID, flt: dict[str, Any], update: dict[str, Any],
        *, refusal: str,
    ) -> ScheduleVersionHeader:
        now = datetime.now(timezone.utc)
        document = self._db[VERSIONS].find_one_and_update(
            {"_id": schedule_version_id, **flt},
            {"$set": {**update, "updated_at": now}, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER, session=self._session)
        if document is not None:
            return _header_from(document)
        current = self._header_doc(schedule_version_id)  # raises when missing
        raise ImmutableRecordError(f"{refusal} (current status {current['status']})")

    def transition(
        self, schedule_version_id: UUID, *, to_status: str, expected: set[str] | None = None,
        rejection_reason: str | None = None,
    ) -> ScheduleVersionHeader:
        status_filter: dict[str, Any] = (
            {"status": {"$in": sorted(expected)}} if expected is not None
            else {"status": {"$nin": sorted(FROZEN_STATUSES)}})
        update: dict[str, Any] = {"status": to_status}
        if to_status == "REJECTED":
            update["rejection_reason"] = rejection_reason
        return self._guarded(
            schedule_version_id, status_filter, update,
            refusal=f"cannot move the schedule version to {to_status}")

    def mark_approved(
        self, schedule_version_id: UUID, *, reviewer_id: UUID, approved_at: datetime,
        effective_from: date | None,
    ) -> ScheduleVersionHeader:
        return self._guarded(
            schedule_version_id, {"status": "IN_REVIEW"},
            {"status": "APPROVED", "approved_by": reviewer_id,
             "approved_at": encode(approved_at),
             "effective_from": effective_from.isoformat() if effective_from else None},
            refusal="only an in-review schedule can be approved")

    # -- decisions ------------------------------------------------------------------
    def add_decision(self, record: ReviewDecisionRecord) -> None:
        self._header_doc(record.schedule_version_id)
        try:
            self._db[DECISIONS].insert_one(to_document(record), session=self._session)
        except DuplicateKeyError as error:
            raise DuplicateRecordError(
                f"review decision {record.id} already exists", key=str(record.id)) from error

    def list_decisions(
        self, schedule_version_id: UUID, *, reviewer_id: UUID | None = None,
        decisions: set[str] | None = None,
    ) -> list[ReviewDecisionRecord]:
        query: dict[str, Any] = {"schedule_version_id": schedule_version_id}
        if reviewer_id is not None:
            query["reviewer_id"] = reviewer_id
        if decisions is not None:
            query["decision"] = {"$in": sorted(decisions)}
        cursor = self._db[DECISIONS].find(query, session=self._session).sort(
            [("created_at", ASCENDING), ("_id", ASCENDING)])
        records = []
        for doc in cursor:
            record = from_document(ReviewDecisionRecord, doc)
            records.append(record.model_copy(update={"created_at": aware(record.created_at)}))
        return records
