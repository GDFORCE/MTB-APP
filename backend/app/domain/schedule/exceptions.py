class ScheduleDomainError(ValueError):
    """Base error for deterministic schedule domain failures."""


class ScheduleNotApprovedError(ScheduleDomainError):
    """Raised when patient evaluation is attempted for a non-approved schedule."""


class ImmutableScheduleError(ScheduleDomainError):
    """Raised when an approved schedule is modified."""


class UnsupportedTimingError(ScheduleDomainError):
    """Raised when a timing expression has no deterministic implementation."""


class TimeOfDayRequiredError(ScheduleDomainError):
    """A supported intraday rule lacks the reference's time of day."""

    def __init__(self):
        super().__init__("A time of day is required for an hours/minutes rule; a calendar date is insufficient.")

