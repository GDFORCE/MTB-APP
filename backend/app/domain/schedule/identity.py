from __future__ import annotations

"""One canonical construction of a materialized occurrence's identity (AD-4f).

Before this module existed, the same identity was built independently in four
places in ``schedule_service.py`` plus a fifth, differently-named function in
``evaluator.py`` -- and two of the five disagreed in format for the identical
activity (``"DAY_30:0#WEIGHT"`` vs ``"DAY_30#0#WEIGHT"``), a mismatch nothing
currently joins against and so nothing has yet caught. Every construction site
now calls one of these two functions, so a future join can rely on the format
actually matching.
"""


def event_logical_key(event_code: str, occurrence_index: int) -> str:
    """Identity of one materialized visit occurrence."""
    return f"{event_code}:{occurrence_index}"


def activity_logical_key(event_code: str, occurrence_index: int, activity_code: str) -> str:
    """Identity of one activity inside one materialized visit occurrence.

    This is also the format the append-only actual-time record
    (``PatientActivityRecord.logical_key``, written by ``record_activity``)
    uses, so a generated activity and its recorded actual can be joined on
    this value directly.
    """
    return f"{event_code}#{occurrence_index}#{activity_code}"
