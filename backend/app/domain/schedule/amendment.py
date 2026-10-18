"""Protocol amendments as data: content identity, event mapping and adoption points (P6).

Pure domain code. Three ideas live here:

* a schedule version has a **content hash** over everything that affects what a
  patient is told (never over lifecycle fields such as status), so an approved
  version can be proven unchanged after its successor is approved (P6-01);
* an **event mapping** states which V1 visit a V2 visit is. A shared event code
  is NOT evidence of equivalence (an amendment may reuse ``V4`` for a different
  visit), so a code-only match is proposed for review rather than trusted, and a
  fact is only carried to a V2 visit through a mapping that is confirmed (P6-02);
* an **adoption point** says when a patient moves to V2: never by default, only
  immediately or at a stated event, cycle start or date (P6-04).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Iterable
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from .models import UniversalSchedule
from .timing import StrictModel

#: The first PERSISTED content-hash contract. Nothing was stored under any earlier
#: interpretation. A future change to what is hashed or how it is canonicalised gets
#: a NEW version string; old hashes are verified with their own version, never
#: reinterpreted (see ``verify_content_hash``).
CONTENT_HASH_VERSION = "schedule-content.v1"
HEADER_HASH_VERSION = "schedule-header.v1"
MAPPING_ENGINE_VERSION = "event-mapping.v1"
SIMULATION_POLICY_VERSION = "amendment-simulation.v1"

#: What the content hash deliberately leaves out, and why:
#:
#: * ``schedule_metadata.status`` - lifecycle (APPROVED -> SUPERSEDED must not change
#:   what an approved version SAYS);
#: * ``created_at`` / ``validation_issues`` - provenance of the draft, not schedule meaning;
#: * ``schedule_metadata.effective_from`` - when the version starts applying. It is
#:   NOT covered by the content hash, so it is covered by the header integrity hash
#:   below and bound separately into every simulation and adoption decision.
#:
#: Everything else is hashed: events (timing, basis, windows, dependencies, conditions,
#: recurrence, activities, constraints, track membership), tracks and links, anchors,
#: populations/arms/cohorts, repeat blocks, processes, calendars, evidence and claims,
#: and the schema version. Identifiers are part of the content: an approved version is
#: an exact artefact and a re-assembled copy must reproduce it.
_LIFECYCLE_METADATA = ("status", "effective_from")
_LIFECYCLE_TOP_LEVEL = ("created_at", "validation_issues")


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_lists(payload: dict) -> dict:
    """Top-level collections are sets of identified things: store order (which differs
    between a draft and its reconstruction) must not change the hash."""
    for key, value in list(payload.items()):
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            payload[key] = sorted(
                value, key=lambda item: (str(item.get("code", "")), str(item.get("id", "")),
                                         canonical_json(item)))
    return payload


def schedule_content_hash(schedule: UniversalSchedule) -> str:
    """SHA-256 of the schedule's content under ``CONTENT_HASH_VERSION``."""
    payload = schedule.model_dump(mode="json")
    for key in _LIFECYCLE_TOP_LEVEL:
        payload.pop(key, None)
    metadata = payload.get("schedule_metadata") or {}
    for key in _LIFECYCLE_METADATA:
        metadata.pop(key, None)
    return sha256_text(f"{CONTENT_HASH_VERSION}|{canonical_json(_canonical_lists(payload))}")


class HashStatus(StrEnum):
    VERIFIED = "VERIFIED"
    #: stored hash differs from the content: the approved version changed (or the
    #: stored hash is damaged). Nothing may be simulated, compared or adopted from it.
    MISMATCH = "MISMATCH"
    #: an approved version stored before hashes existed. It is NOT declared verified
    #: and its content is never rewritten to manufacture a hash; callers decide whether
    #: a legacy source may be used, and say so.
    UNVERIFIED_LEGACY = "UNVERIFIED_LEGACY"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"


def verify_content_hash(
    schedule: UniversalSchedule, stored_hash: str | None, stored_version: str | None,
) -> HashStatus:
    if not stored_hash:
        return HashStatus.UNVERIFIED_LEGACY
    if stored_version != CONTENT_HASH_VERSION:
        return HashStatus.UNSUPPORTED_VERSION
    return HashStatus.VERIFIED if schedule_content_hash(schedule) == stored_hash \
        else HashStatus.MISMATCH


def _iso_instant(value: object) -> str | None:
    if value is None:
        return None
    from datetime import timezone as _tz
    if hasattr(value, "astimezone"):
        utc = value.astimezone(_tz.utc)
        return utc.replace(microsecond=(utc.microsecond // 1000) * 1000).isoformat()
    return str(value)


def header_integrity_hash(
    *, schedule_version_id: object, organization_id: object, trial_id: object,
    protocol_id: object, protocol_version_id: object, schedule_definition_id: object,
    version_number: int, based_on_schedule_version_id: object, effective_from: object,
    approved_by: object, approved_at: object, content_hash: str, content_hash_version: str,
) -> str:
    """Integrity of the approval header: the lineage, the effective date and who
    approved what, when. The content hash cannot vouch for these (``effective_from``
    is excluded from it), so an approved header is verified against this instead."""
    fields = {
        "id": str(schedule_version_id), "organization_id": str(organization_id),
        "trial_id": str(trial_id), "protocol_id": str(protocol_id),
        "protocol_version_id": str(protocol_version_id),
        "schedule_definition_id": str(schedule_definition_id), "version_number": version_number,
        "based_on": str(based_on_schedule_version_id) if based_on_schedule_version_id else None,
        "effective_from": str(effective_from) if effective_from else None,
        "approved_by": str(approved_by) if approved_by else None,
        "approved_at": _iso_instant(approved_at),
        "content_hash": content_hash, "content_hash_version": content_hash_version}
    return sha256_text(f"{HEADER_HASH_VERSION}|{canonical_json(fields)}")


# --------------------------------------------------------------------------- mapping
class MappingType(StrEnum):
    #: the same visit, same identity (code may or may not be the same)
    EQUIVALENT = "EQUIVALENT"
    #: the same visit under a different code
    RENAMED = "RENAMED"
    #: one V1 visit became several V2 visits
    SPLIT = "SPLIT"
    #: several V1 visits became one V2 visit
    MERGED = "MERGED"
    #: a V1 visit has no V2 counterpart
    REMOVED = "REMOVED"
    #: a V2 visit has no V1 counterpart
    ADDED = "ADDED"


class MappingReviewStatus(StrEnum):
    #: nothing was uncertain (an identical code, name and type; or no counterpart at all)
    AUTO_CONFIRMED = "AUTO_CONFIRMED"
    #: proposed by the tool, a person must confirm or replace it
    PROPOSED = "PROPOSED"
    #: a person confirmed or replaced it
    CONFIRMED = "CONFIRMED"
    #: a person said the proposed equivalence is wrong. The row is kept for the audit
    #: trail but is not part of the effective mapping; the reviewer's replacement
    #: mappings (REMOVED / ADDED) are.
    REJECTED = "REJECTED"


class OccurrenceRule(StrictModel):
    """One explicit occurrence-level pairing for a SPLIT or MERGED mapping. A
    completed actual is carried to exactly the pairs listed here and nowhere else:
    it is never duplicated across the new visits."""

    source_code: str = Field(min_length=1)
    source_index: int = Field(default=0, ge=0)
    target_code: str = Field(min_length=1)
    target_index: int = Field(default=0, ge=0)


#: The only automatic matches that need no person. Every other basis is a proposal.
AUTO_TRUSTED_PROVENANCE = frozenset({"AUTO_EXACT", "AUTO_NO_COUNTERPART"})


class EventMapping(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    mapping_type: MappingType
    source_codes: list[str] = Field(default_factory=list)
    target_codes: list[str] = Field(default_factory=list)
    #: 0..1; a proposal's strength, shown to the reviewer. Not a decision.
    confidence: float = Field(default=1.0, ge=0, le=1)
    #: how the proposal was reached: AUTO_EXACT, AUTO_CODE_ONLY, AUTO_NAME_MATCH,
    #: AUTO_SIMILAR_NAME, AUTO_NAME_CONTAINMENT, AUTO_NO_COUNTERPART, REVIEWER
    provenance: str = "AUTO_EXACT"
    rationale: str = ""
    review_status: MappingReviewStatus = MappingReviewStatus.PROPOSED
    occurrence_rules: list[OccurrenceRule] = Field(default_factory=list)
    reviewed_by: UUID | None = None
    review_reason: str | None = None

    @model_validator(mode="after")
    def shape_matches_type(self) -> "EventMapping":
        sources, targets, kind = len(self.source_codes), len(self.target_codes), self.mapping_type
        if len(set(self.source_codes)) != sources or len(set(self.target_codes)) != targets:
            raise ValueError("a mapping lists each event code once")
        expected = {
            MappingType.EQUIVALENT: (sources == 1 and targets == 1),
            MappingType.RENAMED: (sources == 1 and targets == 1),
            MappingType.SPLIT: (sources == 1 and targets >= 2),
            MappingType.MERGED: (sources >= 2 and targets == 1),
            MappingType.REMOVED: (sources == 1 and targets == 0),
            MappingType.ADDED: (sources == 0 and targets == 1),
        }[kind]
        if not expected:
            raise ValueError(f"a {kind.value} mapping has the wrong number of source/target codes")
        if self.occurrence_rules and kind not in {MappingType.SPLIT, MappingType.MERGED}:
            raise ValueError("occurrence rules belong to SPLIT and MERGED mappings only")
        for rule in self.occurrence_rules:
            if rule.source_code not in self.source_codes or rule.target_code not in self.target_codes:
                raise ValueError("an occurrence rule must pair a listed source with a listed target")
        # Canonical order: rule order must never change behaviour or the fingerprint.
        # An exact repeat is one rule; a source occurrence sent to two places is refused
        # (no first-match policy, and a performed fact is never duplicated).
        unique = {(r.source_code, r.source_index, r.target_code, r.target_index): r
                  for r in self.occurrence_rules}
        destinations: dict[tuple[str, int], tuple[str, int]] = {}
        for source_code, source_index, target_code, target_index in unique:
            key = (source_code, source_index)
            if destinations.setdefault(key, (target_code, target_index)) != (target_code, target_index):
                raise ValueError(
                    f"{source_code!r} occurrence {source_index} is sent to more than one visit")
        self.occurrence_rules = [unique[key] for key in sorted(unique)]
        status = self.review_status
        if status in {MappingReviewStatus.CONFIRMED, MappingReviewStatus.REJECTED} \
                and self.reviewed_by is None:
            raise ValueError("a reviewed mapping records who reviewed it")
        if status == MappingReviewStatus.AUTO_CONFIRMED:
            if kind in {MappingType.SPLIT, MappingType.MERGED}:
                raise ValueError("a split or merge always needs a person's reviewed rules")
            if self.provenance not in AUTO_TRUSTED_PROVENANCE:
                raise ValueError("only an exact or no-counterpart match can be confirmed automatically")
        return self

    @property
    def resolved(self) -> bool:
        return self.review_status in {
            MappingReviewStatus.AUTO_CONFIRMED, MappingReviewStatus.CONFIRMED}

    @property
    def carries_facts_automatically(self) -> bool:
        return self.mapping_type in {MappingType.EQUIVALENT, MappingType.RENAMED}


def effective_mappings(mappings: Iterable[EventMapping]) -> list[EventMapping]:
    """The mappings in force: a rejected proposal stays on record but does not count."""
    return [item for item in mappings if item.review_status != MappingReviewStatus.REJECTED]


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def propose_event_mappings(left: UniversalSchedule, right: UniversalSchedule) -> list[EventMapping]:
    """Propose how V1's visits map to V2's. Deterministic.

    Only an identical code AND display name AND event type is trusted without
    review. Everything else that implies identity is a proposal with the reason
    it was proposed; nothing is inferred from a code alone.
    """
    left_events = {event.code: event for event in left.events}
    right_events = {event.code: event for event in right.events}
    mappings: list[EventMapping] = []

    for code in sorted(left_events.keys() & right_events.keys()):
        before, after = left_events[code], right_events[code]
        same = _norm(before.display_name) == _norm(after.display_name) \
            and before.event_type == after.event_type
        mappings.append(EventMapping(
            mapping_type=MappingType.EQUIVALENT, source_codes=[code], target_codes=[code],
            confidence=1.0 if same else 0.4,
            provenance="AUTO_EXACT" if same else "AUTO_CODE_ONLY",
            review_status=(MappingReviewStatus.AUTO_CONFIRMED if same
                           else MappingReviewStatus.PROPOSED),
            rationale=("identical code, name and type" if same else
                       f"the code {code!r} is shared but the name or type differs "
                       f"({before.display_name!r} / {after.display_name!r}); confirm it is the same visit")))

    free_left = sorted(left_events.keys() - right_events.keys())
    free_right = sorted(right_events.keys() - left_events.keys())
    taken_right: set[str] = set()
    taken_left: set[str] = set()

    # one-to-one by name (strongest first)
    for code in free_left:
        name = _norm(left_events[code].display_name)
        label = _norm(left_events[code].protocol_label)
        candidates = [
            other for other in free_right if other not in taken_right
            and _norm(right_events[other].display_name) == name and name]
        provenance, confidence = "AUTO_NAME_MATCH", 0.8
        if not candidates:
            candidates = [
                other for other in free_right if other not in taken_right
                and _norm(right_events[other].protocol_label) == label and label]
            provenance, confidence = "AUTO_NAME_MATCH", 0.7
        if len(candidates) == 1:
            other = candidates[0]
            taken_left.add(code)
            taken_right.add(other)
            mappings.append(EventMapping(
                mapping_type=MappingType.RENAMED, source_codes=[code], target_codes=[other],
                confidence=confidence, provenance=provenance,
                review_status=MappingReviewStatus.PROPOSED,
                rationale=f"{code!r} and {other!r} carry the same visit name; confirm they are the same visit"))

    # split / merge by name containment
    for code in free_left:
        if code in taken_left:
            continue
        name = _norm(left_events[code].display_name)
        parts = [other for other in free_right if other not in taken_right and name
                 and _norm(right_events[other].display_name).startswith(name + " ")]
        if len(parts) >= 2:
            taken_left.add(code)
            taken_right.update(parts)
            mappings.append(EventMapping(
                mapping_type=MappingType.SPLIT, source_codes=[code], target_codes=parts,
                confidence=0.5, provenance="AUTO_NAME_CONTAINMENT",
                review_status=MappingReviewStatus.PROPOSED,
                rationale=f"{code!r} may have been split into {', '.join(parts)}; a reviewer "
                          "must say which occurrence goes to which new visit"))
    for other in free_right:
        if other in taken_right:
            continue
        name = _norm(right_events[other].display_name)
        parts = [code for code in free_left if code not in taken_left and name
                 and _norm(left_events[code].display_name).startswith(name + " ")]
        if len(parts) >= 2:
            taken_right.add(other)
            taken_left.update(parts)
            mappings.append(EventMapping(
                mapping_type=MappingType.MERGED, source_codes=parts, target_codes=[other],
                confidence=0.5, provenance="AUTO_NAME_CONTAINMENT",
                review_status=MappingReviewStatus.PROPOSED,
                rationale=f"{', '.join(parts)} may have been merged into {other!r}; a reviewer "
                          "must say which occurrence goes to the new visit"))

    # similar names: a weak rename proposal
    for code in free_left:
        if code in taken_left:
            continue
        best: tuple[float, str] | None = None
        for other in free_right:
            if other in taken_right:
                continue
            ratio = SequenceMatcher(
                None, _norm(left_events[code].display_name),
                _norm(right_events[other].display_name)).ratio()
            if ratio >= 0.75 and (best is None or ratio > best[0]):
                best = (ratio, other)
        if best is not None:
            taken_left.add(code)
            taken_right.add(best[1])
            mappings.append(EventMapping(
                mapping_type=MappingType.RENAMED, source_codes=[code], target_codes=[best[1]],
                confidence=round(best[0] * 0.6, 2), provenance="AUTO_SIMILAR_NAME",
                review_status=MappingReviewStatus.PROPOSED,
                rationale=f"{code!r} and {best[1]!r} have similar names; confirm they are the same visit"))

    for code in free_left:
        if code not in taken_left:
            mappings.append(EventMapping(
                mapping_type=MappingType.REMOVED, source_codes=[code], target_codes=[],
                confidence=1.0, provenance="AUTO_NO_COUNTERPART",
                review_status=MappingReviewStatus.AUTO_CONFIRMED,
                rationale=f"{code!r} has no counterpart in the new version"))
    for code in free_right:
        if code not in taken_right:
            mappings.append(EventMapping(
                mapping_type=MappingType.ADDED, source_codes=[], target_codes=[code],
                confidence=1.0, provenance="AUTO_NO_COUNTERPART",
                review_status=MappingReviewStatus.AUTO_CONFIRMED,
                rationale=f"{code!r} is new in the new version"))
    return mappings


def mapping_revision(mappings: Iterable[EventMapping]) -> str:
    """A fingerprint of the mapping set's MEANING and review state. A simulation
    or decision binds to it, so changing any mapping invalidates both."""
    rows = sorted(
        (item.mapping_type.value, sorted(item.source_codes), sorted(item.target_codes),
         item.review_status.value,
         sorted((r.source_code, r.source_index, r.target_code, r.target_index)
                for r in item.occurrence_rules))
        for item in effective_mappings(mappings))
    return sha256_text(f"{MAPPING_ENGINE_VERSION}|{canonical_json(rows)}")


def _issue(code: str, path: str, detail: str) -> dict[str, str]:
    return {"code": code, "path": path, "detail": detail}


def mapping_coverage_problems(
    left: UniversalSchedule, right: UniversalSchedule, mappings: Iterable[EventMapping],
) -> list[dict[str, str]]:
    """STRUCTURAL correspondence only: every V1 and V2 event is covered exactly once,
    only by events that exist. It says nothing about whether a person has reviewed
    the mapping - use ``mapping_readiness_problems`` (or ``mapping_problems`` for both)."""
    problems: list[dict[str, str]] = []
    left_codes = {event.code for event in left.events}
    right_codes = {event.code for event in right.events}
    seen_sources: dict[str, int] = {}
    seen_targets: dict[str, int] = {}
    for item in effective_mappings(mappings):
        for code in item.source_codes:
            seen_sources[code] = seen_sources.get(code, 0) + 1
            if code not in left_codes:
                problems.append(_issue("MAPPING_SOURCE_UNKNOWN", f"mapping[{item.id}].source",
                                       f"{code!r} is not an event of the earlier version"))
        for code in item.target_codes:
            seen_targets[code] = seen_targets.get(code, 0) + 1
            if code not in right_codes:
                problems.append(_issue("MAPPING_TARGET_UNKNOWN", f"mapping[{item.id}].target",
                                       f"{code!r} is not an event of the later version"))
    for code in sorted(left_codes):
        if seen_sources.get(code, 0) != 1:
            problems.append(_issue("MAPPING_SOURCE_COVERAGE", f"event[{code}]",
                                   f"{code!r} (earlier version) is mapped {seen_sources.get(code, 0)} times"))
    for code in sorted(right_codes):
        if seen_targets.get(code, 0) != 1:
            problems.append(_issue("MAPPING_TARGET_COVERAGE", f"event[{code}]",
                                   f"{code!r} (later version) is mapped {seen_targets.get(code, 0)} times"))
    return problems


def mapping_readiness_problems(mappings: Iterable[EventMapping]) -> list[dict[str, str]]:
    """REVIEW readiness: nothing left proposed, and every split/merge states where its
    occurrences go. Simulation, approval and adoption require this to be empty."""
    problems: list[dict[str, str]] = []
    for item in effective_mappings(mappings):
        label = f"mapping[{item.id}]"
        if not item.resolved:
            problems.append(_issue(
                "MAPPING_UNREVIEWED", label,
                f"{', '.join(item.source_codes) or '-'} -> {', '.join(item.target_codes) or '-'} "
                f"({item.mapping_type.value}) still needs a reviewer's decision: {item.rationale}"))
            continue
        if item.mapping_type in {MappingType.SPLIT, MappingType.MERGED}:
            ruled_sources = {r.source_code for r in item.occurrence_rules}
            ruled_targets = {r.target_code for r in item.occurrence_rules}
            if not item.occurrence_rules or ruled_sources != set(item.source_codes) \
                    or ruled_targets != set(item.target_codes):
                problems.append(_issue(
                    "MAPPING_RULES_MISSING", label,
                    "a confirmed split/merge must state, for every earlier and later visit, "
                    "which occurrence goes where"))
    return problems


def mapping_problems(
    left: UniversalSchedule, right: UniversalSchedule, mappings: Iterable[EventMapping],
) -> list[dict[str, str]]:
    """Structural AND readiness issues, each coded: the complete gate."""
    items = list(mappings)
    return [*mapping_coverage_problems(left, right, items), *mapping_readiness_problems(items)]


class Translation(StrEnum):
    #: the fact moves to a V2 occurrence
    TARGET = "TARGET"
    #: the visit was removed: the fact stays in V1 history, on purpose
    NONE = "NONE"
    #: not resolvable (unreviewed mapping, missing/ambiguous rule, overlapping rows, no mapping)
    UNRESOLVED = "UNRESOLVED"


_Result = tuple[Translation, "tuple[str, int] | None", "str | None"]


def _unresolved(reason: str) -> _Result:
    return Translation.UNRESOLVED, None, reason


def translate_occurrence(
    mappings: Iterable[EventMapping], source_code: str, source_index: int,
) -> _Result:
    """Where does one V1 occurrence go in V2? A unique answer, or UNRESOLVED with a reason.

    Never first-match: two effective rows naming the same earlier visit are ambiguous,
    and a rule that sent one occurrence to two places cannot exist (validated)."""
    rows = [item for item in effective_mappings(mappings) if source_code in item.source_codes]
    if not rows:
        return _unresolved(f"{source_code!r} is not covered by any mapping")
    if len(rows) > 1:
        return _unresolved(f"{source_code!r} appears in {len(rows)} mappings, so its destination is ambiguous")
    item = rows[0]
    if not item.resolved:
        return _unresolved(f"the mapping for {source_code!r} has not been reviewed")
    if item.mapping_type == MappingType.REMOVED:
        return Translation.NONE, None, None
    if item.carries_facts_automatically:
        return Translation.TARGET, (item.target_codes[0], source_index), None
    matches = {(r.target_code, r.target_index) for r in item.occurrence_rules
               if r.source_code == source_code and r.source_index == source_index}
    if len(matches) == 1:
        return Translation.TARGET, next(iter(matches)), None
    return _unresolved(f"{source_code!r} occurrence {source_index} has no reviewed split/merge rule")


def reverse_translate_occurrence(
    mappings: Iterable[EventMapping], target_code: str, target_index: int,
) -> _Result:
    """Which V1 occurrence does one V2 occurrence correspond to? Only where exactly one does.

    A many-to-one relation (a merge) has no unique inverse from the mapping alone:
    several earlier occurrences may feed one later one, so which actual belongs where
    needs provenance a reviewed transfer policy would supply. Until then it is
    UNRESOLVED and a reversal is blocked - never resolved by taking the first row."""
    rows = [item for item in effective_mappings(mappings) if target_code in item.target_codes]
    if not rows:
        return _unresolved(f"{target_code!r} is not covered by any mapping")
    if len(rows) > 1:
        return _unresolved(f"{target_code!r} appears in {len(rows)} mappings, so its origin is ambiguous")
    item = rows[0]
    if not item.resolved:
        return _unresolved(f"the mapping for {target_code!r} has not been reviewed")
    if item.mapping_type == MappingType.ADDED:
        return _unresolved(
            f"{target_code!r} exists only in the later version, so a fact recorded on it "
            "cannot be returned to the earlier one")
    if item.carries_facts_automatically:
        return Translation.TARGET, (item.source_codes[0], target_index), None
    origins = {(r.source_code, r.source_index) for r in item.occurrence_rules
               if r.target_code == target_code and r.target_index == target_index}
    if len(origins) == 1:
        return Translation.TARGET, next(iter(origins)), None
    if len(origins) > 1:
        return _unresolved(
            f"{target_code!r} occurrence {target_index} is fed by {len(origins)} earlier "
            "occurrences, so a fact recorded on it cannot be returned to one of them "
            "without a reviewed transfer policy")
    return _unresolved(f"{target_code!r} occurrence {target_index} has no reviewed split/merge rule")


# --------------------------------------------------------------------- adoption points
class AdoptionMode(StrEnum):
    REMAIN_ON_V1 = "REMAIN_ON_V1"
    ADOPT_IMMEDIATELY = "ADOPT_IMMEDIATELY"
    ADOPT_AT_EVENT = "ADOPT_AT_EVENT"
    ADOPT_AT_CYCLE = "ADOPT_AT_CYCLE"
    ADOPT_ON_DATE = "ADOPT_ON_DATE"


DEFERRED_MODES = frozenset({
    AdoptionMode.ADOPT_AT_EVENT, AdoptionMode.ADOPT_AT_CYCLE, AdoptionMode.ADOPT_ON_DATE})


class AdoptionPoint(StrictModel):
    """When a patient moves. The event and cycle are identities of the patient's
    CURRENT (V1) schedule; the date is a calendar date, never an instant."""

    mode: AdoptionMode
    event_code: str | None = None
    occurrence_index: int | None = Field(default=None, ge=0)
    repeat_block_code: str | None = None
    cycle_number: int | None = Field(default=None, ge=1)
    on_date: date | None = None

    @model_validator(mode="after")
    def fields_match_mode(self) -> "AdoptionPoint":
        has = {
            "event": self.event_code is not None or self.occurrence_index is not None,
            "cycle": self.repeat_block_code is not None or self.cycle_number is not None,
            "date": self.on_date is not None,
        }
        if self.mode == AdoptionMode.ADOPT_AT_EVENT:
            if self.event_code is None or self.occurrence_index is None:
                raise ValueError("adopting at an event needs the event code and occurrence index")
            if has["cycle"] or has["date"]:
                raise ValueError("adopting at an event takes no cycle or date")
        elif self.mode == AdoptionMode.ADOPT_AT_CYCLE:
            if self.repeat_block_code is None or self.cycle_number is None:
                raise ValueError("adopting at a cycle needs the repeat block code and cycle number")
            if has["event"] or has["date"]:
                raise ValueError("adopting at a cycle takes no event or date")
        elif self.mode == AdoptionMode.ADOPT_ON_DATE:
            if self.on_date is None:
                raise ValueError("adopting on a date needs the date")
            if has["event"] or has["cycle"]:
                raise ValueError("adopting on a date takes no event or cycle")
        elif any(has.values()):
            raise ValueError(f"{self.mode.value} takes no effective point")
        return self


def find_cycle_plan(schedule: UniversalSchedule, block_code: str):
    """The cycle plan of a repeat block, or None when the block or its plan is absent."""
    block = next((item for item in schedule.repeat_blocks if item.code == block_code), None)
    return block.cycle_plan if block is not None else None


def actual_cycle_start(
    plan, cycle_number: int, *, actual_anchors: dict[str, date],
    start_event_actuals: list[date],
) -> date | None:
    """The ACTUAL start date of cycle N, by the SAME contract the evaluator uses
    (``evaluator._actual_cycle_start``): a person-recorded ``<code>_<n>_START`` anchor,
    else the N-th recorded actual date of the plan's start event in date order (cycles are
    given in sequence), and for cycle 1 the actual date of the plan's own start anchor.
    No occurrence index is assumed: ``None`` means no actual start exists yet, and the
    decision keeps waiting - an effective date is never invented."""
    from .models import cycle_start_anchor_code

    recorded = actual_anchors.get(cycle_start_anchor_code(plan.code, cycle_number))
    if recorded is not None:
        return recorded
    ordered = sorted(start_event_actuals)
    if cycle_number - 1 < len(ordered):
        return ordered[cycle_number - 1]
    if cycle_number == 1:
        return actual_anchors.get(plan.start_reference.code)
    return None
