"""One trusted decision: may THIS plane change this patient's schedule?

A patient record carries ``authority_plane`` - which store is authoritative for
their clinical schedule. Every schedule-changing writer in the command plane
(commands, the older actual route, corrections, activities, state facts, impact
confirmation, replays) asks here, so a copy of a patient whose authority lies
elsewhere can never be changed from this plane and drift from what is displayed.

Fail closed: only an explicit ``"MONGO"`` classification, set by trusted native
enrolment (never by a request body), authorizes a change.

* ``None`` - an UNCLASSIFIED record (for example a mirror created by the operational
  enrolment bridge). It is NOT proof of authority: changes are refused until a
  trusted native enrolment (or a future auditable classification) says otherwise.
  Reads and history stay available, and nothing is relabelled in bulk.
* anything else (``"SQL"``, ``"LEGACY"`` ...) - refused with a safe 409.
"""

from __future__ import annotations

from .patients import PatientRecord

COMMAND_PLANE = "MONGO"
UNCLASSIFIED = "UNCLASSIFIED"


class PlaneAuthorityError(ValueError):
    """The plane is not authoritative for this patient (HTTP 409)."""

    code = "PLANE_NOT_AUTHORITATIVE"
    status = 409

    def __init__(self, authority: str):
        if authority == UNCLASSIFIED:
            message = (
                "this patient has no verified schedule authority in this plane, so a change "
                "recorded here was refused; the schedule remains readable")
        else:
            message = (
                f"this patient's schedule is authoritative in {authority}; a change recorded in "
                "this plane would diverge from what is displayed, so it was refused")
        super().__init__(message)
        self.authority = authority


def require_command_plane_authority(patient: PatientRecord) -> str:
    """Return ``NATIVE`` when this plane is explicitly authoritative, else raise."""
    plane = patient.authority_plane
    if plane == COMMAND_PLANE:
        return "NATIVE"
    raise PlaneAuthorityError(plane or UNCLASSIFIED)
