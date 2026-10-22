"""Pilot cohort cutover: shadow -> pilot -> rollback, with a legacy-write guard (P11-03/04/05).

Nothing here runs by itself and nothing here touches a legacy collection, ``UCTSM_AUTHORITATIVE`` or a
trial read mode. Promotion only classifies EXPLICITLY NAMED, reconciled, parity-matching mirror patients
as plane-authoritative (``authority_plane="MONGO"``), which is exactly what the legacy-write guard then
refuses to let the legacy store contradict. A promotion needs the grant-only ``cohort.cutover``
capability and a second person (maker-checker); a rollback refuses to silently discard plane-only changes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID

from ..authorization.catalog import Capability
from ..authorization.policy import Principal, ResourceContext, authorize
from .cohort_records import MAX_MEMBERS, CohortMember, CohortRecord
from .errors import PersistenceError, RecordNotFoundError
from .legacy_migration import SOURCE_SYSTEM
from .ports import UnitOfWork
from .records import AuditRecord

MIGRATED = "MIGRATED_TO_UCTSM"


class CutoverRefusedError(PersistenceError):
    """A cutover step was refused; ``code`` is stable and ``details`` carry no clinical content."""

    def __init__(self, code: str, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code, self.details = code, details or {}


class CohortCutoverService:
    def __init__(self, uow: UnitOfWork, *, clock: Callable[[], datetime] | None = None):
        self._uow = uow
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _actor(principal: Principal, organization_id: UUID) -> UUID:
        decision = authorize(principal, Capability.COHORT_CUTOVER, ResourceContext())
        if decision.denied or principal.organization_id != str(organization_id):
            raise CutoverRefusedError("CAPABILITY_MISSING")
        return UUID(principal.user_id)

    # -- shadow -----------------------------------------------------------------------------
    def create_shadow(self, principal: Principal, organization_id: UUID, trial_id: UUID, name: str,
                      external_patient_ids: list[str]) -> CohortRecord:
        actor = self._actor(principal, organization_id)
        ids = list(dict.fromkeys(external_patient_ids))
        if not ids or len(ids) > MAX_MEMBERS:
            raise CutoverRefusedError("COHORT_SIZE_INVALID", {"max": MAX_MEMBERS})

        def work(tx):
            members, problems = [], {}
            for external in ids:
                mapping = tx.migrations.get_mapping(organization_id, SOURCE_SYSTEM, external)
                if mapping is None or mapping.patient_id is None:
                    problems[external] = "MAPPING_MISSING"
                    continue
                patient = tx.patients.get(organization_id, mapping.patient_id)
                if patient.trial_id != trial_id:
                    problems[external] = "PATIENT_IN_OTHER_TRIAL"
                    continue
                members.append(CohortMember(external_patient_id=external, patient_id=patient.id,
                                            previous_authority=patient.authority_plane))
            if problems:
                raise CutoverRefusedError("COHORT_MEMBERS_INVALID", {"members": problems})
            cohort = CohortRecord(organization_id=organization_id, trial_id=trial_id, name=name,
                                  members=members, created_by=actor)
            tx.cohorts.add(cohort)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor, action="COHORT_SHADOW_CREATED",
                entity_type="TRIAL", entity_id=trial_id, after={"cohort_id": str(cohort.id), "members": len(members)}))
            return cohort
        return self._uow.run(work)

    def readiness(self, organization_id: UUID, cohort_id: UUID) -> dict[str, Any]:
        return self._uow.read(lambda tx: self._readiness(tx, organization_id, tx.cohorts.get(organization_id, cohort_id)))

    @staticmethod
    def _readiness(tx, organization_id: UUID, cohort: CohortRecord) -> dict[str, Any]:
        runs = tx.parity.list_for_trial(organization_id, cohort.trial_id)
        rows = []
        for member in cohort.members:
            reasons = []
            mapping = tx.migrations.get_mapping(organization_id, SOURCE_SYSTEM, member.external_patient_id)
            if mapping is None:
                reasons.append("MAPPING_MISSING")
            elif mapping.status != "RECONCILED" or mapping.patient_id != member.patient_id:
                reasons.append("MAPPING_NOT_RECONCILED")
            try:
                patient = tx.patients.get(organization_id, member.patient_id)
            except RecordNotFoundError:
                reasons.append("PATIENT_NOT_FOUND")
                patient = None
            if patient is not None:
                if cohort.state == "SHADOW" and patient.authority_plane not in (None, "UNCLASSIFIED"):
                    reasons.append("ALREADY_AUTHORITATIVE_OR_CLASSIFIED")
                mine = [run for run in runs if run.patient_id == patient.id
                        and run.schedule_version_id == patient.current_schedule_version_id]
                latest = max(mine, key=lambda run: run.ran_at, default=None)
                if latest is None or latest.verdict != "MATCH":
                    reasons.append("PARITY_NOT_MATCH")
            rows.append({"external_patient_id": member.external_patient_id, "ready": not reasons, "reasons": reasons})
        return {"cohort_id": str(cohort.id), "state": cohort.state,
                "ready": bool(rows) and all(row["ready"] for row in rows), "patients": rows}

    # -- pilot ------------------------------------------------------------------------------
    def promote(self, principal: Principal, organization_id: UUID, cohort_id: UUID, *, reason: str,
                snapshot: dict[str, Any] | None = None, max_snapshot_age_hours: int = 24) -> CohortRecord:
        """``snapshot`` is the report of ``recovery_cli snapshot`` taken before promotion. It must be a completed
        encrypted snapshot no older than ``max_snapshot_age_hours``; its hash is recorded in the audit event."""
        approver = self._actor(principal, organization_id)
        if not reason.strip():
            raise ValueError("a promotion reason is required")
        evidence = self._snapshot_evidence(snapshot, max_snapshot_age_hours)

        def work(tx):
            cohort = tx.cohorts.get(organization_id, cohort_id)
            if cohort.state != "SHADOW":
                raise CutoverRefusedError("COHORT_NOT_IN_SHADOW", {"state": cohort.state})
            if approver == cohort.created_by:
                raise CutoverRefusedError("MAKER_CHECKER_SAME_PERSON")
            readiness = self._readiness(tx, organization_id, cohort)      # re-checked inside the transaction
            if not readiness["ready"]:
                raise CutoverRefusedError("COHORT_NOT_READY", {"patients": [
                    row for row in readiness["patients"] if not row["ready"]]})
            members = []
            for member in cohort.members:
                patient = tx.patients.get(organization_id, member.patient_id)
                members.append(member.model_copy(update={"previous_authority": patient.authority_plane}))
                tx.patients.save(patient.model_copy(update={"authority_plane": "MONGO"}))
            cohort.members, cohort.state = members, "PILOT"
            cohort.promoted_by, cohort.promoted_at = approver, self._clock()
            saved = tx.cohorts.save(cohort, expected_revision=cohort.revision)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=approver, action="COHORT_PROMOTED_TO_PILOT",
                entity_type="TRIAL", entity_id=cohort.trial_id, reason=reason,
                after={"cohort_id": str(cohort.id), "members": len(members), "created_by": str(cohort.created_by),
                       "snapshot": evidence}))
            return saved
        return self._uow.run(work)

    def _snapshot_evidence(self, snapshot: dict[str, Any] | None, max_age_hours: int) -> dict[str, Any]:
        if not snapshot:
            raise CutoverRefusedError("SNAPSHOT_REQUIRED")
        digest, file_hash = str(snapshot.get("manifest_hash", "")), str(snapshot.get("encrypted_file_hash", ""))
        hex64 = lambda text: len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)
        if snapshot.get("status") != "ENCRYPTED_LOGICAL_SNAPSHOT" or not (hex64(digest) and file_hash):
            raise CutoverRefusedError("SNAPSHOT_INVALID")
        try:
            taken = datetime.fromisoformat(str(snapshot["created_at"]))
        except (KeyError, ValueError):
            raise CutoverRefusedError("SNAPSHOT_INVALID") from None
        if taken.tzinfo is None:
            raise CutoverRefusedError("SNAPSHOT_INVALID")
        age = (self._clock() - taken).total_seconds()
        if age < 0 or age > max_age_hours * 3600:
            raise CutoverRefusedError("SNAPSHOT_STALE", {"max_age_hours": max_age_hours})
        return {"manifest_hash": digest, "encrypted_file_hash": file_hash, "created_at": taken.isoformat()}

    # -- rollback ---------------------------------------------------------------------------
    def plane_activity_since_promotion(self, organization_id: UUID, cohort_id: UUID) -> dict[str, int]:
        return self._uow.read(lambda tx: self._activity(tx, organization_id, tx.cohorts.get(organization_id, cohort_id)))

    @staticmethod
    def _activity(tx, organization_id: UUID, cohort: CohortRecord) -> dict[str, int]:
        since = cohort.promoted_at
        commands = occurrences = evaluations = 0
        for member in cohort.members:
            commands += sum(1 for row in tx.ledger.list_for_patient(organization_id, member.patient_id)
                            if since is None or row.recorded_at >= since)
            occurrences += sum(1 for row in tx.facts.list_occurrences(member.patient_id)
                               if since is None or (row.recorded_at or since) >= since)
            patient = tx.patients.get(organization_id, member.patient_id)
            if patient.current_schedule_version_id:
                evaluations += sum(1 for row in tx.evaluations.list_evaluations(
                    patient.id, patient.current_schedule_version_id)
                    if since is None or row.evaluated_at >= since)
        return {"commands": commands, "occurrences": occurrences, "evaluations": evaluations}

    def rollback(self, principal: Principal, organization_id: UUID, cohort_id: UUID, *, reason: str,
                 accept_plane_changes_loss: bool = False) -> CohortRecord:
        actor = self._actor(principal, organization_id)
        if not reason.strip():
            raise ValueError("a rollback reason is required")

        def work(tx):
            cohort = tx.cohorts.get(organization_id, cohort_id)
            if cohort.state != "PILOT":
                raise CutoverRefusedError("COHORT_NOT_IN_PILOT", {"state": cohort.state})
            activity = self._activity(tx, organization_id, cohort)
            # A new evaluation by itself is not a clinical change; commands and facts are.
            changed = activity["commands"] + activity["occurrences"]
            if changed and not accept_plane_changes_loss:
                raise CutoverRefusedError("PLANE_CHANGES_WOULD_BE_LOST", activity)
            for member in cohort.members:
                patient = tx.patients.get(organization_id, member.patient_id)
                tx.patients.save(patient.model_copy(update={"authority_plane": member.previous_authority}))
            cohort.state, cohort.rolled_back_by, cohort.rolled_back_at = "ROLLED_BACK", actor, self._clock()
            saved = tx.cohorts.save(cohort, expected_revision=cohort.revision)
            tx.audit.append(AuditRecord(
                organization_id=organization_id, actor_id=actor, action="COHORT_ROLLED_BACK",
                entity_type="TRIAL", entity_id=cohort.trial_id, reason=reason,
                after={"cohort_id": str(cohort.id), "plane_activity": activity,
                       "accepted_loss": bool(changed and accept_plane_changes_loss)}))
            return saved
        return self._uow.run(work)

    # -- legacy write guard -------------------------------------------------------------------
    def legacy_write_block(self, external_patient_id: str) -> dict[str, str] | None:
        """None when the legacy store may still write; otherwise a SAFE explicit migration response."""
        cohort = self._uow.read(lambda tx: tx.cohorts.find_blocking_membership(external_patient_id))
        if cohort is None:
            return None
        return {"code": MIGRATED, "detail": "This patient's schedule is now managed in UCTSM; legacy schedule "
                                            "writes are refused. Use the UCTSM schedule screens.",
                "cohort_state": cohort.state}
