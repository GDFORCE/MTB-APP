"""Pilot cohort cutover state (P11-03/04/05). A cohort is a SMALL, explicit set of patients."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .records import Record, utc_now

MAX_MEMBERS = 200
STATES = ("SHADOW", "PILOT", "ROLLED_BACK")


class CohortMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_patient_id: str
    patient_id: UUID
    #: authority_plane before promotion, restored on rollback (None = unclassified mirror).
    previous_authority: str | None = None


class CohortRecord(Record):
    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    trial_id: UUID
    name: str = Field(min_length=1, max_length=120)
    state: str = "SHADOW"
    members: list[CohortMember] = Field(default_factory=list, max_length=MAX_MEMBERS)
    created_by: UUID
    promoted_by: UUID | None = None
    promoted_at: datetime | None = None
    rolled_back_by: UUID | None = None
    rolled_back_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    revision: int = 1
