"""File integrity checks for the evidence/document worker pipeline: real
magic-byte sniffing (spec §8.4 MIME_MATCH) and an optional malware scanner
(spec §8.4 MALWARE_SCAN). Deliberately its own small module, independent of
server.py's `_ALLOWED_UPLOADS` table, so the worker process doesn't need to
import server.py's whole module graph (see db.py's docstring for the same
reasoning).

Malware scanning is strict, not best-effort (v1.3 correction #15): when no
scanner is configured, `scan()` returns `MalwareScanStatus.UNAVAILABLE` --
the caller (the worker's evidence.verify handler) must keep the file
QUARANTINED/PROCESSING_BLOCKED in that case, never treat UNAVAILABLE as CLEAN.
"""
from __future__ import annotations

import io
import os
from enum import Enum
from typing import Optional, Tuple

# (allowed claimed content-types, magic-byte prefixes to check at offset 0).
# MP4/MOV (ISO-BMFF containers) put their box type at offset 4, not 0 --
# checked separately below, matching the existing m4a-handling pattern in
# server.py's _ALLOWED_UPLOADS.
_MAGIC_BY_MEDIA_TYPE = {
    "image/jpeg": ({"image/jpeg"}, (b"\xff\xd8\xff",)),
    "image/png": ({"image/png"}, (b"\x89PNG\r\n\x1a\n",)),
    "image/webp": ({"image/webp"}, (b"RIFF",)),  # WEBP fourcc is at offset 8, checked below
    # Phase 4 (documents). The header must be at the very start: a polyglot
    # that merely CONTAINS "%PDF-" further in is not a PDF for our purposes,
    # it's a file pretending to be one.
    "application/pdf": ({"application/pdf"}, (b"%PDF-",)),
}
_ISO_BMFF_BOX_TYPES = (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip")


def sniff_media_type(data: bytes, claimed_media_type: str) -> bool:
    """True if the real file bytes match the claimed media type. Never
    trusts the claimed content-type alone (spec's MIME_MATCH rule)."""
    if not data:
        return False
    if claimed_media_type in ("video/mp4", "video/quicktime"):
        return len(data) > 8 and data[4:8] in _ISO_BMFF_BOX_TYPES
    spec = _MAGIC_BY_MEDIA_TYPE.get(claimed_media_type)
    if not spec:
        return False
    _, magics = spec
    if claimed_media_type == "image/webp":
        return data.startswith(b"RIFF") and len(data) > 11 and data[8:12] == b"WEBP"
    return any(data.startswith(m) for m in magics)


class MalwareScanStatus(str, Enum):
    CLEAN = "CLEAN"
    DETECTED = "DETECTED"
    UNAVAILABLE = "UNAVAILABLE"


REQUIRE_SCAN_ENV = "REMOTE_AUDIT_REQUIRE_MALWARE_SCAN"


def scan_is_required() -> bool:
    """Whether an unreachable scanner blocks processing.

    Defaults to TRUE. Set `REMOTE_AUDIT_REQUIRE_MALWARE_SCAN=false` only in
    development, where standing up ClamAV to click through a demo is not a
    sensible trade.

    Turning it off does NOT make unscanned files count as clean — nothing in
    this codebase ever reports an unscanned file as CLEAN. It lets processing
    continue and records an explicit `FILE_NOT_SCANNED` finding, so the file
    reaches a reviewer clearly labelled as never having been scanned, rather
    than silently passing as if it had been.
    """
    raw = os.environ.get(REQUIRE_SCAN_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _clamd_configured() -> bool:
    return bool(os.environ.get("CLAMAV_HOST"))


async def scan(data: bytes) -> Tuple[MalwareScanStatus, Optional[str]]:
    """Scans `data` with clamd if configured; returns UNAVAILABLE (never
    CLEAN) if not configured or the scanner can't be reached -- per v1.3
    correction #15, an unreachable scanner must never be treated as a clean
    result. Returns (status, detail)."""
    if not _clamd_configured():
        return MalwareScanStatus.UNAVAILABLE, "CLAMAV_HOST not configured"
    try:
        import clamd  # lazy: only needed when a scanner is actually configured

        host = os.environ["CLAMAV_HOST"]
        port = int(os.environ.get("CLAMAV_PORT", "3310"))
        client = clamd.ClamdNetworkSocket(host=host, port=port, timeout=10)
        result = client.instream(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 -- any scanner failure degrades to UNAVAILABLE, never CLEAN
        return MalwareScanStatus.UNAVAILABLE, f"scan failed: {type(exc).__name__}: {exc}"
    verdict = (result or {}).get("stream", (None, None))
    status, signature = verdict
    if status == "FOUND":
        return MalwareScanStatus.DETECTED, signature
    return MalwareScanStatus.CLEAN, None
