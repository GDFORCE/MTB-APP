"""Genuine-document identity tests for NCT02088554 PERIGON (Prot_003.pdf).

PROVENANCE (see docs/evidence/protocol-identity/NCT02088554/source-manifest.json):
the PDF is the PUBLIC official registry copy retrieved from ClinicalTrials.gov. It is
NOT the historically mislabelled upload and NOT an amendment pair. Cases below that
use a wrong expected identifier are CONSTRUCTED from genuine content, and say so.

The registry adapter is exercised two ways, and the difference is explicit:
* a LIVE call to the public ClinicalTrials.gov API (skipped, not faked, when the
  network is unreachable - ``@live``);
* an INJECTED transport (a scripted stand-in: it proves adapter behaviour, NOT
  that the registry says anything).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.protocol_identity import PagesInput, ProtocolIdentityService
from app.domain.protocol_identity import ClaimSource, claim_from_text
from app.domain.protocol_identity.models import RegistryStatus
from app.extraction.documents import pages_from_pdf
from app.persistence.memory.identity_store import InMemoryIdentityStore
from app.services.clinicaltrials_registry import ClinicalTrialsGovRegistry

EVIDENCE = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "protocol-identity" / "NCT02088554"
PDF = EVIDENCE / "Prot_003.pdf"
MANIFEST = json.loads((EVIDENCE / "source-manifest.json").read_text(encoding="utf-8"))

pytestmark = pytest.mark.skipif(not PDF.exists(), reason="official evidence PDF is not present")


@pytest.fixture(scope="module")
def pages():
    data = PDF.read_bytes()
    assert hashlib.sha256(data).hexdigest() == MANIFEST["sha256"]       # it IS the recorded file
    from protocol_identity_runtime import pages_input      # the product's own conversion
    return pages_input(pages_from_pdf(data))


def _live_registry():
    registry = ClinicalTrialsGovRegistry()
    probe = registry.lookup("NCT02088554", kind=None)
    if probe.status is RegistryStatus.UNAVAILABLE:
        pytest.skip(f"ClinicalTrials.gov unreachable from this environment: {probe.detail}")
    return registry


def assess(service, org, pages, *, expected, sha=None):
    return service.assess(
        organization_id=org, owner_user_id="u1", filename="protocol.pdf",
        content_type="application/pdf", size_bytes=1,
        binary_sha256=sha or uuid4().hex.ljust(64, "0"), pages=pages,
        expected=[claim_from_text(v, ClaimSource.SUBMITTED) for v in expected])


def test_the_genuine_registry_pdf_reads_as_perigon_with_its_nct_number(pages):
    assert len(pages) == MANIFEST["pages"]
    head = " ".join(p.text for p in pages[:5]).upper()
    assert "PERIGON" in head


def test_genuine_document_matches_its_own_registry_identifier_live():
    registry = _live_registry()
    comparison = registry.lookup("NCT02088554", kind=None)
    assert comparison.status is RegistryStatus.CONFIRMED
    assert comparison.provider == "clinicaltrials.gov/api/v2"
    assert "PERIGON" in comparison.detail.upper()


def test_a_wrong_expected_identifier_on_genuine_content_is_quarantined_live(pages):
    """CONSTRUCTED from genuine content: the expected id is the historically
    mislabelled NCT02088541, the content is the real NCT02088554 protocol."""
    service = ProtocolIdentityService(InMemoryIdentityStore(), _live_registry())
    result = assess(service, uuid4(), pages, expected=["NCT02088541"])
    assert result.blocked is True
    assert result.classification.value in {"MISMATCH", "UNCERTAIN"}


def test_the_correctly_labelled_genuine_document_stays_uncertain_until_reviewed_live(pages):
    """The official copy prints no NCT number, so the document does not prove its own
    identity. It is NOT auto-accepted on word overlap with the registry record; the
    registry descriptors are recorded as review support and an authorised, reasoned
    reviewer confirms the identity."""
    from app.application.protocol_identity import ReviewAction

    service = ProtocolIdentityService(InMemoryIdentityStore(), _live_registry())
    org = uuid4()
    first = assess(service, org, pages, expected=["NCT02088554"])
    assert first.blocked is True and first.classification.value == "UNCERTAIN"
    assert [i.code for i in first.issues if i.severity.value == "BLOCKING"] == [
        "EXPECTED_IDENTIFIER_NOT_STATED"]
    assert first.registry.status is RegistryStatus.CONFIRMED          # review support only
    assert "PERIGON" in (first.registry.detail or "").upper()
    outcome = service.resolve(
        organization_id=org, identity_id=first.id, actor_id="reviewer-1",
        action=ReviewAction.CONFIRM_DOCUMENT_IDENTITY,
        reason="Registry record NCT02088554 (PERIGON) matches this document's title page",
        expected_revision=first.revision, corrected_identifier="NCT02088554")
    assert outcome.identity.review_status.value == "APPROVED"
    assert outcome.resume_intent_id is not None


# ---- adapter behaviour with an INJECTED transport (a stand-in, labelled as such) ----------

def test_adapter_outage_is_unavailable_never_a_fabricated_match():
    calls = []

    def down(url, timeout):
        calls.append(url)
        raise TimeoutError("scripted outage")
    adapter = ClinicalTrialsGovRegistry(down, sleep=lambda _s: None)
    result = adapter.lookup("NCT02088554", kind=None)
    assert result.status is RegistryStatus.UNAVAILABLE and len(calls) == 2     # one bounded retry
    assert adapter.lookup("NCT02088554", kind=None).status is RegistryStatus.UNAVAILABLE
    assert len(calls) == 4                                                      # outage never cached


def test_adapter_404_and_non_nct_identifiers():
    adapter = ClinicalTrialsGovRegistry(lambda url, timeout: (404, None), sleep=lambda _s: None)
    assert adapter.lookup("NCT00000000", kind=None).status is RegistryStatus.NOT_FOUND
    assert adapter.lookup("PRT-123", kind=None).status is RegistryStatus.NOT_CHECKED


def test_adapter_caches_a_confirmed_answer_and_sends_only_the_identifier():
    seen = []

    def ok(url, timeout):
        seen.append(url)
        return 200, {"protocolSection": {"identificationModule": {
            "nctId": "NCT02088554", "briefTitle": "Scripted",
            "organization": {"fullName": "Org"}}}}
    adapter = ClinicalTrialsGovRegistry(ok)
    adapter.lookup("nct02088554", kind=None)
    adapter.lookup("NCT02088554", kind=None)
    assert len(seen) == 1
    assert seen[0].endswith("/NCT02088554?fields=protocolSection.identificationModule")


# ---- F2: shared words are NOT identity (INJECTED transport: stand-ins, not the registry) ----

def _scripted(acronym, organization, nct="NCT02088554"):
    return ClinicalTrialsGovRegistry(lambda url, timeout: (200, {"protocolSection": {
        "identificationModule": {"nctId": nct, "briefTitle": "Scripted", "acronym": acronym,
                                 "organization": {"fullName": organization}}}}))


def _pages(*texts):
    from app.application.protocol_identity import PagesInput
    return [PagesInput(number=i + 1, text=text, usable=True) for i, text in enumerate(texts)]


def test_matching_registry_descriptors_do_not_accept_the_genuine_document_automatically(pages):
    for acronym, organization in [("PERIGON", "Medtronic Cardiac Surgery"),
                                  ("PERIGON", "Acme Pharma"), ("OTHERSTUDY", "Medtronic")]:
        service = ProtocolIdentityService(InMemoryIdentityStore(), _scripted(acronym, organization))
        result = assess(service, uuid4(), pages, expected=["NCT02088554"])
        assert result.blocked is True and result.classification.value == "UNCERTAIN"
        assert all(i.code != "IDENTIFIED_BY_REGISTRY_CORROBORATION" for i in result.issues)


def test_an_unrelated_study_that_shares_a_prefix_and_cites_the_acronym_stays_quarantined():
    """Reproduction of the review: registry acronym ORBIT / 'National Heart Research
    Institute'; the upload is a different diagnostic study of the 'National Cancer
    Institute' (protocol OTHER-777) that only cites ORBIT in background text."""
    page = ("Protocol OTHER-777\n"
            "A Diagnostic Imaging Study\n"
            "Sponsor: National Cancer Institute\n"
            "Background: the ORBIT trial (a different study) reported earlier results.\n")
    service = ProtocolIdentityService(
        InMemoryIdentityStore(), _scripted("ORBIT", "National Heart Research Institute",
                                           nct="NCT12345678"))
    result = assess(service, uuid4(), _pages(page), expected=["NCT12345678"])
    assert result.blocked is True and result.classification.value != "MATCH"


def test_substring_collision_and_wrong_protocol_number_do_not_match():
    page = ("Protocol ORBITAL-2\n"
            "Sponsor: Heartland Research Group\n"
            "A Study of Orbital Imaging\n")
    service = ProtocolIdentityService(
        InMemoryIdentityStore(), _scripted("ORBIT", "Heartland Research Group", nct="NCT12345678"))
    result = assess(service, uuid4(), _pages(page), expected=["NCT12345678"])
    assert result.blocked is True


def test_contradictory_authoritative_identifier_is_a_mismatch_whatever_the_registry_says():
    page = ("Protocol Title Page\n"
            "Clinical Trial Registration: NCT00000001\n"
            "Sponsor: Medtronic\n"
            "PERIGON\n")
    service = ProtocolIdentityService(
        InMemoryIdentityStore(), _scripted("PERIGON", "Medtronic", nct="NCT02088554"))
    result = assess(service, uuid4(), _pages(page), expected=["NCT02088554"])
    assert result.blocked is True
    assert result.classification.value in {"MISMATCH", "UNCERTAIN"}


def test_registry_answer_for_a_different_identifier_is_never_recorded_as_confirmed():
    adapter = _scripted("PERIGON", "Medtronic", nct="NCT99999999")
    comparison = adapter.lookup("NCT02088554", kind=None)
    assert comparison.status is RegistryStatus.UNAVAILABLE
    assert comparison.title is None and comparison.organization is None


def test_a_filename_only_claim_stays_quarantined(pages):
    service = ProtocolIdentityService(
        InMemoryIdentityStore(), _scripted("PERIGON", "Medtronic Cardiac Surgery"))
    result = service.assess(
        organization_id=uuid4(), owner_user_id="u1", filename="NCT02088554.pdf",
        content_type="application/pdf", size_bytes=1, binary_sha256="b" * 64, pages=pages,
        expected=[claim_from_text("NCT02088554", ClaimSource.FILENAME)])
    assert result.blocked is True
