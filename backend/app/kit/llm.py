"""The model client for the kit pipeline: OpenAI chat completions, JSON out.

Interface matches the kit's ``client.json(prompt, {images, onText})``. The
model is ``KIT_EXTRACTION_MODEL`` (default ``gpt-6-sol``); the key is the
backend's ``OPENAI_API_KEY``. Nothing here reads the old extraction settings.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable

DEFAULT_MODEL = "gpt-6-sol"
DEFAULT_MAX_COMPLETION_TOKENS = 64000


class AnalysisError(Exception):
    def __init__(self, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.code = code
        self.extra = extra


def _repair(text: str) -> str:
    """Make near-JSON parseable: drop comments and trailing commas (outside strings)."""
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end - 1
        elif c in "}]":
            j = len(out) - 1
            while j >= 0 and out[j].isspace():
                j -= 1
            if j >= 0 and out[j] == ",":
                del out[j]
            out.append(c)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def parse_json_tolerant(text: str) -> Any:
    """The whole reply, else one ```json fence, else the first { or [ to the last } or ];
    each as is, then with comments and trailing commas removed."""
    def attempt(s: str):
        # strict=False allows raw line breaks inside strings, which some models emit.
        try:
            return True, json.loads(s, strict=False)
        except ValueError:
            return False, None

    candidates = [text.strip()]
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
    if fence:
        candidates.append(fence.group(1).strip())
    start = re.search(r"[\[{]", text)
    end = max(text.rfind("}"), text.rfind("]"))
    if start and end > start.start():
        candidates.append(text[start.start():end + 1])
    for candidate in candidates:
        ok, value = attempt(candidate)
        if ok:
            return value
    for candidate in candidates:
        ok, value = attempt(_repair(candidate))
        if ok:
            return value
    raise AnalysisError("invalid_json", "The reply did not contain valid JSON.")


def dump_bad_reply(text: str) -> str | None:
    """Keep an unreadable reply on disk (local, git-ignored) so it can be inspected."""
    try:
        import time
        from pathlib import Path

        folder = Path(__file__).resolve().parents[2] / "uploads" / "kit-debug"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"invalid-reply-{int(time.time())}.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)
    except OSError:
        return None



#: Waits (seconds) between attempts when the model reports it is busy or unreachable.
RETRY_WAITS = (5, 15, 30)


class OpenAIKitClient:
    def __init__(self, *, api_key: str | None = None, model: str | None = None,
                 max_tokens: int | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or ""
        self.model = model or os.getenv("KIT_EXTRACTION_MODEL") or DEFAULT_MODEL
        # Models to try, in order, when the main one stays busy (KIT_FALLBACK_MODELS, comma separated).
        self.fallbacks = [m.strip() for m in (os.getenv("KIT_FALLBACK_MODELS") or "").split(",") if m.strip()]
        self.max_tokens = max_tokens or int(
            os.getenv("KIT_MAX_COMPLETION_TOKENS") or DEFAULT_MAX_COMPLETION_TOKENS)
        #: Called with a short status ("model busy, retrying in 15s") so a UI can show it.
        self.on_status: Callable[[str], None] | None = None

    def _stream_once(self, openai: Any, model: str, content: list[dict[str, Any]],
                     on_text: Callable[[int], None] | None) -> tuple[str, str | None, str | None]:
        # max_retries=0: retrying is handled by json(), with a visible status and fallback models.
        client = openai.OpenAI(api_key=self.api_key, max_retries=0)
        stream = client.chat.completions.create(
            model=model, max_completion_tokens=self.max_tokens, stream=True,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": content}])
        parts: list[str] = []
        finish = None
        refusal = None
        written = 0
        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if getattr(delta, "refusal", None):
                refusal = (refusal or "") + delta.refusal
            if delta.content:
                parts.append(delta.content)
                written += len(delta.content)
                if on_text:
                    on_text(written)
            if choice.finish_reason:
                finish = choice.finish_reason
        return "".join(parts), finish, refusal

    def json(self, prompt: str, *, images: list[dict[str, Any]] | None = None,
             on_text: Callable[[int], None] | None = None) -> Any:
        if not self.api_key:
            raise AnalysisError("no_api_key", "OPENAI_API_KEY is not set on the server.")
        import time

        import openai

        content: list[dict[str, Any]] = []
        for img in images or []:
            content.append({"type": "text", "text": f"Page {img.get('page', '')} image:"})
            content.append({"type": "image_url", "image_url": {
                "url": f"data:{img.get('mediaType', 'image/jpeg')};base64,{img['base64']}"}})
        content.append({"type": "text", "text": prompt
                        + "\n\nYour reply will be parsed by a program: output the JSON value only."})

        def status(message: str) -> None:
            if self.on_status:
                self.on_status(message)

        busy: Exception | None = None
        bad_reply = False
        for model in [self.model, *self.fallbacks]:
            for attempt in range(len(RETRY_WAITS) + 1):
                try:
                    text, finish, refusal = self._stream_once(openai, model, content, on_text)
                except openai.AuthenticationError as error:
                    raise AnalysisError("auth", "The model key was rejected.") from error
                except openai.RateLimitError as error:
                    lowered = str(error).lower()
                    if "insufficient_quota" in lowered or "no credits" in lowered or "credits are depleted" in lowered:
                        raise AnalysisError("no_credits", "The model account has no credits left.") from error
                    busy, bad_reply = error, False
                except (openai.InternalServerError, openai.APIConnectionError, openai.APITimeoutError) as error:
                    busy, bad_reply = error, False
                except openai.BadRequestError as error:
                    raise AnalysisError("prompt_too_large" if "context" in str(error).lower() else "upstream_error",
                                        f"The model request was rejected: {error}") from error
                except openai.OpenAIError as error:
                    raise AnalysisError("upstream_error", f"The model request failed: {error}") from error
                else:
                    if refusal:
                        raise AnalysisError("refused", "The model declined to analyse this document.")
                    if finish == "length":
                        raise AnalysisError("invalid_json", "The reply was cut short. Raise KIT_MAX_COMPLETION_TOKENS.")
                    # Anything but a normal stop means the reply may be cut off (dropped
                    # connection, safety stop): never parse a partial reply as if it were whole.
                    if text.strip() and finish == "stop":
                        try:
                            return parse_json_tolerant(text)
                        except AnalysisError:
                            pass
                    saved = dump_bad_reply(text) if text.strip() else None
                    bad_reply, busy = True, AnalysisError(
                        "invalid_json", f"unreadable reply (finish={finish}, {len(text)} characters)", saved=saved)
                    problem = ("returned an unfinished reply" if finish != "stop" else "returned a reply that was not valid JSON")
                    if attempt < len(RETRY_WAITS):
                        wait = RETRY_WAITS[attempt]
                        status(f"{model} {problem} ({len(text) // 1000}k characters), asking again in {wait}s")
                        time.sleep(wait)
                    continue
                if attempt < len(RETRY_WAITS):
                    wait = RETRY_WAITS[attempt]
                    status(f"{model} is busy, retrying in {wait}s")
                    time.sleep(wait)
            if model != (self.fallbacks or [self.model])[-1]:
                status(f"{model} did not give a usable reply, trying another model")
        if bad_reply:
            raise AnalysisError(
                "invalid_json", "The model did not return a complete, readable answer after several tries. "
                "Try again, or use a stronger model.") from busy
        raise AnalysisError("model_busy", "The model is busy right now. Try again in a few minutes.") from busy
