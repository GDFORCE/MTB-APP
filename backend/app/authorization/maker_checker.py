"""Maker-checker: the person who materially changed a schedule may not approve it
(P3-04). Pure - no database, no FastAPI.

POLICY (``maker-checker.v1``)
-----------------------------
* **Configuration** is deployment-owned: ``UCTSM_MAKER_CHECKER`` (``on`` / ``off``)
  and ``UCTSM_MAKER_CHECKER_SCOPE`` (``LAST_EDITOR`` | ``ANY_EDITOR``). It is read
  from the server environment on every approval; no request field, header or
  token can change it, and the setting in force is stamped on the approval audit.
* **Default is OFF** (compatibility: existing single-reviewer workflows keep
  working). This default is a PROPOSAL awaiting owner approval, not a decision.
* **Scope** ``LAST_EDITOR`` is the check the requirements state: the approver may
  not be ``last_material_editor_id``. ``ANY_EDITOR`` additionally excludes every
  actor who has materially edited this draft. Which one a deployment needs is a
  product decision; ``LAST_EDITOR`` is the default because it is the stated one.
* **Material** edits change clinical meaning: timing, temporal basis, windows,
  dependencies, recurrence, applicability, activities, qualifier interpretation,
  anchor interpretation, holiday calendars, adding a visit. Renaming a visit
  label, confirming a row, viewing, exporting or validating is not material.
* **Provenance** is recorded where the edit happens, in the same transaction, on
  the version itself. It is never inferred from event status or from "the latest
  audit row".
* **Legacy drafts**: a draft edited before provenance existed has no
  ``material_editor_ids``. If it carries human CORRECT/ADD review decisions, every
  reviewer named on them is treated as a possible editor (conservative); if it
  carries none there is no maker to segregate. Authorship is never invented and
  nothing is rewritten on approved versions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Mapping

MAKER_CHECKER_POLICY_VERSION = "maker-checker.v1"

#: Event fields whose change is a clinical-meaning change.
MATERIAL_EVENT_FIELDS = frozenset({
    "event_type", "timing", "window", "recurrence", "recurrence_plan", "applicability",
    "visit_mode", "allowed_visit_modes", "dependency_mode", "calendar_recurrence",
    "activities", "conditions", "qualifiers", "anchor", "anchor_reference",
})
#: Presentation only.
NON_MATERIAL_EVENT_FIELDS = frozenset({"display_name", "protocol_label"})


def is_material_event_change(changed_fields: Iterable[str]) -> bool:
    return any(name in MATERIAL_EVENT_FIELDS or name not in NON_MATERIAL_EVENT_FIELDS
               for name in changed_fields)


_IGNORED_DIFF_KEYS = frozenset({"metadata", "evidence_refs", "updated_at", "created_at"})


def legacy_correctors(decisions: Iterable[tuple]) -> tuple[str, ...]:
    """Reviewers named on human CORRECT/ADD decisions that were MATERIAL.

    Each item is ``(reviewer_id, field_path[, previous_value, new_value])``. A
    decision is ignored only when it can be shown to be presentation-only: its
    field path, or - for a whole-event correction - the keys that differ between
    ``previous_value`` and ``new_value`` are all label fields or a row
    confirmation. Anything that cannot be shown to be non-material counts, so
    authorship is never under-attributed."""
    ignorable = NON_MATERIAL_EVENT_FIELDS | {"review"}
    names: set[str] = set()
    for item in decisions:
        reviewer_id, field_path = item[0], item[1]
        previous = item[2] if len(item) > 2 else None
        current = item[3] if len(item) > 3 else None
        parts = [part.strip() for part in (field_path or "").split(",") if part.strip()]
        if not parts and isinstance(previous, dict) and isinstance(current, dict):
            parts = [key for key in set(previous) | set(current)
                     if previous.get(key) != current.get(key) and key not in _IGNORED_DIFF_KEYS]
        if parts and all(part in ignorable for part in parts):
            continue
        names.add(str(reviewer_id))
    return tuple(sorted(names))


class Scope(StrEnum):
    LAST_EDITOR = "LAST_EDITOR"
    ANY_EDITOR = "ANY_EDITOR"


@dataclass(frozen=True)
class MakerCheckerPolicy:
    enabled: bool = False
    scope: Scope = Scope.LAST_EDITOR
    version: str = MAKER_CHECKER_POLICY_VERSION

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "MakerCheckerPolicy":
        source = os.environ if env is None else env
        # Selected policy (15 Oct 2026): ON unless a DEPLOYMENT explicitly sets it off.
        # An unset or unrecognised value keeps the safer, enabled behaviour.
        raw_enabled = str(source.get("UCTSM_MAKER_CHECKER", "")).strip().lower()
        enabled = raw_enabled not in {"0", "false", "no", "off"}
        raw = str(source.get("UCTSM_MAKER_CHECKER_SCOPE", "")).strip().upper()
        scope = Scope.ANY_EDITOR if raw == Scope.ANY_EDITOR.value else Scope.LAST_EDITOR
        return cls(enabled=enabled, scope=scope)


@dataclass(frozen=True)
class Provenance:
    last_material_editor_id: str | None = None
    material_editor_ids: tuple[str, ...] = ()
    #: reviewers named on human CORRECT/ADD decisions of a draft with no provenance
    legacy_correctors: tuple[str, ...] = ()

    @property
    def has_provenance(self) -> bool:
        return self.last_material_editor_id is not None or bool(self.material_editor_ids)


@dataclass(frozen=True)
class MakerCheckerOutcome:
    allowed: bool
    reason_code: str
    enabled: bool
    policy_version: str
    scope: str
    last_material_editor_id: str | None
    provenance_known: bool

    def audit(self, *, approver_id: str, revision: str | None) -> dict:
        return {
            "maker_checker_enabled": self.enabled, "policy_version": self.policy_version,
            "scope": self.scope, "reason_code": self.reason_code,
            "last_material_editor_id": self.last_material_editor_id,
            "provenance_known": self.provenance_known, "approver_id": approver_id,
            "revision": revision,
        }


class MakerCheckerViolation(ValueError):
    """Raised at the approval commit; carries the machine-readable outcome."""

    def __init__(self, outcome: MakerCheckerOutcome, message: str):
        super().__init__(message)
        self.outcome = outcome


def evaluate(policy: MakerCheckerPolicy, approver_id: str, provenance: Provenance) -> MakerCheckerOutcome:
    approver = str(approver_id)
    last = provenance.last_material_editor_id
    base = dict(enabled=policy.enabled, policy_version=policy.version, scope=policy.scope.value,
                last_material_editor_id=last, provenance_known=provenance.has_provenance)
    if not policy.enabled:
        return MakerCheckerOutcome(True, "MAKER_CHECKER_DISABLED", **base)
    if provenance.has_provenance:
        excluded = {last} if policy.scope is Scope.LAST_EDITOR else set(provenance.material_editor_ids) | {last}
        excluded.discard(None)
        if approver in excluded:
            return MakerCheckerOutcome(False, "SELF_APPROVAL_OF_MATERIAL_EDIT", **base)
        return MakerCheckerOutcome(True, "INDEPENDENT_APPROVER", **base)
    if provenance.legacy_correctors:
        if approver in set(provenance.legacy_correctors):
            return MakerCheckerOutcome(False, "LEGACY_DRAFT_APPROVER_IS_A_CORRECTOR", **base)
        return MakerCheckerOutcome(True, "LEGACY_DRAFT_APPROVER_NOT_A_CORRECTOR", **base)
    return MakerCheckerOutcome(True, "NO_MATERIAL_EDITS_RECORDED", **base)


def enforce(policy: MakerCheckerPolicy, approver_id: str, provenance: Provenance) -> MakerCheckerOutcome:
    outcome = evaluate(policy, approver_id, provenance)
    if not outcome.allowed:
        raise MakerCheckerViolation(
            outcome,
            "Maker-checker: the person who made material changes to this schedule "
            "cannot approve it. Another authorised approver must approve this version.")
    return outcome
