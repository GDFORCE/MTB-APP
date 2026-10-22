"""P9-05: hot/cold archive of superseded evaluation projections.

Only the bulky per-visit projections (``patient_events`` + confinement episodes) of a
*superseded* evaluation move to cold storage. The immutable evaluation row, its input/output
hashes and the lineage stay in the hot store, so history and references remain verifiable.
The sealed payload is AES-256-GCM encrypted, bound to the evaluation id and its content hash,
and is read back and hash-verified BEFORE any hot data is removed. A legal/retention hold
blocks archiving. Nothing here runs unless an operator calls it; no default policy archives.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .archive_records import (
    ARCHIVED, FORMAT, HOT, RESTORED, ArchiveIntegrityError, ArchiveRecord, ArchiveRefusedError,
    EvaluationArchivedError)
from .errors import RecordNotFoundError
from .patients import ConfinementEpisodeRecord, PatientEventRecord
from .ports import UnitOfWork
from .records import AuditRecord, utc_now

MAX_BYTES = 64 * 1024 * 1024


class ArchiveStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...


class FileArchiveStore:
    """Encrypted-object store on a local directory (an operator mounts durable storage)."""

    def __init__(self, root: str | os.PathLike):
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root.resolve() not in path.parents:
            raise ArchiveIntegrityError("archive key escapes the store")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_bytes(data)
        os.replace(temp, path)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise ArchiveIntegrityError("archived payload is missing")
        return path.read_bytes()


@dataclass(frozen=True)
class ArchivePolicy:
    """Archive only evaluations at least ``min_age_days`` old. There is no default instance."""
    min_age_days: int

    def __post_init__(self):
        if self.min_age_days < 1:
            raise ValueError("a retention age of at least one day is required")


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _aad(evaluation_id: UUID, payload_hash: str) -> bytes:
    return f"{FORMAT}|{evaluation_id}|{payload_hash}".encode()


def _seal(key: bytes, evaluation_id: UUID, payload: bytes, payload_hash: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, payload, _aad(evaluation_id, payload_hash))


def _unseal(key: bytes, evaluation_id: UUID, blob: bytes, payload_hash: str) -> bytes:
    try:
        return AESGCM(key).decrypt(blob[:12], blob[12:], _aad(evaluation_id, payload_hash))
    except InvalidTag as error:
        raise ArchiveIntegrityError("archived payload failed authentication") from error


class EvaluationArchiveService:
    def __init__(self, uow: UnitOfWork, store: ArchiveStore, key: bytes):
        if len(key) != 32:
            raise ValueError("a 32-byte archive key is required")
        self._uow, self._store, self._key = uow, store, key

    @staticmethod
    def _audit(tx, organization_id, actor_id, action, evaluation_id, after, reason=None):
        tx.audit.append(AuditRecord(
            organization_id=organization_id, actor_id=actor_id, action=action,
            entity_type="EVALUATION", entity_id=evaluation_id, after=after, reason=reason))

    # -- holds -----------------------------------------------------------------------
    def place_hold(self, organization_id: UUID, evaluation_id: UUID, *, actor_id: UUID, reason: str):
        if not reason.strip():
            raise ValueError("a hold reason is required")

        def work(tx):
            evaluation = tx.evaluations.get_evaluation(evaluation_id)
            if evaluation.organization_id != organization_id:
                raise RecordNotFoundError("evaluation not found")
            row = tx.archives.get(evaluation_id) or ArchiveRecord(
                organization_id=organization_id, patient_id=evaluation.patient_id,
                evaluation_id=evaluation_id, state=HOT)
            row.hold, row.hold_reason, row.held_by = True, reason, actor_id
            tx.archives.save(row)
            self._audit(tx, organization_id, actor_id, "EVALUATION_ARCHIVE_HOLD_PLACED",
                        evaluation_id, {"state": row.state}, reason)
            return row
        return self._uow.run(work)

    def release_hold(self, organization_id: UUID, evaluation_id: UUID, *, actor_id: UUID, reason: str):
        if not reason.strip():
            raise ValueError("a release reason is required")

        def work(tx):
            row = tx.archives.get(evaluation_id)
            if row is None or row.organization_id != organization_id or not row.hold:
                raise RecordNotFoundError("no active hold")
            row.hold, row.hold_reason, row.held_by = False, None, None
            tx.archives.save(row)
            self._audit(tx, organization_id, actor_id, "EVALUATION_ARCHIVE_HOLD_RELEASED",
                        evaluation_id, {"state": row.state}, reason)
            return row
        return self._uow.run(work)

    # -- archive ---------------------------------------------------------------------
    def archive(self, organization_id: UUID, evaluation_id: UUID, *, actor_id: UUID,
                policy: ArchivePolicy, now: datetime | None = None) -> ArchiveRecord:
        now = now or datetime.now(timezone.utc)

        def read(tx):
            evaluation = tx.evaluations.get_evaluation(evaluation_id)
            if evaluation.organization_id != organization_id:
                raise RecordNotFoundError("evaluation not found")
            self._check_eligible(tx, evaluation, policy, now)
            return (evaluation, tx.evaluations.list_events(evaluation_id),
                    tx.evaluations.list_confinements(evaluation_id))
        evaluation, events, confinements = self._uow.read(read)

        payload = _canonical({
            "format": FORMAT, "evaluation_id": str(evaluation_id),
            "events": [item.model_dump(mode="json") for item in events],
            "confinements": [item.model_dump(mode="json") for item in confinements]})
        if len(payload) > MAX_BYTES:
            raise ArchiveRefusedError("projection payload exceeds the archive object limit")
        payload_hash = hashlib.sha256(payload).hexdigest()
        object_key = f"{organization_id}/{evaluation_id}.enc"
        self._store.put(object_key, _seal(self._key, evaluation_id, payload, payload_hash))
        # Verify the cold copy BEFORE removing anything from the hot store.
        check = _unseal(self._key, evaluation_id, self._store.get(object_key), payload_hash)
        if hashlib.sha256(check).hexdigest() != payload_hash:
            raise ArchiveIntegrityError("cold copy does not match the projections read")

        def commit(tx):
            current = tx.evaluations.get_evaluation(evaluation_id)
            self._check_eligible(tx, current, policy, now)    # re-check inside the transaction
            row = tx.archives.get(evaluation_id) or ArchiveRecord(
                organization_id=organization_id, patient_id=evaluation.patient_id,
                evaluation_id=evaluation_id, state=ARCHIVED)
            row.state, row.object_key, row.payload_hash = ARCHIVED, object_key, payload_hash
            row.event_count, row.confinement_count = len(events), len(confinements)
            row.archived_by, row.archived_at, row.restored_at = actor_id, now, None
            tx.archives.save(row)
            tx.evaluations.purge_projections(evaluation_id)
            self._audit(tx, organization_id, actor_id, "EVALUATION_ARCHIVED", evaluation_id,
                        {"payload_hash": payload_hash, "events": len(events),
                         "confinements": len(confinements)})
            return row
        return self._uow.run(commit)

    @staticmethod
    def _check_eligible(tx, evaluation, policy: ArchivePolicy, now: datetime):
        row = tx.archives.get(evaluation.id)
        if row is not None and row.hold:
            raise ArchiveRefusedError("the evaluation is under a hold")
        if row is not None and row.state == ARCHIVED:
            raise ArchiveRefusedError("the evaluation is already archived")
        if evaluation.status != "COMPLETED":
            raise ArchiveRefusedError("only a completed evaluation can be archived")
        pointer = tx.evaluations.get_pointer(evaluation.patient_id, evaluation.schedule_version_id)
        if pointer is not None and pointer.current_evaluation_id == evaluation.id:
            raise ArchiveRefusedError("the current evaluation is never archived")
        if evaluation.evaluated_at > now - timedelta(days=policy.min_age_days):
            raise ArchiveRefusedError("the evaluation is younger than the retention age")

    # -- restore / verify ------------------------------------------------------------
    def _load(self, row: ArchiveRecord) -> dict[str, Any]:
        blob = self._store.get(row.object_key)
        payload = _unseal(self._key, row.evaluation_id, blob, row.payload_hash)
        if hashlib.sha256(payload).hexdigest() != row.payload_hash:
            raise ArchiveIntegrityError("archived payload hash differs from the recorded hash")
        return json.loads(payload)

    def verify(self, organization_id: UUID, evaluation_id: UUID) -> dict[str, Any]:
        row = self._uow.read(lambda tx: tx.archives.get(evaluation_id))
        if row is None or row.organization_id != organization_id or row.state != ARCHIVED:
            raise RecordNotFoundError("evaluation is not archived")
        doc = self._load(row)
        if (len(doc["events"]), len(doc["confinements"])) != (row.event_count, row.confinement_count):
            raise ArchiveIntegrityError("archived record counts differ from the metadata")
        return {"evaluation_id": str(evaluation_id), "payload_hash": row.payload_hash,
                "events": row.event_count, "confinements": row.confinement_count, "verified": True}

    def restore(self, organization_id: UUID, evaluation_id: UUID, *, actor_id: UUID) -> ArchiveRecord:
        row = self._uow.read(lambda tx: tx.archives.get(evaluation_id))
        if row is None or row.organization_id != organization_id or row.state != ARCHIVED:
            raise RecordNotFoundError("evaluation is not archived")
        doc = self._load(row)
        events = [PatientEventRecord.model_validate(item) for item in doc["events"]]
        confinements = [ConfinementEpisodeRecord.model_validate(item) for item in doc["confinements"]]

        def work(tx):
            current = tx.archives.get(evaluation_id)
            if current is None or current.state != ARCHIVED:
                raise ArchiveRefusedError("the evaluation is no longer archived")
            tx.evaluations.restore_projections(evaluation_id, events, confinements)
            current.state, current.restored_at = RESTORED, utc_now()
            tx.archives.save(current)
            self._audit(tx, organization_id, actor_id, "EVALUATION_ARCHIVE_RESTORED", evaluation_id,
                        {"payload_hash": row.payload_hash, "events": len(events)})
            return current
        return self._uow.run(work)
