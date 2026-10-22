"""Wiring between the live upload routes (server.py) and the protocol identity gate.

Everything here is synchronous and driver-free except the lazily built Mongo
store. The async orchestration (storage, drafts, HTTP) stays in ``server.py``.

Identity store connection
-------------------------
Identity records live in the UCTSM MongoDB database, the same database the
repository ports use, so they never share a database with the legacy collections:

1. ``UCTSM_MONGO_URL`` + ``UCTSM_MONGO_DB`` when set (the documented settings);
2. otherwise the app's own cluster (``MONGO_URL``) and the database
   ``<DB_NAME>_uctsm`` - a distinct database, created on first use.

Migrations (including 0005) run once per process, lazily, and are idempotent.
Transactions need a replica set. When the store cannot be reached the gate FAILS
CLOSED: the upload is refused with 503 rather than extracted unverified.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.application.protocol_identity import (
    IdentityStore, PagesInput, ProtocolDocumentIdentity, ProtocolIdentityService, RegistryLookup,
    ReviewStatus,
)
from app.domain.protocol_identity import (
    ClaimSource, ExpectedClaim, IdentityClassification, claim_from_filename, claim_from_text,
)

log = logging.getLogger("mtb.protocol_identity")

REVIEW_PERMISSION = "protocol_identity.review"

_lock = threading.Lock()
_store: IdentityStore | None = None
_registry: RegistryLookup | None = None
_service: ProtocolIdentityService | None = None


def configure(*, store: IdentityStore | None = None, registry: RegistryLookup | None = None) -> None:
    """Install a store / registry provider (tests, or a future registry client)."""
    global _store, _registry, _service
    with _lock:
        _store, _registry, _service = store, registry, None


def _build_store() -> IdentityStore:
    from app.persistence.mongodb.client import build_client, get_database
    from app.persistence.mongodb.identity_repository import MongoIdentityStore
    from app.persistence.mongodb.migrate import run_migrations
    from app.persistence.mongodb.settings import MongoPostureError, MongoSettings, MongoSettingsError

    try:
        settings = MongoSettings.from_env()
    except MongoPostureError:
        raise                      # configured but unsafe: never fall back to another URL
    except MongoSettingsError:
        url, name = os.getenv("MONGO_URL", "").strip(), os.getenv("DB_NAME", "").strip()
        if not url or not name:
            raise HTTPException(
                503, "The protocol identity store is not configured (set UCTSM_MONGO_URL "
                     "and UCTSM_MONGO_DB). Protocols are not extracted unverified.")
        settings = MongoSettings(url=url, database=f"{name}_uctsm").enforce_environment_posture()
    client = build_client(settings, server_selection_timeout_ms=8000)
    database = get_database(client, settings)
    run_migrations(database)
    return MongoIdentityStore(client, database)


def service() -> ProtocolIdentityService:
    global _store, _service
    with _lock:
        if _service is None:
            if _store is None:
                try:
                    _store = _build_store()
                except HTTPException:
                    raise
                except Exception as error:  # noqa: BLE001
                    log.exception("protocol identity store unavailable")
                    raise HTTPException(
                        503, "The protocol identity store is unavailable "
                             f"({type(error).__name__}). Protocols are not extracted "
                             "unverified; try again shortly.") from error
            registry = _registry
            if registry is None:
                from app.services.clinicaltrials_registry import registry_from_environment
                registry = registry_from_environment()      # None -> NoRegistry (NOT_CHECKED)
            _service = ProtocolIdentityService(_store, registry)
        return _service


# ---------------------------------------------------------------------- inputs

def organization_uuid(value: Any) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise HTTPException(403, "No organization context is available") from error


def pages_input(pages) -> list[PagesInput]:
    """DocumentPage -> identity input. A scanned / scrambled / empty page is not
    ``usable`` and contributes nothing to the identity or the fingerprint."""
    return [PagesInput(
        number=page.page_number, text=page.text or "",
        usable=getattr(page, "text_status", "text") == "text" and bool(page.text.strip()))
        for page in pages]


def expected_claims(
    *, submitted: str | None, filename: str, trial_record_ids: list[str] | None = None,
) -> list[ExpectedClaim]:
    """The identities this file is claimed to be, strongest first.

    * ``submitted``: explicit user input (Add Trial's protocol ID field);
    * ``trial_record_ids``: persisted identifiers of the authorized trial;
    * a registry identifier printed in the FILE NAME (weakest). A file name with
      no identifier yields no claim - a name is never an invented identity.
    """
    claims: list[ExpectedClaim] = []
    for value in trial_record_ids or []:
        claim = claim_from_text(value, ClaimSource.TRIAL_RECORD)
        if claim:
            claims.append(claim)
    if submitted:
        claim = claim_from_text(submitted, ClaimSource.SUBMITTED)
        if claim:
            claims.append(claim)
    from_name = claim_from_filename(filename)
    if from_name:
        claims.append(from_name)
    unique: dict[tuple[str, str], ExpectedClaim] = {}
    for claim in claims:
        unique.setdefault((claim.normalized, claim.source.value), claim)
    return list(unique.values())


def clear_stored_document(
    *, organization_id: UUID, owner: str, filename: str, pages, binary_sha256: str,
    content_type: str, expected_values: list[str], trial_id: str | None = None,
    protocol_id: UUID | None = None, protocol_version_id: UUID | None = None,
    stage: str = "WORKER",
) -> ProtocolDocumentIdentity:
    """The gate for a document that is already stored (a UCTSM extraction run).

    Same pipeline as an upload - fingerprint, cited identity, classification -
    with the expected identity taken from persisted protocol records. Raises
    ``IdentityBlockedError`` BEFORE any model call when it does not pass."""
    from app.application.protocol_identity import IdentityBlockedError

    identity = service().assess(
        organization_id=organization_id, owner_user_id=owner, filename=filename,
        content_type=content_type, size_bytes=0, binary_sha256=binary_sha256,
        pages=pages_input(pages),
        expected=expected_claims(submitted=None, filename=filename,
                                 trial_record_ids=expected_values),
        trial_id=trial_id, protocol_id=protocol_id, protocol_version_id=protocol_version_id,
        stage=stage)
    if identity.blocked:
        raise IdentityBlockedError(identity)
    return identity


def blocked_identity_for(binary_sha256: str, organization_id: UUID) -> ProtocolDocumentIdentity | None:
    """A still-quarantined upload of exactly these bytes, if one exists. Used at
    queue time for fast feedback; the worker re-assesses regardless."""
    store = _store
    if store is None:
        return None
    from app.application.protocol_identity import ReviewStatus
    rows = store.read(lambda tx: tx.list_for_review(
        organization_id, review_status=ReviewStatus.PENDING_REVIEW, limit=200))
    for row in rows:
        if row.binary_sha256 == binary_sha256 and row.blocked:
            return row
    return None


# --------------------------------------------------------------------- outputs

def allowed_actions(identity: ProtocolDocumentIdentity) -> list[str]:
    if identity.review_status is not ReviewStatus.PENDING_REVIEW:
        return []
    actions = ["KEEP_QUARANTINED", "REJECT"]
    if identity.effective_classification is IdentityClassification.MISMATCH:
        actions.insert(0, "CORRECT_EXPECTED_IDENTITY")
    else:
        actions[0:0] = ["CONFIRM_DOCUMENT_IDENTITY", "CORRECT_EXPECTED_IDENTITY"]
    return actions


def identity_view(identity: ProtocolDocumentIdentity, *, detail: bool = False,
                  reviewer: bool = False) -> dict[str, Any]:
    """The API shape. Never includes storage keys; owner only for reviewers."""
    facts = identity.facts
    view: dict[str, Any] = {
        "id": str(identity.id),
        "classification": identity.effective_classification.value,
        "original_classification": identity.classification.value,
        "review_status": identity.review_status.value,
        "revision": identity.revision,
        "permitted": identity.permitted,
        "filename": identity.filename,
        "trial_id": identity.trial_id,
        "stage": identity.stage,
        "created_at": identity.created_at.isoformat(),
        "issue_codes": [item.code for item in identity.issues if item.severity.value == "BLOCKING"],
        "allowed_actions": allowed_actions(identity),
        "duplicate_of_id": str(identity.duplicate_of_id) if identity.duplicate_of_id else None,
        "predecessor_id": str(identity.predecessor_id) if identity.predecessor_id else None,
        "binary_sha256": identity.binary_sha256,
        "text_sha256": identity.text_fingerprint.sha256,
        "text_verified_full_text": identity.text_fingerprint.verified_full_text,
    }
    if reviewer:
        view["owner_user_id"] = identity.owner_user_id
    if detail:
        view.update({
            "issues": [item.model_dump(mode="json") for item in identity.issues],
            "expected": [item.model_dump(mode="json") for item in identity.expected],
            "document_identifiers": [item.model_dump(mode="json") for item in facts.identifiers],
            "title": facts.title.model_dump(mode="json") if facts.title else None,
            "sponsor": facts.sponsor.model_dump(mode="json") if facts.sponsor else None,
            "intervention": facts.intervention.model_dump(mode="json") if facts.intervention else None,
            "version_label": facts.version_label.model_dump(mode="json") if facts.version_label else None,
            "version_date": facts.version_date.isoformat() if facts.version_date else None,
            "registry": identity.registry.model_dump(mode="json"),
            "text_coverage": identity.text_fingerprint.coverage.model_dump(mode="json"),
            "normalization_version": identity.text_fingerprint.algorithm_version,
            "comparisons": identity.comparisons,
            "suggested_correction": identity.suggested_correction,
        })
    return view


def blocked_detail(identity: ProtocolDocumentIdentity) -> dict[str, Any]:
    """The 409 body for a quarantined upload. Distinct ``code`` and
    ``workflow_state`` so a client cannot mistake it for an extraction failure."""
    if identity.review_status is ReviewStatus.REJECTED:
        message = "This protocol document was rejected by a reviewer."
    elif identity.effective_classification is IdentityClassification.MISMATCH:
        message = ("This file does not appear to be the protocol it was submitted as. "
                   "It is held for review and was not extracted.")
    else:
        message = ("The identity of this protocol document could not be verified. "
                   "It is held for review and was not extracted.")
    return {
        "code": "PROTOCOL_IDENTITY_BLOCKED",
        "workflow_state": ("REJECTED" if identity.review_status is ReviewStatus.REJECTED
                           else "QUARANTINED"),
        "message": message,
        "identity": identity_view(identity, detail=True),
        "next_action": ("An authorized reviewer must resolve it from Protocol identity review."
                        if identity.review_status is ReviewStatus.PENDING_REVIEW else None),
    }
