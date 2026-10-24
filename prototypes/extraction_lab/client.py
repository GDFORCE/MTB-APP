"""Gemini structured-output adapter injected into the existing extraction runner."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import time
from typing import Callable

from jsonschema import Draft202012Validator

from . import bootstrap  # noqa: F401
from app.extraction.llm_client import DocumentContextClient, ExtractionModelError
from .schemas import response_schema, schema_signature, stage_for_prompt

# Listed by Google's official pricing/model docs on 2026-10-22. Availability
# and free-tier eligibility are checked by the user's project, not this list.
MODEL_CHOICES = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
# Preselected in the app; override with GEMINI_MODEL in the prototype's .env.
DEFAULT_MODEL = "gemini-3.5-flash-lite"
# Tried in this order after the chosen model, when it is overloaded or unreachable.
FALLBACK_PREFERENCE = ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]
# Availability failures only. Key, schema and content errors never trigger a switch.
SWITCH_MARKERS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "INTERNAL", "504",
                  "DEADLINE_EXCEEDED", "404", "NOT_FOUND", "TIMED OUT", "TIMEOUT")


def fallback_chain(primary: str) -> list[str]:
    primary = primary.strip().removeprefix("models/")
    return [primary] + [m for m in FALLBACK_PREFERENCE if m != primary]


def is_availability_error(error: Exception) -> bool:
    text = f"{type(error).__name__} {error}".upper()
    return any(marker in text for marker in SWITCH_MARKERS)


def safe_error(error: Exception, key: str = "") -> str:
    text = str(error)
    if key:
        text = text.replace(key, "[REDACTED]")
    text = re.sub(r"(?i)(key|token|authorization)([=:\s]+)[^\s,&\"']+", r"\1\2[REDACTED]", text)
    return text[:700]


@dataclass
class RequestBudget:
    maximum: int = 8
    interval_seconds: float = 12.0
    calls: int = 0
    last_request: float | None = None
    records: list[dict] = field(default_factory=list)
    progress: Callable[[str], None] | None = None

    def acquire(self, stage: str, pace: bool = True):
        if self.calls >= self.maximum:
            raise ExtractionModelError(f"Session request budget reached ({self.maximum}); completed answers remain in memory for retry.")
        if pace and self.last_request is not None:
            delay = self.interval_seconds - (time.monotonic() - self.last_request)
            if delay > 0:
                if self.progress:
                    self.progress(f"Pacing requests: waiting {delay:.0f}s before {stage}.")
                time.sleep(delay)
        self.calls += 1
        self.last_request = time.monotonic()
        if self.progress:
            self.progress(f"Request {self.calls}/{self.maximum}: {stage}")


class StructuredGeminiClient(DocumentContextClient):
    def __init__(self, *, api_key: str, model: str, budget: RequestBudget,
                 constrained: bool = True, pdf_bytes: bytes | None = None,
                 sdk_client=None, fallback_models: list[str] | tuple[str, ...] = ()):
        if not api_key.strip():
            raise ValueError("Enter a Gemini API key in the sidebar or set GEMINI_API_KEY locally.")
        if not model.strip():
            raise ValueError("Select a model available to your project.")
        self._api_key = api_key
        primary = model.strip().removeprefix("models/")
        chain = [primary] + [m.strip().removeprefix("models/") for m in fallback_models if m.strip()]
        # Shared by scoped copies, so a switch made in one call holds for the whole run.
        self._route = {"chain": list(dict.fromkeys(chain)), "index": 0}
        # The cache key stays on the chosen model so finished pages are reused after a switch.
        self._model = f"{primary}|{'schema' if constrained else 'json-only'}|{schema_signature()}"
        self._document_text = ""
        self._pdf_bytes = pdf_bytes
        self.budget = budget
        self.constrained = constrained
        if sdk_client is None:
            from google import genai
            from google.genai import types
            # One SDK attempt per recorded call; no hidden quota retry storm.
            sdk_client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
                timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)))
        self._sdk = sdk_client

    @property
    def model_name(self) -> str:
        return self._route["chain"][self._route["index"]]

    def complete(self, *, system: str, prompt: str, max_tokens: int = 16000) -> str:
        from google.genai import types

        try:
            stage = stage_for_prompt(prompt)
        except ValueError as error:
            raise ExtractionModelError(str(error)) from error
        schema = response_schema(stage)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=schema if self.constrained else None,
            system_instruction=system, temperature=0.1, max_output_tokens=max_tokens)
        contents = []
        if self._pdf_bytes:
            contents.append(types.Part.from_bytes(data=self._pdf_bytes, mime_type="application/pdf"))
        if self._document_text:
            contents.append(f"<protocol_document>\n{self._document_text}\n</protocol_document>")
        contents.append(prompt)
        first_attempt = True
        while True:
            model = self.model_name
            self.budget.acquire(stage, pace=first_attempt)
            first_attempt = False
            record = {"request": self.budget.calls, "stage": stage, "mode": "schema" if self.constrained else "json-only",
                      "model": model, "status": "STARTED"}
            self.budget.records.append(record)
            started = time.monotonic()
            try:
                response = self._sdk.models.generate_content(model=model, contents=contents, config=config)
                candidates = getattr(response, "candidates", None) or []
                finish = getattr(candidates[0], "finish_reason", None) if candidates else None
                finish = getattr(finish, "value", str(finish or ""))
                record["finish_reason"] = finish
                if finish not in {"STOP", ""}:
                    raise ExtractionModelError(f"Provider stopped with {finish}; this is not a complete extraction.")
                text = response.text or ""
                value = json.loads(text)
                problems = list(Draft202012Validator(schema).iter_errors(value))
                record["schema_valid"] = not problems
                record["schema_errors"] = [f"{'.'.join(map(str, e.path)) or '$'}: {e.message}" for e in problems[:10]]
                # In baseline mode the real reader receives schema-invalid JSON;
                # this lets us measure format failures instead of repairing them here.
                if problems and self.constrained:
                    raise ExtractionModelError("Structured response failed local schema validation: " + record["schema_errors"][0])
                usage = getattr(response, "usage_metadata", None)
                if usage:
                    record["usage"] = usage.model_dump(mode="json", exclude_none=True)
                record["status"] = "OK"
                return text
            except Exception as error:
                record["status"] = "FAILED"
                record["error"] = safe_error(error, self._api_key)
                route = self._route
                if is_availability_error(error) and route["index"] + 1 < len(route["chain"]):
                    route["index"] += 1
                    record["switched_to"] = self.model_name
                    if self.budget.progress:
                        self.budget.progress(f"{model} is unavailable; switching to {self.model_name}.")
                    continue
                raise ExtractionModelError(record["error"]) from None
            finally:
                record["seconds"] = round(time.monotonic() - started, 3)


class ReplayClient(DocumentContextClient):
    """Explicit offline fixtures; never presents scripted data as model output."""
    def __init__(self, metadata: dict, reply: dict):
        self.metadata = metadata
        self.reply = reply
        self._model = "offline-fixture-replay-v1"
        self._pdf_bytes = None
        self._document_text = ""

    def complete(self, *, system: str, prompt: str, max_tokens: int = 16000) -> str:
        stage = stage_for_prompt(prompt)
        if stage == "repair":
            raise ExtractionModelError("Offline fixture contains an invalid fact; no synthetic correction is supplied.")
        return json.dumps(self.metadata if stage == "metadata" else self.reply)
