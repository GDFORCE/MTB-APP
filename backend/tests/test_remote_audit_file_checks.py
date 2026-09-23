"""Tests for magic-byte sniffing and the malware-scan gate
(app/services/remote_audit/file_checks.py) — v1.3 correction #15: an
unconfigured/unreachable scanner must return UNAVAILABLE, never CLEAN.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.remote_audit.file_checks import MalwareScanStatus, scan, sniff_media_type  # noqa: E402

JPEG_MAGIC = b"\xff\xd8\xff\xe0\x00\x10JFIF"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0d"
WEBP_MAGIC = b"RIFF\x24\x00\x00\x00WEBPVP8 "
MP4_MAGIC = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00"


def test_correct_jpeg_bytes_match_claimed_type():
    assert sniff_media_type(JPEG_MAGIC, "image/jpeg") is True


def test_png_bytes_claiming_jpeg_is_rejected():
    assert sniff_media_type(PNG_MAGIC, "image/jpeg") is False


def test_correct_png_bytes_match_claimed_type():
    assert sniff_media_type(PNG_MAGIC, "image/png") is True


def test_correct_webp_bytes_match_claimed_type():
    assert sniff_media_type(WEBP_MAGIC, "image/webp") is True


def test_correct_mp4_bytes_match_claimed_type():
    assert sniff_media_type(MP4_MAGIC, "video/mp4") is True


def test_executable_disguised_as_jpeg_is_rejected():
    """The spec's own named threat: 'file extension says JPG but bytes are
    executable' -- an MZ (Windows PE) header claiming to be a JPEG."""
    exe_bytes = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00"
    assert sniff_media_type(exe_bytes, "image/jpeg") is False


def test_empty_bytes_never_match_anything():
    assert sniff_media_type(b"", "image/jpeg") is False


def test_unsupported_claimed_type_is_rejected():
    assert sniff_media_type(JPEG_MAGIC, "application/x-msdownload") is False


@pytest.mark.asyncio
async def test_scan_is_unavailable_not_clean_when_unconfigured(monkeypatch):
    monkeypatch.delenv("CLAMAV_HOST", raising=False)
    status, detail = await scan(b"anything")
    assert status == MalwareScanStatus.UNAVAILABLE
    assert status != MalwareScanStatus.CLEAN
