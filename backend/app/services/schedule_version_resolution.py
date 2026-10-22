"""Which approved schedule version applies to a trial, and which a new enrolment uses.

Extracted from ``schedule_service`` (P12-02); ``schedule_service`` re-exports every name."""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.domain.schedule.models import ScheduleStatus


def current_protocol_approved_versions(trial_id: UUID):
    """Approved schedule versions under a trial's CURRENT protocol version.

    AD-4d: before this existed, "which schedule version currently applies to
    this trial" was answered by two independently-written queries -- the
    canonical Add-Patient endpoint filtered by ``Protocol.current_version_id``;
    ``OperationalBridgeService._approved_version`` did not. A schedule approved
    under a SUPERSEDED protocol version was therefore enrollable through the
    operational bridge and refused by canonical Add-Patient -- two different
    answers to one question. Every caller now resolves it through this one
    query, using the stricter of the two prior behaviours.
    """
    return (
        select(db.ScheduleVersion)
        .join(db.ScheduleDefinition, db.ScheduleDefinition.id == db.ScheduleVersion.schedule_definition_id)
        .join(db.ProtocolVersion, db.ProtocolVersion.id == db.ScheduleDefinition.protocol_version_id)
        .join(db.Protocol, db.Protocol.id == db.ProtocolVersion.protocol_id)
        .where(
            db.Protocol.trial_id == trial_id,
            db.Protocol.current_version_id == db.ProtocolVersion.id,
            db.ScheduleVersion.status == ScheduleStatus.APPROVED.value,
        )
    )


def in_force_today(versions: list[db.ScheduleVersion]) -> list[db.ScheduleVersion]:
    """Doc 9 s8: a version approved but not yet effective must not look available."""
    today = date.today()
    return [item for item in versions if item.effective_from is None or item.effective_from <= today]


def _enrollable_protocol_version(session: Session, trial_id: UUID) -> tuple[UUID | None, list[db.ScheduleVersion]]:
    """The protocol version new patients are enrolled under, and its approved versions.

    Normally the protocol's ``current_version_id``. An amendment (a protocol
    version uploaded after the current one) replaces it for NEW enrolments once
    one of its schedule versions is approved AND in force today; until then the
    current version keeps taking enrolments (doc 9 s8, PT-05). A superseded
    (older) protocol version is never returned - AD-4d is unchanged.
    """
    protocol = session.scalar(select(db.Protocol).where(db.Protocol.trial_id == trial_id))
    if protocol is None:
        return None, []
    current = session.get(db.ProtocolVersion, protocol.current_version_id) if protocol.current_version_id else None
    rows = session.execute(
        select(db.ScheduleVersion, db.ProtocolVersion)
        .join(db.ScheduleDefinition, db.ScheduleDefinition.id == db.ScheduleVersion.schedule_definition_id)
        .join(db.ProtocolVersion, db.ProtocolVersion.id == db.ScheduleDefinition.protocol_version_id)
        .where(db.ProtocolVersion.protocol_id == protocol.id,
               db.ScheduleVersion.status == ScheduleStatus.APPROVED.value)
    ).all()
    by_protocol_version: dict[UUID, list[db.ScheduleVersion]] = {}
    created: dict[UUID, datetime | None] = {}
    for version, protocol_version in rows:
        by_protocol_version.setdefault(protocol_version.id, []).append(version)
        created[protocol_version.id] = protocol_version.created_at
    chosen = current.id if current is not None else None
    chosen_rank: tuple | None = None
    for protocol_version_id, versions in by_protocol_version.items():
        if current is not None and protocol_version_id != current.id:
            from app.application.version_intent import activation_eligible
            candidate = session.get(db.ProtocolVersion, protocol_version_id)
            if not activation_eligible(candidate.metadata_json):
                continue
            uploaded = created.get(protocol_version_id)
            if uploaded is None or current.created_at is None or uploaded < current.created_at:
                continue  # superseded, never enrollable again
        in_force = in_force_today(versions)
        if not in_force:
            continue
        rank = (created.get(protocol_version_id) or datetime.min.replace(tzinfo=timezone.utc),
                max((item.approved_at or datetime.min.replace(tzinfo=timezone.utc)) for item in in_force))
        rank = tuple(value.replace(tzinfo=None) if value.tzinfo else value for value in rank)
        if chosen_rank is None or rank > chosen_rank:
            chosen, chosen_rank = protocol_version_id, rank
    return chosen, by_protocol_version.get(chosen, []) if chosen is not None else []


def resolve_current_schedule_version(
    session: Session, trial_id: UUID, schedule_definition_id: UUID,
) -> db.ScheduleVersion:
    """The one schedule version a new enrolment/link against this definition should use.

    The linked definition may belong to another protocol version of the same
    trial than the enrollable one (building an amendment draft re-points the
    operational link at the amendment's definition before it is approved, and an
    older link still names the original). It is then mapped to the matching
    definition of the enrollable protocol version - same schedule type and name,
    or the only one - never guessed between several.
    """
    protocol_version_id, approved_all = _enrollable_protocol_version(session, trial_id)
    approved = [item for item in approved_all if item.schedule_definition_id == schedule_definition_id]
    if not approved and approved_all:
        linked = session.get(db.ScheduleDefinition, schedule_definition_id)
        same_trial = linked is not None and session.scalar(
            select(db.Protocol.trial_id)
            .join(db.ProtocolVersion, db.ProtocolVersion.protocol_id == db.Protocol.id)
            .where(db.ProtocolVersion.id == linked.protocol_version_id)) == trial_id
        if same_trial and linked.protocol_version_id != protocol_version_id:
            definition_ids = {item.schedule_definition_id for item in approved_all}
            candidates = [session.get(db.ScheduleDefinition, item) for item in definition_ids]
            matches = [item for item in candidates if item is not None
                       and item.schedule_type == linked.schedule_type and item.name == linked.name]
            if len(matches) != 1:
                same_type = [item for item in candidates if item is not None and item.schedule_type == linked.schedule_type]
                matches = same_type if len(same_type) == 1 else (candidates if len(candidates) == 1 else [])
            if len(matches) == 1:
                approved = [item for item in approved_all if item.schedule_definition_id == matches[0].id]
            elif candidates:
                raise ValueError(
                    "the approved protocol version has several schedules and none matches the "
                    "linked one; choose the schedule this patient is enrolled under")
    approved.sort(key=lambda item: item.version_number, reverse=True)
    versions_in_force = in_force_today(approved)
    if versions_in_force:
        return versions_in_force[0]
    if approved:
        soonest = min(item.effective_from for item in approved if item.effective_from is not None)
        raise ValueError(
            "no approved canonical schedule is in force today; the approved "
            f"version takes effect on {soonest.isoformat()}"
        )
    raise ValueError("no approved canonical schedule is available for the linked trial")
