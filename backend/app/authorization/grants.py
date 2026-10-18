"""Where explicit capability assignments live (P3-01 / P3-02).

A grant is a persisted, revocable record made by an authorised administrator -
never something a client can supply. The authorization guard reads grants fresh
on every request, so revocation takes effect on the next call (no caching).

``GrantStore`` is the port; ``MemoryGrantStore`` is the test double and the
reference behaviour. The production adapter (MongoDB, in ``server.py``) must
behave identically.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Protocol

from .policy import Grant


@dataclass(frozen=True)
class GrantRecord:
    id: str
    organization_id: str
    user_id: str
    capability: str
    trial_id: str | None
    granted_by: str
    reason: str
    created_at: str
    revoked_at: str | None = None
    revoked_by: str | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def as_grant(self) -> Grant:
        return Grant(self.capability, self.trial_id)


class GrantStore(Protocol):
    async def active_for_user(self, user_id: str, organization_id: str) -> tuple[Grant, ...]: ...

    async def list_for_organization(self, organization_id: str) -> list[GrantRecord]: ...

    async def add(self, *, organization_id: str, user_id: str, capability: str,
                  trial_id: str | None, granted_by: str, reason: str) -> GrantRecord: ...

    async def revoke(self, grant_id: str, *, organization_id: str, revoked_by: str) -> GrantRecord | None: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryGrantStore:
    def __init__(self) -> None:
        self._rows: dict[str, GrantRecord] = {}

    async def active_for_user(self, user_id: str, organization_id: str) -> tuple[Grant, ...]:
        return tuple(
            row.as_grant() for row in self._rows.values()
            if row.active and row.user_id == user_id and row.organization_id == organization_id)

    async def list_for_organization(self, organization_id: str) -> list[GrantRecord]:
        return [row for row in self._rows.values() if row.organization_id == organization_id]

    async def add(self, *, organization_id: str, user_id: str, capability: str,
                  trial_id: str | None, granted_by: str, reason: str) -> GrantRecord:
        record = GrantRecord(str(uuid.uuid4()), organization_id, user_id, capability, trial_id,
                             granted_by, reason, _now())
        self._rows[record.id] = record
        return record

    async def revoke(self, grant_id: str, *, organization_id: str, revoked_by: str) -> GrantRecord | None:
        row = self._rows.get(grant_id)
        if row is None or row.organization_id != organization_id or not row.active:
            return None
        updated = replace(row, revoked_at=_now(), revoked_by=revoked_by)
        self._rows[grant_id] = updated
        return updated

    # test convenience
    def grant_sync(self, user_id: str, organization_id: str, capability: str,
                   trial_id: str | None = None) -> GrantRecord:
        record = GrantRecord(str(uuid.uuid4()), organization_id, user_id, capability, trial_id,
                             "test", "fixture", _now())
        self._rows[record.id] = record
        return record
