"""Protocol document identity: normalization, cited extraction, classification (P2)."""

from .classify import classify_identity
from .extract import (
    claim_from_filename, claim_from_text, extract_identity, normalize_identifier, parse_date,
)
from .models import *  # noqa: F401,F403
from .normalize import NORMALIZATION_VERSION, fingerprint_pages, normalize_text

__all__ = [
    "classify_identity", "claim_from_filename", "claim_from_text", "extract_identity",
    "normalize_identifier", "parse_date", "NORMALIZATION_VERSION", "fingerprint_pages",
    "normalize_text",
]
