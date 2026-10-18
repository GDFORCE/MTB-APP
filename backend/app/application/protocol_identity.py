"""Protocol identity gate: records, store port and workflow (P2). No database imports.

Ordering enforced by callers through this service:

    validate -> binary hash -> text -> normalized fingerprint -> cited identity
    -> deterministic classification -> (quarantine | reuse | claim one extraction)

* An upload is an append-only provenance record. It is never unique by hash: the
  same file may be uploaded by several people and each upload keeps its own
  expected identity and decision.
* An extraction JOB is unique by (organization, content key, engine signature)
  (``claim_key``), enforced by the store, so concurrent uploads and repeated
  review resumes produce one model run.
* A reviewer decision is an append-only ``IdentityReviewRecord``; the decision,
  the identity's new revision and the durable resume intent commit together.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Callable, Protocol, TypeVar
from uuid import UUID, uuid4

from pydantic import Field

from app.domain.protocol_identity import (
    BLOCKING, PERMITTED, ClassificationInput, ClaimSource, DocumentIdentityFacts,
    ExpectedClaim, IdentifierKind, IdentityClassification, IdentityIssue, PriorDocument,
    RegistryComparison, RegistryStatus, TextFingerprint, classify_identity, extract_identity,
    fingerprint_pages, normalize_identifier,
)

from .errors import (
    DuplicateRecordError, PersistenceError, RecordNotFoundError, StaleRevisionError,
)
from .records import Record, utc_now

T = TypeVar("T")

SCHEMA_VERSION = 1
#: How long a claimed extraction may run before another request may take it over.
JOB_LEASE = timedelta(minutes=45)
_RETRIES = 3


class ReviewStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReviewAction(StrEnum):
    CORRECT_EXPECTED_IDENTITY = "CORRECT_EXPECTED_IDENTITY"
    CONFIRM_DOCUMENT_IDENTITY = "CONFIRM_DOCUMENT_IDENTITY"
    REJECT = "REJECT"
    KEEP_QUARANTINED = "KEEP_QUARANTINED"


class JobState(StrEnum):
    PENDING = "PENDING"        # a durable intent, not yet picked up
    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IdentityStateError(PersistenceError):
    """The identity is not in a state that allows this action (HTTP 409)."""


class ExtractionClaimLostError(IdentityStateError):
    """The worker's claim was taken over (its lease expired and another worker
    claimed a newer attempt). Its late result is obsolete and was discarded."""


class IdentityBlockedError(PersistenceError):
    """The document has not passed the identity gate (HTTP 409)."""

    def __init__(self, identity: "ProtocolDocumentIdentity"):
        codes = ", ".join(item.code for item in identity.issues if item.severity.value == "BLOCKING")
        super().__init__(
            f"ProtocolIdentityBlocked: {identity.effective_classification.value}"
            f"{f' ({codes})' if codes else ''}; identity {identity.id}; "
            "no extraction was run")
        self.identity = identity


# --------------------------------------------------------------------- records

class ProtocolDocumentIdentity(Record):
    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    owner_user_id: str
    #: Legacy (operational) trial id when uploading to an existing trial. A
    #: pre-trial Add Trial upload has none and none is invented.
    trial_id: str | None = None
    canonical_trial_id: UUID | None = None
    protocol_id: UUID | None = None
    protocol_version_id: UUID | None = None
    schema_version: int = SCHEMA_VERSION
    revision: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    filename: str
    content_type: str
    size_bytes: int
    storage_key: str | None = None
    stage: str = "UPLOAD"                      # UPLOAD | QUEUE | WORKER

    expected: list[ExpectedClaim] = Field(default_factory=list)
    facts: DocumentIdentityFacts
    binary_sha256: str
    text_fingerprint: TextFingerprint
    registry: RegistryComparison = Field(default_factory=RegistryComparison)

    classification: IdentityClassification
    issues: list[IdentityIssue] = Field(default_factory=list)
    comparisons: list[dict[str, Any]] = Field(default_factory=list)
    suggested_correction: dict[str, Any] | None = None
    duplicate_of_id: UUID | None = None
    predecessor_id: UUID | None = None

    review_status: ReviewStatus = ReviewStatus.NOT_REQUIRED
    #: The classification a reviewer-approved rerun produced (None until then).
    resolved_classification: IdentityClassification | None = None
    #: Normalized identifiers that count for deduplication - only set once the
    #: identity is permitted, so a quarantined upload never seeds a duplicate.
    accepted_identifiers: list[str] = Field(default_factory=list)
    extraction_result_id: UUID | None = None

    @property
    def effective_classification(self) -> IdentityClassification:
        return self.resolved_classification or self.classification

    @property
    def permitted(self) -> bool:
        """May proceed to extraction / reuse / consumption."""
        if self.review_status in {ReviewStatus.PENDING_REVIEW, ReviewStatus.REJECTED}:
            return False
        return self.effective_classification in PERMITTED

    @property
    def blocked(self) -> bool:
        return not self.permitted

    @property
    def content_key(self) -> str:
        """What 'the same document' means for extraction: the verified full-text
        fingerprint when it is complete, otherwise the exact bytes."""
        if self.text_fingerprint.verified_full_text and self.text_fingerprint.sha256:
            return f"text:{self.text_fingerprint.sha256}"
        return f"bin:{self.binary_sha256}"


class IdentityReviewRecord(Record):
    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    identity_id: UUID
    actor_id: str
    action: ReviewAction
    reason: str
    expected_revision: int
    resulting_revision: int
    classification_before: IdentityClassification
    classification_after: IdentityClassification | None = None
    review_status_after: ReviewStatus
    corrected_identifier: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class IdentityJobRecord(Record):
    """An extraction claim (``kind='EXTRACTION'``) or a durable review resume
    intent (``kind='RESUME'``). Unique per (organization, claim_key)."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    kind: str
    claim_key: str
    identity_id: UUID
    state: JobState
    revision: int = 1
    attempt: int = 0
    lease_expires_at: datetime | None = None
    result_id: UUID | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ExtractionResultRecord(Record):
    """A durable, reusable full-extraction result (unlike the 2-hour prepared
    draft). ``signature`` pins provider / prompt / reader / compiler versions."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    identity_id: UUID
    claim_key: str
    signature: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)


# ------------------------------------------------------------------ store port

class IdentityTx(Protocol):
    def add_identity(self, record: ProtocolDocumentIdentity) -> None: ...
    def get_identity(self, organization_id: UUID, identity_id: UUID) -> ProtocolDocumentIdentity: ...
    def save_identity(self, record: ProtocolDocumentIdentity, *, expected_revision: int) -> None: ...
    def list_accepted(
        self, organization_id: UUID, identifiers: list[str],
    ) -> list[ProtocolDocumentIdentity]: ...
    def list_for_review(
        self, organization_id: UUID, *, review_status: ReviewStatus | None, limit: int,
    ) -> list[ProtocolDocumentIdentity]: ...
    def add_review(self, record: IdentityReviewRecord) -> None: ...
    def list_reviews(self, organization_id: UUID, identity_id: UUID) -> list[IdentityReviewRecord]: ...
    def find_job(self, organization_id: UUID, claim_key: str) -> IdentityJobRecord | None: ...
    def add_job(self, record: IdentityJobRecord) -> None: ...
    def save_job(self, record: IdentityJobRecord, *, expected_revision: int) -> None: ...
    def add_result(self, record: ExtractionResultRecord) -> None: ...
    def get_result(self, organization_id: UUID, result_id: UUID) -> ExtractionResultRecord: ...


class IdentityStore(Protocol):
    def run(self, fn: Callable[[IdentityTx], T]) -> T: ...
    def read(self, fn: Callable[[IdentityTx], T]) -> T: ...


class RegistryLookup(Protocol):
    """External registry verification. No production provider exists yet; the
    app's Mongo ``protocol_registry`` is NOT one and is never used as one."""

    def lookup(self, identifier: str, *, kind: IdentifierKind) -> RegistryComparison: ...


class NoRegistry:
    def lookup(self, identifier: str, *, kind: IdentifierKind) -> RegistryComparison:
        return RegistryComparison(
            status=RegistryStatus.NOT_CHECKED, provider=None, identifier=identifier,
            detail="No external registry provider is configured.")


# --------------------------------------------------------------- service types

class PagesInput(Record):
    """Per-page text as read from the file; ``usable`` is False for scanned or
    unreadable pages (their text, if any, is not trusted)."""

    number: int
    text: str
    usable: bool


class ExtractionPlan(Record):
    kind: str                                    # REUSE | RUN | IN_PROGRESS | BLOCKED
    identity: ProtocolDocumentIdentity
    job: IdentityJobRecord | None = None
    result: ExtractionResultRecord | None = None
    claim_key: str | None = None


class ResolveOutcome(Record):
    identity: ProtocolDocumentIdentity
    review: IdentityReviewRecord
    resume_intent_id: UUID | None = None
    replayed: bool = False


def engine_claim_key(identity: ProtocolDocumentIdentity, signature: str) -> str:
    digest = hashlib.sha256(f"{identity.content_key}|{signature}".encode()).hexdigest()
    return f"extract:{digest}"


def prior_documents(rows: list[ProtocolDocumentIdentity]) -> list[PriorDocument]:
    """Earlier accepted documents as the classifier sees them. A document that was
    itself recognised as a duplicate is not a separate protocol, so lineage and
    deduplication always point at its source."""
    rows = [row for row in rows if row.duplicate_of_id is None]
    return [PriorDocument(
        identity_id=str(row.id),
        identifiers=list(row.accepted_identifiers),
        text_sha256=row.text_fingerprint.sha256,
        verified_full_text=row.text_fingerprint.verified_full_text,
        version_label=row.facts.version_label.value if row.facts.version_label else None,
        version_date=row.facts.version_date,
        created_at_iso=row.created_at.isoformat()) for row in rows]


class ProtocolIdentityService:
    def __init__(
        self, store: IdentityStore, registry: RegistryLookup | None = None,
        clock: Callable[[], datetime] = utc_now,
    ):
        self._store = store
        self._registry = registry or NoRegistry()
        self._clock = clock

    def _run(self, work: Callable[[IdentityTx], T]) -> T:
        last: Exception | None = None
        for _ in range(_RETRIES):
            try:
                return self._store.run(work)
            except StaleRevisionError as error:
                last = error
        assert last is not None
        raise last

    # ----------------------------------------------------------------- assess
    def assess(
        self, *, organization_id: UUID, owner_user_id: str, filename: str, content_type: str,
        size_bytes: int, binary_sha256: str, pages: list[PagesInput],
        expected: list[ExpectedClaim], trial_id: str | None = None,
        canonical_trial_id: UUID | None = None, protocol_id: UUID | None = None,
        protocol_version_id: UUID | None = None, storage_key: str | None = None,
        identity_id: UUID | None = None, stage: str = "UPLOAD",
    ) -> ProtocolDocumentIdentity:
        """Run the identity pipeline on already-validated, already-read pages and
        persist the decision. Makes no extraction call."""
        fingerprint = fingerprint_pages([(p.number, p.text, p.usable) for p in pages])
        facts = extract_identity(
            [(p.number, p.text) for p in pages if p.usable],
            expected_numbers=[claim.value for claim in expected
                              if claim.kind is IdentifierKind.PROTOCOL_NUMBER
                              and claim.source is not ClaimSource.FILENAME])
        registry = self._registry_for(facts, expected)
        identifiers = sorted({item.normalized for item in facts.authoritative()} | {
            claim.normalized for claim in expected})
        priors = self._store.read(lambda tx: prior_documents(
            tx.list_accepted(organization_id, identifiers) if identifiers else []))
        result = classify_identity(ClassificationInput(
            expected=expected, facts=facts, fingerprint=fingerprint, registry=registry,
            priors=priors))
        blocked = result.classification in BLOCKING
        record = ProtocolDocumentIdentity(
            **({"id": identity_id} if identity_id else {}),
            organization_id=organization_id, owner_user_id=owner_user_id, trial_id=trial_id,
            canonical_trial_id=canonical_trial_id, protocol_id=protocol_id,
            protocol_version_id=protocol_version_id, filename=filename, content_type=content_type,
            size_bytes=size_bytes, storage_key=storage_key, stage=stage, expected=expected,
            facts=facts, binary_sha256=binary_sha256, text_fingerprint=fingerprint,
            registry=registry, classification=result.classification, issues=result.issues,
            comparisons=result.comparisons, suggested_correction=result.suggested_correction,
            duplicate_of_id=UUID(result.duplicate_of) if result.duplicate_of else None,
            predecessor_id=UUID(result.predecessor) if result.predecessor else None,
            review_status=ReviewStatus.PENDING_REVIEW if blocked else ReviewStatus.NOT_REQUIRED,
            accepted_identifiers=([] if blocked else identifiers))
        self._store.run(lambda tx: tx.add_identity(record))
        return record

    def _registry_for(
        self, facts: DocumentIdentityFacts, expected=(),
    ) -> RegistryComparison:
        registry_ids = facts.authoritative(registry=True)
        if not registry_ids:
            # The document names no registry id of its own. Look up the identifier it
            # is CLAIMED to be (submitted / trial record) so the registry's title and
            # organization can corroborate - never to promote the claim to a fact.
            claimed = next((claim for claim in expected
                            if claim.kind.is_registry and claim.source is not ClaimSource.FILENAME), None)
            if claimed is None:
                return RegistryComparison(
                    status=RegistryStatus.NOT_CHECKED,
                    detail="The document states no registry identifier.")
            try:
                return self._registry.lookup(claimed.normalized, kind=claimed.kind)
            except Exception as error:  # noqa: BLE001
                return RegistryComparison(
                    status=RegistryStatus.UNAVAILABLE, identifier=claimed.normalized,
                    detail=f"Registry lookup failed: {type(error).__name__}")
        first = registry_ids[0]
        try:
            return self._registry.lookup(first.normalized, kind=first.kind)
        except Exception as error:  # noqa: BLE001 - a registry outage is data, not a crash
            return RegistryComparison(
                status=RegistryStatus.UNAVAILABLE, identifier=first.normalized,
                detail=f"Registry lookup failed: {type(error).__name__}")

    # ------------------------------------------------------- extraction claim
    def begin_extraction(self, identity: ProtocolDocumentIdentity, signature: str) -> ExtractionPlan:
        """Decide, atomically, whether to reuse a stored result, run one
        extraction, or report that one is already running."""
        if identity.blocked:
            return ExtractionPlan(kind="BLOCKED", identity=identity)
        key = engine_claim_key(identity, signature)
        org = identity.organization_id

        def work(tx: IdentityTx) -> ExtractionPlan:
            now = self._clock()
            job = tx.find_job(org, key)
            if job is None:
                job = IdentityJobRecord(
                    organization_id=org, kind="EXTRACTION", claim_key=key, identity_id=identity.id,
                    state=JobState.CLAIMED, attempt=1, lease_expires_at=now + JOB_LEASE)
                tx.add_job(job)           # DuplicateRecordError -> someone else claimed it
                return ExtractionPlan(kind="RUN", identity=identity, job=job, claim_key=key)
            if job.state is JobState.COMPLETED and job.result_id is not None:
                result = tx.get_result(org, job.result_id)
                return ExtractionPlan(kind="REUSE", identity=identity, job=job, result=result,
                                      claim_key=key)
            if job.state is JobState.CLAIMED and job.lease_expires_at and job.lease_expires_at > now:
                return ExtractionPlan(kind="IN_PROGRESS", identity=identity, job=job, claim_key=key)
            # FAILED, PENDING or an expired lease: take it over exactly once (CAS on revision).
            taken = job.model_copy(update={
                "state": JobState.CLAIMED, "attempt": job.attempt + 1, "error": None,
                "lease_expires_at": now + JOB_LEASE, "updated_at": now, "revision": job.revision + 1})
            tx.save_job(taken, expected_revision=job.revision)
            return ExtractionPlan(kind="RUN", identity=identity, job=taken, claim_key=key)

        try:
            return self._run(work)
        except DuplicateRecordError:
            # Lost the claim race: report what the winner is doing.
            return self._store.read(lambda tx: self._observe(tx, identity, key))
        except StaleRevisionError:
            return self._store.read(lambda tx: self._observe(tx, identity, key))

    def _observe(self, tx: IdentityTx, identity: ProtocolDocumentIdentity, key: str) -> ExtractionPlan:
        job = tx.find_job(identity.organization_id, key)
        if job is not None and job.state is JobState.COMPLETED and job.result_id is not None:
            return ExtractionPlan(
                kind="REUSE", identity=identity, job=job, claim_key=key,
                result=tx.get_result(identity.organization_id, job.result_id))
        return ExtractionPlan(kind="IN_PROGRESS", identity=identity, job=job, claim_key=key)

    @staticmethod
    def _owns(job: IdentityJobRecord | None, plan: "ExtractionPlan") -> bool:
        """The caller still holds THE attempt it was given: the job is CLAIMED and
        its attempt number is the plan's. Reading the newest revision is not
        ownership - a takeover bumps the attempt."""
        return (job is not None and plan.job is not None and job.state is JobState.CLAIMED
                and job.attempt == plan.job.attempt)

    def renew_extraction(self, plan: ExtractionPlan) -> None:
        """Extend the lease of the attempt this worker holds (obsolete -> raises)."""
        assert plan.job is not None and plan.claim_key is not None

        def work(tx: IdentityTx) -> None:
            job = tx.find_job(plan.identity.organization_id, plan.claim_key)
            if not self._owns(job, plan):
                raise ExtractionClaimLostError("this extraction attempt was taken over")
            now = self._clock()
            tx.save_job(job.model_copy(update={
                "lease_expires_at": now + JOB_LEASE, "updated_at": now,
                "revision": job.revision + 1}), expected_revision=job.revision)

        self._run(work)

    def complete_extraction(
        self, plan: ExtractionPlan, *, signature: str, payload: dict[str, Any],
    ) -> ExtractionResultRecord:
        assert plan.job is not None and plan.claim_key is not None
        identity = plan.identity

        def work(tx: IdentityTx) -> ExtractionResultRecord:
            job = tx.find_job(identity.organization_id, plan.claim_key)
            if (job is not None and job.state is JobState.COMPLETED and job.result_id is not None
                    and plan.job is not None and job.attempt == plan.job.attempt):
                # a retry after a lost response, by the SAME winning claim
                return tx.get_result(identity.organization_id, job.result_id)
            if not self._owns(job, plan):
                raise ExtractionClaimLostError(
                    "this extraction attempt was taken over; its result was discarded")
            result = ExtractionResultRecord(
                organization_id=identity.organization_id, identity_id=identity.id,
                claim_key=plan.claim_key, signature=signature, payload=payload)
            tx.add_result(result)
            assert job is not None
            tx.save_job(job.model_copy(update={
                "state": JobState.COMPLETED, "result_id": result.id, "error": None,
                "lease_expires_at": None, "updated_at": self._clock(),
                "revision": job.revision + 1}), expected_revision=job.revision)
            current = tx.get_identity(identity.organization_id, identity.id)
            tx.save_identity(current.model_copy(update={
                "extraction_result_id": result.id, "revision": current.revision + 1,
                "updated_at": self._clock()}), expected_revision=current.revision)
            return result

        return self._run(work)

    def fail_extraction(self, plan: ExtractionPlan, error: str) -> None:
        if plan.job is None or plan.claim_key is None:
            return

        def work(tx: IdentityTx) -> None:
            job = tx.find_job(plan.identity.organization_id, plan.claim_key)
            if job is None or job.state is JobState.COMPLETED or not self._owns(job, plan):
                return          # an obsolete worker's failure must not touch a newer claim
            tx.save_job(job.model_copy(update={
                "state": JobState.FAILED, "error": error[:1000], "lease_expires_at": None,
                "updated_at": self._clock(), "revision": job.revision + 1}),
                expected_revision=job.revision)

        self._run(work)

    def attach_storage(
        self, identity: ProtocolDocumentIdentity, storage_key: str,
    ) -> ProtocolDocumentIdentity:
        """Record where a quarantined upload's bytes are kept (private, for review)."""
        def work(tx: IdentityTx) -> ProtocolDocumentIdentity:
            current = tx.get_identity(identity.organization_id, identity.id)
            updated = current.model_copy(update={
                "storage_key": storage_key, "revision": current.revision + 1,
                "updated_at": self._clock()})
            tx.save_identity(updated, expected_revision=current.revision)
            return updated

        return self._run(work)

    # ----------------------------------------------------------------- reading
    def get(self, organization_id: UUID, identity_id: UUID) -> ProtocolDocumentIdentity:
        return self._store.read(lambda tx: tx.get_identity(organization_id, identity_id))

    def result(self, organization_id: UUID, result_id: UUID) -> ExtractionResultRecord:
        return self._store.read(lambda tx: tx.get_result(organization_id, result_id))

    def detail(self, organization_id: UUID, identity_id: UUID) -> tuple[
            ProtocolDocumentIdentity, list[IdentityReviewRecord]]:
        def work(tx: IdentityTx):
            return (tx.get_identity(organization_id, identity_id),
                    tx.list_reviews(organization_id, identity_id))
        return self._store.read(work)

    def list_for_review(
        self, organization_id: UUID, *, review_status: ReviewStatus | None = ReviewStatus.PENDING_REVIEW,
        limit: int = 100,
    ) -> list[ProtocolDocumentIdentity]:
        return self._store.read(lambda tx: tx.list_for_review(
            organization_id, review_status=review_status, limit=min(max(limit, 1), 200)))

    def require_permitted(
        self, organization_id: UUID, identity_id: UUID, *, trial_id: str | None = None,
    ) -> ProtocolDocumentIdentity:
        """Consumption / worker recheck: the identity must exist in this
        organization, have passed (or been approved), and not belong to another trial."""
        try:
            identity = self.get(organization_id, identity_id)
        except RecordNotFoundError as error:
            raise IdentityBlockedError(_missing_identity(organization_id)) from error
        if identity.blocked:
            raise IdentityBlockedError(identity)
        if trial_id and identity.trial_id and identity.trial_id != trial_id:
            raise IdentityStateError("This protocol document was verified for another trial")
        return identity

    # ----------------------------------------------------------------- review
    def resolve(
        self, *, organization_id: UUID, identity_id: UUID, actor_id: str, action: ReviewAction,
        reason: str, expected_revision: int, corrected_identifier: str | None = None,
    ) -> ResolveOutcome:
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("A reason is required to resolve a quarantined protocol document")

        def work(tx: IdentityTx) -> ResolveOutcome:
            identity = tx.get_identity(organization_id, identity_id)
            reviews = tx.list_reviews(organization_id, identity_id)
            if identity.review_status is not ReviewStatus.PENDING_REVIEW:
                # An identical repeat of the decision that resolved it is a replay.
                last = reviews[-1] if reviews else None
                if last and last.actor_id == actor_id and last.action is action \
                        and last.corrected_identifier == corrected_identifier \
                        and last.expected_revision == expected_revision:
                    intent = tx.find_job(organization_id, f"resume:{identity_id}")
                    return ResolveOutcome(identity=identity, review=last, replayed=True,
                                          resume_intent_id=intent.id if intent else None)
                raise IdentityStateError(
                    f"This identity is already {identity.review_status.value.lower()}")
            if identity.revision != expected_revision:
                raise StaleRevisionError(
                    "The identity changed since it was loaded; reload it before resolving")
            now = self._clock()
            before = identity.effective_classification

            if action is ReviewAction.KEEP_QUARANTINED:
                updated = identity.model_copy(update={
                    "revision": identity.revision + 1, "updated_at": now})
                status_after, after = identity.review_status, None
            elif action is ReviewAction.REJECT:
                updated = identity.model_copy(update={
                    "review_status": ReviewStatus.REJECTED, "revision": identity.revision + 1,
                    "updated_at": now})
                status_after, after = ReviewStatus.REJECTED, None
            else:
                updated, after = self._approve(
                    tx, identity, action, corrected_identifier, now)
                status_after = ReviewStatus.APPROVED

            review = IdentityReviewRecord(
                organization_id=organization_id, identity_id=identity_id, actor_id=actor_id,
                action=action, reason=reason, expected_revision=expected_revision,
                resulting_revision=updated.revision, classification_before=before,
                classification_after=after, review_status_after=status_after,
                corrected_identifier=corrected_identifier,
                evidence={"issues": [item.code for item in identity.issues],
                          "comparisons": identity.comparisons,
                          "binary_sha256": identity.binary_sha256,
                          "text_sha256": identity.text_fingerprint.sha256})
            tx.save_identity(updated, expected_revision=identity.revision)
            tx.add_review(review)
            intent_id = None
            if status_after is ReviewStatus.APPROVED:
                # The durable resume intent commits WITH the approval: approval
                # without an intent cannot exist, and one intent exists per identity.
                intent = IdentityJobRecord(
                    organization_id=organization_id, kind="RESUME",
                    claim_key=f"resume:{identity_id}", identity_id=identity_id,
                    state=JobState.PENDING)
                tx.add_job(intent)
                intent_id = intent.id
            return ResolveOutcome(identity=updated, review=review, resume_intent_id=intent_id)

        return self._run(work)

    def _approve(
        self, tx: IdentityTx, identity: ProtocolDocumentIdentity, action: ReviewAction,
        corrected_identifier: str | None, now: datetime,
    ) -> tuple[ProtocolDocumentIdentity, IdentityClassification]:
        if not corrected_identifier or not corrected_identifier.strip():
            raise ValueError("The identifier the reviewer is confirming is required")
        from app.domain.protocol_identity import claim_from_text

        claim = claim_from_text(corrected_identifier, ClaimSource.REVIEWER_CONFIRMED)
        assert claim is not None
        stated = [item for item in identity.facts.authoritative() if item.kind is claim.kind]
        if action is ReviewAction.CORRECT_EXPECTED_IDENTITY and not any(
                item.normalized == claim.normalized for item in stated):
            raise ValueError(
                f"{claim.normalized} is not an identifier the document states as its own; "
                "use a confirmed identity instead or reject the upload")
        if stated and all(item.normalized != claim.normalized for item in stated):
            raise ValueError(
                f"The document identifies itself as {stated[0].normalized}; "
                f"{claim.normalized} contradicts that")
        # Re-run the same deterministic classifier with the reviewer's claim
        # standing in for the original expectation. Priors exclude this identity.
        identifiers = sorted({claim.normalized} | {
            item.normalized for item in identity.facts.authoritative()})
        priors = [item for item in prior_documents(tx.list_accepted(
            identity.organization_id, identifiers)) if item.identity_id != str(identity.id)]
        rerun = classify_identity(ClassificationInput(
            expected=[claim], facts=identity.facts, fingerprint=identity.text_fingerprint,
            registry=identity.registry, priors=priors))
        if rerun.classification in BLOCKING:
            codes = ", ".join(item.code for item in rerun.issues if item.severity.value == "BLOCKING")
            raise ValueError(
                f"The evidence still does not support accepting this document ({codes})")
        return identity.model_copy(update={
            "review_status": ReviewStatus.APPROVED, "resolved_classification": rerun.classification,
            "expected": [*identity.expected, claim],
            "duplicate_of_id": UUID(rerun.duplicate_of) if rerun.duplicate_of else None,
            "predecessor_id": UUID(rerun.predecessor) if rerun.predecessor else None,
            "accepted_identifiers": identifiers, "revision": identity.revision + 1,
            "updated_at": now}), rerun.classification

    # ------------------------------------------------------------ resume intent
    def claim_resume(self, organization_id: UUID, identity_id: UUID) -> IdentityJobRecord | None:
        """Take the durable resume intent exactly once. None when there is none, it
        is already running, or it already completed."""
        def work(tx: IdentityTx) -> IdentityJobRecord | None:
            job = tx.find_job(organization_id, f"resume:{identity_id}")
            now = self._clock()
            if job is None or job.state is JobState.COMPLETED:
                return None
            if job.state is JobState.CLAIMED and job.lease_expires_at and job.lease_expires_at > now:
                return None
            taken = job.model_copy(update={
                "state": JobState.CLAIMED, "attempt": job.attempt + 1, "error": None,
                "lease_expires_at": now + JOB_LEASE, "updated_at": now, "revision": job.revision + 1})
            tx.save_job(taken, expected_revision=job.revision)
            return taken

        try:
            return self._run(work)
        except StaleRevisionError:
            return None

    def finish_resume(self, job: IdentityJobRecord, *, error: str | None = None) -> None:
        def work(tx: IdentityTx) -> None:
            current = tx.find_job(job.organization_id, job.claim_key)
            if current is None:
                return
            state = JobState.FAILED if error else JobState.COMPLETED
            tx.save_job(current.model_copy(update={
                "state": state, "error": (error or "")[:1000] or None, "lease_expires_at": None,
                "updated_at": self._clock(), "revision": current.revision + 1}),
                expected_revision=current.revision)

        self._run(work)


def _missing_identity(organization_id: UUID) -> ProtocolDocumentIdentity:
    """A placeholder describing 'no verified identity', carrying no tenant data."""
    from app.domain.protocol_identity import (
        TextCoverage, TextFingerprint as _Fingerprint,
    )
    return ProtocolDocumentIdentity(
        organization_id=organization_id, owner_user_id="", filename="", content_type="",
        size_bytes=0, facts=DocumentIdentityFacts(), binary_sha256="",
        text_fingerprint=_Fingerprint(
            algorithm_version="", sha256=None, char_count=0,
            coverage=TextCoverage(pages_total=0, pages_with_text=0), verified_full_text=False),
        classification=IdentityClassification.UNCERTAIN,
        review_status=ReviewStatus.PENDING_REVIEW,
        issues=[IdentityIssue(
            code="IDENTITY_NOT_FOUND", severity="BLOCKING",  # type: ignore[arg-type]
            message="No verified identity exists for this protocol document.")])
