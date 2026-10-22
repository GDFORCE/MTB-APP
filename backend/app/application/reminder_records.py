"""Patient notification preferences and reminder notices on the UCTSM plane (P10-04)."""
from __future__ import annotations

import re
from datetime import date, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from .records import Record, utc_now

CONSENTS = ("GRANTED", "DECLINED", "WITHDRAWN")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class NotificationPreferenceRecord(Record):
    """Explicit, patient-stated preferences. There are NO defaults: a patient without a GRANTED record
    is never reminded, and a missing zone/time is a refusal, not a guess."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    patient_id: UUID
    consent: str
    timezone: str
    delivery_time: str                      # local HH:MM the reminder should arrive
    lead_days: int = Field(ge=0, le=30)     # how many days before the planned date
    quiet_start: str | None = None          # local HH:MM; both or neither
    quiet_end: str | None = None
    recorded_by: UUID
    recorded_at: datetime = Field(default_factory=utc_now)
    revision: int = 1

    @field_validator("consent")
    @classmethod
    def _consent(cls, value: str) -> str:
        if value not in CONSENTS:
            raise ValueError("consent must be GRANTED, DECLINED or WITHDRAWN")
        return value

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError) as error:
            raise ValueError("timezone must be a known IANA zone name") from error
        return value

    @field_validator("delivery_time", "quiet_start", "quiet_end")
    @classmethod
    def _time(cls, value):
        if value is not None and not _HHMM.match(value):
            raise ValueError("times must be local HH:MM")
        return value

    @model_validator(mode="after")
    def _quiet_pair(self):
        if (self.quiet_start is None) != (self.quiet_end is None):
            raise ValueError("quiet hours need both a start and an end")
        if self.quiet_start is not None and self.quiet_start == self.quiet_end:
            raise ValueError("quiet hours cannot start and end at the same time")
        return self


class ReminderNoticeRecord(Record):
    """One reminder the plane decided to deliver (ACTIVE) or stopped delivering (WITHDRAWN)."""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    patient_id: UUID
    reminder_key: str
    logical_key: str
    event_code: str
    state: str = "ACTIVE"
    scheduled_for: datetime
    nominal_date: date
    policy_state: str
    requires_attendance: bool
    message: str
    evaluation_id: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)
    withdrawn_at: datetime | None = None
    withdrawn_reason: str | None = None
