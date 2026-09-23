"""Opt-in exact-request reuse for interrupted evaluations, never app traffic."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4


class CheckpointClient:
    def __init__(self, delegate, journal: Path, *, document_hash: str, resume: bool = False, shared=None):
        self.delegate = delegate
        self.journal = journal
        self.document_hash = document_hash
        self.shared = shared if shared is not None else {"calls": 0, "reused": 0, "cache": {}, "run_id": str(uuid4())}
        if shared is None and resume and journal.exists():
            for line in journal.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a stopped final write is not a reusable response
                if entry.get("status") == "completed" and isinstance(entry.get("response"), str) and entry.get("key"):
                    self.shared["cache"][entry["key"]] = entry["response"]

    @property
    def calls(self):
        return self.shared["calls"]

    @property
    def reused(self):
        return self.shared["reused"]

    def for_document(self, **kwargs):
        return CheckpointClient(self.delegate.for_document(**kwargs), self.journal,
            document_hash=self.document_hash, shared=self.shared)

    def _record(self, entry):
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.journal.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"run_id": self.shared["run_id"], **entry}, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def complete(self, **kwargs):
        from app.extraction.llm_client import DEFAULT_MAX_TOKENS
        request = {"checkpoint_version": 1, "provider": type(self.delegate).__qualname__,
            "model": getattr(self.delegate, "_model", None), "document_sha256": self.document_hash,
            "pdf_attached": bool(getattr(self.delegate, "_pdf_bytes", None)),
            "document_context": getattr(self.delegate, "_document_text", ""),
            "arguments": {"max_tokens": DEFAULT_MAX_TOKENS, **kwargs}}
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        if key in self.shared["cache"]:
            self.shared["reused"] += 1
            print(f"reused response {self.reused}: {kwargs['prompt'].splitlines()[0][:75]}", flush=True)
            self._record({"status": "reused", "key": key})
            return self.shared["cache"][key]
        self.shared["calls"] += 1
        call = self.calls
        print(f"model call {call}: {kwargs['prompt'].splitlines()[0][:85]}", flush=True)
        self._record({"status": "started", "call": call, "key": key, "request": request})
        try:
            response = self.delegate.complete(**kwargs)
        except Exception as error:
            self._record({"status": "failed", "call": call, "key": key, "error": str(error)})
            raise
        self._record({"status": "completed", "call": call, "key": key, "response": response})
        # Only responses loaded with --resume are reused. Repeated live calls
        # within this run remain independent measurements.
        return response
