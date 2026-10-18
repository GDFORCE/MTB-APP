"""Deterministic protocol-identity classification (P2-03). A pure function.

Precedence (first rule that applies wins; later rules are never consulted):

1. NO USABLE TEXT        no characters at all                      -> UNCERTAIN
2. COMPETING IDS         two different authoritative identifiers
                         of the same kind                          -> UNCERTAIN
3. REGISTRY CONFLICT     a confirmed registry record disagrees     -> MISMATCH
4. EXPECTED vs DOCUMENT  (see below)                               -> MISMATCH / UNCERTAIN
5. NO EXPECTED IDENTITY  a self-consistent authoritative id        -> identity passes
                         otherwise                                 -> UNCERTAIN
6. DEDUPLICATION         only after identity has passed:
     same verified text hash as an accepted prior                  -> DUPLICATE
     same protocol, evidenced new version/date + changed text      -> NEW_VERSION
     same protocol, changed text, no version evidence              -> UNCERTAIN
     otherwise                                                     -> MATCH

Rule 4 compares like with like. An expected registry identifier conflicts only
with a document authoritative registry identifier OF THE SAME REGISTRY; an
expected sponsor protocol number conflicts only with a labelled authoritative
protocol number. An expected value that the document simply does not state (and
that no authoritative identifier contradicts) is UNCERTAIN, not a mismatch -
absence of a usable identifier is never evidence of a match, but it is not proof
of a different protocol either.

Conflict always beats hash equality: rules 1-5 run before 6, so a file whose
bytes equal an accepted protocol is still a MISMATCH when it was submitted as
another trial.

Registry availability: NOT_CHECKED / UNAVAILABLE / NOT_FOUND are recorded as INFO
(`REGISTRY_UNVERIFIED`) and never change the outcome by themselves - internal
evidence (document identifiers, expected identity) suffices. Only a CONFLICT
from a registry record blocks.
"""

from __future__ import annotations

from .models import (
    ClassificationInput, ClassificationResult, ClaimSource, IdentifierEvidence,
    IdentifierKind, IdentityClassification, IdentityIssue, IssueSeverity, PriorDocument,
    RegistryStatus,
)

_BLOCK = IssueSeverity.BLOCKING
_INFO = IssueSeverity.INFO


def _issue(code: str, message: str, severity: IssueSeverity = _BLOCK,
           evidence: IdentifierEvidence | None = None) -> IdentityIssue:
    return IdentityIssue(
        code=code, severity=severity, message=message,
        page=evidence.page if evidence else None, snippet=evidence.snippet if evidence else None)


def classify_identity(data: ClassificationInput) -> ClassificationResult:
    facts, fingerprint = data.facts, data.fingerprint
    issues: list[IdentityIssue] = []
    comparisons: list[dict] = []
    authoritative = facts.authoritative()

    # registry status is provenance, recorded whatever else happens
    if data.registry.status in {RegistryStatus.NOT_CHECKED, RegistryStatus.UNAVAILABLE,
                                RegistryStatus.NOT_FOUND}:
        issues.append(_issue(
            "REGISTRY_UNVERIFIED",
            f"Registry verification is {data.registry.status.value.lower().replace('_', ' ')}; "
            "the decision rests on the document's own identifiers.", _INFO))

    def blocked(classification: IdentityClassification, code: str, message: str,
                evidence: IdentifierEvidence | None = None,
                suggestion: dict | None = None) -> ClassificationResult:
        issues.append(_issue(code, message, _BLOCK, evidence))
        return ClassificationResult(
            classification=classification, issues=issues, comparisons=comparisons,
            suggested_correction=suggestion)

    # A reviewer who read the document may attest its identity. That replaces the
    # machine's need for readable text / a single stated identifier (rules 1-2),
    # but never overrides an identifier the document states (rule 4) or a
    # confirmed registry conflict (rule 3).
    reviewed = any(claim.source is ClaimSource.REVIEWER_CONFIRMED for claim in data.expected)

    # 1 -- nothing readable
    if not reviewed and (fingerprint.char_count == 0 or fingerprint.sha256 is None):
        return blocked(
            IdentityClassification.UNCERTAIN, "NO_USABLE_TEXT",
            "The document has no readable text, so its identity cannot be checked. "
            "A scanned file needs a reviewer.")

    # 2 -- competing authoritative identifiers of one kind
    for kind in IdentifierKind:
        values = {item.normalized for item in authoritative if item.kind is kind}
        if len(values) > 1 and not reviewed:
            first = next(item for item in authoritative if item.kind is kind)
            return blocked(
                IdentityClassification.UNCERTAIN, "COMPETING_IDENTIFIERS",
                f"The document states more than one {kind.value.lower().replace('_', ' ')}: "
                f"{', '.join(sorted(values))}.", first)

    # 3 -- a registry record that disagrees
    if data.registry.status is RegistryStatus.CONFLICT:
        return blocked(
            IdentityClassification.MISMATCH, "REGISTRY_CONFLICT",
            f"The registry record for {data.registry.identifier or 'this identifier'} "
            f"does not match the document. {data.registry.detail}".strip())

    # 4 -- expected identity versus the document, like with like
    claims = sorted(data.expected, key=lambda claim: _PRIORITY[claim.source])
    stronger_matched = False
    for claim in claims:
        same_kind = [item for item in authoritative if item.kind is claim.kind]
        stated = [item for item in facts.identifiers if item.normalized == claim.normalized]
        comparison = {
            "expected": claim.normalized, "kind": claim.kind.value, "source": claim.source.value,
            "document_authoritative": sorted({item.normalized for item in same_kind}),
        }
        if claim.source is ClaimSource.FILENAME and stronger_matched:
            # An identifier in a file name is the weakest claim: once a submitted or
            # recorded identity has matched the document it is not consulted.
            comparison["result"] = "SUPERSEDED_BY_STRONGER_CLAIM"
            comparisons.append(comparison)
            continue
        if claim.source is ClaimSource.REVIEWER_CONFIRMED and not any(
                item.normalized != claim.normalized for item in same_kind):
            # attested by a person; nothing the document states contradicts it
            comparison["result"] = "REVIEWER_ATTESTED"
            comparisons.append(comparison)
            stronger_matched = True
            continue
        if claim.source is not ClaimSource.FILENAME and any(
                item.authoritative and item.normalized == claim.normalized for item in stated):
            stronger_matched = True
        if any(item.authoritative and item.normalized == claim.normalized for item in stated):
            comparison["result"] = "MATCH"
            comparisons.append(comparison)
            continue
        if same_kind:
            actual = same_kind[0]
            comparison["result"] = "CONFLICT"
            comparisons.append(comparison)
            filename_only = claim.source is ClaimSource.FILENAME
            code = "FILENAME_IDENTIFIER_CONFLICT" if filename_only else "EXPECTED_IDENTIFIER_CONFLICT"
            return blocked(
                IdentityClassification.MISMATCH, code,
                f"This file was submitted as {claim.normalized}, but the document identifies "
                f"itself as {actual.normalized} (page {actual.page}).", actual,
                suggestion={"expected_identifier": actual.normalized, "kind": actual.kind.value})
        comparison["result"] = "NOT_STATED"
        comparisons.append(comparison)
        mention = next((item for item in stated if not item.authoritative), None)
        return blocked(
            IdentityClassification.UNCERTAIN, "EXPECTED_IDENTIFIER_NOT_STATED",
            f"The document does not state {claim.normalized} as its own identifier"
            + (f" (it is only mentioned on page {mention.page}: {mention.context})."
               if mention else "."),
            mention)

    # 5 -- no expectation to compare with: need the document to name itself
    if not claims:
        if not authoritative:
            return blocked(
                IdentityClassification.UNCERTAIN, "NO_AUTHORITATIVE_IDENTIFIER",
                "The document does not state a registry identifier or protocol number as "
                "its own, and no expected identity was supplied.")
        comparisons.append({
            "expected": None, "result": "SELF_CONSISTENT",
            "document_authoritative": sorted({item.normalized for item in authoritative})})

    # 6 -- the identity passed; now is it a repeat or a new version?
    identifiers = {item.normalized for item in authoritative} | {
        claim.normalized for claim in claims}
    return _deduplicate(data, issues, comparisons, identifiers)


_PRIORITY = {
    ClaimSource.REVIEWER_CONFIRMED: 0, ClaimSource.SUBMITTED: 1,
    ClaimSource.TRIAL_RECORD: 2, ClaimSource.FILENAME: 3,
}


def _deduplicate(
    data: ClassificationInput, issues: list[IdentityIssue], comparisons: list[dict],
    identifiers: set[str],
) -> ClassificationResult:
    fingerprint, facts = data.fingerprint, data.facts

    def done(classification: IdentityClassification, **extra) -> ClassificationResult:
        return ClassificationResult(
            classification=classification, issues=issues, comparisons=comparisons, **extra)

    related = [prior for prior in data.priors if identifiers & set(prior.identifiers)]
    if not related:
        return done(IdentityClassification.MATCH)
    if not fingerprint.verified_full_text:
        issues.append(_issue(
            "FINGERPRINT_INCOMPLETE",
            "Part of the document has no readable text, so it cannot be recognised as a "
            "repeat of an earlier upload; it is treated as a separate document.", _INFO))
        return done(IdentityClassification.MATCH)

    same = [prior for prior in related if prior.verified_full_text
            and prior.text_sha256 == fingerprint.sha256]
    if same:
        source = min(same, key=lambda prior: prior.created_at_iso)
        comparisons.append({"duplicate_of": source.identity_id, "basis": "normalized-text-sha256"})
        return done(IdentityClassification.DUPLICATE, duplicate_of=source.identity_id)

    verified = [prior for prior in related if prior.verified_full_text]
    if not verified:
        issues.append(_issue(
            "PRIOR_FINGERPRINT_INCOMPLETE",
            "The earlier upload of this protocol had incomplete text, so a changed version "
            "cannot be established.", _INFO))
        return done(IdentityClassification.MATCH)

    latest = max(verified, key=lambda prior: (
        prior.version_date.isoformat() if prior.version_date else "", prior.created_at_iso))
    label = facts.version_label.value.strip().lower() if facts.version_label else None
    known_labels = {(prior.version_label or "").strip().lower() for prior in verified} - {""}
    changed_label = label is not None and label not in known_labels
    later_date = (facts.version_date is not None and latest.version_date is not None
                  and facts.version_date > latest.version_date)
    earlier_date = (facts.version_date is not None and latest.version_date is not None
                    and facts.version_date < latest.version_date)
    comparisons.append({
        "changed_content_from": latest.identity_id, "version_label": label,
        "known_labels": sorted(known_labels),
        "version_date": facts.version_date.isoformat() if facts.version_date else None,
        "predecessor_date": latest.version_date.isoformat() if latest.version_date else None})

    if earlier_date:
        issues.append(_issue(
            "VERSION_ORDER_CONFLICT",
            "The document is dated earlier than an already-accepted version of this protocol "
            "but its text differs. A reviewer must decide.", _BLOCK))
        return done(IdentityClassification.UNCERTAIN)
    if changed_label or later_date:
        return done(IdentityClassification.NEW_VERSION, predecessor=latest.identity_id)
    issues.append(_issue(
        "CONTENT_CHANGED_WITHOUT_VERSION_EVIDENCE",
        "The text differs from an earlier upload of this protocol but the document shows no "
        "new version label or later date. It is not treated as an amendment automatically.",
        _BLOCK))
    return done(IdentityClassification.UNCERTAIN)
