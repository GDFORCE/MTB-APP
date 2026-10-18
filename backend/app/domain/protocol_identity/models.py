"""Typed values for protocol identity (P2). Pure: no database, HTTP or model types."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class IdentityClassification(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNCERTAIN = "UNCERTAIN"
    DUPLICATE = "DUPLICATE"
    NEW_VERSION = "NEW_VERSION"


#: Classifications that let a document proceed to extraction / reuse.
PERMITTED = frozenset({
    IdentityClassification.MATCH, IdentityClassification.DUPLICATE,
    IdentityClassification.NEW_VERSION,
})
#: Classifications that quarantine the upload for a reviewer.
BLOCKING = frozenset({IdentityClassification.MISMATCH, IdentityClassification.UNCERTAIN})


class IdentifierKind(StrEnum):
    REGISTRY_NCT = "REGISTRY_NCT"
    REGISTRY_CTRI = "REGISTRY_CTRI"
    REGISTRY_EUDRACT = "REGISTRY_EUDRACT"
    REGISTRY_ISRCTN = "REGISTRY_ISRCTN"
    PROTOCOL_NUMBER = "PROTOCOL_NUMBER"

    @property
    def is_registry(self) -> bool:
        return self is not IdentifierKind.PROTOCOL_NUMBER


class _Value(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class IdentifierEvidence(_Value):
    """One identifier as the document prints it, with where and why it counts."""

    kind: IdentifierKind
    value: str                         # as printed
    normalized: str                    # comparison form
    page: int
    snippet: str
    #: True only for a statement of THIS protocol's own identity: front matter or a
    #: labelled identifier line, and not inside references / appendices.
    authoritative: bool
    context: str = ""                  # why it was / was not treated as authoritative


class TextEvidence(_Value):
    """A labelled descriptive field (title, sponsor, intervention, version...)."""

    value: str
    page: int
    snippet: str


class DocumentIdentityFacts(_Value):
    """Everything deterministic extraction could cite from the document."""

    identifiers: list[IdentifierEvidence] = Field(default_factory=list)
    title: TextEvidence | None = None
    sponsor: TextEvidence | None = None
    intervention: TextEvidence | None = None
    version_label: TextEvidence | None = None
    version_date: date | None = None
    version_date_evidence: TextEvidence | None = None

    def authoritative(self, *, registry: bool | None = None) -> list[IdentifierEvidence]:
        return [item for item in self.identifiers if item.authoritative and (
            registry is None or item.kind.is_registry == registry)]


class TextCoverage(_Value):
    pages_total: int
    pages_with_text: int
    pages_without_text: list[int] = Field(default_factory=list)


class TextFingerprint(_Value):
    """Full-document normalized-text fingerprint (not the model's page-marked
    prompt text, not a cropped snippet)."""

    algorithm_version: str
    sha256: str | None               # None when there is no usable text at all
    char_count: int
    coverage: TextCoverage
    #: True only when EVERY page contributed text. An incomplete fingerprint is
    #: kept for provenance but never counts as a verified full-document match.
    verified_full_text: bool


class ClaimSource(StrEnum):
    SUBMITTED = "SUBMITTED"                  # the user typed / chose it
    TRIAL_RECORD = "TRIAL_RECORD"            # persisted, authorized trial or protocol
    FILENAME = "FILENAME"                    # an identifier printed in the file name
    REVIEWER_CONFIRMED = "REVIEWER_CONFIRMED"


class ExpectedClaim(_Value):
    """An identity the caller says this document is."""

    kind: IdentifierKind
    value: str
    normalized: str
    source: ClaimSource


class RegistryStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    CONFLICT = "CONFLICT"
    NOT_FOUND = "NOT_FOUND"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_CHECKED = "NOT_CHECKED"


class RegistryComparison(_Value):
    """Provenance of an external registry check. ``NOT_CHECKED`` / ``UNAVAILABLE``
    are explicit, never a fabricated match."""

    status: RegistryStatus = RegistryStatus.NOT_CHECKED
    provider: str | None = None
    identifier: str | None = None
    checked_at: str | None = None
    detail: str = ""
    #: Registry-side descriptors (only set for a CONFIRMED record).
    title: str | None = None
    acronym: str | None = None
    organization: str | None = None


class IssueSeverity(StrEnum):
    BLOCKING = "BLOCKING"
    INFO = "INFO"


class IdentityIssue(_Value):
    code: str
    severity: IssueSeverity
    message: str
    page: int | None = None
    snippet: str | None = None


class PriorDocument(_Value):
    """What the classifier may know about an earlier ACCEPTED document in the
    caller's authorized scope. Contains no other tenant's data by construction."""

    identity_id: str
    identifiers: list[str]                  # authoritative, normalized
    text_sha256: str | None
    verified_full_text: bool
    version_label: str | None = None
    version_date: date | None = None
    created_at_iso: str = ""


class ClassificationInput(_Value):
    expected: list[ExpectedClaim] = Field(default_factory=list)
    facts: DocumentIdentityFacts
    fingerprint: TextFingerprint
    registry: RegistryComparison = Field(default_factory=RegistryComparison)
    priors: list[PriorDocument] = Field(default_factory=list)


class ClassificationResult(_Value):
    classification: IdentityClassification
    issues: list[IdentityIssue] = Field(default_factory=list)
    comparisons: list[dict] = Field(default_factory=list)
    duplicate_of: str | None = None
    predecessor: str | None = None
    #: A correction the evidence supports, offered to a reviewer. Never applied.
    suggested_correction: dict | None = None

    @property
    def permitted(self) -> bool:
        return self.classification in PERMITTED
