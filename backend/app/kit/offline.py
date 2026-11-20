"""Protocol analysis without a model provider: prepared answers for known protocol files.

Turned on with ``KIT_OFFLINE=1`` (the answers shipped in ``offline_answers/``) or
``KIT_OFFLINE_DIR=<folder>`` (a folder with the same layout). The uploaded file still goes through
every real step of the upload (PDF reading, crop and hidden-text handling, table location, clean-up,
the cross-check merge, quote verification, workflow building); only the three requests that would
go to a model are answered from files prepared for that exact file (matched by SHA-256)::

    offline_answers/manifest.json
      {"protocols": [{"name": "NCT02130024", "sha256": "<64 hex>", "model": "nct02130024.model.json",
                      "crosscheck": null | "<file>", "events": null | "<file>", "how": "who wrote it and from what"}]}

A file with no prepared answer fails with ``no_offline_model``; nothing is guessed. No network call
is ever made in this mode.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .llm import AnalysisError

PACKAGED = Path(__file__).parent / "offline_answers"
MODEL_PROMPT = "You are a clinical trial protocol analyst. Below is text"
CROSSCHECK_PROMPT = "You are checking a clinical trial visit-schedule model against the protocol's schedule tables"
EVENTS_PROMPT = "checking ONE thing"


def offline_folder() -> Path | None:
    folder = (os.getenv("KIT_OFFLINE_DIR") or "").strip()
    if folder:
        return Path(folder)
    return PACKAGED if (os.getenv("KIT_OFFLINE") or "").strip().lower() in ("1", "true", "yes") else None


class OfflineKitClient:
    """Stands where OpenAIKitClient stands in ``kit_routes._run``."""

    def __init__(self, folder: Path, sha256: str):
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        entry = next((p for p in manifest.get("protocols") or [] if p.get("sha256") == sha256), None)
        if entry is None:
            raise AnalysisError("no_offline_model", f"Offline mode has no prepared analysis for this file (SHA-256 {sha256[:12]}…). "
                                "It reads the PDF but never calls a model; add the file's answers to "
                                f"{folder / 'manifest.json'} or turn offline mode off.")
        self.folder, self.entry = folder, entry
        self.model = f"offline:{entry['name']}"
        self.on_status = None
        self.calls: list[str] = []

    def _load(self, key: str, default: Any) -> Any:
        name = self.entry.get(key)
        return json.loads((self.folder / name).read_text(encoding="utf-8")) if name else default

    def json(self, prompt: str, *, images: list[dict[str, Any]] | None = None, on_text: Any = None) -> Any:
        if prompt.startswith(MODEL_PROMPT):
            self.calls.append("model")
            return self._load("model", None)
        if prompt.startswith(CROSSCHECK_PROMPT):
            self.calls.append("crosscheck")
            return self._load("crosscheck", {"corrections": [], "notes": []})
        if EVENTS_PROMPT in prompt[:400]:
            self.calls.append("events")
            return self._load("events", {"events": []})
        # A request this mode has no prepared answer for (a borderless table read from its image).
        raise AnalysisError("no_offline_model", "Offline mode has no prepared answer for this analysis step.")
