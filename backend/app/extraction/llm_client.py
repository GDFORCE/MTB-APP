"""Transport for the canonical extraction pipeline's model calls.

Deliberately thin. It knows how to send a prompt and get JSON back; it knows
nothing about schedules. That separation is what lets the whole extraction
pipeline be tested without a network call, by handing it a scripted client.

The pipeline asks the model one focused question per semantic category rather
than one enormous question about the whole protocol. That costs more calls but
each answer is checkable against a small contract, and a model that fails to
answer one category degrades that category only - it does not corrupt the rest.
"""

from __future__ import annotations

import base64
import copy
from contextlib import closing
import io
import json
import logging
import os
import re
from typing import Any, Protocol

log = logging.getLogger(__name__)

#: Matches the Claude model the rest of the product already uses.
DEFAULT_MODEL = "claude-opus-5"

#: Matches the Gemini model the legacy pipeline already uses
#: (protocol_extraction.py's GeminiProtocolExtractor).
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"

#: Generous, because a schedule-of-assessments answer for one category on a large
#: protocol can be long. Truncated JSON is unrecoverable, so this is not a place
#: to economise.
DEFAULT_MAX_TOKENS = 16000


class ExtractionModelError(RuntimeError):
    """The model could not be reached or did not answer usefully."""


class ExtractionNotConfigured(ExtractionModelError):
    """No API key: the caller should surface this rather than silently degrade."""


class LLMClient(Protocol):
    """Anything that can answer a prompt with text.

    Implementations must not retry silently forever, and must raise
    ExtractionModelError rather than returning a plausible-looking empty answer:
    an empty answer is indistinguishable from "the protocol does not say this",
    and those two must never be confused.
    """

    def complete(self, *, system: str, prompt: str, max_tokens: int = ...) -> str: ...


class DocumentContextClient:
    """Create bounded document inputs without changing credentials or model."""

    def for_document(self, *, document_text: str, page_numbers: list[int]):
        if not page_numbers or len(set(page_numbers)) != len(page_numbers) or min(page_numbers) < 1:
            raise ValueError("Document packets require distinct positive physical page numbers.")
        scoped = copy.copy(self)
        scoped._document_text = (
            "This packet contains only the selected original document pages. Cite ORIGINAL physical "
            "page numbers, not the printed footer or the position in the attached packet.\n"
            + json.dumps({"attached_page_to_original_page": dict(enumerate(page_numbers, start=1))})
            + "\n" + document_text
        )
        if self._pdf_bytes:
            import pypdfium2 as pdfium
            with closing(pdfium.PdfDocument(self._pdf_bytes)) as original, closing(pdfium.PdfDocument.new()) as packet:
                if max(page_numbers) > len(original):
                    raise ValueError("Document packet references a page outside the PDF.")
                packet.import_pages(original, [number - 1 for number in page_numbers])
                output = io.BytesIO()
                packet.save(output)
                scoped._pdf_bytes = output.getvalue()
        return scoped


class AnthropicClient(DocumentContextClient):
    """Claude, via the Anthropic Messages API.

    The document text is sent as a cached prefix. A full extraction asks about
    nineteen semantic categories against the same protocol, so caching the
    document makes every call after the first far cheaper.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        document_text: str = "",
        pdf_bytes: bytes | None = None,
    ):
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._model = model or os.getenv("UCTSM_EXTRACTION_MODEL") or DEFAULT_MODEL
        self._document_text = document_text
        # AD-2a: the native PDF, attached only when at least one page has no
        # usable embedded text (runner.py's build_provider decides this) - a
        # scanned page's own image, which Claude reads natively, is the only
        # ingestion path for it; pypdfium2's text layer has nothing to offer.
        self._pdf_bytes = pdf_bytes

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _client(self):
        if not self._api_key:
            raise ExtractionNotConfigured(
                "ANTHROPIC_API_KEY is not set; extraction cannot run"
            )
        import anthropic  # lazy so the app boots without the dependency or key

        return anthropic.Anthropic(api_key=self._api_key)

    def complete(
        self, *, system: str, prompt: str, max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        client = self._client()
        content: list[dict[str, Any]] = []
        if self._pdf_bytes:
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(self._pdf_bytes).decode("ascii"),
                },
                "cache_control": {"type": "ephemeral"},
            })
        if self._document_text:
            content.append({
                "type": "text",
                "text": f"<protocol_document>\n{self._document_text}\n</protocol_document>",
                "cache_control": {"type": "ephemeral"},
            })
        content.append({"type": "text", "text": prompt})
        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as error:  # noqa: BLE001 - re-raised as our own type
            raise ExtractionModelError(f"model request failed: {error}") from error
        return "".join(
            getattr(block, "text", "")
            for block in (getattr(response, "content", None) or [])
            if getattr(block, "type", "") == "text"
        )


class GeminiClient(DocumentContextClient):
    """Google Gemini, via the genai SDK - the same ``LLMClient`` contract as
    ``AnthropicClient``, so ``ClaudeExtractionProvider`` (the assembler; the
    name predates this client and is unrelated to which provider answers)
    works unchanged regardless of which one is behind it. A fallback
    provider sitting behind the same interface, not a second pipeline.

    Gemini's own native PDF understanding covers the AD-2a image fallback the
    same way Claude's does (``types.Part.from_bytes``), so
    ``build_provider``'s "attach the PDF only when a page needs it" decision
    (runner.py) applies unchanged no matter which client ends up handling
    the call.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        document_text: str = "",
        pdf_bytes: bytes | None = None,
    ):
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        self._model = model or os.getenv("UCTSM_EXTRACTION_MODEL") or DEFAULT_GEMINI_MODEL
        self._document_text = document_text
        self._pdf_bytes = pdf_bytes

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _client(self):
        if not self._api_key:
            raise ExtractionNotConfigured(
                "GEMINI_API_KEY is not set; extraction cannot run"
            )
        from google import genai  # lazy so the app boots without the dependency or key

        return genai.Client(api_key=self._api_key)

    def complete(
        self, *, system: str, prompt: str, max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        client = self._client()
        from google.genai import types

        contents: list[Any] = []
        if self._pdf_bytes:
            contents.append(types.Part.from_bytes(
                data=self._pdf_bytes, mime_type="application/pdf"))
        if self._document_text:
            contents.append(f"<protocol_document>\n{self._document_text}\n</protocol_document>")
        contents.append(prompt)
        try:
            response = client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.1,
                    system_instruction=system,
                    # See GeminiProtocolExtractor's identical setting: a
                    # thinking-capable model otherwise shares max_output_tokens
                    # between invisible reasoning and the actual answer, which
                    # can truncate the JSON before it's written. 1 is the
                    # smallest budget every generation observed so far accepts
                    # (0 is rejected outright by newer models).
                    thinking_config=types.ThinkingConfig(thinking_budget=1),
                ),
            )
        except Exception as error:  # noqa: BLE001 - re-raised as our own type
            raise ExtractionModelError(f"model request failed: {error}") from error
        return response.text or ""


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def parse_json_object(text: str) -> dict[str, Any]:
    """Read one JSON object out of a model reply.

    Tolerates a code fence and a stray preface, because those are formatting
    noise rather than a difference of meaning. Anything else raises: a reply we
    cannot parse must become a visible extraction issue, never an empty result
    that reads as "the protocol does not mention this".
    """
    cleaned = _FENCE.sub("", (text or "").strip())
    if not cleaned:
        raise ExtractionModelError("model returned an empty response")
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        first, last = cleaned.find("{"), cleaned.rfind("}")
        if first < 0 or last <= first:
            raise ExtractionModelError(
                "model response was not JSON"
            ) from None
        try:
            value = json.loads(cleaned[first:last + 1])
        except json.JSONDecodeError as error:
            raise ExtractionModelError(f"model response was not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ExtractionModelError("model response was not a JSON object")
    return value


def parse_json_list(text: str, key: str) -> list[dict[str, Any]]:
    """Read ``{key: [...]}`` from a model reply, tolerating a bare list."""
    cleaned = _FENCE.sub("", (text or "").strip())
    if cleaned.startswith("["):
        try:
            items = json.loads(cleaned)
        except json.JSONDecodeError as error:
            raise ExtractionModelError(f"model response was not valid JSON: {error}") from error
    else:
        value = parse_json_object(text)
        if key not in value:
            raise ExtractionModelError(f"model response is missing required list {key!r}")
        items = value[key]
    if not isinstance(items, list):
        raise ExtractionModelError(f"expected a list under {key!r}")
    if any(not isinstance(item, dict) for item in items):
        raise ExtractionModelError(f"every entry under {key!r} must be an object")
    return items
