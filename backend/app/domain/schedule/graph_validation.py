"""Structural and source integrity checks shared by approval and execution.

Paths use declared codes rather than extraction order, so persisted/reordered graphs
produce the same diagnostics. Repeats are not dependency cycles: only explicit activity
and dimension-parent reference edges are traversed here.
"""
from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from .models import Severity, UniversalSchedule, ValidationIssue


def issue(code: str, path: str, message: str, *, entity=None, **details) -> ValidationIssue:
    return ValidationIssue(
        issue_code=code, severity=Severity.ERROR, blocking=True, message=message,
        entity_id=getattr(entity, "id", None),
        details={"field_path": path, **details})


def _cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    """Iterative DFS; no recursion-depth failure on an imported large graph."""
    colour: dict[str, int] = {}
    found: set[tuple[str, ...]] = set()
    for root in sorted(edges):
        if colour.get(root):
            continue
        stack = [(root, iter(sorted(edges.get(root, ()))))]; active = [root]
        colour[root] = 1
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                colour[node] = 2; stack.pop(); active.pop()
            elif colour.get(child) == 1:
                found.add(tuple(sorted(active[active.index(child):])))
            elif not colour.get(child):
                colour[child] = 1; active.append(child)
                stack.append((child, iter(sorted(edges.get(child, ())))))
    return [list(row) for row in sorted(found)]


def graph_integrity_issues(schedule: UniversalSchedule) -> list[ValidationIssue]:
    from .timing import ActivityReference
    from .validator import timing_references

    issues: list[ValidationIssue] = []
    identities: dict[UUID, str] = {}
    entities = {}
    collections = (
        "events", "anchors", "epochs", "arms", "cohorts", "populations", "dimensions",
        "conditional_definitions", "repeat_blocks", "confinement_episodes", "processes",
        "tracks", "evidence")
    for name in collections:
        for index, entity in enumerate(getattr(schedule, name)):
            label = getattr(entity, "code", None) or str(entity.id)
            path = f"{name}/{label}"
            if entity.id in identities:
                issues.append(issue("GRAPH_DUPLICATE_ID", path + "/id",
                                    "Two definitions share one identity", entity=entity,
                                    first_path=identities[entity.id]))
            else:
                identities[entity.id] = path
            entities[entity.id] = (name, entity)
            if name != "events":
                continue
            codes = {}; edges = defaultdict(set)
            for ai, activity in enumerate(entity.activities):
                apath = path + f"/activities/{activity.code or ai}"
                if activity.id in identities:
                    issues.append(issue("GRAPH_DUPLICATE_ID", apath + "/id",
                                        "Activity identity belongs to another definition",
                                        entity=activity, first_path=identities[activity.id]))
                identities.setdefault(activity.id, apath)
                entities[activity.id] = ("activities", activity)
                if activity.code:
                    if activity.code.casefold() in codes:
                        issues.append(issue("GRAPH_DUPLICATE_ACTIVITY_CODE", apath + "/code",
                                            "Activity codes must be unique within a visit", entity=activity))
                    codes[activity.code.casefold()] = activity
                    edges[activity.code]
                    for reference in timing_references(activity.timing):
                        if isinstance(reference, ActivityReference):
                            edges[activity.code].add(reference.activity_code)
            for cycle in _cycles(edges):
                issues.append(issue("GRAPH_ACTIVITY_CYCLE", path + "/activities",
                                    "Activity timing references form a cycle", entity=entity,
                                    activity_codes=cycle))

    dimension_edges = defaultdict(set)
    pools = {"ARM": list(schedule.arms), "COHORT": list(schedule.cohorts),
             "POPULATION": list(schedule.populations)}
    for dimension in schedule.dimensions:
        pools.setdefault(dimension.dimension_type, []).append(dimension)
    # Never mutate schedule lists while constructing the namespace lookup.
    for kind, pool in pools.items():
        for entity in pool:
            key = f"{kind}:{entity.code}"; dimension_edges[key]
            if entity.parent_code is not None:
                parent = f"{entity.parent_dimension_type}:{entity.parent_code}"
                dimension_edges[key].add(parent)
                if not any(row.code == entity.parent_code
                           for row in pools.get(entity.parent_dimension_type, ())):
                    issues.append(issue("GRAPH_DIMENSION_PARENT_MISSING", f"dimensions/{key}/parent_code",
                                        "The declared parent dimension does not exist", entity=entity,
                                        parent=parent))
    for cycle in _cycles(dimension_edges):
        issues.append(issue("GRAPH_DIMENSION_CYCLE", "dimensions", "Dimension parents form a cycle",
                            dimension_codes=cycle))

    entities[schedule.schedule_version_id] = ("schedule", schedule)
    for event in schedule.events:
        for owner in (event, *event.activities):
            for qualifier in owner.qualifiers:
                entities.setdefault(qualifier.id, ("qualifiers", qualifier))
    for episode in schedule.confinement_episodes:
        for day in episode.days:
            entities.setdefault(day.id, ("confinement_days", day))
    evidence = {row.id: row for row in schedule.evidence}
    protocol_id = schedule.schedule_metadata.protocol_version_id
    for index, claim in enumerate(schedule.claim_evidence):
        path = f"claim_evidence/{claim.claim_entity_id}/{claim.claim_type}/{claim.claim_path or ''}"
        if claim.claim_entity_id not in entities:
            issues.append(issue("GRAPH_ORPHAN_CLAIM", path, "Evidence claim names no schedule definition",
                                claim_entity_id=str(claim.claim_entity_id)))
        source = evidence.get(claim.evidence_id)
        if source is None:
            continue  # The existing MISSING_EVIDENCE check owns this diagnostic.
        source_version = source.source_locator.get("protocol_version_id")
        if source_version is not None and str(source_version) != str(protocol_id):
            issues.append(issue("EVIDENCE_VERSION_MISMATCH", path, "Evidence belongs to another protocol version",
                                evidence_id=str(source.id)))
        if not (source.source_text or "").strip():
            issues.append(issue("EVIDENCE_QUOTE_MISSING", f"evidence/{source.id}/source_text",
                                "Executable claims need the supporting source text", entity=source))

    return sorted(issues, key=lambda row: (str(row.details["field_path"]), row.issue_code, row.message))


def locate_issues(schedule: UniversalSchedule, issues: list[ValidationIssue]) -> list[ValidationIssue]:
    """Add stable exact locations to older validator diagnostics without changing codes."""
    paths = {}
    for name in ("events", "anchors", "epochs", "arms", "cohorts", "populations", "dimensions",
                 "conditional_definitions", "repeat_blocks", "confinement_episodes", "processes", "tracks"):
        for row in getattr(schedule, name):
            path = f"{name}/{getattr(row, 'code', row.id)}"; paths[row.id] = path
            for ai, activity in enumerate(getattr(row, "activities", ())):
                paths[activity.id] = path + f"/activities/{activity.code or ai}"
    for row in issues:
        if "field_path" not in row.details:
            field = row.details.get("field")
            path = paths.get(row.entity_id, "schedule")
            row.details = {**row.details, "field_path": path + (f"/{field}" if field else "")}
    return sorted(issues, key=lambda row: (str(row.details["field_path"]), row.issue_code, row.message))
