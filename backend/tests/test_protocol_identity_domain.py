"""P2-02/P2-03: normalization, cited extraction and the pure classifier.

No database, no model. Fixtures are SYNTHETIC text that reproduces the PERIGON
identity conflict; they are not the real protocol PDFs.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.protocol_identity import (
    ClassificationInput, ClaimSource, IdentityClassification, PriorDocument, RegistryComparison,
    RegistryStatus, classify_identity, claim_from_filename, claim_from_text, extract_identity,
    fingerprint_pages, normalize_text, parse_date,
)

FILLER = " The study schedule follows the assessments described below in detail. " * 4
PERIGON_FRONT = (
    "PROTOCOL\nTitle: PERIGON, a study of aprocitentan in resistant hypertension\n"
    "Sponsor: Example Pharma\nProtocol Number: ID-080A301\n"
    "ClinicalTrials.gov Identifier: NCT02088554\nVersion 2.0\nDate: 12 March 2015\n" + FILLER)


def pages(*texts: str, usable: bool = True):
    return [(index + 1, text, usable) for index, text in enumerate(texts)]


def run(texts: list[str], *, expected: list | None = None, priors: list | None = None,
        usable: bool = True, registry: RegistryComparison | None = None):
    rows = pages(*texts, usable=usable)
    return classify_identity(ClassificationInput(
        expected=expected or [],
        facts=extract_identity([(n, t) for n, t, ok in rows if ok]),
        fingerprint=fingerprint_pages(rows),
        registry=registry or RegistryComparison(), priors=priors or []))


def prior_of(texts: list[str], identity_id="p1", **extra) -> PriorDocument:
    rows = pages(*texts)
    fp = fingerprint_pages(rows)
    facts = extract_identity([(n, t) for n, t, _ in rows])
    return PriorDocument(
        identity_id=identity_id, identifiers=sorted({i.normalized for i in facts.authoritative()}),
        text_sha256=fp.sha256, verified_full_text=fp.verified_full_text,
        version_label=facts.version_label.value if facts.version_label else None,
        version_date=facts.version_date, created_at_iso="2026-01-01T00:00:00+00:00", **extra)


BODY = [PERIGON_FRONT, "Synopsis of the study design and objectives. " * 8,
        "References\n1. Earlier work see NCT02088541 [3].\n"]


# ----------------------------------------------------------------- normalization
def test_normalization_ignores_layout_and_typography_but_not_content():
    a = normalize_text("Dose – 5 mg  daily\n\nStart of page 2\fnext")
    b = normalize_text("Dose - 5 mg daily Start of page 2 next")
    assert a == b
    assert normalize_text("Dose 5 mg daily") != normalize_text("Dose 50 mg daily")
    assert normalize_text("Amendment 2, 12 March 2015") != normalize_text("Amendment 3, 12 March 2015")
    assert normalize_text("NCT02088554") != normalize_text("NCT02088541")
    assert "ﬁ" not in normalize_text("scheﬁdule")


def test_fingerprint_is_stable_across_repagination_and_never_verified_when_a_page_is_blank():
    full = fingerprint_pages(pages("A" * 150, "B" * 150))
    flowed = fingerprint_pages(pages("A" * 150 + " " + "B" * 150))
    assert full.sha256 == flowed.sha256 and full.verified_full_text and flowed.verified_full_text
    partial = fingerprint_pages([(1, "A" * 300, True), (2, "", False)])
    assert partial.sha256 is not None and not partial.verified_full_text
    assert partial.coverage.pages_without_text == [2]
    empty = fingerprint_pages([(1, "", False)])
    assert empty.sha256 is None and not empty.verified_full_text


# -------------------------------------------------------------------- extraction
def test_authoritative_identifier_is_the_front_matter_one_not_the_reference_one():
    facts = extract_identity([(1, PERIGON_FRONT), (3, BODY[2])])
    authoritative = {i.normalized for i in facts.authoritative()}
    assert authoritative == {"NCT02088554", "IDO80A301"} or "NCT02088554" in authoritative
    assert "NCT02088541" not in authoritative
    cited = next(i for i in facts.identifiers if i.normalized == "NCT02088541")
    assert not cited.authoritative and cited.page == 3
    assert facts.version_label.value == "2.0"
    assert facts.version_date == date(2015, 3, 12)
    assert facts.title.value.startswith("PERIGON") and facts.sponsor.value == "Example Pharma"


def test_dates_are_never_guessed_from_ambiguous_numeric_forms():
    assert parse_date("03/04/2015") is None
    assert parse_date("March 12, 2015") == date(2015, 3, 12)
    assert parse_date("2015-03-12") == date(2015, 3, 12)


def test_a_filename_without_an_identifier_is_not_an_expected_identity():
    assert claim_from_filename("protocol_final_v2.pdf") is None
    claim = claim_from_filename("NCT02088541.pdf")
    assert claim.normalized == "NCT02088541" and claim.source is ClaimSource.FILENAME


# ---------------------------------------------------------------- classification
def test_wrong_submitted_id_against_the_documents_own_id_is_a_mismatch_with_evidence():
    result = run(BODY, expected=[claim_from_text("NCT02088541", ClaimSource.SUBMITTED)])
    assert result.classification is IdentityClassification.MISMATCH
    blocking = [i for i in result.issues if i.severity.value == "BLOCKING"][0]
    assert blocking.code == "EXPECTED_IDENTIFIER_CONFLICT" and blocking.page == 1
    assert "NCT02088554" in blocking.message and "NCT02088541" in blocking.message
    assert result.suggested_correction == {
        "expected_identifier": "NCT02088554", "kind": "REGISTRY_NCT"}


def test_matching_expected_id_and_no_expected_id_with_a_self_stated_one_both_match():
    assert run(BODY, expected=[claim_from_text("nct 02088554", ClaimSource.SUBMITTED)]
               ).classification is IdentityClassification.MATCH
    assert run(BODY).classification is IdentityClassification.MATCH


def test_filename_identifier_loses_to_a_matching_submitted_identifier():
    result = run(BODY, expected=[
        claim_from_text("NCT02088554", ClaimSource.SUBMITTED),
        claim_from_filename("NCT02088541.pdf")])
    assert result.classification is IdentityClassification.MATCH
    assert any(c.get("result") == "SUPERSEDED_BY_STRONGER_CLAIM" for c in result.comparisons)


def test_filename_identifier_alone_conflicting_is_a_mismatch_with_its_own_code():
    result = run(BODY, expected=[claim_from_filename("NCT02088541.pdf")])
    assert result.classification is IdentityClassification.MISMATCH
    assert any(i.code == "FILENAME_IDENTIFIER_CONFLICT" for i in result.issues)


@pytest.mark.parametrize("texts,usable,code", [
    ([""], True, "NO_USABLE_TEXT"),
    (["Study of a drug. " * 40], True, "NO_AUTHORITATIVE_IDENTIFIER"),
    (["Scanned content that must not be trusted NCT02088554 " * 5], False, "NO_USABLE_TEXT"),
])
def test_unreadable_empty_or_identifier_free_documents_are_uncertain(texts, usable, code):
    result = run(texts, usable=usable)
    assert result.classification is IdentityClassification.UNCERTAIN
    assert any(i.code == code for i in result.issues)


def test_competing_authoritative_identifiers_are_uncertain():
    both = PERIGON_FRONT + "\nClinicalTrials.gov Identifier: NCT02088541\n"
    result = run([both])
    assert result.classification is IdentityClassification.UNCERTAIN
    assert any(i.code == "COMPETING_IDENTIFIERS" for i in result.issues)


def test_an_expected_id_only_mentioned_in_references_is_uncertain_not_matched():
    result = run(["Protocol overview. " * 30, "References\nSee NCT02088541 [4].\n"],
                 expected=[claim_from_text("NCT02088541", ClaimSource.SUBMITTED)])
    assert result.classification is IdentityClassification.UNCERTAIN
    assert any(i.code == "EXPECTED_IDENTIFIER_NOT_STATED" for i in result.issues)


def test_registry_unavailable_is_info_and_a_registry_conflict_blocks():
    unavailable = RegistryComparison(status=RegistryStatus.UNAVAILABLE, provider="x")
    ok = run(BODY, registry=unavailable)
    assert ok.classification is IdentityClassification.MATCH
    assert any(i.code == "REGISTRY_UNVERIFIED" and i.severity.value == "INFO" for i in ok.issues)
    conflict = run(BODY, registry=RegistryComparison(
        status=RegistryStatus.CONFLICT, identifier="NCT02088554", detail="Title differs."))
    assert conflict.classification is IdentityClassification.MISMATCH


def test_equivalent_text_with_different_bytes_is_a_duplicate_after_identity_passes():
    prior = prior_of(BODY)
    reflowed = [text.replace(" ", "  ").replace("\n", "\n\n") for text in BODY]
    result = run(reflowed, priors=[prior])
    assert result.classification is IdentityClassification.DUPLICATE
    assert result.duplicate_of == "p1"


def test_conflict_beats_hash_equality():
    result = run(BODY, priors=[prior_of(BODY)],
                 expected=[claim_from_text("NCT02088541", ClaimSource.SUBMITTED)])
    assert result.classification is IdentityClassification.MISMATCH
    assert result.duplicate_of is None


def test_changed_content_with_new_version_evidence_is_new_version_with_lineage():
    amended = [BODY[0].replace("Version 2.0", "Version 3.0").replace("12 March 2015", "5 June 2016"),
               BODY[1] + " Dose changed to 80 mg.", BODY[2]]
    result = run(amended, priors=[prior_of(BODY)])
    assert result.classification is IdentityClassification.NEW_VERSION
    assert result.predecessor == "p1"


def test_changed_content_without_version_evidence_is_not_silently_an_amendment():
    changed = [BODY[0], BODY[1] + " Dose changed to 80 mg.", BODY[2]]
    result = run(changed, priors=[prior_of(BODY)])
    assert result.classification is IdentityClassification.UNCERTAIN
    assert any(i.code == "CONTENT_CHANGED_WITHOUT_VERSION_EVIDENCE" for i in result.issues)


def test_an_earlier_dated_but_different_document_needs_review():
    older = [BODY[0].replace("12 March 2015", "1 January 2014").replace("2.0", "1.0"),
             BODY[1] + " Older wording.", BODY[2]]
    result = run(older, priors=[prior_of(BODY)])
    assert result.classification is IdentityClassification.UNCERTAIN
    assert any(i.code == "VERSION_ORDER_CONFLICT" for i in result.issues)


def test_an_incomplete_fingerprint_is_never_called_a_duplicate():
    result = run([BODY[0], ""], priors=[prior_of([BODY[0], ""])])
    assert result.classification is IdentityClassification.MATCH
