"""Fair progress for the deferred-adoption worker (P6 repair F4).

Adds ``last_checked_at`` rotation support: the worker takes the least recently checked open
decisions first, bounded in the query, so a not-yet-due decision cannot monopolise a pass.
Additive and idempotent (a named, partial index); no document is rewritten and migration 0009
is untouched.
"""

from pymongo import ASCENDING

VERSION = 10
NAME = "amendment_worker_rotation"


def apply(database) -> None:
    database["amendment_decisions"].create_index(
        [("status", ASCENDING), ("last_checked_at", ASCENDING), ("created_at", ASCENDING)],
        name="ix_decision_pending_rotation", partialFilterExpression={"is_open": True})
