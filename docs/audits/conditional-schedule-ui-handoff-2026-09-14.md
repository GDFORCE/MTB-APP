# Conditional schedule tables: implementation handoff

Date: 2026-09-14. Status: **plan only; none of the changes proposed here are implemented by this document.**

## 1. Decision and scope

Keep the existing extraction and scheduling engine. Add a shared, evidence-backed presentation model that distinguishes replacement pathways, additional conditional visits, and unplanned visits. Rebuilding extraction is unnecessary for this UI request. A small semantic contract extension is needed where the existing extracted rules do not explicitly describe pathway membership and replacement scope.

Implement this first in the post-extraction preview and the canonical protocol schedule/review screen. Both already share `SimpleScheduleTable`. These are protocol definitions: showing a conditional visit here must not activate it for a patient.

Do not change patient date evaluation, condition confirmation, reminders, cohort assignment, protocol version assignment, or read-mode settings as part of this work. Patient schedules contain actual operational instances and require different treatment from a protocol's hypothetical alternatives. Section 8 explains that boundary.

The AIM/heart-attack example below reflects the user's description. Its exact trigger, replacement scope, timing, and activities were not verified against the AIM document during this inspection. Do not hardcode that example as a medical or extraction rule.

## 2. What the current checked-in/workspace code actually displays

These findings come from targeted source inspection, not a database scan or a new extraction run. The workspace contains substantial prior modifications; older handoff descriptions do not always match it.

| Surface | Current display | Relevant files |
| --- | --- | --- |
| Add Trial extraction preview | A separate preview for each extracted schedule. Each preview uses a compact Visit / Timing / Window table, visit count, and collapsed extraction checks. Multiple previews represent extracted schedules, not automatically conditional alternatives. | `frontend/app/(app)/sponsor/add-trial.tsx`, `ExtractionPreview.tsx` |
| Protocol upload review | Uses the same extraction preview component. | `ProtocolUploadReview.tsx` |
| Canonical protocol review | Compact Visit / Timing / Window tables, version/status, expandable schedule checks, existing review/approval controls and source evidence. | `ProtocolScheduleScreen.tsx` |
| Compact table grouping | Up to three nonempty sections: Scheduled visits; Repeating visits; Visits when a condition is met. Every conditional visit is moved to the last section regardless of whether it supplements or replaces the normal pathway. | `SimpleScheduleTable.tsx` |
| Compact expanded row | Visit type, attendance mode, applicability, status, recurrence, Clinical and Administrative activities, activity rules, qualifiers, review reasons, and source evidence. A warning icon currently means “Check details,” not “conditional visit.” | `SimpleScheduleTable.tsx` |
| Patient schedule, narrow screen | `ScheduleTable` renders mobile cards below 760px, rather than the compact three-column protocol table. Patient fields and activity groups are retained. | `PatientScheduleScreen.tsx`, `ScheduleTable.tsx` |
| Patient schedule, wide screen | Horizontal table: Visit, Visit Name, Expected Date, Allowed Window, Type, Activities, Actual Date, Status, Notes. | `ScheduleTable.tsx` |
| Patient alternate view | A Table/Timeline toggle switches to a chronological patient timeline. A repeat-rule footer explains continuing schedules. | `PatientScheduleScreen.tsx` |
| Patient supporting information | Reference-date table, confinement/stay table, conditional requirements with confirmation actions, proposed schedule-impact table, and deviations. These are distinct from protocol schedule alternatives. | `PatientScheduleScreen.tsx`, `ScheduleTable.tsx` |
| Shared table's non-patient mode | Code supports a wider protocol table with Timing, Window, and Applies To instead of patient dates. Current compact preview/review callers use `SimpleScheduleTable`; do not treat the wider mode as their current rendering. | `ScheduleTable.tsx` |
| Clinical review route | Currently re-exports `ScheduleReviewInboxScreen`, which directs reviewers to the canonical protocol schedule. It is not the old separate legacy schedule table described in historical status notes. | `frontend/app/(app)/clinical/schedule-review.tsx` |

UCTSM filenames above are under `frontend/src/features/uctsm/` unless a full path is given. This inventory covers schedule-related surfaces inspected for this request, not unrelated app tables.

### Current data gaps

1. `presentation.ts:toProtocolScheduleRows` determines `isConditional` from `event.conditions` or an optional caller-provided list of conditional event codes. The preview and protocol screen currently do not pass that list. Targets referenced only by schedule-level conditional actions can therefore be missed by this grouping.
2. The row model has one boolean `isConditional`; it does not encode additive versus replacement behavior, pathway identity, or replacement scope.
3. Backend `UniversalSchedule` has `conditional_definitions` and `repeat_blocks`. The frontend `UniversalSchedule` type currently omits both. Its separate `ConditionalDefinition` type represents a **patient condition response**, not the canonical protocol definition. Do not reuse that type for a different payload.
4. `ScheduleEvent.activation` exists, but the compact protocol row model does not preserve an explicit on-demand classification. There is no dedicated final unplanned table.
5. Backend actions include ADD_EVENT, REPEAT_EVENT, CANCEL_EVENT, STOP_BLOCK, PAUSE_BLOCK, RESUME_BLOCK, EXTEND_CONFINEMENT, and MANUAL_REVIEW. These express effects but do not themselves guarantee that an entire schedule is replaced.

## 3. Required visual behavior

### Case A: a condition replaces a schedule or an explicitly identified part of it

Show two expanded tables by default:

```
Normal schedule
Follow while [replacement condition] has not occurred.
Visit                  Timing                         Window
Baseline               [existing timing]              [existing window]
Routine follow-up      [existing timing]              [existing window]

If [protocol condition] occurs
Replaces [explicit affected phase/visits] from [protocol transition point].
Visit                  Timing                         Window
Triggered visit        [relative to condition date]   [existing window]
Treatment follow-up    [protocol anchor/rule]          [existing window]
```

Use actual protocol labels, not the bracketed placeholders. These are two views of one schedule definition, not two newly created independent schedules. Keep one approval/version context around them.

For partial replacement, say “Replaces maintenance visits” or the exact supported scope; never claim the entire protocol stops. Common continuing visits may appear in both tables only when their continuing applicability is explicitly supported. Mark them “Continues from normal schedule.” Reusing a display row must not create another canonical event or change visit counts.

If the event occurs midway through participation, do not imply that earlier completed visits disappear. The subtitle describes the change from the transition point onward. Timing remains anchored to the extracted rule; displaying an alternate table must never reset all dates to baseline or to the condition date.

Multiple explicit replacement branches may have separate named tables. Preserve AND/OR conditions and applicability. Do not generate every theoretical combination of independent conditions. If interacting branches cannot be represented accurately, expose their unresolved interaction instead of selecting an arbitrary winner.

### Case B: a condition only adds visits

Keep these visits in the normal table with a small condition symbol directly beside the visit name:

```
Visit                  Timing                         Window
Baseline               [existing timing]              [existing window]
Extra assessment ◇     [relative to its trigger]       [existing window]
Routine follow-up      [existing timing]              [existing window]

◇ Only if the stated condition is met. Tap the visit for details.
```

Use a small existing vector icon available in the project; the diamond above is illustrative. Keep it visually distinct from a warning triangle and a footnote marker. Include the accessible label “Conditional visit: [condition label]” on the row. The whole row remains the touch target; the tiny icon does not need a separate button.

Show the actual condition near the top of the expanded details. A short “If [condition]” subtitle is useful where it fits; do not replace every rule with the vague “If required by protocol.” Preserve the exact longer condition and evidence in details.

Place additive visits using existing explicit protocol/phase ordering. A visit anchored to an unknown future event has no global chronological position: retain its source order or place it at the end of its relevant phase, with trigger-relative timing visible. Do not sort it using a fabricated day number.

A conditional activity inside an otherwise mandatory visit is different: keep the visit normal and label the activity as conditional in its expanded Clinical/Administrative group. Do not mark the whole visit optional.

### Case C: unplanned/unscheduled visits

After all main and alternate pathway tables, show one small **Unplanned visits** table, only when there are matching definitions. Use Visit / When needed / Window columns and the same expandable activity/source details.

Classify using structured `activation === ON_DEMAND` or explicit `event_type === UNSCHEDULED`, with contradictory metadata surfaced for review. Do not infer this category from a missing date, a word in the visit name, or the presence of a condition. A visit required 7 days after a condition is triggered is not automatically unplanned.

The “When needed” cell describes the extracted trigger/request rule. Use “Not stated” if absent; never invent “any time” or a zero-day window. Add a brief line: “Created when needed; these visits have no routine due date.”

An unplanned visit specific to an alternate pathway appears once in this final table, with an applicability/condition label linking it to that pathway. It must not also appear among ordinary dated definitions.

### Consistency rules

- Keep three columns on phones and current cream/burgundy styling. Preserve expandable Clinical/Administrative activities, qualifiers, source pages, attendance mode, and review explanations.
- Integrate ordinary recurring rules into their appropriate normal/alternate table, showing cadence in Timing. This intentionally replaces the current separate Repeating visits section; do not flatten open-ended recurrence into an invented finite list.
- Keep review warnings separate from condition symbols. A conditional visit is not automatically an extraction error.
- Hide empty auxiliary sections. If there is no normal pathway, say so rather than inventing normal visits.
- Show unique visit-definition counts; repeated display of a shared event across pathways must not inflate the count.
- Keep independent extracted schedules as separate outer previews, each with its own tables. Do not merge cohort schedules merely because their names look similar.

## 4. Recommended data contract

Add a pure `buildProtocolScheduleSections(schedule, projections)` presenter in a new `scheduleSections.ts`, reusing existing `toProtocolScheduleRows` formatting. Pass its result to `SimpleScheduleTable`; the renderer should no longer classify rows using three boolean filters.

Proposed view types, not existing API fields:

```typescript
type SectionKind = 'NORMAL' | 'ALTERNATE' | 'UNPLANNED' | 'UNRESOLVED';
type ProtocolScheduleSection = {
  id: string;
  kind: SectionKind;
  title: string;
  subtitle?: string;
  rows: ScheduleDisplayRow[];
};
type ScheduleDisplayRow = {
  key: string; // section ID + canonical event ID; stable across rerenders
  row: ProtocolScheduleRow; // existing formatting and evidence
  conditionLabel?: string;
  conditionEvidenceRefs: string[];
  conditionalAddition: boolean;
  continuesFromNormal: boolean;
};
```

Add correctly named canonical protocol condition/action/block types to `types.ts`. Include `conditional_definitions`, `repeat_blocks`, and `schedule_metadata.extensions`; tolerate absent arrays in older responses. Preserve compound predicates as structured data. Use `condition_display` where supported, with an honest fallback and linked source rather than guessing a translation.

For explicit pathway relationships, use the **existing** backend `schedule_metadata.extensions` list with a validated, versioned entry. This avoids adding a second schedule entity just for display. Proposed shape:

```json
{
  "kind": "conditional_pathways.v1",
  "pathways": [{
    "id": "stable-pathway-code",
    "label": "Protocol-stated pathway label",
    "condition_code": "EXISTING_CONDITION_CODE",
    "effect": "REPLACEMENT",
    "replacement_scope": "PARTIAL",
    "event_codes": ["ALTERNATE_EVENT"],
    "replaces_event_codes": ["AFFECTED_NORMAL_EVENT"],
    "replaces_block_codes": [],
    "continues_event_codes": [],
    "transition_text": "Exact supported transition description",
    "evidence_refs": ["EXISTING_EVIDENCE_UUID"]
  }]
}
```

Validate this entry with a dedicated backend model, not an unchecked arbitrary JSON cast. Define `effect` as ADDITIVE / REPLACEMENT / UNRESOLVED, and replacement scope as FULL / PARTIAL / UNRESOLVED. Reference existing condition expressions by code; compound expressions stay in the canonical condition definition. Branch applicability is inherited from canonical condition/event applicability, never replaced by a label.

The extension describes layout and explicit pathway relationships. **Existing canonical conditions/actions remain authoritative for execution.** A replacement label cannot cancel visits. Validation must reject a claimed replacement that conflicts with its canonical action targets or applicability. If the protocol genuinely calls for replacement but extracted execution actions are incomplete, raise a blocking semantic issue through the existing validation mechanism. Do not silently turn a UI metadata edit into an operational change.

Check all referenced codes and evidence; expand replaced blocks using existing membership; reject duplicate pathway IDs and incompatible memberships. Multiple independent conditions referencing a shared event must preserve their predicates rather than being flattened into unconditional membership. An unknown extension version must result in an explicit unsupported-layout notice with all visits still visible.

Existing snapshots without this extension remain readable. Direct event predicates support conditional labels, but do not prove an additive effect when interacting stop/cancel rules exist. An ADD_EVENT action alone also does not prove an entire condition is additive: inspect all its actions and interactions.

## 5. Classification algorithm and fallback

1. Build canonical maps by event, condition, block, and evidence code/ID. Index event conditions plus schedule-level and event-level conditional actions. Verify that action targets resolve to the correct entity type.
2. Identify unplanned definitions first; reserve them for the final table. Retain condition/pathway membership on their display rows.
3. Use valid explicit pathway metadata to form normal and replacement sections. Preserve shared events only when explicitly listed/supported. Do not infer “whole replacement” from visit count, a heart-attack keyword, a STOP_BLOCK alone, or a different treatment name.
4. For clearly additive conditions with no conflicting replacement actions, leave affected visits in their appropriate primary/pathway table with a condition marker. A REPEAT_EVENT affecting an existing mandatory visit must not relabel every occurrence of that visit optional; describe the conditional repeat separately in its details.
5. If conditionality is known but its effect or membership is unresolved, keep the event visible in a compact **Conditional visits — placement needs review** section. Explain the missing distinction and link evidence. Never silently place a possibly replacement-only visit in the normal pathway. Classification uncertainty is different from uncertain date calculation.
6. Put nonconditional and ordinary recurring definitions in their applicable normal section. Preserve source/phase order and existing timing/window formatting.
7. Append the unplanned table. Check coverage: every canonical event appears somewhere; additional appearances are allowed only for explicitly shared pathway rows. Keep summary counts based on unique canonical IDs.

Do not suppress a genuine ambiguity just to make the screenshot look clean. Purely cosmetic uncertainty must not introduce new clinical approval gates; missing executable semantics should continue to block approval through backend validation.

## 6. Where the metadata comes from

First test the presenter against small authored canonical fixtures. Then inspect one already saved AIM extraction/source fixture, if available, to establish whether its existing conditions/actions actually encode the requested switch. No broad database scan and no paid extraction run are necessary to build this UI.

If explicit relationship metadata is missing, extend the current active extraction assembly/semantic output only enough to capture condition-to-pathway relationships and evidence. Likely touchpoints are `backend/app/extraction/source_inventory.py`, `source_first.py`, and `prompts.py`; identify the active path before editing. Do not add a separate LLM call solely to choose a table style.

Require protocol evidence for replacement scope and transition timing. Preserve ambiguity as UNRESOLVED. Verify that assembly, validation, native import, persistence, and reload retain the extension; the existence of an extensions field does not prove every intermediate layer round-trips it. If review correction is needed, use the existing versioned review/edit mechanism with an audit reason; never mutate an approved snapshot in place.

This is a bounded improvement in semantic coverage, not proof that extraction works for every protocol. UI fixtures prove presentation, and a real reviewed protocol proves only that specific case.

## 7. File-by-file implementation order

| Step | Files | Deliverable |
| --- | --- | --- |
| 1 | `types.ts`; backend `app/domain/schedule/models.py` and `validator.py` where needed | Canonical condition/block typings; validated optional pathway extension; backwards compatibility and conflict checks. |
| 2 | New `scheduleSections.ts`; `presentation.ts` | Pure section builder, condition/action indexing, raw activation/type preservation, stable keys, meaningful condition labels. Update the old comment saying all conditional protocol rows belong in a separate table. |
| 3 | `SimpleScheduleTable.tsx` | Render supplied sections; conditional glyph/legend; replacement subtitle; final unplanned table; preserve expanded detail and evidence behavior. |
| 4 | `ExtractionPreview.tsx`; `ProtocolScheduleScreen.tsx` | Both use the identical section builder. Count unique definitions. Evidence lookup includes the condition/pathway evidence for the tapped row. Existing review and approval controls remain functional. |
| 5 | Active extraction and import code only if needed | Populate/retain explicit, evidenced pathway relationships without extra provider calls; unresolved cases remain visible. Test fresh preview and saved/reloaded schedule parity. |
| 6 | Frontend tests; focused backend tests | Behavioral coverage listed below, plus narrow-screen manual verification. |

Do not modify legacy operational routes as a shortcut. Read `AGENTS.md` and handoff sections C3/D-4 before any route migration. The current workspace differs from the historical documentation, so inspect actual files and preserve prior edits/deletions. Do not flip `UCTSM_AUTHORITATIVE` or a trial's schedule read mode.

## 8. Patient schedules and condition confirmation

The user's screenshots are definition/review screens. Complete that scope first. Do not put inactive hypothetical visits into `PatientScheduleScreen` as though they already have due dates.

The existing patient flow is condition recorded/proposed, impact preview, then confirmation. It owns activation and operational dates. Preserve that workflow and completed historical visits. Table expansion or selecting an alternate display must never call the condition-confirmation endpoint.

In any later patient adaptation, separate unplanned **templates available to create** from actual recorded unplanned **occurrences**. Actual occurrences must remain visible in the patient history/timeline with their date, reason, and status. Hiding every UNSCHEDULED instance from history would be incorrect. Keep inactive alternatives as labeled reference information, separate from the active patient schedule.

## 9. Acceptance tests

Use small synthetic canonical fixtures for deterministic rendering tests. Add a focused section-builder test file to the existing test scripts. Do not substitute source-string assertions for behavior tests.

| Fixture | Required result |
| --- | --- |
| Ordinary schedule | One normal table, no condition legend, no empty extra sections. |
| Normal plus repeating visits | One normal table; recurrence remains a rule with correct existing timing text. |
| One additive visit | Same table, only affected visit has condition marker, normal visits remain mandatory. |
| Conditional activity only | No visit-level optional marker; activity condition appears in details. |
| Condition referenced only by ADD_EVENT action | Visit correctly identified despite empty `event.conditions`. |
| Full replacement | Normal and alternate tables, explicit transition subtitle, no implied simultaneous routine execution. |
| Partial replacement | Only specified phase/events marked replaced; continuing follow-up preserved. |
| Shared continuing event | Appears in supported pathways, unique definition count unchanged, stable distinct display keys. |
| Two branches and compound predicate | Correct labels/applicability; AND/OR meaning preserved; no invented combination table. |
| Multiple independent extracted schedules | Separate outer previews remain separate; each has its own condition sections. |
| On-demand / UNSCHEDULED definition | Only final unplanned table, no routine due date. |
| Condition-specific unplanned definition | Final table once, with its pathway/condition label. |
| Missing anchor | Honest awaiting-anchor/timing message; not misclassified as unplanned. |
| Ambiguous replacement or unknown extension version | All events visible with explicit unresolved placement; no guessed switch. |
| Stop/resume/repeat without replacement | No fabricated alternate schedule; effect remains described on its actual target. |
| Old saved schedule without new metadata | Still readable; no runtime error or unconditional relabeling of known conditional visits. |
| Invalid code/evidence or contradictory action mapping | Validator issue and appropriate existing review block; never silent data loss. |
| Save/reload | Same sections, condition labels, evidence and unique counts before and after persistence. |
| UI details/accessibility | Long names wrap; large font and small phone width work; condition and review markers differ; row expansion/evidence work. |
| Patient nonregression | Viewing definitions performs no mutation; existing impact confirmation/date/history behavior passes unchanged. |

Frontend verification from `frontend/`: `npm run test:uctsm` and `npx tsc --noEmit -p tsconfig.uctsm-workflow.json`. Inspect the test scripts and scoped config before use, and include the new test file. Backend contract changes require focused validator/import/condition tests and the relevant curated gate from the handoff; do not run the entire backend suite indiscriminately. No new paid LLM evaluations or broad database queries for this presentation task.

## 10. Completion criteria and instruction for Sol

The task is done when preview and persisted protocol review show the same correct three cases; no visit or activity is lost; ambiguous rules remain explained; approval, source links, dates, condition confirmation and histories retain their existing behavior; and the listed relevant tests pass. Report implementation completion separately from extraction accuracy. Do not claim universal protocol support based on these fixtures.

Suggested handoff prompt:

> Implement `docs/audits/conditional-schedule-ui-handoff-2026-09-14.md`. Start with the shared section presenter and fixtures, then integrate both compact protocol surfaces. Keep full replacements in alternate tables, additive conditional visits inline with a small accessible marker, and unplanned definitions in the final table. Use explicit canonical rules and validated evidenced metadata; do not infer a replacement from labels or visit counts. Preserve patient execution and existing dirty-worktree changes. Use targeted reads and existing local fixtures, no broad database scan or paid LLM run. Complete the focused tests and report any unresolved semantic contract gaps honestly.
