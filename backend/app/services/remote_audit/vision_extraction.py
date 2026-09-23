"""AI-assisted structured extraction from equipment/certificate photos.

Production-Grade Implementation Plan v1.3, Phases 3 (equipment auto-identify
from a photo) and 4 (calibration-certificate date extraction) — the concept
is lifted from a local prototype (D:\\...\\pssv-equipment-register) that did
the same thing against Kimi K3; this reuses Claude/Gemini instead, since both
are already configured in this backend (ANTHROPIC_API_KEY / GEMINI_API_KEY)
for protocol extraction, rather than adding a third AI vendor.

Deliberately its OWN small module rather than extending
`app/extraction/llm_client.py`'s `AnthropicClient`/`GeminiClient` in place:
those classes are tuned for PDF protocol extraction (prompt caching, a
19-category pipeline) and are load-bearing, well-tested infrastructure this
work should not risk destabilizing. What's safely reusable from there --
`parse_json_object`, the error types, the default model names -- is imported
directly; the image-capable API calls are implemented fresh here.

Human-in-the-loop, always (this app's own architecture doc: "extract ->
review -> commit", "no silent writes -- ever", "fallback = manual entry,
always available"). Every result is a SUGGESTION with a confidence level and
raw-response provenance; nothing here writes to the system of record -- the
caller stores the suggestion separately from a reviewer's confirmed value,
exactly like Phase 4's OCR-assist design elsewhere in this plan.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Tuple

from app.extraction.llm_client import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_MODEL,
    ExtractionModelError,
    ExtractionNotConfigured,
    parse_json_object,
)

Confidence = Literal["high", "medium", "low"]

EQUIPMENT_PHOTO_PROMPT = """You are an equipment verification assistant for a clinical trial remote site audit. You will be shown a photo of medical equipment.

Respond ONLY with a single JSON object, no markdown, no backticks, no explanation. Schema:
{"name": string|null, "manufacturer": string|null, "model": string|null, "serial_number": string|null, "category": string|null, "notes": string|null, "confidence": "high"|"medium"|"low", "confidence_notes": string|null}

Use null for anything not clearly visible or legible. NEVER invent a serial number, model number, or manufacturer name -- if you cannot read it with confidence, use null and explain why in confidence_notes."""

CERTIFICATE_PHOTO_PROMPT = """You are reviewing a calibration/preventive-maintenance/inspection certificate for a piece of clinical trial site equipment.

Respond ONLY with a single JSON object, no markdown, no backticks, no explanation. Schema:
{"certificate_number": string|null, "calibration_date": string|null, "due_date": string|null, "calibrated_by": string|null, "result": string|null, "confidence": "high"|"medium"|"low", "confidence_notes": string|null}

Dates must be ISO YYYY-MM-DD. calibration_date is the LAST completed calibration/inspection date; due_date is the next calibration due date. Use null for anything not visible or legible. NEVER invent a certificate number or a date -- an ambiguous date format (e.g. 03/04/2026) must be flagged in confidence_notes, not silently interpreted."""

_VALID_CONFIDENCE = ("high", "medium", "low")


@dataclass(frozen=True)
class VisionExtractionResult:
    fields: Dict[str, Any]
    confidence: Confidence
    confidence_notes: Optional[str]
    provider: str
    model: str
    raw_response: str


def _anthropic_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _gemini_configured() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


async def _call_claude_vision(*, image_bytes: bytes, media_type: str, prompt: str) -> Tuple[str, str]:
    import anthropic  # lazy so the app boots without the dependency or key

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = os.environ.get("REMOTE_AUDIT_VISION_MODEL_ANTHROPIC", DEFAULT_MODEL)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64", "media_type": media_type,
                            "data": base64.standard_b64encode(image_bytes).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
    except Exception as error:  # noqa: BLE001 -- re-raised as our own type
        raise ExtractionModelError(f"model request failed: {error}") from error
    text = "".join(
        getattr(block, "text", "")
        for block in (getattr(response, "content", None) or [])
        if getattr(block, "type", "") == "text"
    )
    return text, model


async def _call_gemini_vision(*, image_bytes: bytes, media_type: str, prompt: str) -> Tuple[str, str]:
    from google import genai  # lazy so the app boots without the dependency or key
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.environ.get("REMOTE_AUDIT_VISION_MODEL_GEMINI", DEFAULT_GEMINI_MODEL)
    try:
        response = client.models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=image_bytes, mime_type=media_type), prompt],
            config=types.GenerateContentConfig(
                max_output_tokens=1024,
                temperature=0.1,
                # See AnthropicClient/GeminiClient's identical setting in
                # app/extraction/llm_client.py: a thinking-capable model
                # otherwise shares its token budget with invisible reasoning,
                # which can truncate the JSON answer.
                thinking_config=types.ThinkingConfig(thinking_budget=1),
            ),
        )
    except Exception as error:  # noqa: BLE001
        raise ExtractionModelError(f"model request failed: {error}") from error
    return response.text or "", model


async def analyze_image(*, image_bytes: bytes, media_type: str, prompt: str) -> VisionExtractionResult:
    """Tries Claude first, falls back to Gemini if Claude isn't configured --
    this app's existing 'one AI adapter, swappable provider' pattern. Raises
    ExtractionNotConfigured only if NEITHER is configured; the caller then
    falls back to manual entry, never a crash (LLD doc's own guardrail:
    "fallback = manual entry, always available").
    """
    if _anthropic_configured():
        text, model = await _call_claude_vision(image_bytes=image_bytes, media_type=media_type, prompt=prompt)
        provider = "anthropic"
    elif _gemini_configured():
        text, model = await _call_gemini_vision(image_bytes=image_bytes, media_type=media_type, prompt=prompt)
        provider = "gemini"
    else:
        raise ExtractionNotConfigured("neither ANTHROPIC_API_KEY nor GEMINI_API_KEY is set")

    parsed = parse_json_object(text)
    confidence = parsed.get("confidence")
    if confidence not in _VALID_CONFIDENCE:
        confidence = "low"  # an unparseable/missing confidence is never treated as high
    return VisionExtractionResult(
        fields={k: v for k, v in parsed.items() if k not in ("confidence", "confidence_notes")},
        confidence=confidence,  # type: ignore[arg-type]
        confidence_notes=parsed.get("confidence_notes"),
        provider=provider, model=model, raw_response=text,
    )


async def analyze_equipment_photo(*, image_bytes: bytes, media_type: str) -> VisionExtractionResult:
    return await analyze_image(image_bytes=image_bytes, media_type=media_type, prompt=EQUIPMENT_PHOTO_PROMPT)


async def analyze_certificate_photo(*, image_bytes: bytes, media_type: str) -> VisionExtractionResult:
    return await analyze_image(image_bytes=image_bytes, media_type=media_type, prompt=CERTIFICATE_PHOTO_PROMPT)
