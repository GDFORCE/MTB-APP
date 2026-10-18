"""Legacy upload / consume wrappers are mapped to the central capability.

Static (source) completeness check plus behaviour covered by the journey and
extraction-endpoint suites. The wrappers keep their LEGACY ownership checks and the
P2 identity gate; the capability is the central, grant-aware, audited decision.
"""

from __future__ import annotations

import re
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
WRAPPERS = [
    "extract_protocol_details", "extract_protocol_alias", "extract_schedule",
    "consume_protocol_extraction", "build_canonical_schedule",
]


def body_of(name: str) -> str:
    start = SOURCE.index(f"async def {name}(")
    following = re.search(r"\n@api\.|\nasync def |\ndef ", SOURCE[start + 10:])
    return SOURCE[start:start + 10 + (following.start() if following else 4000)]


def test_every_live_upload_and_consume_wrapper_checks_the_central_capability_first():
    for name in WRAPPERS:
        body = body_of(name)
        check = body.find("_require_uctsm_capability(user, authz_catalog.Capability.SCHEDULE_EXTRACT)")
        assert check != -1, f"{name} bypasses the central capability"
        for guard in ("db.trials.find_one", "await file.read", "db.protocol_extractions"):
            hit = body.find(guard)
            assert hit == -1 or check < hit, f"{name}: capability must precede {guard}"


def test_consume_keeps_draft_ownership_and_the_identity_recheck():
    body = body_of("consume_protocol_extraction")
    assert "'user_id': user['id']" in body                       # trusted draft ownership
    assert "_require_verified_draft(draft, user, trial_id)" in body


def test_wrappers_do_not_widen_authoring_approval_or_adoption():
    for name in WRAPPERS:
        body = body_of(name)
        for forbidden in ("Capability.SCHEDULE_APPROVE", "Capability.SCHEDULE_AUTHOR",
                          "Capability.AMENDMENT_ADOPT"):
            assert forbidden not in body
