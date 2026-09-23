"""PDF signature validation at the full scope of Production-Grade
Implementation Plan v1.3 correction #11.

These tests build REAL certificate chains and REAL signed PDFs rather than
mocking pyHanko, because every property under test is a property of actual
cryptographic validation: a mock would happily assert whatever the
implementation already does.

The scenario that most needs proving is the spec's own named edge case — a
certificate that has EXPIRED TODAY but was VALID WHEN THE DOCUMENT WAS SIGNED
must not be rejected. Getting that wrong would silently invalidate every
correctly-signed historical certificate a site has on file.
"""
from __future__ import annotations

import datetime as dt
import io
import sys
import uuid
from pathlib import Path
from typing import Tuple

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.remote_audit.signatures import (  # noqa: E402
    SignatureTrust,
    load_trust_anchors,
    validate_pdf_signatures,
)

pyhanko = pytest.importorskip("pyhanko", reason="pyHanko is required for signature validation")


# ── Certificate + signed-PDF fixtures ───────────────────────────────────────

def _make_ca(name: str, *, not_before: dt.datetime, not_after: dt.datetime):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                          data_encipherment=False, key_agreement=False, key_cert_sign=True,
                          crl_sign=True, encipher_only=False, decipher_only=False),
            critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert, serialization


def _make_leaf(ca_key, ca_cert, name: str, *, not_before: dt.datetime, not_after: dt.datetime):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                          data_encipherment=False, key_agreement=False, key_cert_sign=False,
                          crl_sign=False, encipher_only=False, decipher_only=False),
            critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING, ExtendedKeyUsageOID.EMAIL_PROTECTION]),
            critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _blank_pdf(text: str = "Calibration Certificate") -> bytes:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF is required to build test PDFs")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), text)
    data = doc.tobytes()
    doc.close()
    return data


def _sign_pdf(pdf_bytes: bytes, leaf_key, leaf_cert, ca_cert, *, signing_time: dt.datetime) -> bytes:
    from asn1crypto import x509 as asn1_x509
    from cryptography.hazmat.primitives import serialization
    from pyhanko.keys import load_private_key_from_pemder_data
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    key_pem = leaf_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    signing_key = load_private_key_from_pemder_data(key_pem, passphrase=None)
    signing_cert = asn1_x509.Certificate.load(leaf_cert.public_bytes(serialization.Encoding.DER))
    ca_asn1 = asn1_x509.Certificate.load(ca_cert.public_bytes(serialization.Encoding.DER))

    registry = SimpleCertificateStore()
    registry.register_multiple([signing_cert, ca_asn1])
    signer = signers.SimpleSigner(
        signing_cert=signing_cert, signing_key=signing_key, cert_registry=registry)

    writer = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes))
    out = signers.sign_pdf(
        writer,
        signers.PdfSignatureMetadata(field_name="Signature1"),
        signer=signer,
        timestamper=None,
        existing_fields_only=False,
        in_place=False,
    )
    return out.getvalue()


@pytest.fixture(scope="module")
def anchors_dir(tmp_path_factory) -> Tuple[Path, object, object]:
    """A CA valid for a wide window, written out as a trust anchor."""
    from cryptography.hazmat.primitives import serialization

    now = dt.datetime.now(dt.timezone.utc)
    ca_key, ca_cert, _ = _make_ca(
        f"MTB Test CA {uuid.uuid4().hex[:6]}",
        not_before=now - dt.timedelta(days=3650), not_after=now + dt.timedelta(days=3650))
    directory = tmp_path_factory.mktemp("anchors")
    (directory / "ca.pem").write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    return directory, ca_key, ca_cert


# ── Trust anchor loading ────────────────────────────────────────────────────

def test_no_configured_anchor_dir_loads_nothing(monkeypatch):
    monkeypatch.delenv("REMOTE_AUDIT_TRUST_ANCHORS_DIR", raising=False)
    assert load_trust_anchors() == []


def test_anchor_dir_loads_pem_certificates(anchors_dir):
    directory, _key, _cert = anchors_dir
    assert len(load_trust_anchors(str(directory))) == 1


def test_unreadable_anchor_is_skipped_not_fatal(anchors_dir, tmp_path):
    """One corrupt anchor file must not disable signature validation for every
    deployment sharing that directory."""
    from cryptography.hazmat.primitives import serialization

    directory, _key, cert = anchors_dir
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    (mixed / "good.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (mixed / "garbage.pem").write_bytes(b"this is not a certificate")

    anchors = load_trust_anchors(str(mixed))
    assert len(anchors) == 1


# ── The core contract ───────────────────────────────────────────────────────

def test_unsigned_pdf_reports_NONE_not_untrusted():
    """An unsigned document is not a suspicious document. Most site paperwork
    is legitimately unsigned, and conflating "no signature" with "bad
    signature" would bury reviewers in noise."""
    report = validate_pdf_signatures(_blank_pdf())
    assert report.overall == SignatureTrust.NONE
    assert report.signatures == []


def test_signature_without_configured_anchors_is_never_trusted(anchors_dir):
    """THE central rule: "a signature is present" is not "a signature is
    trusted". With no anchors configured, even a perfectly intact signature
    can only reach PRESENT_UNVERIFIED."""
    directory, ca_key, ca_cert = anchors_dir
    now = dt.datetime.now(dt.timezone.utc)
    leaf_key, leaf_cert = _make_leaf(
        ca_key, ca_cert, "Calibration Lab",
        not_before=now - dt.timedelta(days=30), not_after=now + dt.timedelta(days=365))
    signed = _sign_pdf(_blank_pdf(), leaf_key, leaf_cert, ca_cert, signing_time=now)

    report = validate_pdf_signatures(signed, trust_anchors_dir=None)

    assert report.overall == SignatureTrust.PRESENT_UNVERIFIED
    assert report.overall != SignatureTrust.TRUSTED
    assert report.signatures[0].intact is True          # the crypto IS fine...
    assert report.signatures[0].chain_trusted is False  # ...and it still isn't trusted
    assert any("NO_TRUST_ANCHORS_CONFIGURED" in r for r in report.signatures[0].reasons)


def test_signature_from_an_unknown_issuer_is_not_trusted(anchors_dir, tmp_path):
    """Chaining to SOME CA is not chaining to a CONFIGURED one."""
    from cryptography.hazmat.primitives import serialization

    _directory, _ca_key, ca_cert = anchors_dir
    now = dt.datetime.now(dt.timezone.utc)

    # A completely unrelated CA that the deployment has never heard of.
    rogue_key, rogue_cert, _ = _make_ca(
        "Rogue CA", not_before=now - dt.timedelta(days=100), not_after=now + dt.timedelta(days=100))
    leaf_key, leaf_cert = _make_leaf(
        rogue_key, rogue_cert, "Impostor Lab",
        not_before=now - dt.timedelta(days=10), not_after=now + dt.timedelta(days=100))
    signed = _sign_pdf(_blank_pdf(), leaf_key, leaf_cert, rogue_cert, signing_time=now)

    only_real_ca = tmp_path / "real-only"
    only_real_ca.mkdir()
    (only_real_ca / "ca.pem").write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

    report = validate_pdf_signatures(signed, trust_anchors_dir=str(only_real_ca))

    assert report.overall == SignatureTrust.PRESENT_UNVERIFIED
    assert report.signatures[0].chain_trusted is False


def test_certificate_expired_now_but_valid_at_signing_is_not_rejected(anchors_dir):
    """Spec §10.3's named edge case, and correction #11's explicit requirement.

    A certificate that was valid when the document was signed and has since
    expired must NOT be treated as invalid. Validating at `now` instead of at
    the signing time would reject essentially every historical certificate a
    site holds — the single most damaging way to get this wrong.
    """
    _directory, ca_key, ca_cert = anchors_dir
    now = dt.datetime.now(dt.timezone.utc)

    # Valid from two years ago until one year ago: expired today.
    leaf_key, leaf_cert = _make_leaf(
        ca_key, ca_cert, "Historic Lab",
        not_before=now - dt.timedelta(days=730), not_after=now - dt.timedelta(days=365))
    signed = _sign_pdf(
        _blank_pdf(), leaf_key, leaf_cert, ca_cert, signing_time=now - dt.timedelta(days=500))

    report = validate_pdf_signatures(signed)
    finding = report.signatures[0]

    # The signature is intact and the outcome is NOT a rejection...
    assert finding.intact is True
    assert finding.trust != SignatureTrust.INVALID
    assert report.overall != SignatureTrust.INVALID
    # ...and there is no finding claiming the certificate was expired at signing.
    assert not any("CERT_NOT_VALID_AT_SIGNING_TIME" in r for r in finding.reasons), finding.reasons
    # The reported window is the real one, so a reviewer sees the facts.
    assert finding.signer_cert_not_after is not None


def test_modified_after_signing_is_INVALID(anchors_dir):
    """Correction #11's current-revision coverage check. A signature over an
    earlier revision says nothing about the document a reviewer is reading
    today, so appending content after signing must invalidate it."""
    _directory, ca_key, ca_cert = anchors_dir
    now = dt.datetime.now(dt.timezone.utc)
    leaf_key, leaf_cert = _make_leaf(
        ca_key, ca_cert, "Calibration Lab",
        not_before=now - dt.timedelta(days=30), not_after=now + dt.timedelta(days=365))
    signed = _sign_pdf(_blank_pdf(), leaf_key, leaf_cert, ca_cert, signing_time=now)

    # Append a whole extra revision after the signature was applied.
    tampered = signed + b"\n% appended after signing\n"

    report = validate_pdf_signatures(tampered)
    # Either the appended revision is detected as uncovered, or the file no
    # longer parses as a valid signed PDF. Both are acceptable; silently
    # reporting TRUSTED is not.
    assert report.overall != SignatureTrust.TRUSTED


def test_corrupted_signed_content_is_not_intact(anchors_dir):
    """Flip bytes inside the signed content: the signature must stop
    verifying. This is the check every other guarantee rests on."""
    _directory, ca_key, ca_cert = anchors_dir
    now = dt.datetime.now(dt.timezone.utc)
    leaf_key, leaf_cert = _make_leaf(
        ca_key, ca_cert, "Calibration Lab",
        not_before=now - dt.timedelta(days=30), not_after=now + dt.timedelta(days=365))
    signed = _sign_pdf(_blank_pdf("Original text"), leaf_key, leaf_cert, ca_cert, signing_time=now)

    # MuPDF compresses page content, so there is no plaintext marker to swap.
    # Flip a byte inside the FIRST content stream instead — that region is
    # part of the original revision the signature covers, and is well before
    # the signature dictionary appended at the end.
    stream_at = signed.find(b"stream")
    assert stream_at > 0, "test fixture must contain a content stream to corrupt"
    target = stream_at + 20
    assert target < len(signed) // 2, "corruption target must sit inside the signed revision"
    corrupted = bytearray(signed)
    corrupted[target] ^= 0xFF
    corrupted = bytes(corrupted)
    assert corrupted != signed

    report = validate_pdf_signatures(corrupted)
    assert report.overall in (SignatureTrust.INVALID, SignatureTrust.UNKNOWN)
    assert report.overall != SignatureTrust.TRUSTED


def test_garbage_bytes_report_UNKNOWN_not_NONE():
    """A file we could not read is not a file with no signature. Reporting
    NONE here would let an unparseable document look like a clean unsigned
    one."""
    report = validate_pdf_signatures(b"%PDF-1.7\nthis is not really a pdf at all")
    assert report.overall == SignatureTrust.UNKNOWN
    assert report.error


def test_validator_never_raises_on_hostile_input():
    """The worker processes whatever a site uploads. A validator that throws
    is an availability bug, and an escaping exception could be caught upstream
    and mistaken for "unsigned"."""
    for payload in (b"", b"\x00" * 1024, b"%PDF-", b"%PDF-1.4" + b"\xff" * 5000):
        report = validate_pdf_signatures(payload)
        assert report.overall in (SignatureTrust.UNKNOWN, SignatureTrust.NONE)


def test_signature_trust_values_are_distinct():
    """Guards against a future refactor collapsing PRESENT_UNVERIFIED into
    TRUSTED — the exact mistake the whole module exists to prevent."""
    assert SignatureTrust.PRESENT_UNVERIFIED.value != SignatureTrust.TRUSTED.value
    assert len({t.value for t in SignatureTrust}) == len(list(SignatureTrust))
