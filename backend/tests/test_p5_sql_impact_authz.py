"""F5: the older SQL impact API cannot be a weaker route around the high-impact grant.

A persisted process transition (hold / resume / stop) needs
``patient.command.high_impact`` in addition to the generic impact right, at typed
preview AND at confirm/replay, derived from the trusted stored proposal. The real
guard and the real registered routes are used (see ``test_uctsm_authz_http``).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.authorization.catalog import Capability                  # noqa: E402
from app.db import models as db                                   # noqa: E402
from test_uctsm_authz_http import (                               # noqa: E402
    ORG_OWNER, ORG_SITE, World, person, world,                    # noqa: F401 (fixture)
)

HIGH = Capability.PATIENT_COMMAND_HIGH_IMPACT.value
TRANSITION = {"target": {"type": "PROCESS", "process_code": "TREATMENT"},
              "to_state": "STOPPED", "effective_at": "2026-02-01",
              "reason": "DECISION", "authority": "PI"}


def preview(world: World, who: str, *, transition: bool):
    body = {"horizon": "2026-12-31", "reason": "clinical decision"}
    if transition:
        body["process_transition"] = TRANSITION
    return world.call(who, "POST", f"/patients/{world.patient_a_id}/schedule/impact-preview", json=body)


def stored_proposal(world: World, *, transition: bool) -> str:
    with world.factory() as session:
        patient = session.get(db.Patient, UUID(world.patient_a_id))
        version = UUID(world.version_a)
        impact = {"events": []}
        if transition:
            impact["process_transition"] = {**TRANSITION, "status": "PENDING_CONFIRMATION"}
        row = db.ScheduleImpactProposal(
            patient_id=patient.id, from_schedule_version_id=version, to_schedule_version_id=version,
            horizon=date(2026, 12, 31), input_hash="0" * 64, impact=impact, status="PENDING",
            reason="r", created_by=uuid4(),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        session.add(row)
        session.commit()
        return str(row.id)


def confirm(world: World, who: str, proposal_id: str):
    return world.call(who, "POST", f"/schedule-impact-proposals/{proposal_id}/confirm",
                      json={"reason": "confirmed"})


def authorized(response) -> bool:
    """Past the guard and the extra check (the service may still say 409/422)."""
    return response.status_code not in (403, 404)


def test_assigned_pi_without_the_grant_cannot_preview_a_stop_but_can_preview_ordinary_impact(world):
    denied = preview(world, "pi_a", transition=True)
    assert denied.status_code == 403
    assert denied.headers.get("X-Authz-Capability") == HIGH
    assert authorized(preview(world, "pi_a", transition=False))        # ordinary workflow intact


def test_explicitly_granted_assigned_pi_passes_authorization(world):
    world.grants.grant_sync(world.pi_a["id"], ORG_SITE, HIGH)
    assert authorized(preview(world, "pi_a", transition=True))


def test_a_grant_does_not_make_an_ineligible_role_a_high_impact_actor(world):
    crc = person("crc", "crc", ORG_SITE)
    world.users["crc"] = crc
    world.access.link(crc, trial="op-trial-A", patient="op-pat-A")
    world.grants.grant_sync(crc["id"], ORG_SITE, HIGH)
    world.grants.grant_sync(crc["id"], ORG_SITE, Capability.PATIENT_IMPACT_CONFIRM.value)
    assert preview(world, "crc", transition=True).status_code == 403
    pid = stored_proposal(world, transition=True)
    assert confirm(world, "crc", pid).status_code == 403


def test_confirm_requires_the_grant_derived_from_the_stored_proposal(world):
    with_transition = stored_proposal(world, transition=True)
    ordinary = stored_proposal(world, transition=False)
    denied = confirm(world, "pi_a", with_transition)
    assert denied.status_code == 403 and denied.headers.get("X-Authz-Capability") == HIGH
    assert authorized(confirm(world, "pi_a", ordinary))               # not a high-impact proposal
    world.grants.grant_sync(world.pi_a["id"], ORG_SITE, HIGH)
    assert authorized(confirm(world, "pi_a", with_transition))


def test_revocation_is_enforced_on_the_next_preview_confirm_and_replay(world):
    record = world.grants.grant_sync(world.pi_a["id"], ORG_SITE, HIGH)
    pid = stored_proposal(world, transition=True)
    assert authorized(preview(world, "pi_a", transition=True))
    asyncio.run(world.grants.revoke(record.id, organization_id=ORG_SITE, revoked_by="admin"))
    assert preview(world, "pi_a", transition=True).status_code == 403
    assert confirm(world, "pi_a", pid).status_code == 403             # a replay is refused too


def test_request_body_cannot_hide_a_transition_from_the_check(world):
    # the stored proposal, not the confirm body, decides: an empty/forged body is irrelevant
    pid = stored_proposal(world, transition=True)
    response = world.call("pi_a", "POST", f"/schedule-impact-proposals/{pid}/confirm",
                          json={"reason": "x", "process_transition": None})
    assert response.status_code in (403, 422)
