"""Which approved schedule version applies to a trial / definition (AD-4d).

One resolver, used by every caller, on the repository ports. A behaviour-
preserving port of ``resolve_current_schedule_version`` and its helpers in
``app.services.schedule_service``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from .authoring import ScheduleVersionHeader
from .ports import UnitOfWork

_MIN = datetime.min.replace(tzinfo=timezone.utc)


def in_force_today(
    versions: list[ScheduleVersionHeader], *, today: date | None = None,
) -> list[ScheduleVersionHeader]:
    """Doc 9 s8: a version approved but not yet effective must not look available."""
    day = today or date.today()
    return [item for item in versions if item.effective_from is None or item.effective_from <= day]


def current_protocol_approved_versions(
    tx: UnitOfWork, organization_id: UUID, trial_id: UUID,
) -> list[ScheduleVersionHeader]:
    """Approved versions under the trial's CURRENT protocol version (AD-4d)."""
    protocol = tx.authoring.find_protocol_for_trial(trial_id)
    if protocol is None or protocol.current_version_id is None:
        return []
    return [
        item for item in tx.schedules.list_headers(
            organization_id=organization_id, status="APPROVED", trial_id=trial_id)
        if item.protocol_version_id == protocol.current_version_id]


def enrollable_protocol_version(
    tx: UnitOfWork, organization_id: UUID, trial_id: UUID, *, today: date | None = None,
) -> tuple[UUID | None, list[ScheduleVersionHeader]]:
    """The protocol version new patients are enrolled under, and its approved versions.

    Normally the protocol's ``current_version_id``. An amendment replaces it for
    NEW enrolments once one of its schedule versions is approved AND in force;
    until then the current version keeps taking enrolments (doc 9 s8, PT-05). A
    superseded (older) protocol version is never returned.
    """
    protocol = tx.authoring.find_protocol_for_trial(trial_id)
    if protocol is None:
        return None, []
    current = tx.authoring.get_protocol_version(protocol.current_version_id) \
        if protocol.current_version_id else None
    approved = [
        item for item in tx.schedules.list_headers(
            organization_id=organization_id, status="APPROVED", trial_id=trial_id)]
    by_protocol_version: dict[UUID, list[ScheduleVersionHeader]] = {}
    created: dict[UUID, datetime | None] = {}
    for header in approved:
        by_protocol_version.setdefault(header.protocol_version_id, []).append(header)
        if header.protocol_version_id not in created:
            created[header.protocol_version_id] = tx.authoring.get_protocol_version(
                header.protocol_version_id).created_at
    chosen = current.id if current is not None else None
    chosen_rank: tuple | None = None
    for protocol_version_id, versions in by_protocol_version.items():
        if current is not None and protocol_version_id != current.id:
            from .version_intent import activation_eligible
            candidate = tx.authoring.get_protocol_version(protocol_version_id)
            if not activation_eligible(candidate.metadata,
                    declared_lineage=any(row.based_on_schedule_version_id is not None for row in versions)):
                continue
            uploaded = created.get(protocol_version_id)
            if uploaded is None or current.created_at is None or uploaded < current.created_at:
                continue  # superseded, never enrollable again
        forced = in_force_today(versions, today=today)
        if not forced:
            continue
        rank = (created.get(protocol_version_id) or _MIN,
                max((item.approved_at or _MIN) for item in forced))
        rank = tuple(value.replace(tzinfo=None) if value.tzinfo else value for value in rank)
        if chosen_rank is None or rank > chosen_rank:
            chosen, chosen_rank = protocol_version_id, rank
    return chosen, (by_protocol_version.get(chosen, []) if chosen is not None else [])


def resolve_current_schedule_version(
    tx: UnitOfWork, organization_id: UUID, trial_id: UUID, schedule_definition_id: UUID,
    *, today: date | None = None,
) -> ScheduleVersionHeader:
    """The one schedule version a new enrolment/link against this definition should use.

    The linked definition may belong to another protocol version of the same
    trial than the enrollable one. It is then mapped to the matching definition
    of the enrollable protocol version - same schedule type and name, or the
    only one - never guessed between several.
    """
    protocol_version_id, approved_all = enrollable_protocol_version(
        tx, organization_id, trial_id, today=today)
    approved = [item for item in approved_all if item.schedule_definition_id == schedule_definition_id]
    if not approved and approved_all:
        try:
            linked = tx.authoring.get_definition(schedule_definition_id)
        except KeyError:
            linked = None
        same_trial = linked is not None and linked.trial_id == trial_id
        if same_trial and linked.protocol_version_id != protocol_version_id:
            definition_ids = {item.schedule_definition_id for item in approved_all}
            candidates = [tx.authoring.get_definition(item) for item in definition_ids]
            matches = [item for item in candidates
                       if item.schedule_type == linked.schedule_type and item.name == linked.name]
            if len(matches) != 1:
                same_type = [item for item in candidates if item.schedule_type == linked.schedule_type]
                matches = same_type if len(same_type) == 1 else (
                    candidates if len(candidates) == 1 else [])
            if len(matches) == 1:
                approved = [item for item in approved_all
                            if item.schedule_definition_id == matches[0].id]
            elif candidates:
                raise ValueError(
                    "the approved protocol version has several schedules and none matches the "
                    "linked one; choose the schedule this patient is enrolled under")
    approved.sort(key=lambda item: item.version_number, reverse=True)
    versions_in_force = in_force_today(approved, today=today)
    if versions_in_force:
        return versions_in_force[0]
    if approved:
        soonest = min(item.effective_from for item in approved if item.effective_from is not None)
        raise ValueError(
            "no approved canonical schedule is in force today; the approved "
            f"version takes effect on {soonest.isoformat()}")
    raise ValueError("no approved canonical schedule is available for the linked trial")
