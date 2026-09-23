"""PDF digital-signature validation — spec §10.3, at the full scope required
by Production-Grade Implementation Plan v1.3 correction #11.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE: "a signature is present" is never
"a signature is trusted". `SignatureTrust.PRESENT_UNVERIFIED` and
`SignatureTrust.TRUSTED` are different values precisely so that no caller can
accidentally collapse them, and only TRUSTED may contribute to a document
reaching ACCEPTED.

Every check correction #11 names is performed and reported individually:

  1. Cryptographic integrity        -> `intact`
  2. Chain to a CONFIGURED anchor   -> `chain_trusted` (no anchors configured
                                       means UNKNOWN, never trusted)
  3. Signing time / trusted TS      -> `timestamp_trusted`, `signing_time`
  4. Cert validity AT SIGNING TIME  -> validation runs at the signing moment,
                                       not at `now`, so "expired today but
                                       valid when signed" is accepted rather
                                       than wrongly rejected
  5. Revocation                     -> `revocation_checked` / `revoked`
  6. Current-revision coverage      -> `covers_whole_document`,
                                       `modification_level`

Trust anchors are per-jurisdiction configuration (Phase 0's jurisdiction
config), read from `REMOTE_AUDIT_TRUST_ANCHORS_DIR`. There is deliberately no
built-in anchor set and no fallback to the OS trust store: silently trusting
whatever a machine happens to trust would make "trusted" mean something
different on every deployment.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

TRUST_ANCHORS_DIR_ENV = "REMOTE_AUDIT_TRUST_ANCHORS_DIR"
REVOCATION_MODE_ENV = "REMOTE_AUDIT_SIGNATURE_REVOCATION_MODE"  # soft-fail | hard-fail | require


class SignatureTrust(str, Enum):
    #: No signature field is embedded at all.
    NONE = "NONE"
    #: Signature bytes verify against the document and chain to a configured
    #: trust anchor, at the signing time, with coverage intact.
    TRUSTED = "TRUSTED"
    #: A signature exists and may even be cryptographically intact, but it
    #: could not be shown to be trustworthy — no anchors configured, an
    #: unverifiable signing time, an unknown issuer. Routes to REVIEW_REQUIRED.
    PRESENT_UNVERIFIED = "PRESENT_UNVERIFIED"
    #: Actively bad: broken cryptography, a revoked certificate, or content
    #: changed after signing.
    INVALID = "INVALID"
    #: The validator itself could not run (missing library, internal error).
    #: Never conflated with "no signature" or with "untrusted".
    UNKNOWN = "UNKNOWN"


@dataclass
class SignatureFinding:
    field_name: str
    trust: SignatureTrust
    intact: bool
    chain_trusted: bool
    covers_whole_document: bool
    modification_level: Optional[str]
    signing_time: Optional[str]
    timestamp_trusted: bool
    revocation_checked: bool
    revoked: bool
    signer_common_name: Optional[str]
    signer_cert_not_before: Optional[str]
    signer_cert_not_after: Optional[str]
    cert_valid_at_signing_time: bool
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = dict(self.__dict__)
        data["trust"] = self.trust.value
        return data


@dataclass
class SignatureReport:
    overall: SignatureTrust
    signatures: List[SignatureFinding] = field(default_factory=list)
    anchors_configured: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall": self.overall.value,
            "anchors_configured": self.anchors_configured,
            "error": self.error,
            "signatures": [s.to_dict() for s in self.signatures],
        }


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return None


def load_trust_anchors(directory: Optional[str] = None) -> List[Any]:
    """Load PEM/DER CA certificates from the configured anchor directory.

    Returns an empty list when nothing is configured — which the caller must
    treat as "cannot establish trust", NOT as "trust everything" and not as
    "trust nothing is signed". An unreadable individual file is skipped with a
    log line rather than failing the whole load, so one corrupt anchor can't
    disable signature validation for every deployment sharing the directory.
    """
    path_str = directory or os.environ.get(TRUST_ANCHORS_DIR_ENV)
    if not path_str:
        return []
    root = Path(path_str)
    if not root.is_dir():
        log.warning("remote-audit trust anchor dir %s does not exist; no anchors loaded", path_str)
        return []

    from asn1crypto import pem, x509 as asn1_x509

    anchors: List[Any] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_file() or entry.suffix.lower() not in (".pem", ".crt", ".cer", ".der"):
            continue
        try:
            raw = entry.read_bytes()
            if pem.detect(raw):
                for unarmored in pem.unarmor(raw, multiple=True):
                    anchors.append(asn1_x509.Certificate.load(unarmored[2]))
            else:
                anchors.append(asn1_x509.Certificate.load(raw))
        except Exception as exc:  # noqa: BLE001 -- one bad anchor file must not disable validation
            log.warning("skipping unreadable trust anchor %s: %s", entry.name, exc)
    return anchors


def _common_name(cert: Any) -> Optional[str]:
    try:
        return cert.subject.native.get("common_name")
    except Exception:  # noqa: BLE001
        return None


def _coverage_is_whole_document(status: Any) -> bool:
    """True only when the signature covers the entire file as it stands now.

    Correction #11's "current-revision coverage": a signature over an earlier
    revision of an incrementally-updated PDF says nothing about what the
    reviewer is looking at today, so anything short of ENTIRE_FILE is reported
    as not covering the document.
    """
    try:
        from pyhanko.sign.validation import SignatureCoverageLevel
    except ImportError:
        return False
    return getattr(status, "coverage", None) == SignatureCoverageLevel.ENTIRE_FILE


def _revocation_mode() -> str:
    mode = (os.environ.get(REVOCATION_MODE_ENV) or "soft-fail").strip().lower()
    return mode if mode in ("soft-fail", "hard-fail", "require") else "soft-fail"


def validate_pdf_signatures(data: bytes, *, trust_anchors_dir: Optional[str] = None) -> SignatureReport:
    """Validate every embedded signature in `data`.

    Returns a report; never raises. A validator that throws on a hostile file
    would be an availability bug in the worker, and an exception escaping here
    could otherwise be caught somewhere upstream and mistaken for "unsigned".
    """
    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko_certvalidator import ValidationContext
    except ImportError as exc:
        return SignatureReport(overall=SignatureTrust.UNKNOWN,
                               error=f"signature validator unavailable: {exc}")

    anchors = load_trust_anchors(trust_anchors_dir)

    import io

    try:
        reader = PdfFileReader(io.BytesIO(data), strict=False)
        embedded = list(reader.embedded_signatures)
    except Exception as exc:  # noqa: BLE001 -- malformed PDFs are input, not incidents
        return SignatureReport(overall=SignatureTrust.UNKNOWN, anchors_configured=len(anchors),
                               error=f"could not read signatures: {type(exc).__name__}: {exc}")

    if not embedded:
        return SignatureReport(overall=SignatureTrust.NONE, anchors_configured=len(anchors))

    report = SignatureReport(overall=SignatureTrust.NONE, anchors_configured=len(anchors))
    for sig in embedded:
        report.signatures.append(_validate_one(sig, anchors, validate_pdf_signature, ValidationContext))

    # The document is only as trustworthy as its weakest signature: one
    # broken or revoked signature is not redeemed by a second good one.
    order = [SignatureTrust.INVALID, SignatureTrust.UNKNOWN,
             SignatureTrust.PRESENT_UNVERIFIED, SignatureTrust.TRUSTED]
    report.overall = min((s.trust for s in report.signatures), key=order.index)
    return report


def _validate_one(sig: Any, anchors: List[Any], validate_pdf_signature: Any,
                  ValidationContext: Any) -> SignatureFinding:
    reasons: List[str] = []
    field_name = getattr(sig, "field_name", "") or "(unnamed)"

    # Step 1: establish WHEN to validate. Correction #11 requires certificate
    # validity to be judged at the time of signing rather than at `now` --
    # otherwise every document signed with a since-expired certificate would
    # be wrongly rejected, which is the spec's own named edge case.
    #
    # A self-reported signing time is a claim by the signer, not a fact; a
    # trusted timestamp token is evidence. Both are used as the validation
    # moment, but only the timestamped case can reach TRUSTED.
    self_reported = getattr(sig, "self_reported_timestamp", None)
    validation_moment: Optional[datetime] = self_reported if isinstance(self_reported, datetime) else None

    def _context() -> Any:
        return ValidationContext(
            trust_roots=list(anchors) if anchors else [],
            allow_fetching=False,          # the worker has no default egress
            revocation_mode=_revocation_mode(),
            moment=validation_moment,
        )

    try:
        status = validate_pdf_signature(sig, signer_validation_context=_context())
    except Exception as exc:  # noqa: BLE001
        return SignatureFinding(
            field_name=field_name, trust=SignatureTrust.UNKNOWN, intact=False, chain_trusted=False,
            covers_whole_document=False, modification_level=None, signing_time=_iso(validation_moment),
            timestamp_trusted=False, revocation_checked=False, revoked=False,
            signer_common_name=None, signer_cert_not_before=None, signer_cert_not_after=None,
            cert_valid_at_signing_time=False,
            reasons=[f"VALIDATION_ERROR: {type(exc).__name__}: {exc}"],
        )

    intact = bool(getattr(status, "intact", False))
    chain_trusted = bool(getattr(status, "trusted", False)) and bool(anchors)
    revoked = bool(getattr(status, "revoked", False))
    covers = _coverage_is_whole_document(status)
    mod_level = getattr(status, "modification_level", None)
    mod_level_name = getattr(mod_level, "name", None) if mod_level is not None else None

    ts_validity = getattr(status, "timestamp_validity", None)
    timestamp_trusted = bool(ts_validity is not None and getattr(ts_validity, "valid", False)
                             and getattr(ts_validity, "intact", False))
    if timestamp_trusted:
        ts_time = getattr(ts_validity, "timestamp", None)
        if isinstance(ts_time, datetime):
            validation_moment = ts_time

    signer_cert = getattr(status, "signing_cert", None)
    not_before = not_after = None
    cert_valid_at_signing = False
    if signer_cert is not None:
        try:
            validity = signer_cert["tbs_certificate"]["validity"]
            not_before = validity["not_before"].native
            not_after = validity["not_after"].native
            if validation_moment and not_before and not_after:
                cert_valid_at_signing = not_before <= validation_moment <= not_after
        except Exception:  # noqa: BLE001 -- an unparseable validity window is reported, not guessed
            reasons.append("CERT_VALIDITY_UNREADABLE")

    revocation_details = getattr(status, "revocation_details", None)
    revocation_checked = revocation_details is not None

    # Step 2: turn the individual checks into one honest verdict.
    if not intact:
        trust = SignatureTrust.INVALID
        reasons.append("SIGNATURE_NOT_INTACT: the signed bytes do not match the document")
    elif revoked:
        trust = SignatureTrust.INVALID
        reasons.append("SIGNER_CERTIFICATE_REVOKED")
    elif not covers:
        # Intact over an older revision — the file changed after signing.
        trust = SignatureTrust.INVALID
        reasons.append(
            f"SIGNATURE_DOES_NOT_COVER_CURRENT_REVISION (modification level: {mod_level_name or 'unknown'})")
    elif not anchors:
        trust = SignatureTrust.PRESENT_UNVERIFIED
        reasons.append(
            "NO_TRUST_ANCHORS_CONFIGURED: the signature is cryptographically intact but no "
            "issuer could be trusted, because this deployment has no trust anchors configured")
    elif not chain_trusted:
        trust = SignatureTrust.PRESENT_UNVERIFIED
        reasons.append("SIGNER_NOT_TRUSTED: the certificate does not chain to a configured trust anchor")
    elif not cert_valid_at_signing:
        trust = SignatureTrust.PRESENT_UNVERIFIED
        reasons.append("CERT_NOT_VALID_AT_SIGNING_TIME: the signing certificate's validity window "
                       "does not contain the signing time")
    elif not timestamp_trusted:
        # Everything checks out EXCEPT that the signing time rests on the
        # signer's own word. That is enough for a human to accept and not
        # enough for the machine to, so it routes to review rather than
        # being either rubber-stamped or thrown out.
        trust = SignatureTrust.PRESENT_UNVERIFIED
        reasons.append("NO_TRUSTED_TIMESTAMP: the signing time is self-reported and cannot be "
                       "independently verified")
    else:
        trust = SignatureTrust.TRUSTED

    if not revocation_checked and trust == SignatureTrust.TRUSTED and _revocation_mode() == "require":
        trust = SignatureTrust.PRESENT_UNVERIFIED
        reasons.append("REVOCATION_STATUS_UNKNOWN: policy requires a revocation check that could not be performed")

    return SignatureFinding(
        field_name=field_name, trust=trust, intact=intact, chain_trusted=chain_trusted,
        covers_whole_document=covers, modification_level=mod_level_name,
        signing_time=_iso(validation_moment), timestamp_trusted=timestamp_trusted,
        revocation_checked=revocation_checked, revoked=revoked,
        signer_common_name=_common_name(signer_cert) if signer_cert is not None else None,
        signer_cert_not_before=_iso(not_before), signer_cert_not_after=_iso(not_after),
        cert_valid_at_signing_time=cert_valid_at_signing, reasons=reasons,
    )
