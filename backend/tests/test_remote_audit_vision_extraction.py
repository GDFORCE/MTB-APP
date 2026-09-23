"""Tests for AI-assisted equipment/certificate photo extraction
(app/services/remote_audit/vision_extraction.py). Network calls are mocked --
this proves the provider-selection, confidence-parsing, and fallback-to-manual
logic, not that Claude/Gemini's vision APIs work (that's outside this repo's
control to test).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.extraction.llm_client import ExtractionNotConfigured  # noqa: E402
from app.services.remote_audit import vision_extraction as ve  # noqa: E402


@pytest.mark.asyncio
async def test_uses_claude_when_anthropic_key_configured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    async def fake_claude(*, image_bytes, media_type, prompt):
        assert prompt == ve.EQUIPMENT_PHOTO_PROMPT
        return json.dumps({
            "name": "Infusion Pump", "manufacturer": "Acme", "model": "IP-500",
            "serial_number": "SN12345", "category": "infusion", "notes": None,
            "confidence": "high", "confidence_notes": None,
        }), "claude-opus-5"

    async def fail_gemini(**kwargs):
        raise AssertionError("Gemini should not be called when Claude is configured")

    monkeypatch.setattr(ve, "_call_claude_vision", fake_claude)
    monkeypatch.setattr(ve, "_call_gemini_vision", fail_gemini)

    result = await ve.analyze_equipment_photo(image_bytes=b"fake", media_type="image/jpeg")
    assert result.provider == "anthropic"
    assert result.confidence == "high"
    assert result.fields["manufacturer"] == "Acme"
    assert "confidence" not in result.fields
    assert "confidence_notes" not in result.fields


@pytest.mark.asyncio
async def test_falls_back_to_gemini_when_claude_not_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    async def fake_gemini(*, image_bytes, media_type, prompt):
        assert prompt == ve.CERTIFICATE_PHOTO_PROMPT
        return json.dumps({
            "certificate_number": "CAL-2026-001", "calibration_date": "2026-01-15",
            "due_date": "2027-01-15", "calibrated_by": "Acme Service Co",
            "result": "pass", "confidence": "medium", "confidence_notes": "stamp partially obscured",
        }), "gemini-3.6-flash"

    monkeypatch.setattr(ve, "_call_gemini_vision", fake_gemini)

    result = await ve.analyze_certificate_photo(image_bytes=b"fake", media_type="image/png")
    assert result.provider == "gemini"
    assert result.confidence == "medium"
    assert result.confidence_notes == "stamp partially obscured"
    assert result.fields["certificate_number"] == "CAL-2026-001"


@pytest.mark.asyncio
async def test_raises_not_configured_when_neither_provider_available(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ExtractionNotConfigured):
        await ve.analyze_equipment_photo(image_bytes=b"fake", media_type="image/jpeg")


@pytest.mark.asyncio
async def test_missing_confidence_defaults_to_low_never_high(monkeypatch):
    """A model that forgets to report confidence must never be treated as
    confident by default -- this is a correctness-critical default, not a
    cosmetic one."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    async def fake_claude(*, image_bytes, media_type, prompt):
        return json.dumps({"name": "Ventilator"}), "claude-opus-5"

    monkeypatch.setattr(ve, "_call_claude_vision", fake_claude)

    result = await ve.analyze_equipment_photo(image_bytes=b"fake", media_type="image/jpeg")
    assert result.confidence == "low"


@pytest.mark.asyncio
async def test_invalid_confidence_value_defaults_to_low(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    async def fake_claude(*, image_bytes, media_type, prompt):
        return json.dumps({"name": "Ventilator", "confidence": "very sure!"}), "claude-opus-5"

    monkeypatch.setattr(ve, "_call_claude_vision", fake_claude)

    result = await ve.analyze_equipment_photo(image_bytes=b"fake", media_type="image/jpeg")
    assert result.confidence == "low"
