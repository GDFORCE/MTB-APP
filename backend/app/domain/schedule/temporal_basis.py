"""Temporal basis of event references: legacy equivalence and read-time upgrade.

P1-01. ``EventReference.temporal_basis`` is required, but every schedule stored
before the field existed has none. Those references are NOT guessed at and NOT
rewritten in storage: when a stored event is read, each reference without a basis
is given the basis the engine already used for it, and is marked
``LEGACY_EQUIVALENT`` so it is never mistaken for something a person stated.

What the engine already used (evaluator ``_evaluate_event``) depends on the
owning event's dependency mode, and only for the references that event lists as
dependencies:

* ``ACTUAL_PREVIOUS_EVENT`` - a listed dependency counts from its recorded actual
  visit date; anything else counts from the current planned date.
* ``MANUAL`` - a listed dependency counts from the date a person entered.
* ``NOMINAL``, unstated or ``UNCLEAR`` - the current planned date. (An unclear
  mode already blocks approval of an event that has dependencies.)

Pure functions over plain dicts and domain models: no persistence, no I/O.
"""

from __future__ import annotations

from typing import Any

from .timing import BasisSource, EventReference, TemporalBasis

#: Payload keys of a stored event that can hold an event reference.
_REFERENCE_KEYS = ("timing", "recurrence", "recurrence_plan", "calendar_recurrence", "activities")


def _mode_name(mode: object) -> str | None:
    value = getattr(mode, "value", mode)
    return str(value) if value is not None else None


def legacy_basis(mode: object, *, is_dependency: bool) -> TemporalBasis:
    """The basis the engine uses today for one reference of an event."""
    name = _mode_name(mode)
    if is_dependency and name == "ACTUAL_PREVIOUS_EVENT":
        return TemporalBasis.ACTUAL_VISIT
    if is_dependency and name == "MANUAL":
        return TemporalBasis.MANUAL_ANCHOR
    return TemporalBasis.CURRENT_PLANNED


def mode_is_stated(mode: object) -> bool:
    return _mode_name(mode) not in {None, "UNCLEAR"}


def effective_temporal_basis(
    reference: EventReference, *, owner_mode: object, is_dependency: bool,
) -> TemporalBasis | None:
    """The basis to evaluate a reference with.

    An UNCONFIRMED reference (extraction could not tell) takes its basis from the
    owner's stated dependency mode once a reviewer has stated one; while the mode
    is unstated there is no basis, so ``None`` - the caller must not date it.
    """
    if reference.basis_source != BasisSource.UNCONFIRMED:
        return reference.temporal_basis
    if not mode_is_stated(owner_mode):
        return None
    return legacy_basis(owner_mode, is_dependency=is_dependency)


def _upgrade(node: Any, mode: object, dependency_codes: frozenset[str]) -> Any:
    if isinstance(node, list):
        return [_upgrade(item, mode, dependency_codes) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: _upgrade(value, mode, dependency_codes) for key, value in node.items()}
    if out.get("kind") == "EVENT" and "event_code" in out and "temporal_basis" not in out:
        basis = legacy_basis(mode, is_dependency=out["event_code"] in dependency_codes)
        out["temporal_basis"] = basis.value
        out["basis_source"] = BasisSource.LEGACY_EQUIVALENT.value
    return out


def upgrade_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """A copy of a stored event payload whose references all carry a basis.

    References that already have one are left exactly as stored; the input is
    never mutated. Idempotent.
    """
    dependency_codes = frozenset(
        str(item.get("source_event_code"))
        for item in (payload.get("dependencies") or []) if isinstance(item, dict))
    mode = payload.get("dependency_mode")
    result = dict(payload)
    for key in _REFERENCE_KEYS:
        if key in result:
            result[key] = _upgrade(result[key], mode, dependency_codes)
    return result


def upgrade_schedule_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """``upgrade_event_payload`` for every event of a whole schedule payload."""
    events = payload.get("events")
    if not isinstance(events, list):
        return payload
    return {**payload, "events": [
        upgrade_event_payload(item) if isinstance(item, dict) else item for item in events]}
