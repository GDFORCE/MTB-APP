"""Durable, resumable extraction stage artifacts (IN-04, AT-20, NF-02).

Every model call the reader makes is stored as a JSON artifact keyed by
everything that determines its answer: document hash, prompt version, model,
call node, the exact pages and a hash of the exact prompt text. A retry of the
same document looks the key up first and reuses a completed (``OK``) answer
instead of calling the model again; a failed or interrupted call is stored
too (for diagnosis) but never reused.

Raw model replies live here - controlled artifact storage - not in log lines.
The store is injected: tests use ``MemoryArtifactStore``; the API passes
``StorageArtifactStore`` over the app's file storage (local disk or S3). With
no store, nothing is persisted and nothing is reused (``NullArtifactStore``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)

ARTIFACT_SCHEMA = "uctsm-extraction-artifact.v1"
ARTIFACT_PREFIX = "extraction-artifacts"


class ArtifactStore(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def put(self, key: str, value: dict[str, Any]) -> bool: ...


class NullArtifactStore:
    """Persists nothing; every call is a fresh model call."""

    durable = False

    def get(self, key: str) -> dict[str, Any] | None:
        return None

    def put(self, key: str, value: dict[str, Any]) -> bool:
        return False


class MemoryArtifactStore:
    """In-process store for tests and single-process retries."""

    durable = True

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.puts = 0

    def get(self, key: str) -> dict[str, Any] | None:
        value = self.items.get(key)
        return json.loads(json.dumps(value)) if value is not None else None

    def put(self, key: str, value: dict[str, Any]) -> bool:
        self.items[key] = json.loads(json.dumps(value, default=str))
        self.puts += 1
        return True


def _run_async(coroutine_factory):
    """Run an async storage call from the reader's worker thread.

    The reader runs in a threadpool (``run_in_threadpool``) under the API, so
    ``anyio.from_thread`` reaches the event loop; outside any loop (worker,
    scripts) a private loop is used.
    """
    try:
        import anyio.from_thread
        return anyio.from_thread.run(coroutine_factory)
    except RuntimeError:
        return asyncio.run(coroutine_factory())


class StorageArtifactStore:
    """Artifacts as JSON blobs in the app's file storage (``storage.py``)."""

    durable = True

    def __init__(self, storage: Any):
        self.storage = storage

    def get(self, key: str) -> dict[str, Any] | None:
        opener = getattr(self.storage, "open", None)
        if opener is None:
            return None
        try:
            data, _content_type = _run_async(lambda: opener(key))
            value = json.loads(data.decode("utf-8"))
            return value if isinstance(value, dict) else None
        except Exception:  # noqa: BLE001 - a missing/unreadable artifact is a cache miss
            return None

    def put(self, key: str, value: dict[str, Any]) -> bool:
        data = json.dumps(value, default=str).encode("utf-8")
        try:
            _run_async(lambda: self.storage.save(key, data, "application/json"))
            return True
        except Exception as error:  # noqa: BLE001 - reported on the ledger, never hidden
            log.warning("extraction artifact %s could not be saved: %s", key, error)
            return False


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def call_key(*, document_hash: str, prompt_version: str, model: str, node: str,
             pages: list[int], prompt: str, system: str) -> str:
    """Storage key of one model call. Same inputs -> same key -> reusable."""
    identity = json.dumps({
        "document_hash": document_hash, "prompt_version": prompt_version, "model": model,
        "node": node, "pages": pages, "prompt_sha": sha256_text(system + "\n" + prompt),
    }, sort_keys=True)
    return f"{ARTIFACT_PREFIX}/{document_hash}/{node}-{sha256_text(identity)[:32]}.json"


def manifest_key(document_hash: str, prompt_version: str) -> str:
    return f"{ARTIFACT_PREFIX}/{document_hash}/manifest-{sha256_text(prompt_version)[:16]}.json"
