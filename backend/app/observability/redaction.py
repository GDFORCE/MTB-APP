"""PHI / secret redaction for logs, traces and metric labels (P13-01).

Redaction is deny-by-default for free text that looks like an identifier and key-based for
structured data. It is a safety net for telemetry, not a licence to log patient data.
"""
from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"
#: A key containing any of these fragments has its whole value replaced.
SENSITIVE_KEY_FRAGMENTS = (
    "password", "passwd", "secret", "token", "authorization", "cookie", "api_key", "apikey",
    "otp", "private_key", "credential", "signature",
    "patient_name", "first_name", "last_name", "full_name", "email", "phone", "address",
    "date_of_birth", "dob", "ssn", "national_id", "mrn", "medical_record", "note", "narrative",
    "payload", "quote", "snapshot",
)
_PATTERNS = (
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer " + REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), REDACTED),   # JWT
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), REDACTED),                    # e-mail
    (re.compile(r"(?i)(mongodb(?:\+srv)?|postgres(?:ql)?|redis)://[^\s'\"]+"), REDACTED),         # URLs with creds
    (re.compile(r"(?<!\d)(?:\+?\d[\s-]?){9,15}(?!\d)"), REDACTED),                                # phone-like
)
MAX_DEPTH = 8
MAX_TEXT = 2000


def redact_text(value: str) -> str:
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "...[TRUNCATED]"


def _sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def redact(value: Any, *, _depth: int = 0) -> Any:
    if _depth > MAX_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        return {str(key): (REDACTED if _sensitive(str(key)) else redact(item, _depth=_depth + 1))
                for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item, _depth=_depth + 1) for item in list(value)[:200]]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))
