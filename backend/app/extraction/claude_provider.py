"""An ExtractionProvider backed by a language model.

The division of labour here is the important part, and it is deliberate:

    The MODEL reads the protocol and produces CLAIMS with citations.
    This module ASSEMBLES those claims into the canonical schedule.

Assembly is ordinary code, not another model call. That is not a performance
decision. Source traceability has to survive the pipeline, and a model asked to
emit its own ClaimEvidence entries will sometimes forget one - at which point a
schedule looks complete while a reviewer can no longer check where a rule came
from. Generating the traceability mechanically from each claim's citations makes
that failure impossible rather than unlikely.

Three consequences follow, and each is tested:

  * a claim citing evidence that does not exist is DROPPED with an issue, not
    kept with a dangling reference;
  * a claim that fails model validation becomes an issue naming the claim, not a
    crash and not a coerced approximation;
  * an event with no timing claim gets an UNRESOLVED timing that states why. The
    validator blocks approval on it, which is correct: a visit whose date nobody
    could determine must reach a human, not a patient.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, TypeAdapter, ValidationError

from app.domain.schedule.condition import ConditionExpression
from app.domain.schedule.models import (
    Activity, Anchor, ApplicabilityRule, ClaimEvidence, ConditionalAction,
    ConditionalDefinition, ConfinementDayDefinition, ConfinementEpisodeDefinition,
    DayNumbering, Dependency, DependencyMode, Epoch, Event, Evidence, GenericDimension, Qualifier,
    RecurrenceRule, RepeatBlock, SCHEDULE_ORIGIN_ROLE, ScheduleMetadata, StudyDimension,
    UniversalSchedule,
)
from app.domain.schedule.timing import (
    AnchorReference, EventReference, TimingExpression, NominalWindowTiming,
    OffsetTiming, ProtocolDayTiming, TemporalAmount,
)
from .llm_client import (
    ExtractionModelError, LLMClient, parse_json_list, parse_json_object,
)
from .prompts import CATEGORY_PROMPTS, DOCUMENT_STRUCTURE, EVIDENCE, SCHEDULE_OPTIONS, SYSTEM

log = logging.getLogger(__name__)

#: Maps a pipeline category to the claim_type recorded against the entity it
#: produces. This is what makes traceability mechanical: the assembler never has
#: to be told which claim type to write, it follows from which stage produced it.
CLAIM_TYPES: dict[str, str] = {
    "protocol_metadata": "SCHEDULE_METADATA",
    "epochs": "EPOCH",
    "arms_cohorts_populations": "DIMENSION",
    "anchors": "ANCHOR",
    "events": "EVENT_NAME",
    "event_types": "EVENT_TYPE",
    "timing": "TIMING",
    "conditions": "CONDITION",
    "applicability": "APPLICABILITY",
    "dependencies": "DEPENDENCY",
    "dependency_modes": "DEPENDENCY_MODE",
    "recurrence": "RECURRENCE",
    "repeat_blocks": "REPEAT_BLOCK",
    "activities": "ACTIVITY",
    "activity_timing": "ACTIVITY_TIMING",
    "activity_conditions": "ACTIVITY_CONDITION",
    "qualifiers": "QUALIFIER",
    "conditional_actions": "CONDITIONAL_ACTION",
    "conditional_resolution": "CONDITIONAL_RESOLUTION",
    "confinement": "CONFINEMENT_EPISODE",
}

NAMED_DIMENSIONS = {"ARM": "arms", "COHORT": "cohorts", "POPULATION": "populations"}

#: Validate what the model produced as the discriminated union it claims to be.
#: Round-tripping a whole event instead would let an invalid expression sit in
#: the schedule until serialisation, where it degrades to a warning.
TIMING = TypeAdapter(TimingExpression)
CONDITION = TypeAdapter(ConditionExpression)


def _scope_suffix(option: dict[str, Any]) -> str:
    """Told once, in the system prompt, so every category call inherits it.

    The model has already read the whole document by the time any category
    prompt runs (the document is baked into the client, not re-sent per
    call) - what it is missing is which of several independent schedules it
    is being asked about right now.
    """
    label = str(option.get("label") or option.get("id") or "").strip()
    description = str(option.get("description") or "").strip()
    location = str(option.get("source_location") or "").strip()
    detail = f" ({description})" if description else ""
    where = f" It is found at {location}." if location else ""
    return (
        f"\n\nThis document prints more than one independent Schedule of "
        f"Assessments. Answer every question about ONLY the schedule "
        f"'{label}'{detail}.{where} Ignore every other independent schedule "
        f"in this document."
    )


def _issue(
    code: str, message: str, *,
    severity: str = "ERROR", blocking: bool = True, **details: Any,
) -> dict[str, Any]:
    return {
        "issue_code": code, "severity": severity, "blocking": blocking,
        "message": message, "entity_type": "SCHEDULE", "details": details,
    }


class ClaudeExtractionProvider:
    """Implements the ExtractionProvider protocol against an LLM client.

    One model call per semantic category. A category that fails records an issue
    and contributes nothing, so a single bad answer degrades one dimension of
    meaning instead of losing the whole extraction.
    """

    def __init__(
        self, client: LLMClient, *, schedule_name: str = "Schedule of Assessments",
        schedule_option: dict[str, Any] | None = None,
    ):
        self.client = client
        self.schedule_name = schedule_name
        #: Doc 10-adjacent multi-schedule support: when this provider was built
        #: to extract ONE independent Schedule of Assessments out of a document
        #: that prints several (see runner.run_extraction_for_all_options), every
        #: prompt must be told which one - otherwise the model, having read the
        #: whole document, has no way to know which schedule's claims to report.
        self.schedule_option = schedule_option
        self._system = SYSTEM if schedule_option is None else SYSTEM + _scope_suffix(schedule_option)
        #: Model-chosen refs ("e1") mapped to the UUIDs the canonical model uses.
        #: The model never sees a UUID, so it cannot invent one that resolves.
        self._evidence_ids: dict[str, UUID] = {}
        self._evidence: list[Evidence] = []
        self._issues: list[dict[str, Any]] = []
        self._assembled: dict[str, Any] | None = None

    # --- pipeline nodes ---------------------------------------------------------

    def document_structure(self, state: dict[str, Any]) -> dict[str, Any]:
        try:
            return parse_json_object(self._ask(DOCUMENT_STRUCTURE, state))
        except ExtractionModelError as error:
            self._issues.append(_issue(
                "UNSUPPORTED_PROTOCOL_CONSTRUCT",
                f"could not map the protocol's structure: {error}",
            ))
            return {"sections": [], "tables": []}

    def discover_schedule_evidence(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            raw = parse_json_list(self._ask(EVIDENCE, state), "evidence")
        except ExtractionModelError as error:
            self._issues.append(_issue(
                "MISSING_EVIDENCE",
                f"could not collect source passages: {error}",
            ))
            return []

        output: list[dict[str, Any]] = []
        for item in raw:
            ref = str(item.get("ref") or "").strip()
            payload = {key: value for key, value in item.items() if key != "ref"}
            payload.setdefault("evidence_type", "SECTION")
            try:
                evidence = Evidence.model_validate(payload)
            except ValidationError as error:
                self._issues.append(_issue(
                    "MISSING_EVIDENCE",
                    f"a cited passage could not be recorded: {error.errors()[:2]}",
                    ref=ref,
                ))
                continue
            if ref:
                if ref in self._evidence_ids:
                    self._issues.append(_issue("MISSING_EVIDENCE", "A source reference was reused for different passages.", ref=ref))
                    continue
                self._evidence_ids[ref] = evidence.id
            page = next((page for page in state.get("pages", [])
                         if page.get("page_number") == evidence.page_number), None)
            if page is None:
                self._issues.append(_issue("MISSING_EVIDENCE", "A citation names a page outside the uploaded document.", ref=ref))
            elif page.get("text_status", "text") == "text":
                normalize = lambda text: re.sub(r"\s+", " ", text or "").strip().casefold()
                if not evidence.source_text or normalize(evidence.source_text) not in normalize(page.get("text", "")):
                    self._issues.append(_issue(
                        "UNVERIFIED_SOURCE_QUOTE", "A quoted passage could not be verified on its cited page. Check the original table.",
                        ref=ref, page_number=evidence.page_number, source_text=evidence.source_text,
                    ))
            # The model's own ref is kept so a reviewer can trace a claim back to
            # the answer that produced it, not just to the passage.
            self._evidence_ids.setdefault(str(evidence.id), evidence.id)
            self._evidence.append(evidence)
            output.append(evidence.model_dump(mode="json"))
        return output

    def detect_schedule_options(self, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Whether this document prints more than one independent schedule.

        Not a graph node: it runs once, before the caller decides how many
        times to run the rest of the pipeline (runner.py's
        run_extraction_for_all_options). A failure here degrades to "no
        multi-schedule detected" - the single-schedule path is the safe
        default for the overwhelming majority of protocols, not a hard
        failure with nowhere to attach an issue.
        """
        try:
            raw = parse_json_list(
                self.client.complete(system=self._system, prompt=SCHEDULE_OPTIONS), "options")
        except ExtractionModelError as error:
            log.warning("schedule-option detection failed, assuming a single schedule: %s", error)
            self._issues.append(_issue(
                "SCHEDULE_DISCOVERY_FAILED",
                "Could not determine whether the document contains multiple schedules. "
                "Retry extraction before approval.", error=str(error),
            ))
            return []
        options: list[dict[str, Any]] = []
        for item in raw:
            option_id = str(item.get("id") or "").strip()
            label = str(item.get("label") or "").strip()
            if not option_id or not label:
                continue
            options.append({
                "id": option_id, "label": label,
                "description": str(item.get("description") or "").strip(),
                "source_location": str(item.get("source_location") or "").strip(),
            })
        return options

    def extract_claims(self, category: str, state: dict[str, Any]) -> list[dict[str, Any]]:
        prompt = CATEGORY_PROMPTS.get(category)
        if prompt is None:
            return []
        try:
            raw = parse_json_list(self._ask(self._with_evidence(prompt), state), "claims")
        except ExtractionModelError as error:
            self._issues.append(_issue(
                "UNSUPPORTED_PROTOCOL_CONSTRUCT",
                f"could not extract {category.replace('_', ' ')}: {error}",
                category=category,
            ))
            return []

        claims: list[dict[str, Any]] = []
        for index, item in enumerate(raw):
            candidate = item.get("candidate")
            if not isinstance(candidate, dict):
                continue
            resolved = self._resolve_evidence(item.get("evidence_ids"), category)
            if not resolved:
                # Doc-wide rule: an uncited claim cannot be checked against the
                # protocol, so it is reported rather than absorbed.
                self._issues.append(_issue(
                    "MISSING_EVIDENCE",
                    f"a {category.replace('_', ' ')} claim cited no usable source passage",
                    category=category,
                    claim=str(item.get("claim_id") or f"{category}-{index}"),
                ))
                continue
            claims.append({
                "claim_id": str(item.get("claim_id") or f"{category}-{index}"),
                "claim_type": CLAIM_TYPES.get(category, category.upper()),
                "statement": str(item.get("statement") or "")[:2000],
                "evidence_ids": [str(value) for value in resolved],
                "candidate": {**candidate, "_category": category},
                "confidence": _confidence(item.get("confidence")),
            })
        return claims

    def build_relationships(self, state: dict[str, Any]) -> dict[str, Any]:
        """Relationships are derived from the claims themselves, not asked for."""
        return {}

    def completeness_check(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Problems found while READING the protocol - bad citations, bad JSON."""
        issues, self._issues = self._issues, []
        return issues

    def consistency_check(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Problems found while ASSEMBLING - claims that do not cohere.

        Assembly happens here rather than in the assembly node because the graph
        collects issues before it collects the schedule. A claim naming a visit
        that was never extracted, or a visit whose timing never arrived, is
        exactly what a reviewer needs told, and running assembly later would
        compute those findings and then discard them.
        """
        self._assembled = _assemble(
            claims=state.get("claims", []),
            evidence=self._evidence,
            default_name=self.schedule_name,
            issues=self._issues,
        )
        issues, self._issues = self._issues, []
        return issues

    def assemble_schedule(self, state: dict[str, Any]) -> dict[str, Any]:
        """The schedule built during the consistency check."""
        if self._assembled is None:
            self._assembled = _assemble(
                claims=state.get("claims", []), evidence=self._evidence,
                default_name=self.schedule_name, issues=self._issues,
            )
        return self._assembled

    # --- helpers ------------------------------------------------------------------

    def _ask(self, prompt: str, state: dict[str, Any]) -> str:
        # Calls have no conversation memory. Carry the established identity
        # registry forward so timing/activities cannot independently rename visits.
        registry: dict[str, list[dict[str, Any]]] = {}
        for claim in state.get("claims", []):
            candidate = claim.get("candidate") or {}
            category = candidate.get("_category")
            if category not in {"events", "anchors", "activities", "epochs",
                                "arms_cohorts_populations", "repeat_blocks", "conditions"}:
                continue
            identity = {key: candidate[key] for key in (
                "code", "event_code", "display_name", "protocol_label", "dimension_type",
            ) if key in candidate}
            if identity:
                registry.setdefault(category, []).append(identity)
        if registry:
            prompt += (
                "\n\nEstablished identifiers from earlier extraction stages:\n"
                + json.dumps(registry, ensure_ascii=False)
                + "\nUse these exact codes when referencing their entities. Do not "
                "invent synonyms or merge distinct cycle days. If no identifier "
                "matches the source, report the unresolved reference explicitly."
            )
        if state.get("document_map"):
            prompt += "\n\nDocument section map:\n" + json.dumps(state["document_map"], ensure_ascii=False)
        return self.client.complete(system=self._system, prompt=prompt)

    def _with_evidence(self, prompt: str) -> str:
        """Append the citable passages, so claims can only cite what exists."""
        if not self._evidence:
            return prompt
        lines = [
            f'- {ref}: p{item.page_number or "?"} "{(item.source_text or "")[:240]}"'
            for ref, evidence_id in self._evidence_ids.items()
            for item in self._evidence
            if item.id == evidence_id and not _is_uuid(ref)
        ]
        return (
            f"{prompt}\n\nCite only these passages, by their ref:\n"
            + "\n".join(lines)
        )

    def _resolve_evidence(self, values: Any, category: str) -> list[UUID]:
        if not isinstance(values, list):
            return []
        resolved: list[UUID] = []
        for value in values:
            found = self._evidence_ids.get(str(value).strip())
            if found is not None and found not in resolved:
                resolved.append(found)
        return resolved


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _confidence(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return min(1.0, max(0.0, number))


# --- deterministic assembly --------------------------------------------------------


class _Assembler:
    """Turns claims into a UniversalSchedule, recording evidence as it goes.

    Every ``_claim`` call both attaches the entity and records its traceability,
    so there is no path that produces an entity without its citation.
    """

    def __init__(self, default_name: str, issues: list[dict[str, Any]]):
        self.default_name = default_name
        self.issues = issues
        self.metadata = ScheduleMetadata(name=default_name)
        self.epochs: list[Epoch] = []
        self.dimensions: dict[str, list[StudyDimension]] = {
            "arms": [], "cohorts": [], "populations": [],
        }
        self.generic: list[GenericDimension] = []
        self.anchors: list[Anchor] = []
        self.events: dict[str, Event] = {}
        self.conditionals: dict[str, ConditionalDefinition] = {}
        self.repeat_blocks: list[RepeatBlock] = []
        self.confinements: list[ConfinementEpisodeDefinition] = []
        self.claim_evidence: list[ClaimEvidence] = []

    # -- traceability ---------------------------------------------------------------

    def cite(
        self, claim: dict[str, Any], entity_type: str, entity_id: UUID,
        *, claim_path: str | None = None, claim_type: str | None = None,
    ) -> None:
        for evidence_id in claim.get("evidence_ids", []):
            try:
                value = UUID(str(evidence_id))
            except ValueError:
                continue
            self.claim_evidence.append(ClaimEvidence(
                evidence_id=value,
                claim_type=claim_type or claim.get("claim_type", "OTHER"),
                claim_entity_type=entity_type, claim_entity_id=entity_id,
                claim_path=claim_path, confidence=claim.get("confidence"),
            ))

    def refs(self, claim: dict[str, Any]) -> list[UUID]:
        output: list[UUID] = []
        for evidence_id in claim.get("evidence_ids", []):
            try:
                output.append(UUID(str(evidence_id)))
            except ValueError:
                continue
        return output

    def reject(self, claim: dict[str, Any], category: str, error: Exception) -> None:
        detail = (
            error.errors(include_context=False)[:2] if isinstance(error, ValidationError) else str(error))
        self.issues.append(_issue(
            "UNSUPPORTED_PROTOCOL_CONSTRUCT",
            f"a {category.replace('_', ' ')} claim did not describe something the "
            f"schedule model can represent",
            category=category, claim=claim.get("claim_id"), detail=detail,
        ))


def _assemble(
    *,
    claims: list[dict[str, Any]],
    evidence: list[Evidence],
    default_name: str,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    builder = _Assembler(default_name, issues)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        category = str((claim.get("candidate") or {}).get("_category") or "")
        if category:
            grouped.setdefault(category, []).append(claim)

    # Order matters: events must exist before anything can attach to them.
    _apply_metadata(builder, grouped.get("protocol_metadata", []))
    _apply_epochs(builder, grouped.get("epochs", []))
    _apply_dimensions(builder, grouped.get("arms_cohorts_populations", []))
    _apply_anchors(builder, grouped.get("anchors", []))
    _apply_events(builder, grouped.get("events", []))
    _apply_event_types(builder, grouped.get("event_types", []))
    _apply_timing(builder, grouped.get("timing", []))
    _apply_conditions(builder, grouped.get("conditions", []))
    _apply_dependencies(builder, grouped.get("dependencies", []))
    _apply_dependency_modes(builder, grouped.get("dependency_modes", []))
    _apply_recurrence(builder, grouped.get("recurrence", []))
    _apply_repeat_blocks(builder, grouped.get("repeat_blocks", []))
    _apply_activities(builder, grouped.get("activities", []))
    _apply_activity_timing(builder, grouped.get("activity_timing", []))
    # After activities: both need to look one up by code.
    _apply_activity_conditions(builder, grouped.get("activity_conditions", []))
    _apply_qualifiers(builder, grouped.get("qualifiers", []))
    _apply_conditionals(builder, grouped.get("conditional_actions", []))
    _apply_conditional_resolution(builder, grouped.get("conditional_resolution", []))
    # After conditionals: an applicability claim may target one by code
    # instead of an event/activity.
    _apply_applicability(builder, grouped.get("applicability", []))
    _apply_confinement(builder, grouped.get("confinement", []))
    _fill_missing_timing(builder)

    schedule = UniversalSchedule(
        schedule_metadata=builder.metadata,
        epochs=builder.epochs,
        arms=builder.dimensions["arms"],
        cohorts=builder.dimensions["cohorts"],
        populations=builder.dimensions["populations"],
        dimensions=builder.generic,
        anchors=builder.anchors,
        events=list(builder.events.values()),
        conditional_definitions=list(builder.conditionals.values()),
        repeat_blocks=builder.repeat_blocks,
        confinement_episodes=builder.confinements,
        evidence=list(evidence),
        claim_evidence=builder.claim_evidence,
    )
    return schedule.model_dump(mode="json")


def _candidate(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in (claim.get("candidate") or {}).items()
        if key != "_category"
    }


def _normalize_requiredness(value: object) -> str:
    """``Requiredness`` has no ``UNCLEAR`` member - it is ``UNRESOLVED`` - but a
    model answering honestly that the protocol does not state this may still
    say "unclear". Without this, that correct answer raised a
    ``ValidationError`` and silently dropped the WHOLE activity claim (not
    just the field) via ``builder.reject()``, which is worse than a wrong
    default: the activity simply vanished from the schedule.
    """
    text = str(value or "").strip().upper()
    return "UNRESOLVED" if text in {"", "UNCLEAR"} else text


def _apply_metadata(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims[:1]:
        values = _candidate(claim)
        builder.metadata = ScheduleMetadata(
            name=str(values.get("name") or builder.default_name),
            description=values.get("description"),
            schedule_type=str(values.get("schedule_type") or "PRIMARY"),
        )


def _apply_epochs(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        try:
            epoch = Epoch(**values, evidence_refs=builder.refs(claim))
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "epochs", error)
            continue
        builder.epochs.append(epoch)
        builder.cite(claim, "EPOCH", epoch.id)


def _apply_dimensions(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        dimension_type = str(values.pop("dimension_type", "") or "").upper()
        try:
            if dimension_type in NAMED_DIMENSIONS:
                dimension = StudyDimension(**values)
                builder.dimensions[NAMED_DIMENSIONS[dimension_type]].append(dimension)
            elif dimension_type:
                dimension = GenericDimension(dimension_type=dimension_type, **values)
                builder.generic.append(dimension)
            else:
                raise ValueError("a patient group needs its dimension type")
        except (ValidationError, TypeError, ValueError) as error:
            builder.reject(claim, "arms_cohorts_populations", error)
            continue
        builder.cite(claim, "DIMENSION", dimension.id)


def _apply_anchors(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    origin_candidates: list[Anchor] = []
    for claim in claims:
        values = _candidate(claim)
        is_origin = bool(values.pop("is_schedule_origin", False))
        try:
            anchor = Anchor(
                **values, evidence_refs=builder.refs(claim),
                # AD-5: RESOLVED is stated explicitly for the one candidate this
                # extraction pass currently produces per anchor claim, rather
                # than left to a model-level default that would silently cover
                # a future ambiguity-detecting pass too.
                status="RESOLVED",
            )
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "anchors", error)
            continue
        builder.anchors.append(anchor)
        builder.cite(claim, "ANCHOR", anchor.id)
        if is_origin:
            origin_candidates.append(anchor)

    # AD-5/AD-2: the schedule's Day 1/Day 0 origin is a protocol-stated fact,
    # never inferred by matching an anchor's name (e.g. "Baseline"). Recording
    # it here, when the model states it, is what lets schedule_origin_anchor()
    # resolve it without that inference.
    if len(origin_candidates) == 1:
        origin = origin_candidates[0]
        origin.derivation_rule = {"role": SCHEDULE_ORIGIN_ROLE}
        builder.metadata.day_numbering = DayNumbering(day_1_anchor=AnchorReference(code=origin.code))
    elif len(origin_candidates) > 1:
        builder.issues.append(_issue(
            "AMBIGUOUS_BASELINE",
            "more than one anchor was extracted as the schedule's Day 1/Day 0 "
            "origin; a reviewer must state which one it is",
            category="anchors",
            candidates=[item.code for item in origin_candidates],
        ))
    elif builder.anchors:
        # AD-5: this schedule has anchors but none was claimed as the Day 1/Day
        # 0 origin - the exact silent gap this fix exists to close, since a
        # model that simply never answers "is_schedule_origin" must not read
        # the same as a model that answered "no". Recorded on
        # ``schedule_metadata.extensions`` (the same durable, re-derivable
        # mechanism ``_information_loss`` already uses) rather than only in
        # ``builder.issues``, so the block survives a re-validation, not just
        # this one import.
        builder.metadata.extensions.append({
            "type": "UNRESOLVED_SCHEDULE_ORIGIN",
            "detail": (
                "No anchor was extracted as the schedule's Day 1/Day 0 origin; "
                "every Day-N date on it would be uncomputable."
            ),
        })


def _apply_events(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        code = str(values.get("code") or "").strip()
        if not code or code in builder.events:
            continue
        try:
            event = Event(
                code=code,
                protocol_label=str(values.get("protocol_label") or code),
                display_name=str(values.get("display_name") or code),
                # AD-5: an unstated event type must not become "SITE_VISIT" -
                # that is an active claim ("come to the hospital"), which is
                # exactly the harm this rule exists to prevent. "OTHER" is the
                # taxonomy's escape member and correctly lands as
                # requires_review instead of a plausible-looking guess.
                event_type=str(values.get("event_type") or "OTHER"),
                sequence_number=values.get("sequence_number"),
                # Replaced by the timing stage. Left unresolved so an event whose
                # timing never arrives blocks approval rather than defaulting.
                timing=TIMING.validate_python({
                    "type": "UNRESOLVED",
                    "reason": "no timing rule was extracted for this visit",
                }),
                evidence_refs=builder.refs(claim),
            )
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "events", error)
            continue
        builder.events[code] = event
        builder.cite(claim, "EVENT", event.id, claim_path="display_name")


def _normalized_label(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _normalize_code(text: str) -> str:
    return re.sub(r"\s+", "", text or "").casefold()


#: Below this token-overlap score a claimed label is not treated as naming
#: any real entity at all - a low-confidence match is worse than none, since
#: it would attach a claim to the wrong visit/anchor silently.
_FUZZY_MATCH_THRESHOLD = 0.5

_NUMBER_RE = re.compile(r"\d+")


def _label_tokens(text: str) -> set[str]:
    return {item for item in re.split(r"[^a-z0-9]+", _normalized_label(text)) if item}


def _label_similarity(claimed: str, candidate_label: str) -> float:
    """Token-overlap (Jaccard) similarity, with one hard rule numbers alone
    justify: "Week 4" and "Week 40" share every character but one and would
    score highly under any generic string-similarity metric, yet are
    different clinical visits. A number embedded in the claimed label is
    never allowed to fuzzy-match a candidate's different number - it must be
    absent from one side or identical, or the candidate is disqualified
    outright regardless of how similar the surrounding words are.
    """
    if not candidate_label:
        return 0.0
    claimed_numbers, candidate_numbers = _NUMBER_RE.findall(claimed), _NUMBER_RE.findall(candidate_label)
    if claimed_numbers != candidate_numbers:
        return 0.0
    claimed_tokens, candidate_tokens = _label_tokens(claimed), _label_tokens(candidate_label)
    if not claimed_tokens or not candidate_tokens:
        return 0.0
    return len(claimed_tokens & candidate_tokens) / len(claimed_tokens | candidate_tokens)


def _best_fuzzy_match(claimed: str, candidates: list[tuple[Any, list[str]]]) -> Any | None:
    """The single best-scoring candidate at or above threshold, from
    ``candidates`` as ``(item, [labels to compare against])`` pairs - or
    ``None`` when nothing scores high enough, or the top two are too close
    to call. AD-5: an ambiguous match is refused, not guessed, the same
    discipline this codebase already applies to a genuinely ambiguous
    schedule origin anchor rather than picking one silently.
    """
    scored = sorted(
        (
            (item, max((_label_similarity(claimed, label) for label in labels), default=0.0))
            for item, labels in candidates
        ),
        key=lambda pair: pair[1], reverse=True,
    )
    if not scored or scored[0][1] < _FUZZY_MATCH_THRESHOLD:
        return None
    if len(scored) > 1 and scored[1][1] >= scored[0][1] - 1e-9:
        return None
    return scored[0][0]


def _event_for(builder: _Assembler, claim: dict[str, Any], category: str) -> Event | None:
    code = str(_candidate(claim).get("event_code") or "").strip()
    event = builder.events.get(code)
    if event is None and code:
        # A later category call sometimes echoes the established code with
        # different casing or stray whitespace (e.g. "v5" for "V5") - this is
        # still the same identifier, not a guess, so it is resolved before
        # falling back to fuzzy label matching below.
        normalized = _normalize_code(code)
        if normalized:
            for candidate_code, candidate_event in builder.events.items():
                if _normalize_code(candidate_code) == normalized:
                    event = candidate_event
                    break
    if event is None and code:
        # AD-2: every category is an independent model call with no memory of
        # the events stage's own answer, so a later claim has to re-derive the
        # code from the document alone. A model sometimes echoes a visit's
        # protocol_label/display_name back instead - sometimes an exact
        # match, sometimes an abbreviation of it (observed with Gemini via
        # eval/pipeline_diff.py on a real protocol: one run echoed "Baseline/
        # Randomization" verbatim, another shortened the code itself to
        # "BASELINE"). Fuzzy label matching recovers both without depending
        # on any one provider's behaviour - the WARNING below keeps the
        # substitution visible rather than silent.
        event = _best_fuzzy_match(
            code,
            [(item, [item.protocol_label, item.display_name]) for item in builder.events.values()],
        )
        if event is not None:
            builder.issues.append(_issue(
                "UNRESOLVED_REFERENCE",
                f"a {category.replace('_', ' ')} claim named visit {code!r} by its "
                f"label rather than its code ({event.code!r}); matched by label - "
                "a reviewer should confirm this is the intended visit",
                severity="WARNING", blocking=not any(
                    _normalized_label(code) == _normalized_label(label)
                    for label in (event.protocol_label, event.display_name)),
                category=category, event_code=event.code, claimed_label=code,
            ))
    if event is None:
        builder.issues.append(_issue(
            "UNRESOLVED_REFERENCE",
            f"a {category.replace('_', ' ')} claim referenced visit {code!r}, "
            "which was not extracted as a visit",
            category=category, event_code=code,
        ))
    return event


def _resolve_labeled_references(builder: _Assembler, value: object, category: str) -> None:
    """The same code-vs-label drift ``_event_for`` recovers can happen one
    level deeper: an ``AnchorReference``/``EventReference`` nested INSIDE a
    timing or recurrence structure (a TRIGGERED timing's ``trigger``, a
    NOMINAL_WITH_WINDOW's inner offset, ``RecurrenceRule.start_reference``,
    ...) is a plain leaf value ``TIMING.validate_python``/``RecurrenceRule.
    model_validate`` accept as-is; nothing else checks it against what the
    anchors/events stages actually extracted. Walks the whole freshly-parsed
    structure recursively (field name and nesting depth vary by timing
    variant, so this does not special-case any one shape) and corrects any
    reference found by label in place, exactly as ``_event_for`` does for the
    top-level ``event_code``.
    """
    if isinstance(value, AnchorReference):
        if any(item.code == value.code for item in builder.anchors):
            return
        match = _best_fuzzy_match(
            value.code,
            [(item, [item.protocol_label or "", item.display_name]) for item in builder.anchors],
        )
        if match is not None:
            builder.issues.append(_issue(
                "UNRESOLVED_REFERENCE",
                f"a {category.replace('_', ' ')} claim named anchor {value.code!r} by its "
                f"label rather than its code ({match.code!r}); matched by label - "
                "a reviewer should confirm this is the intended anchor",
                severity="WARNING", blocking=not any(
                    _normalized_label(value.code) == _normalized_label(label)
                    for label in (match.protocol_label or "", match.display_name)),
                category=category, anchor_code=match.code, claimed_label=value.code,
            ))
            value.code = match.code
    elif isinstance(value, EventReference):
        if value.event_code in builder.events:
            return
        match = _best_fuzzy_match(
            value.event_code,
            [(item, [item.protocol_label, item.display_name]) for item in builder.events.values()],
        )
        if match is not None:
            builder.issues.append(_issue(
                "UNRESOLVED_REFERENCE",
                f"a {category.replace('_', ' ')} claim named visit {value.event_code!r} by its "
                f"label rather than its code ({match.code!r}) inside a timing rule; matched by "
                "label - a reviewer should confirm this is the intended visit",
                severity="WARNING", blocking=not any(
                    _normalized_label(value.event_code) == _normalized_label(label)
                    for label in (match.protocol_label, match.display_name)),
                category=category, event_code=match.code, claimed_label=value.event_code,
            ))
            value.event_code = match.code
    elif isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            _resolve_labeled_references(builder, getattr(value, field_name), category)
    elif isinstance(value, list):
        for item in value:
            _resolve_labeled_references(builder, item, category)


def _apply_event_types(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "event_types")
        if event is None:
            continue
        values = _candidate(claim)
        modes = values.get("allowed_visit_modes") or []
        try:
            event.visit_mode = values.get("visit_mode") or None
            event.allowed_visit_modes = [str(item) for item in modes if item]
            activation = str(values.get("activation") or "SCHEDULED").upper()
            event.activation = "ON_DEMAND" if activation == "ON_DEMAND" else "SCHEDULED"
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "event_types", error)
            continue
        builder.cite(claim, "EVENT", event.id, claim_path="visit_mode")


def _apply_timing(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "timing")
        if event is None:
            continue
        timing = _candidate(claim).get("timing")
        if not isinstance(timing, dict):
            continue
        try:
            event.timing = TIMING.validate_python(timing)
        except (ValidationError, TypeError) as error:
            # A timing we cannot represent stays UNRESOLVED rather than becoming
            # a near-miss the reviewer would have no reason to question.
            builder.reject(claim, "timing", error)
            continue
        _resolve_labeled_references(builder, event.timing, "timing")
        builder.cite(claim, "EVENT", event.id, claim_path="timing")


def _apply_conditions(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "conditions")
        if event is None:
            continue
        condition = _candidate(claim).get("condition")
        if not isinstance(condition, dict):
            continue
        try:
            resolved = CONDITION.validate_python(condition)
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "conditions", error)
            continue
        # A second claim on the same event is a second, distinct condition -
        # appending, not replacing, is what keeps both instead of the model's
        # last answer silently overwriting an earlier one.
        event.conditions.append(resolved)
        builder.cite(claim, "EVENT", event.id, claim_path="conditions")


def _apply_applicability(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    """AD-7: which patients a visit, assessment, or conditional pathway applies to.

    Never inferred from a visit's name or table position - only from an
    explicit protocol statement citing which arm/cohort/population it names.
    Left completely unbuilt before this: the ``conditions`` prompt itself
    draws the line ("a visit that simply applies to one arm is NOT a
    condition - that is applicability") but nothing ever extracted the other
    side of that distinction, so every ApplicabilityRule in the schedule
    model was dead code from extraction's point of view.

    A claim targets exactly one of an event (optionally narrowed to one
    activity within it) or a conditional definition by its own code - a
    ``ConditionalDefinition`` is not tied to any single visit (evaluator.py's
    ``conditional.py:176`` gates the whole pathway, e.g. "this dose-hold rule
    only applies to the dose-escalation cohort", independently of which
    events its actions touch).
    """
    for claim in claims:
        values = _candidate(claim)
        conditional_code = values.get("conditional_code")
        target: Event | Activity | ConditionalDefinition
        entity_type: str
        if conditional_code:
            definition = builder.conditionals.get(str(conditional_code))
            if definition is None:
                builder.issues.append(_issue(
                    "UNRESOLVED_REFERENCE",
                    f"an applicability claim referenced conditional rule "
                    f"{conditional_code!r}, which was not extracted",
                    category="applicability",
                ))
                continue
            target = definition
            entity_type = "CONDITION"
        else:
            event = _event_for(builder, claim, "applicability")
            if event is None:
                continue
            activity_code = values.get("activity_code")
            target = event
            entity_type = "EVENT"
            if activity_code:
                activity = next(
                    (item for item in event.activities
                     if (item.code or item.display_name) == activity_code),
                    None,
                )
                if activity is None:
                    builder.issues.append(_issue(
                        "UNRESOLVED_REFERENCE",
                        f"an applicability claim referenced assessment {activity_code!r}, "
                        f"which is not recorded at {event.code}",
                        category="applicability",
                    ))
                    continue
                target = activity
                entity_type = "ACTIVITY"
        try:
            rule = ApplicabilityRule(
                dimension=str(values.get("dimension") or "").upper(),
                operator=str(values.get("operator") or "IN").upper(),
                values=[str(item) for item in (values.get("values") or [])],
            )
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "applicability", error)
            continue
        target.applicability.append(rule)
        builder.cite(claim, entity_type, target.id, claim_path="applicability")


def _apply_activity_conditions(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    """Which OCCURRENCES of a repeating visit one of its assessments runs on
    ("MRI every second cycle") - distinct from ``applicability`` (which
    PATIENT GROUP an assessment belongs to). Was complete absence before this:
    ``Activity.conditions`` existed on the domain model and the evaluator
    already reads it (``occurrence_number`` in ``condition_state``,
    evaluator.py's ``evaluate_activities``), but no category ever populated it
    - a cycle-gated assessment silently appeared on every occurrence instead.
    """
    for claim in claims:
        event = _event_for(builder, claim, "activity_conditions")
        if event is None:
            continue
        values = _candidate(claim)
        code = str(values.get("activity_code") or "")
        activity = next(
            (item for item in event.activities
             if (item.code or item.display_name) == code),
            None,
        )
        if activity is None:
            builder.issues.append(_issue(
                "UNRESOLVED_REFERENCE",
                f"an activity-condition claim referenced assessment {code!r}, "
                f"which is not recorded at {event.code}",
                category="activity_conditions",
            ))
            continue
        condition = values.get("condition")
        if not isinstance(condition, dict):
            continue
        try:
            resolved = CONDITION.validate_python(condition)
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "activity_conditions", error)
            continue
        activity.conditions.append(resolved)
        builder.cite(claim, "ACTIVITY", activity.id, claim_path="conditions")


def _apply_dependencies(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "dependencies")
        if event is None:
            continue
        values = _candidate(claim)
        try:
            dependency = Dependency(
                source_event_code=str(values.get("source_event_code") or ""),
                dependency_type=str(values.get("dependency_type") or "TEMPORAL"),
            )
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "dependencies", error)
            continue
        event.dependencies.append(dependency)
        builder.cite(claim, "EVENT", event.id, claim_path="dependencies")


def _apply_dependency_modes(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "dependency_modes")
        if event is None:
            continue
        mode = str(_candidate(claim).get("dependency_mode") or "").upper()
        if mode not in {"NOMINAL", "ACTUAL_PREVIOUS_EVENT", "MANUAL", "UNCLEAR"}:
            continue
        event.dependency_mode = DependencyMode(mode)
        builder.cite(claim, "EVENT", event.id, claim_path="dependency_mode")


def _apply_recurrence(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "recurrence")
        if event is None:
            continue
        rule = _candidate(claim).get("recurrence")
        if not isinstance(rule, dict):
            continue
        try:
            event.recurrence = RecurrenceRule.model_validate(rule)
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "recurrence", error)
            continue
        _resolve_labeled_references(builder, event.recurrence, "recurrence")
        nominal = event.timing.nominal if isinstance(event.timing, NominalWindowTiming) else event.timing
        if isinstance(nominal, (OffsetTiming, ProtocolDayTiming)) and nominal.reference == event.recurrence.start_reference:
            first_offset = nominal.offset if isinstance(nominal, OffsetTiming) else TemporalAmount(
                value=nominal.day - 1 if nominal.day > 0 else nominal.day, unit="DAY")
            if event.recurrence.start_offset is not None and event.recurrence.start_offset != first_offset:
                builder.issues.append(_issue(
                    "AMBIGUOUS_TIMING", "The recurrence start conflicts with this visit's first timing rule.",
                    event_code=event.code,
                ))
            else:
                event.recurrence.start_offset = first_offset
        builder.cite(claim, "EVENT", event.id, claim_path="recurrence")


def _apply_repeat_blocks(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        try:
            block = RepeatBlock(**values, evidence_refs=builder.refs(claim))
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "repeat_blocks", error)
            continue
        builder.repeat_blocks.append(block)
        builder.cite(claim, "REPEAT_BLOCK", block.id, claim_type="REPEAT_BLOCK")
        builder.cite(claim, "REPEAT_BLOCK", block.id, claim_type="DEPENDENCY_MODE")


def _apply_activities(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "activities")
        if event is None:
            continue
        values = _candidate(claim)
        values.pop("event_code", None)
        label = str(values.get("protocol_label") or values.get("display_name") or "")
        try:
            activity = Activity(
                code=values.get("code"),
                protocol_label=label,
                display_name=str(values.get("display_name") or label),
                activity_type=str(values.get("activity_type") or "ASSESSMENT"),
                # AD-5: an unstated requiredness must not become "REQUIRED" -
                # that silently inflates the "N of M required assessments
                # complete" denominator with something the protocol never
                # actually stated was required. "UNRESOLVED" is the
                # taxonomy's existing escape member (already used correctly
                # for Qualifier.category below).
                requiredness=_normalize_requiredness(values.get("requiredness")),
                sequence_number=values.get("sequence_number"),
                evidence_refs=builder.refs(claim),
            )
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "activities", error)
            continue
        event.activities.append(activity)
        builder.cite(claim, "ACTIVITY", activity.id, claim_type="ACTIVITY")


def _apply_activity_timing(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        event = _event_for(builder, claim, "activity_timing")
        if event is None:
            continue
        values = _candidate(claim)
        code = str(values.get("activity_code") or "")
        activity = next(
            (item for item in event.activities if (item.code or item.display_name) == code),
            None,
        )
        if activity is None:
            builder.issues.append(_issue(
                "UNRESOLVED_REFERENCE",
                f"an intra-day timing claim referenced assessment {code!r}, "
                f"which is not recorded at {event.code}",
                category="activity_timing",
            ))
            continue
        timing = values.get("timing")
        if not isinstance(timing, dict):
            continue
        try:
            activity.timing = TIMING.validate_python(timing)
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "activity_timing", error)
            continue
        _resolve_labeled_references(builder, activity.timing, "activity_timing")
        builder.cite(claim, "ACTIVITY", activity.id, claim_type="ACTIVITY_TIMING")


def _apply_qualifiers(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        targets = [str(item) for item in (values.get("target_codes") or []) if item]
        try:
            qualifier = Qualifier(
                marker=values.get("marker"),
                text=str(values.get("text") or ""),
                scope=str(values.get("scope") or "CELL"),
                category=str(values.get("category") or "UNRESOLVED"),
                target_codes=targets,
                resolved=bool(values.get("resolved")),
                evidence_refs=builder.refs(claim),
            )
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "qualifiers", error)
            continue

        owner = next(
            (builder.events[code] for code in targets if code in builder.events), None)
        if owner is None:
            owner = next(iter(builder.events.values()), None)
        if owner is None:
            builder.issues.append(_issue(
                "ORPHAN_QUALIFIER",
                f"footnote {qualifier.marker or qualifier.text[:40]!r} has no visit "
                "to attach to",
            ))
            continue
        owner.qualifiers.append(qualifier)
        builder.cite(claim, "QUALIFIER", qualifier.id, claim_type="QUALIFIER")


def _apply_conditionals(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        condition = values.get("condition")
        raw_actions = values.get("actions") or []
        if not isinstance(condition, dict) or not raw_actions:
            continue
        code = str(values.get("code") or "")
        actions: list[dict[str, Any]] = []
        for action in raw_actions:
            if not isinstance(action, dict):
                continue
            actions.append({**action, "condition": action.get("condition") or condition})
        try:
            definition = ConditionalDefinition.model_validate({
                "code": code,
                "protocol_label": values.get("protocol_label") or code,
                "display_name": values.get("display_name") or code,
                "condition": condition,
                "actions": actions,
                "evidence_refs": [str(item) for item in builder.refs(claim)],
            })
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "conditional_actions", error)
            continue
        builder.conditionals[code] = definition
        builder.cite(claim, "CONDITION", definition.id, claim_type="CONDITION")
        builder.cite(claim, "CONDITION", definition.id, claim_type="CONDITIONAL_ACTION")


def _apply_conditional_resolution(
    builder: _Assembler, claims: list[dict[str, Any]],
) -> None:
    for claim in claims:
        values = _candidate(claim)
        definition = builder.conditionals.get(str(values.get("code") or ""))
        resolution = values.get("resolution_condition")
        if definition is None or not isinstance(resolution, dict):
            continue
        try:
            definition.resolution_condition = CONDITION.validate_python(resolution)
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "conditional_resolution", error)
            continue
        builder.cite(
            claim, "CONDITION", definition.id, claim_type="CONDITIONAL_RESOLUTION")


def _apply_confinement(builder: _Assembler, claims: list[dict[str, Any]]) -> None:
    for claim in claims:
        values = _candidate(claim)
        days = []
        for day in values.get("days") or []:
            if not isinstance(day, dict):
                continue
            try:
                days.append(ConfinementDayDefinition.model_validate(day))
            except ValidationError:
                continue
        if not days:
            builder.issues.append(_issue(
                "UNSUPPORTED_PROTOCOL_CONSTRUCT",
                "a confinement claim listed no days, so the stay has no duration",
                claim=claim.get("claim_id"),
            ))
            continue
        try:
            episode = ConfinementEpisodeDefinition(
                code=str(values.get("code") or ""),
                protocol_label=str(values.get("protocol_label") or values.get("code") or ""),
                display_name=str(values.get("display_name") or values.get("code") or ""),
                admission_event_code=str(values.get("admission_event_code") or ""),
                dose_event_codes=[str(item) for item in (values.get("dose_event_codes") or [])],
                discharge_event_code=str(values.get("discharge_event_code") or ""),
                days=days,
                evidence_refs=builder.refs(claim),
            )
        except (ValidationError, TypeError) as error:
            builder.reject(claim, "confinement", error)
            continue
        builder.confinements.append(episode)
        builder.cite(
            claim, "CONFINEMENT_EPISODE", episode.id, claim_type="CONFINEMENT_EPISODE")


def _fill_missing_timing(builder: _Assembler) -> None:
    """Report every visit whose timing never arrived.

    The event already carries an UNRESOLVED timing, which blocks approval on its
    own. This adds the reviewer-facing issue that names the visit, so the fix is
    obvious rather than buried in a validation list.
    """
    for event in builder.events.values():
        if getattr(event.timing, "type", "") != "UNRESOLVED":
            continue
        builder.issues.append(_issue(
            "AMBIGUOUS_TIMING",
            f"no timing rule was extracted for {event.display_name!r}; a reviewer "
            "must supply it before any patient can be scheduled",
            event_code=event.code,
        ))
