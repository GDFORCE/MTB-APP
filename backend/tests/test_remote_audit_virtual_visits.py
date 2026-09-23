"""Virtual visit sessions — spec §12, §16.10.

The two exit criteria from the plan drive most of this file:

  * "E2EE + recording simultaneously is provably impossible to create"
  * "a session can be SCHEDULED before consent exists, but no join/media/
     recording occurs before consent is recorded"

Both are proven here rather than asserted, and the LiveKit-unavailable tests
run for real: no credentials are configured in this environment, so the
"video down, everything else still works" path is genuinely exercised.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.services.remote_audit import virtual_visits as vv  # noqa: E402
from app.services.remote_audit.db import get_db  # noqa: E402

RUN = uuid.uuid4().hex[:8]
AUDIT = f"audit-{RUN}-vv"
HOST = f"host-{RUN}"
SITE = f"site-{RUN}"
START = datetime.now(timezone.utc) + timedelta(hours=1)


@pytest_asyncio.fixture
async def db():
    database = get_db()
    await vv.ensure_indexes(database)
    yield database
    await database[vv.SESSIONS_COLLECTION].delete_many({"audit_id": {"$regex": f"^audit-{RUN}"}})
    await database[vv.OBSERVATIONS_COLLECTION].delete_many({"audit_id": {"$regex": f"^audit-{RUN}"}})


async def schedule(db, **kwargs):
    return await vv.schedule_session(
        db, audit_id=kwargs.pop("audit_id", AUDIT), scheduled_by=HOST,
        scheduled_start_utc=kwargs.pop("scheduled_start_utc", START), **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# E2EE and recording are structurally exclusive
# ═══════════════════════════════════════════════════════════════════════════

def test_privacy_mode_is_one_value_not_two_flags():
    """The design guarantee. If these were two booleans, a careless caller
    could set both — and a server cannot record media it cannot decrypt, so
    the UI would be lying about one of them."""
    assert len(vv.RECORDING_MODES) == 1
    assert vv.PrivacyMode.SERVER_RECORDED in vv.RECORDING_MODES
    assert vv.PrivacyMode.E2EE_NO_RECORDING not in vv.RECORDING_MODES


def test_recording_is_refused_on_an_e2ee_session():
    with pytest.raises(vv.VirtualVisitError) as exc:
        vv.assert_recording_allowed(vv.PrivacyMode.E2EE_NO_RECORDING.value)
    assert exc.value.code == "RECORDING_NOT_PERMITTED"
    assert "not possible" in str(exc.value)


def test_recording_is_refused_on_a_no_recording_server_session():
    with pytest.raises(vv.VirtualVisitError) as exc:
        vv.assert_recording_allowed(vv.PrivacyMode.SERVER_NO_RECORDING.value)
    assert exc.value.code == "RECORDING_NOT_PERMITTED"


def test_recording_is_allowed_only_on_the_recorded_mode():
    vv.assert_recording_allowed(vv.PrivacyMode.SERVER_RECORDED.value)  # must not raise


def test_an_unknown_privacy_mode_is_refused_rather_than_defaulted():
    with pytest.raises(vv.VirtualVisitError) as exc:
        vv.assert_recording_allowed("RECORD_EVERYTHING_PLEASE")
    assert exc.value.code == "UNKNOWN_PRIVACY_MODE"


@pytest.mark.asyncio
async def test_an_e2ee_session_can_never_start_a_recording(db):
    """The exit criterion, end to end: even with consent recorded and the
    session live, egress on an E2EE room is refused."""
    session = await schedule(db, privacy_mode=vv.PrivacyMode.E2EE_NO_RECORDING.value)
    assert session["recording_enabled"] is False

    await vv.record_consent(db, session_id=session["id"], user_id=SITE, granted=True,
                            privacy_mode_shown=vv.PrivacyMode.E2EE_NO_RECORDING.value)
    await vv.transition_session(db, session["id"], vv.SessionState.LIVE.value)

    with pytest.raises(vv.VirtualVisitError) as exc:
        await vv.start_recording(db, session["id"], started_by=HOST)
    assert exc.value.code == "RECORDING_NOT_PERMITTED"


@pytest.mark.asyncio
async def test_no_recording_is_the_default(db):
    """Spec §12.2: recording off unless the jurisdiction or template requires
    it. A default-on recording mode would record calls nobody expected to be
    recorded."""
    session = await schedule(db)
    assert session["privacy_mode"] == vv.PrivacyMode.SERVER_NO_RECORDING.value
    assert session["recording_enabled"] is False


@pytest.mark.asyncio
async def test_there_is_no_path_to_change_the_privacy_mode_after_scheduling(db):
    """Participants consent to a stated posture. A posture that can change
    after they agreed is not one they agreed to — so no update function for it
    exists at all."""
    session = await schedule(db, privacy_mode=vv.PrivacyMode.SERVER_NO_RECORDING.value)
    module_functions = [name for name in dir(vv) if "privacy" in name.lower() and "set" in name.lower()]
    assert module_functions == [], f"a privacy-mode setter exists: {module_functions}"
    assert (await vv.get_session(db, session["id"]))["privacy_mode"] == \
        vv.PrivacyMode.SERVER_NO_RECORDING.value


# ═══════════════════════════════════════════════════════════════════════════
# Consent gates joining, not scheduling (v1.3 correction #4)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_session_can_be_scheduled_before_anyone_has_consented(db):
    """The correction that v1.2 got wrong: a record must exist to be scheduled
    at all, so PENDING consent cannot block creation."""
    session = await schedule(db)
    assert session["state"] == vv.SessionState.SCHEDULED.value
    assert session["consent_status"] == vv.ConsentStatus.PENDING.value
    assert vv.consent_permits_join(session) is False


@pytest.mark.asyncio
async def test_nobody_can_join_before_consent_is_recorded(db):
    session = await schedule(db)
    with pytest.raises(vv.VirtualVisitError) as exc:
        await vv.issue_join_token(db, session_id=session["id"], user_id=SITE,
                                  display_name="Site", role="presenter")
    assert exc.value.code == "CONSENT_REQUIRED"
    assert "no media may flow" in str(exc.value)


@pytest.mark.asyncio
async def test_a_session_cannot_go_live_before_consent(db):
    session = await schedule(db)
    with pytest.raises(vv.VirtualVisitError) as exc:
        await vv.transition_session(db, session["id"], vv.SessionState.LIVE.value)
    assert exc.value.code == "CONSENT_REQUIRED"


@pytest.mark.asyncio
async def test_recording_cannot_start_before_consent(db):
    session = await schedule(db, privacy_mode=vv.PrivacyMode.SERVER_RECORDED.value)
    with pytest.raises(vv.VirtualVisitError) as exc:
        await vv.start_recording(db, session["id"], started_by=HOST)
    assert exc.value.code == "CONSENT_REQUIRED"


@pytest.mark.asyncio
async def test_granted_consent_unblocks_joining(db):
    session = await schedule(db)
    updated = await vv.record_consent(
        db, session_id=session["id"], user_id=SITE, granted=True,
        privacy_mode_shown=vv.PrivacyMode.SERVER_NO_RECORDING.value)
    assert updated["consent_status"] == vv.ConsentStatus.GRANTED.value
    assert vv.consent_permits_join(updated) is True


@pytest.mark.asyncio
async def test_a_single_decline_blocks_the_call(db):
    """Consent is not a majority vote. One participant declining is enough,
    and a later approval from someone else does not clear it."""
    session = await schedule(db)
    await vv.record_consent(db, session_id=session["id"], user_id=SITE, granted=False,
                            privacy_mode_shown=vv.PrivacyMode.SERVER_NO_RECORDING.value)
    after_decline = await vv.get_session(db, session["id"])
    assert after_decline["consent_status"] == vv.ConsentStatus.DECLINED.value

    await vv.record_consent(db, session_id=session["id"], user_id=HOST, granted=True,
                            privacy_mode_shown=vv.PrivacyMode.SERVER_NO_RECORDING.value)
    after_approval = await vv.get_session(db, session["id"])
    assert after_approval["consent_status"] == vv.ConsentStatus.DECLINED.value
    assert vv.consent_permits_join(after_approval) is False


@pytest.mark.asyncio
async def test_consent_given_for_a_different_privacy_mode_is_refused(db):
    """If they agreed to a call that wasn't being recorded and the session is
    recorded, that is not consent to THIS call."""
    session = await schedule(db, privacy_mode=vv.PrivacyMode.SERVER_RECORDED.value)
    with pytest.raises(vv.VirtualVisitError) as exc:
        await vv.record_consent(
            db, session_id=session["id"], user_id=SITE, granted=True,
            privacy_mode_shown=vv.PrivacyMode.SERVER_NO_RECORDING.value)
    assert exc.value.code == "CONSENT_MODE_MISMATCH"
    assert (await vv.get_session(db, session["id"]))["consent_status"] == \
        vv.ConsentStatus.PENDING.value


@pytest.mark.asyncio
async def test_every_consent_decision_is_kept_with_what_was_shown(db):
    session = await schedule(db)
    mode = vv.PrivacyMode.SERVER_NO_RECORDING.value
    await vv.record_consent(db, session_id=session["id"], user_id=SITE, granted=True,
                            privacy_mode_shown=mode)
    await vv.record_consent(db, session_id=session["id"], user_id=HOST, granted=True,
                            privacy_mode_shown=mode)
    records = (await vv.get_session(db, session["id"]))["consent_records"]
    assert len(records) == 2
    # What the participant was actually told is part of the record.
    assert all(r["privacy_mode_shown"] == mode for r in records)


@pytest.mark.asyncio
async def test_a_session_with_consent_not_required_can_join_immediately(db):
    session = await schedule(db, consent_required=False)
    assert session["consent_status"] == vv.ConsentStatus.NOT_REQUIRED.value
    assert vv.consent_permits_join(session) is True


# ═══════════════════════════════════════════════════════════════════════════
# Token grants
# ═══════════════════════════════════════════════════════════════════════════

def test_an_observer_cannot_publish():
    """So a second reviewer can watch without becoming another camera in a
    patient-care area."""
    assert vv.build_participant_grants("observer")["publish"] is False
    assert vv.build_participant_grants("observer")["subscribe"] is True


def test_only_a_host_may_admit_others():
    assert vv.build_participant_grants("host")["admit"] is True
    assert vv.build_participant_grants("presenter")["admit"] is False
    assert vv.build_participant_grants("observer")["admit"] is False


def test_an_unknown_call_role_is_refused():
    with pytest.raises(vv.VirtualVisitError) as exc:
        vv.build_participant_grants("superuser")
    assert exc.value.code == "UNKNOWN_PARTICIPANT_ROLE"


def test_no_role_is_granted_recording_or_admin_rights():
    """Egress is started server-side after assert_recording_allowed — never by
    a client holding a token, however senior the person is."""
    for role in vv.PARTICIPANT_ROLES:
        grants = vv.build_participant_grants(role)
        assert set(grants) == {"publish", "subscribe", "admit"}
        assert "record" not in grants
        assert "room_admin" not in grants


# ═══════════════════════════════════════════════════════════════════════════
# Provider outage isolation (v1.3 correction #15)
# ═══════════════════════════════════════════════════════════════════════════

def livekit_is_configured() -> bool:
    return vv.livekit_configured()


@pytest.mark.skipif(livekit_is_configured(), reason="requires LiveKit NOT configured")
def test_minting_a_token_without_credentials_reports_unavailable():
    with pytest.raises(vv.LiveKitUnavailable) as exc:
        vv.mint_participant_token(room_name="r", identity="u", display_name="U", role="presenter")
    assert exc.value.code == "LIVEKIT_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.skipif(livekit_is_configured(), reason="requires LiveKit NOT configured")
async def test_scheduling_still_works_with_no_video_provider(db):
    """Correction #15: LiveKit being down disables joining, and nothing else.
    Scheduling, consent and observations all keep working, which is what lets
    the rest of the audit proceed."""
    session = await schedule(db)
    assert session["state"] == vv.SessionState.SCHEDULED.value

    await vv.record_consent(db, session_id=session["id"], user_id=SITE, granted=True,
                            privacy_mode_shown=vv.PrivacyMode.SERVER_NO_RECORDING.value)
    observation = await vv.add_observation(
        db, session_id=session["id"], author_id=HOST, body="Noted during the pre-call check.")
    assert observation["sequence"] == 1

    # Only joining is unavailable.
    with pytest.raises(vv.LiveKitUnavailable):
        await vv.issue_join_token(db, session_id=session["id"], user_id=SITE,
                                  display_name="Site", role="presenter")


# ═══════════════════════════════════════════════════════════════════════════
# Session lifecycle
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_an_ended_session_cannot_be_rejoined(db):
    session = await schedule(db, consent_required=False)
    await vv.transition_session(db, session["id"], vv.SessionState.LIVE.value)
    await vv.transition_session(db, session["id"], vv.SessionState.ENDED.value)
    with pytest.raises(vv.VirtualVisitError) as exc:
        await vv.issue_join_token(db, session_id=session["id"], user_id=SITE,
                                  display_name="Site", role="presenter")
    assert exc.value.code == "SESSION_ENDED"


@pytest.mark.asyncio
async def test_a_cancelled_session_cannot_be_joined_or_restarted(db):
    session = await schedule(db, consent_required=False)
    await vv.transition_session(db, session["id"], vv.SessionState.CANCELLED.value)
    with pytest.raises(vv.VirtualVisitError) as exc:
        await vv.issue_join_token(db, session_id=session["id"], user_id=SITE,
                                  display_name="Site", role="presenter")
    assert exc.value.code == "SESSION_CANCELLED"
    with pytest.raises(vv.VirtualVisitError):
        await vv.transition_session(db, session["id"], vv.SessionState.LIVE.value)


def test_terminal_session_states_are_terminal():
    assert vv.SESSION_TRANSITIONS[vv.SessionState.ENDED.value] == set()
    assert vv.SESSION_TRANSITIONS[vv.SessionState.CANCELLED.value] == set()


@pytest.mark.asyncio
async def test_room_names_are_unguessable_and_unique(db):
    """A room name is effectively a shared secret for anyone who can reach the
    LiveKit server, so it must not be derivable from the audit id."""
    first = await schedule(db)
    second = await schedule(db)
    assert first["room_name"] != second["room_name"]
    assert len(first["room_name"]) >= 32


# ═══════════════════════════════════════════════════════════════════════════
# Observations and egress reconciliation
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_observations_are_append_only_and_sequenced(db):
    session = await schedule(db)
    await vv.add_observation(db, session_id=session["id"], author_id=HOST, body="First note.")
    await vv.add_observation(db, session_id=session["id"], author_id=HOST, body="Second note.")
    observations = await vv.list_observations(db, session["id"])
    assert [o["sequence"] for o in observations] == [1, 2]
    assert observations[0]["body"] == "First note."


@pytest.mark.asyncio
async def test_an_empty_observation_is_refused(db):
    session = await schedule(db)
    with pytest.raises(vv.VirtualVisitError) as exc:
        await vv.add_observation(db, session_id=session["id"], author_id=HOST, body="   ")
    assert exc.value.code == "OBSERVATION_BODY_REQUIRED"


@pytest.mark.asyncio
async def test_egress_reconciliation_is_refused_for_a_non_recording_session(db):
    """A stale or spoofed webhook must not be able to attach a recording to a
    session whose mode never permitted one."""
    session = await schedule(db, privacy_mode=vv.PrivacyMode.E2EE_NO_RECORDING.value)
    with pytest.raises(vv.VirtualVisitError) as exc:
        await vv.reconcile_egress_completion(
            db, session_id=session["id"], egress_id="eg-1",
            object_key="somewhere/file.mp4", duration_seconds=120)
    assert exc.value.code == "RECORDING_NOT_PERMITTED"


@pytest.mark.asyncio
async def test_egress_reconciliation_records_the_real_file(db):
    """Until the webhook lands, `recording_object_key` is an intention. This
    is what turns it into a fact."""
    session = await schedule(db, privacy_mode=vv.PrivacyMode.SERVER_RECORDED.value)
    updated = await vv.reconcile_egress_completion(
        db, session_id=session["id"], egress_id="eg-1",
        object_key=f"remote-audit/{AUDIT}/virtual-sessions/{session['id']}.mp4",
        duration_seconds=1800)
    assert updated["recording_egress_id"] == "eg-1"
    assert updated["recording_duration_seconds"] == 1800
    assert updated["recording_completed_at"] is not None
