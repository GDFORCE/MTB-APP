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


#: AT-10: occurrence indexes of condition episode ``e`` start at ``e * STRIDE``.
#: Episode 0 therefore keeps exactly the historical numbering (NF-08), and a
#: recurrence can never reuse - and so overwrite - an earlier episode's
#: identities. A conditional repeat materializes at most 1000 occurrences.
EPISODE_INDEX_STRIDE = 1000


def episode_occurrence_index(episode_index: int, ordinal: int) -> int:
    """Stable occurrence index of the ``ordinal``-th occurrence of an episode."""
    if ordinal >= EPISODE_INDEX_STRIDE:
        raise ValueError("an episode materializes at most EPISODE_INDEX_STRIDE occurrences")
    return episode_index * EPISODE_INDEX_STRIDE + ordinal


def occurrence_identity(occurrence_index: int) -> dict[str, int]:
    """The (episode, ordinal) pair an occurrence index encodes."""
    return {
        "episode": occurrence_index // EPISODE_INDEX_STRIDE,
        "ordinal": occurrence_index % EPISODE_INDEX_STRIDE,
    }
