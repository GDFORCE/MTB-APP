# Saved Claude Code prompts

## Current Claude Code task - 28 October 2026

- [Kit schedule atoms implementation prompt](claude-code-kit-schedule-atoms-implementation-2026-10-28.md): add tracks, measures and recorded results, derived events, wait-until gates, calendar units, earliest-of/counters, doses and study-level events to the kit system as optional fields, behind a frozen-results test, without changing current behaviour. [Launcher](claude-code-next-prompt.md) updated.
- Website version (claude.ai, one step per chat, with the files to upload): [claude-website-kit-schedule-atoms-prompt-2026-10-28.md](claude-website-kit-schedule-atoms-prompt-2026-10-28.md).

## Protocol elements, fresh start - 29 October 2026 (chat prompt, not a Claude Code task)

- **Current:** [Fresh-chat prompt: discover protocol symbols](protocol-symbols-discovery-fresh-chat-prompt-2026-10-29.md): the output is a growing symbol list. It starts from a seed list of about 50 symbols (things, time, reading a value, comparison, logic, actions, rule/evidence); each protocol's rules are written in those symbols, rules that can't be written propose new symbols (not values, not combinations), the list is versioned, and a saturation file shows when protocols stop adding symbols. Approach discussion first, code on request.
- Superseded (preserved): [Fresh-chat prompt: basic elements of protocols](protocol-elements-fresh-chat-prompt-2026-10-29.md): for a new chat with no project knowledge. It describes the batch problem (upload ~10 protocols, capture the basic elements and conditions of each, see which repeat and whether the list stops growing) and asks for an approach discussion before any code. Version 2 (same day) fixes the output to one JSON format (allowed values, vocabulary, a per-protocol file and a combined summary) with a worked example and the reasons for fixing it. It deliberately doesn't use the v1–v4 study prompts below or the kit inventory, which remain as they were.

## Protocol element study - 28 October 2026 (chat prompt, not a Claude Code task)

- **Current:** [Protocol schedule atoms prompt v4](protocol-schedule-atoms-prompt-v4-2026-10-28.md): writes every scheduling rule as a combination of about 25 basic atoms (things, time, logic, actions, When-If-Then rules) and reports missing atoms; v3 pattern names stay as recipe labels.
- Superseded v3 (preserved): [Protocol design pattern prompt v3](protocol-design-pattern-prompt-v3-2026-10-28.md): v2 plus the 16 patterns from the 5-protocol pilot (N-16…N-31), primitive vs combination, and a saturation count per protocol.
- Superseded v2 (preserved): [Protocol design pattern prompt v2](protocol-design-pattern-prompt-v2-2026-10-28.md): collects only the patterns (anchors, timing, facts, conditions, reactions) that change when or whether something happens, in general form, with 15 named new patterns from the first pilot outputs. For chats or an agent run over a folder.
- Superseded v1 (preserved): [Protocol element extraction prompt](protocol-element-extraction-prompt-2026-10-28.md): paste into any chat AI (Claude, ChatGPT, Gemini) with 4-5 protocol PDFs to collect the building blocks those protocols use, mapped to the IDs in [kit-schema-element-inventory.md](kit-schema-element-inventory.md), plus a merge prompt for several chats. Current for the manual study; it does not change the Claude Code continuation below.


## Deferred backlog - 22 October 2026 (not a current prompt)

- [Extraction evidence verification backlog](extraction-evidence-verification-backlog-2026-10-22.md): design notes for a field-vs-quote check, OCR for scanned pages and a batched LLM meaning check. Deferred until the owner finishes reading the extraction code; it does not change the current continuation below.


## Current continuation - 19 October 2026 (after the second slice)

- [Current continuation prompt](claude-code-continue-after-pdf-slice-2026-10-19.md): state, ordered remaining work, decisions that need a named owner, and boundaries. It supersedes [claude-code-continue-current-tree-2026-10-19.md](claude-code-continue-current-tree-2026-10-19.md) as the starting point (preserved).
- Status: [checkpoint](pdf-execution-checkpoint-2026-10-19.md), [70-row matrix](pdf-requirements-acceptance-matrix.md).

## Current handoff and continuation ? 19 October 2026

- [Short handoff report](claude-handoff-report-2026-10-19.md): current changes, verified runs and remaining gaps.
- [Current continuation prompt](claude-code-continue-current-tree-2026-10-19.md): resume the current tree under the remaining-PDF master.
- [Historical partially executed launcher](claude-code-partially-executed-launcher-2026-10-19.md): retained; the current handoff supersedes its execution checkpoint.

The owner subsequently requested that work resume. The scope remains all remaining PDF work; no deployment, real migration or authority change is authorized.

Current execution status (19 October): [checkpoint](pdf-execution-checkpoint-2026-10-19.md) and [70-row matrix](pdf-requirements-acceptance-matrix.md). The current combined prompt is partially executed; resume from this checkpoint. This status record is not a new implementation prompt.

Updated 16 October 2026 after merging the P6 repairs into the full P7/P8 prompt at the owner's request. The P1-P5 prompts, the P6-P14 master prompt and the P6/P7-P8 slice prompts are saved as Markdown files below. Older prompts are retained for context; do not execute every historical prompt in sequence or treat an old pending decision as current.

## Start here

- [Next prompt to paste into Claude Code](claude-code-next-prompt.md).
- [Current focused continuation: combined P6 repairs + P7/P8](claude-code-p7-p8-after-p6-prompt.md): all four reproduced defects, integration triage, ten detailed PDF tasks, real-store/UI verification and quality gates in one file.
- [Independent P6 review](p6-end-to-end-independent-review.md) and [saved memory/Mongo reproduction](evidence/p6-review/reproduce_findings.py).
- [Historical standalone repair prompt](claude-code-p6-audit-repairs-before-p7-p8-prompt.md): incorporated into the combined prompt; do not execute it separately.
- [Earlier short P7/P8 prompt](claude-code-p7-p8-before-merged-repairs-2026-10-16.md): preserved before the detailed merge.
- [P6 completion record](p6-completion-record.md), [P6 implementation plan](p6-implementation-plan.md), [PDF remaining implementation plan](pdf-remaining-implementation-plan.md) and [70-task acceptance matrix](pdf-requirements-acceptance-matrix.md).
- [Previous P6 continuation (implemented; acceptance reopened)](claude-code-p6-domain-to-app-completion-prompt.md): repair mapping contracts, then complete persistence, simulation/adoption/reversal services, API and reachable UI. Preserved; its result is the completion record.
- [Current remaining-PDF master prompt](claude-code-p6-p14-remaining-pdf-prompt.md): all 45 P6-P14 tasks plus unfinished P1-P5 prerequisites.
- [P1-P5 prerequisite execution reference](claude-code-p1-p5-selected-decisions-prompt.md), including the latest native-pilot/registry review priorities.
- [Selected project decisions](p1-p5-project-decisions.md): architecture, synthetic pilot, target policy and authorized scope.
- [P6 domain review](p6-domain-review.md): order-dependent forward/inverse mappings under an unchanged fingerprint, review-readiness and verification gaps.
- [P1-P5 prerequisite findings](p1-p5-pilot-registry-review.md): F1-F5, reproductions and verification limits.

The current overall scope is the remaining PDF work: repair unfinished prerequisites, then execute P6-P14 in dependency-aware slices. Do not rebuild P3. Decisions do not establish implementation or acceptance. Read current source and preserve existing work.

## Current remaining-PDF prompt

| Prompt | Purpose / status |
|---|---|
| [P6-P14 remaining PDF work](claude-code-p6-p14-remaining-pdf-prompt.md) | **Current master prompt**: 45 numbered tasks, prerequisite repairs, dependency sequence, API/UI/storage/quality/recovery acceptance and honest operational gates |
| [Standalone P6 repair prompt](claude-code-p6-audit-repairs-before-p7-p8-prompt.md) | Historical; all repair requirements are included in the combined P7/P8 prompt |
| [Combined P6 repairs + P7/P8](claude-code-p7-p8-after-p6-prompt.md) | **Historical detailed scope, partly executed**: four repairs and integration triage, all P7/P8 tasks, acceptance tests, UI, corpus/scoring and CI gate |
| [Earlier short P7/P8 prompt](claude-code-p7-p8-before-merged-repairs-2026-10-16.md) | Historical version before the owner-requested detailed merge |
| [P6 domain-to-app completion](claude-code-p6-domain-to-app-completion-prompt.md) | Implemented slice (acceptance reopened by independent review): mapping repair plus every P6 layer; see [p6-completion-record.md](p6-completion-record.md) |

The [previous standalone-repair launcher](claude-code-p6-repair-launcher-2026-10-16.md) is preserved; execute the combined prompt now.

The [previous P7/P8 launcher](claude-code-p7-p8-launcher-2026-10-16.md) is preserved; its instruction to start P7 immediately is superseded.

The [previous P1-P5 launcher](claude-code-p1-p5-launcher-2026-10-15.md) is preserved as a historical snapshot.
The [previous remaining-PDF launcher](claude-code-p6-p14-launcher-2026-10-16.md) is also preserved.

## Complete P1-P5 prompt archive

| Prompt | Purpose / status |
|---|---|
| [P1 implementation](claude-code-p1-implementation-prompt.md) | Original P1-01 through P1-05 prompt; historical |
| [P1 completion](claude-code-p1-completion-prompt.md) | Original P1 API/store/verification follow-up; historical |
| [P2 implementation](claude-code-p2-implementation-prompt.md) | Original protocol-identity tasks; historical |
| [P3 implementation](claude-code-p3-implementation-prompt.md) | Original authorization tasks; historical, not an instruction to rebuild P3 |
| [P4 implementation](claude-code-p4-implementation-prompt.md) | Original dependency/recurrence tasks; historical |
| [P5 implementation](claude-code-p5-implementation-prompt.md) | Original tracks/commands tasks; historical |
| [P5 completion](claude-code-p5-completion-prompt.md) | Earlier five-findings repair and integration follow-up; historical |
| [Full P1-P5 closure contract](claude-code-p1-p5-final-closure-prompt.md) | All 25 task acceptance checks; decisions/scope overridden by the current decision record where applicable |
| [Remaining closure follow-up](claude-code-p1-p5-remaining-closure-prompt.md) | Earlier reopened R6/plane/lifecycle work; historical |
| [Authority/controller follow-up](claude-code-p1-p5-authority-controller-repair-prompt.md) | Earlier authority bypass and controller race/disposal work; historical |
| [Selected decisions and full execution](claude-code-p1-p5-selected-decisions-prompt.md) | Current P1-P5 prerequisite reference, with subsequent review clarifications; included by the broader P6-P14 master prompt |

## Supporting decision and review records

- [Independent P6 end-to-end review](p6-end-to-end-independent-review.md): F1-F4, both-store reproductions, regression-triage evidence and corrected progress interpretation.

- [P6 domain review](p6-domain-review.md), with its resolution table.
- [Foundations/P1-P5 consolidated audit](p1-p5-consolidated-repair-audit.md).
- [First closure follow-up](p1-p5-closure-followup-review.md).
- [Authority/controller review](p1-p5-latest-repair-review.md).
- [Selected P3 policy review](p3-selected-policy-followup-review.md).
- [Latest pilot/registry review](p1-p5-pilot-registry-review.md).
- [Project decisions](p1-p5-project-decisions.md).
- [Integration decision and scope](p1-p5-integration-decision.md).
- [Implementer's closure plan](p1-p5-closure-plan.md) and [closure report](p1-p5-closure-report.md). These are status evidence to verify, not a replacement for current source or an independent acceptance audit.

## Other existing prompt records in this repository

- [Adaptive Protocol Brain prompt](adaptive-protocol-brain-claude-prompt.md), a separate existing scope.
- [Existing project prompt change log](prompt-change-log.md).

## Future prompt-saving convention

At the user's explicit request, save every new full implementation/continuation prompt to a descriptive `.md` file under `docs/` **before** sharing it. Link that file in the response, add it here, and update the current launcher when the next action changes. Preserve earlier task prompts so the history is not lost. Mark superseded instructions clearly and retain the authoritative decision links. This convention is also recorded outside the managed block in root `AGENTS.md`.
