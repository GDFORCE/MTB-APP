"""Amendment authoring workflow on the repository ports (P6-01, P6-02, P6-03).

* ``declare_lineage``  - V2 states which approved version it amends (once, before it is approved);
* ``prepare_pair``     - freeze the comparison of two approved versions and the server's
                         proposed event mapping;
* ``review_mapping``   - authenticated human decisions on the mapping, each appended with
                         its reason and revision;
* ``pair_view``        - everything a reviewer needs: both identities, the semantic changes,
                         the mappings and exactly what still blocks adoption.

No patient is read or changed here."""

from __future__ import annotations

from typing import Any, Callable, TypeVar
from uuid import UUID

from pydantic import ValidationError

from app.domain.schedule.amendment import (
    CONTENT_HASH_VERSION, MAPPING_ENGINE_VERSION, EventMapping, MappingReviewStatus,
    MappingType, mapping_revision, schedule_content_hash,
)
from app.domain.schedule.diff import (
    compare_schedule_versions, mapping_aware_changes, trace_changes,
)

from .amendment_common import (
    VerifiedVersion, baseline_rows, load_version, mapping_gate, mapping_summary, require_approved,
    rows_of, rows_to_documents,
)
from .amendments import (
    AmendmentMappingSetRecord, AmendmentPairRecord, MappingDecisionKind, MappingDecisionRecord,
)
from .errors import (
    AmendmentBlockedError, AmendmentIntegrityError, DuplicateRecordError, RecordNotFoundError,
    StaleRevisionError,
)
from .ports import UnitOfWork
from .records import AuditRecord

T = TypeVar("T")
_RETRIES = 3


def _blocked(message: str, code: str, path: str = "") -> AmendmentBlockedError:
    return AmendmentBlockedError(message, [{"code": code, "path": path, "detail": message}])


class AmendmentService:
    def __init__(self, uow: UnitOfWork):
        self._uow = uow

    def _run(self, work: Callable[[UnitOfWork], T]) -> T:
        last: Exception | None = None
        for _ in range(_RETRIES):
            try:
                return self._uow.run(work)
            except (StaleRevisionError, DuplicateRecordError) as error:
                last = error
        assert last is not None
        raise last

    # ------------------------------------------------------------------ lineage (P6-01)
    def declare_lineage(
        self, *, organization_id: UUID, actor_id: UUID, schedule_version_id: UUID,
        based_on_schedule_version_id: UUID, reason: str, expected_trial_id: UUID | None = None,
    ) -> dict[str, Any]:
        """V2 amends V1. Allowed once, before V2 is approved, so the approval hash covers
        it. Same organization, trial and protocol; a LATER protocol version; V1 approved."""
        if not reason or len(reason.strip()) < 3:
            raise _blocked("state why this version amends the earlier one", "REASON_REQUIRED")

        def work(tx: UnitOfWork) -> dict[str, Any]:
            if schedule_version_id == based_on_schedule_version_id:
                raise _blocked("a version cannot amend itself", "LINEAGE_SELF")
            child = tx.schedules.get_header(schedule_version_id)
            if child.organization_id != organization_id:
                raise RecordNotFoundError("schedule version not found")
            parent = load_version(tx, organization_id, based_on_schedule_version_id)
            # The caller was authorised against ONE trial (the URL's). Both versions must belong
            # to it - checked before any write, inside the transaction that would make it.
            if expected_trial_id is not None and (
                    child.trial_id != expected_trial_id or parent.header.trial_id != expected_trial_id):
                raise RecordNotFoundError("schedule version not found")
            require_approved(parent, "earlier")
            if (child.trial_id, child.protocol_id) != (parent.header.trial_id, parent.header.protocol_id):
                raise _blocked("both versions must belong to the same trial and protocol",
                               "LINEAGE_SCOPE")
            if child.protocol_version_id == parent.header.protocol_version_id:
                raise _blocked(
                    "an amendment is a new protocol version; this version shares the earlier "
                    "version's protocol version", "LINEAGE_SAME_PROTOCOL_VERSION")
            if child.status in {"APPROVED", "SUPERSEDED"}:
                raise _blocked("the lineage must be declared before the version is approved",
                               "LINEAGE_AFTER_APPROVAL")
            ours = tx.authoring.get_definition(child.schedule_definition_id)
            theirs = tx.authoring.get_definition(parent.header.schedule_definition_id)
            if (ours.name, ours.schedule_type) != (theirs.name, theirs.schedule_type):
                raise _blocked("an amendment replaces the same schedule (name and type)",
                               "LINEAGE_DIFFERENT_SCHEDULE")
            cursor, seen = parent.header, {child.id}
            while cursor.based_on_schedule_version_id is not None:   # no cycles
                if cursor.based_on_schedule_version_id in seen:
                    raise _blocked("that would make the lineage circular", "LINEAGE_CYCLE")
                seen.add(cursor.id)
                cursor = tx.schedules.get_header(cursor.based_on_schedule_version_id)
            from .errors import ImmutableRecordError
            try:
                header = tx.schedules.set_lineage(schedule_version_id, based_on_schedule_version_id)
            except ImmutableRecordError as error:
                raise _blocked(str(error), "LINEAGE_FIXED") from error
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="SCHEDULE_AMENDMENT_LINEAGE_DECLARED", entity_type="SCHEDULE_VERSION",
                entity_id=header.id, before=None,
                after={"based_on_schedule_version_id": str(based_on_schedule_version_id),
                       "protocol_version_id": str(header.protocol_version_id)},
                reason=reason))
            return {"schedule_version_id": str(header.id),
                    "based_on_schedule_version_id": str(based_on_schedule_version_id),
                    "status": header.status}

        return self._run(work)

    # ------------------------------------------------------------------- pair (P6-02/03)
    def prepare_pair(
        self, *, organization_id: UUID, actor_id: UUID, from_schedule_version_id: UUID,
        to_schedule_version_id: UUID, expected_trial_id: UUID | None = None,
    ) -> dict[str, Any]:
        def work(tx: UnitOfWork) -> dict[str, Any]:
            existing = tx.amendments.find_pair(
                organization_id, from_schedule_version_id, to_schedule_version_id)
            if existing is not None:
                if expected_trial_id is not None and existing.trial_id != expected_trial_id:
                    raise RecordNotFoundError("schedule version not found")      # replay too
                return self._view(tx, organization_id, existing)
            left = load_version(tx, organization_id, from_schedule_version_id)
            right = load_version(tx, organization_id, to_schedule_version_id)
            if expected_trial_id is not None and (
                    left.header.trial_id != expected_trial_id
                    or right.header.trial_id != expected_trial_id):
                raise RecordNotFoundError("schedule version not found")
            require_approved(left, "earlier")
            require_approved(right, "later")
            if (left.header.trial_id, left.header.protocol_id) != (
                    right.header.trial_id, right.header.protocol_id):
                raise _blocked("the versions belong to different trials or protocols", "PAIR_SCOPE")
            if right.header.based_on_schedule_version_id != left.header.id:
                raise _blocked(
                    "the later version has not declared that it amends the earlier one; "
                    "declare the lineage before approving the amendment", "PAIR_NO_LINEAGE")
            diff = compare_schedule_versions(left.schedule, right.schedule)
            changes = trace_changes(left.schedule, right.schedule, diff.typed_changes)
            summary = {
                "total": len(changes),
                "clinical": sum(1 for item in changes if item["significance"] == "CLINICAL"),
                "administrative": sum(1 for item in changes if item["significance"] == "ADMINISTRATIVE"),
                "added_events": diff.added_events, "removed_events": diff.removed_events}
            pair = AmendmentPairRecord(
                organization_id=organization_id, trial_id=left.header.trial_id,
                protocol_id=left.header.protocol_id,
                from_schedule_version_id=left.id, to_schedule_version_id=right.id,
                from_content_hash=left.header.content_hash or schedule_content_hash(left.schedule),
                to_content_hash=right.header.content_hash or schedule_content_hash(right.schedule),
                content_hash_version=CONTENT_HASH_VERSION,
                from_effective_from=left.header.effective_from, to_effective_from=right.header.effective_from,
                change_set=changes, change_summary=summary, created_by=actor_id)
            tx.amendments.add_pair(pair)
            rows = baseline_rows(left.schedule, right.schedule)
            revision = mapping_revision(rows)
            tx.amendments.add_mapping_set(AmendmentMappingSetRecord(
                organization_id=organization_id, trial_id=pair.trial_id, pair_id=pair.id, revision=1,
                mappings=rows_to_documents(rows), mapping_revision=revision,
                engine_version=MAPPING_ENGINE_VERSION))
            tx.amendments.add_mapping_decision(MappingDecisionRecord(
                organization_id=organization_id, trial_id=pair.trial_id, pair_id=pair.id,
                kind=MappingDecisionKind.PROPOSED, reviewer_id=None,
                reason="server-proposed baseline", base_revision=0, resulting_revision=1,
                resulting_mapping_revision=revision, after={"count": len(rows)}))
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="SCHEDULE_AMENDMENT_PAIR_PREPARED", entity_type="AMENDMENT_PAIR",
                entity_id=pair.id, before=None,
                after={"from": str(left.id), "to": str(right.id),
                       "changes": summary["total"], "clinical": summary["clinical"],
                       "from_verification": left.hash_status.value,
                       "to_verification": right.hash_status.value}))
            return self._view(tx, organization_id, pair)

        return self._run(work)

    def pair_view(self, *, organization_id: UUID, pair_id: UUID) -> dict[str, Any]:
        return self._uow.read(
            lambda tx: self._view(tx, organization_id, tx.amendments.get_pair(organization_id, pair_id)))

    def find_pair_view(
        self, *, organization_id: UUID, from_schedule_version_id: UUID,
        to_schedule_version_id: UUID,
    ) -> dict[str, Any] | None:
        def read(tx: UnitOfWork) -> dict[str, Any] | None:
            pair = tx.amendments.find_pair(
                organization_id, from_schedule_version_id, to_schedule_version_id)
            return self._view(tx, organization_id, pair) if pair is not None else None

        return self._uow.read(read)

    def trial_overview(self, *, organization_id: UUID, trial_id: UUID) -> dict[str, Any]:
        """Every schedule version of a trial with its lineage, plus the prepared pairs."""
        def read(tx: UnitOfWork) -> dict[str, Any]:
            versions = []
            for header in sorted(
                    tx.schedules.list_headers(organization_id=organization_id, trial_id=trial_id),
                    key=lambda item: (item.created_at, str(item.id))):
                versions.append({
                    "schedule_version_id": str(header.id), "version_number": header.version_number,
                    "status": header.status, "protocol_version_id": str(header.protocol_version_id),
                    "based_on_schedule_version_id": (
                        str(header.based_on_schedule_version_id)
                        if header.based_on_schedule_version_id else None),
                    "effective_from": header.effective_from.isoformat() if header.effective_from else None,
                    "approved_at": header.approved_at.isoformat() if header.approved_at else None,
                    "content_hash": header.content_hash,
                    "hash_recorded": header.content_hash is not None})
            return {"trial_id": str(trial_id), "versions": versions,
                    "pairs": self._pairs(tx, organization_id, trial_id)}

        return self._uow.read(read)

    @staticmethod
    def _pairs(tx: UnitOfWork, organization_id: UUID, trial_id: UUID) -> list[dict[str, Any]]:
        out = []
        for pair in tx.amendments.list_pairs_for_trial(organization_id, trial_id):
            mapping = tx.amendments.get_mapping_set(organization_id, pair.id)
            out.append({
                "pair_id": str(pair.id), "from_schedule_version_id": str(pair.from_schedule_version_id),
                "to_schedule_version_id": str(pair.to_schedule_version_id),
                "change_summary": pair.change_summary, "mapping_set_revision": mapping.revision,
                "created_at": pair.created_at.isoformat()})
        return out

    def list_for_trial(self, *, organization_id: UUID, trial_id: UUID) -> list[dict[str, Any]]:
        return self._uow.read(lambda tx: self._pairs(tx, organization_id, trial_id))

    def _view(self, tx: UnitOfWork, organization_id: UUID, pair: AmendmentPairRecord) -> dict[str, Any]:
        left = load_version(tx, organization_id, pair.from_schedule_version_id)
        right = load_version(tx, organization_id, pair.to_schedule_version_id)
        for version, recorded, label in (
                (left, pair.from_content_hash, "earlier"), (right, pair.to_content_hash, "later")):
            observed = version.header.content_hash or schedule_content_hash(version.schedule)
            if observed != recorded:
                raise AmendmentIntegrityError(
                    f"the {label} version no longer matches the content this comparison was "
                    "prepared from")
        record = tx.amendments.get_mapping_set(organization_id, pair.id)
        decisions = tx.amendments.list_mapping_decisions(organization_id, pair.id)
        problems = mapping_gate(left.schedule, right.schedule, record, decisions)
        rows = rows_of(record)
        renames = [(item.source_codes[0], item.target_codes[0], item.review_status.value)
                   for item in rows
                   if item.mapping_type in {MappingType.RENAMED, MappingType.EQUIVALENT}
                   and item.review_status != MappingReviewStatus.REJECTED
                   and item.source_codes[0] != item.target_codes[0]]
        changes = mapping_aware_changes(left.schedule, right.schedule, pair.change_set, renames)
        summary = {**pair.change_summary,
                   "total": len(changes),
                   "clinical": sum(1 for item in changes if item["significance"] == "CLINICAL"),
                   "administrative": sum(1 for item in changes
                                         if item["significance"] == "ADMINISTRATIVE")}
        return {
            "pair_id": str(pair.id), "trial_id": str(pair.trial_id),
            "from": left.identity(), "to": right.identity(),
            "change_summary": summary, "changes": changes,
            "mapping_set_revision": record.revision, "mapping_revision": record.mapping_revision,
            "mappings": [item.model_dump(mode="json") for item in rows],
            "mapping_summary": mapping_summary(rows),
            "problems": problems, "ready_for_adoption": not problems,
            "reviews": [{
                "id": str(item.id), "kind": item.kind.value,
                "mapping_id": str(item.mapping_id) if item.mapping_id else None,
                "reviewer_id": str(item.reviewer_id) if item.reviewer_id else None,
                "reason": item.reason, "base_revision": item.base_revision,
                "resulting_revision": item.resulting_revision,
                "created_at": item.created_at.isoformat()} for item in decisions],
            "created_at": pair.created_at.isoformat()}

    # ------------------------------------------------------------- mapping review (P6-02)
    def review_mapping(
        self, *, organization_id: UUID, actor_id: UUID, pair_id: UUID, expected_revision: int,
        action: str, reason: str, mapping_id: UUID | None = None,
        rules: list[dict[str, Any]] | None = None, mapping_type: str | None = None,
        source_codes: list[str] | None = None, target_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        """One authenticated decision. The reviewer is the caller; nothing about
        provenance, status or reviewer identity comes from the request."""
        if not reason or len(reason.strip()) < 3:
            raise _blocked("a reviewer's reason is required", "REASON_REQUIRED")
        action = action.upper()
        if action not in {"CONFIRM", "REJECT", "SET_RULES", "REPLACE"}:
            raise _blocked(f"unknown mapping action {action!r}", "ACTION_UNKNOWN")

        def work(tx: UnitOfWork) -> dict[str, Any]:
            pair = tx.amendments.get_pair(organization_id, pair_id)
            left = load_version(tx, organization_id, pair.from_schedule_version_id)
            right = load_version(tx, organization_id, pair.to_schedule_version_id)
            record = tx.amendments.get_mapping_set(organization_id, pair.id)
            if record.revision != expected_revision:
                raise StaleRevisionError("the mapping set changed since you opened it")
            rows = rows_of(record)
            left_codes = {item.code for item in left.schedule.events}
            right_codes = {item.code for item in right.schedule.events}
            before: dict[str, Any] | None = None
            after: dict[str, Any] | None
            target_id: UUID | None = mapping_id
            kind: MappingDecisionKind

            def find(row_id: UUID | None) -> EventMapping:
                row = next((item for item in rows if item.id == row_id), None)
                if row is None:
                    raise RecordNotFoundError("mapping not found")
                return row

            def checked_rules(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
                items = raw or []
                for item in items:
                    if item.get("source_code") not in left_codes or item.get("target_code") not in right_codes:
                        raise _blocked(
                            "an occurrence rule names a visit that is not in the version it "
                            "belongs to", "RULE_UNKNOWN_EVENT")
                return items

            try:
                if action == "REPLACE":
                    kind = MappingDecisionKind.REPLACE
                    sources, targets = list(source_codes or []), list(target_codes or [])
                    if not set(sources) <= left_codes or not set(targets) <= right_codes:
                        raise _blocked("a replacement names a visit that does not exist",
                                       "REPLACE_UNKNOWN_EVENT")
                    covered = [item for item in rows
                               if item.review_status != MappingReviewStatus.REJECTED
                               and (set(item.source_codes) & set(sources)
                                    or set(item.target_codes) & set(targets))]
                    if covered:
                        raise _blocked(
                            "those visits are already covered by a mapping; reject that mapping "
                            "first", "REPLACE_OVERLAPS")
                    created = EventMapping(
                        mapping_type=MappingType(str(mapping_type)), source_codes=sources,
                        target_codes=targets, confidence=1.0, provenance="REVIEWER",
                        rationale=reason.strip(), review_status=MappingReviewStatus.CONFIRMED,
                        reviewed_by=actor_id, review_reason=reason.strip(),
                        occurrence_rules=checked_rules(rules))
                    rows.append(created)
                    target_id, after = created.id, created.model_dump(mode="json")
                else:
                    current = find(mapping_id)
                    before = current.model_dump(mode="json")
                    if current.review_status == MappingReviewStatus.REJECTED:
                        raise _blocked("that mapping was already rejected", "ALREADY_REJECTED")
                    if action == "REJECT":
                        kind = MappingDecisionKind.REJECT
                        updated = current.model_copy(update={
                            "review_status": MappingReviewStatus.REJECTED, "reviewed_by": actor_id,
                            "review_reason": reason.strip()})
                    elif action == "SET_RULES":
                        kind = MappingDecisionKind.SET_RULES
                        if current.mapping_type not in {MappingType.SPLIT, MappingType.MERGED}:
                            raise _blocked("occurrence rules belong to splits and merges only",
                                           "RULES_NOT_APPLICABLE")
                        updated = EventMapping.model_validate({
                            **current.model_dump(mode="json"), "occurrence_rules": checked_rules(rules),
                            "review_status": MappingReviewStatus.CONFIRMED.value,
                            "reviewed_by": str(actor_id), "review_reason": reason.strip(),
                            "provenance": "REVIEWER"})
                    else:                                    # CONFIRM
                        kind = MappingDecisionKind.CONFIRM
                        if current.mapping_type in {MappingType.SPLIT, MappingType.MERGED} \
                                and not current.occurrence_rules:
                            raise _blocked(
                                "state which occurrence goes where (set the rules) before "
                                "confirming a split or merge", "RULES_MISSING")
                        updated = EventMapping.model_validate({
                            **current.model_dump(mode="json"),
                            "review_status": MappingReviewStatus.CONFIRMED.value,
                            "reviewed_by": str(actor_id), "review_reason": reason.strip()})
                    rows[rows.index(current)] = updated
                    after = updated.model_dump(mode="json")
            except ValidationError as error:
                raise _blocked(
                    "; ".join(str(item["msg"]) for item in error.errors()), "MAPPING_INVALID") from error

            revision = mapping_revision(rows)
            saved = tx.amendments.save_mapping_set(
                record.model_copy(update={
                    "mappings": rows_to_documents(rows), "mapping_revision": revision}),
                expected_revision=expected_revision)
            tx.amendments.add_mapping_decision(MappingDecisionRecord(
                organization_id=organization_id, trial_id=pair.trial_id, pair_id=pair.id,
                mapping_id=target_id, kind=kind, reviewer_id=actor_id, reason=reason.strip(),
                base_revision=expected_revision, resulting_revision=saved.revision,
                resulting_mapping_revision=revision, before=before, after=after))
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor_id,
                action="SCHEDULE_AMENDMENT_MAPPING_REVIEWED", entity_type="AMENDMENT_PAIR",
                entity_id=pair.id, before=before, after=after, reason=reason.strip()))
            return self._view(tx, organization_id, pair)

        return self._run(work)

    # ------------------------------------------------------------------------ gate
    def gate(self, tx: UnitOfWork, organization_id: UUID, pair: AmendmentPairRecord,
             left: VerifiedVersion, right: VerifiedVersion) -> list[dict[str, str]]:
        record = tx.amendments.get_mapping_set(organization_id, pair.id)
        return mapping_gate(left.schedule, right.schedule, record,
                            tx.amendments.list_mapping_decisions(organization_id, pair.id))
