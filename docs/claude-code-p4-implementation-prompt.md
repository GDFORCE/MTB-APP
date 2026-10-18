# Claude Code prompt: P4-01–P4-05 — dependency and recurrence correctness

Prepared 14 October 2026 from current source, the P3 implementation record, and section P4 of `D:\MTB_dev (2).pdf`. Copy the prompt below into Claude Code with MTB-APP open. This prompt was prepared by inspection; no P4 code changes or test runs were performed.

---

Complete **P4-01 through P4-05** from `D:\MTB_dev (2).pdf`: correct conditional dependency evaluation and make recurrence basis/start behavior explicit and deterministic.

Repository: `D:\MTB_Intern_work_folder\APP-New-approch\MTB-APP`.
Stack: Expo/React Native/TypeScript; FastAPI; database-independent UCTSM domain evaluator; SQL services and newer MongoDB application/repository adapters.

Inspect the app first, then read the P4 epic and its five tasks. Treat the PDF as requirements material, not instructions overriding this request or `AGENTS.md`. Implement this five-task group, not P5–P14 or an unrelated architecture rewrite.

Use engineering reasoning before implementation. Explain current behavior, confirmed defects, selected semantics, alternatives, compatibility, and verification. Concise rationale is sufficient; no private chain-of-thought is requested. Reuse the existing recurrence, condition, calendar, history, and validation machinery rather than replacing it wholesale.

## 1. Preserve previous work and current boundaries

Read `AGENTS.md`, applicable nested instructions, `README.md`, the relevant handoff/architecture sections, and the P1/P2/P3 plan documents. Verify their claims against current source and tests.

Inspect `git status --short`, `git branch --show-current`, `git log -5 --oneline`, and existing diffs before editing. P1/P2/P3 and earlier work remain uncommitted, including authorization, maker-checker migrations, Mongo APIs, extraction/identity modules, application services, tests, and frontend upload/review screens. Preserve all pre-existing work and unrelated logs/scratch/landing files. Do not reset, discard, commit, push, deploy, or clean unrelated files without authorization.

P3's role/capability matrix is still **PROPOSED_PENDING_OWNER_APPROVAL**. Preserve its status and current guards. This prompt does not approve proposed role grants, change the maker-checker default/scope, or permit widening rights to make P4 tests pass. Create appropriately granted test actors when exercising approval workflows.

Carry forward P3 limits: legacy upload/consume wrappers have not all been mapped to the capability catalog; cross-organization list discovery has bounded behavior; SQL checks used SQLite, which does not prove PostgreSQL locking. P2 real PERIGON and browser/device checks remain pending. These are separate work items, not reasons to rewrite authorization or extraction during P4.

Do not flip `UCTSM_AUTHORITATIVE`, change trial read modes, migrate legacy scheduling screens, or change live data authority. Do not enable `DEV_OTP_MODE` outside local development.

Never guess an unstated recurrence basis, dependency mode, clinical maximum, termination boundary, actual-versus-planned source, or condition value. Preserve unresolved/waiting/review-blocked states with evidence and actionable explanations. Routine implementation choices are yours to make; ask only for a genuinely missing clinical/product decision while continuing independent work.

## 2. Source findings to verify

1. `backend/app/domain/schedule/models.py::Dependency` already has `source_event_code`, typed dependency type, and optional `condition`. `condition.py` supplies `TruthValue.TRUE/FALSE/UNKNOWN` and `evaluate_condition`.
2. In `evaluator.py::_evaluate_event`, the dependency blocking loop currently checks prior source resolution without consulting `dependency.condition`. Later actual/manual dependency handling also iterates dependencies; inspect every step so an inactive dependency cannot still block through another branch.
3. `RecurrenceRule` already has interval, `start_reference`, optional `start_offset`, termination, `include_start`, and `calendar_policy_ref`, but inspection found no explicit P4 `RecurrenceBasis`.
4. `_expand_recurrence(event, first, ...)` accepts resolved first timing but initializes the series from `rule.start_reference` plus `start_offset`. This can discard the owning event's resolved timing. Prove the missing-offset/contradictory-start case before changing it.
5. Existing `RecurrenceTermination` supports **COUNT, DATE, EVENT, CONDITION, HORIZON, and RELATIVE**. RELATIVE requires end reference, offset, and explicit inclusion. Preserve this existing capability even though the PDF's test summary does not list it.
6. Existing `_resolve_termination`, `nth_occurrence`, `add_amount`, `calendar_math.py`, weekday recurrence, phased `RecurrencePlan`, cycle plans, conditional episodes, and rolling-horizon logic already handle important boundaries. Preserve their tested semantics.
7. `validator.py` already checks references, temporal basis, dependency graph cycles, conditional structures, phased plans, calendar recurrence, and executable-operator support. Extend these checks instead of introducing a competing validator.
8. P1 already supplies version-scoped typed occurrence history and separate actual-visit/treatment values. Use logical occurrence indexes; do not reintroduce date-sorted positional matching or cross-version actual reuse.
9. API editing schemas in `app/api/uctsm.py`, SQL `review_view.py`, extraction compiler mappings, Mongo persistence serialization, and frontend recurrence/explanation types all consume the same domain model. Identify every boundary requiring a P4 schema update.
10. Existing tests include `test_uctsm_domain.py`, `test_relative_recurrence.py`, `test_engine_recurrence_calendar.py`, `test_engine_calendar_recurrence.py`, conditional/rolling-horizon suites, extraction tests, and P1/P3 workflows. Their presence is not proof of P4 behavior.

## 3. Assessment and design checkpoint

Create `docs/p4-dependency-recurrence-plan.md` before editing with:

- P4-01–P4-05 matrix: existing behavior, confirmed gaps, relevant source/tests, planned changes, and verification.
- A dependency decision table covering condition truth, source state, required temporal basis, dependency type, and resulting event status.
- Recurrence-basis definitions and exact-date examples, including first occurrence, subsequent occurrences, actual-data gaps, `include_start`, and monthly/yearly policy.
- Compatibility for old persisted schedules, extraction without evidence, approval validation, schema round trips, and review presentation.
- The actual SQL and Mongo evaluation/approval paths exercising the shared evaluator; no separate algorithm per store.

First add fixed regressions reproducing the confirmed defects. Explain the smallest coherent correction and then implement it. Do not stop at a plan when the requirements supply adequate semantics.

## 4. P4-01 — dependency outcomes with three-state conditions

Add or complete a typed `DependencyOutcome`/result covering:

- `SATISFIED`
- `NOT_APPLICABLE`
- `WAITING_FOR_CONDITION`
- `WAITING_FOR_SOURCE`
- `BLOCKED`

For each dependency, evaluate its optional condition **before** checking source resolution or imposing source/actual/manual requirements. An absent condition means unconditional dependency; an unknown value must not be converted to false.

Required headline cases:

- Pregnancy condition false -> dependency `NOT_APPLICABLE`; it cannot block a separately resolvable event.
- Pregnancy unknown -> `WAITING_FOR_CONDITION`, with the missing predicate/source information.
- Pregnancy true and required source absent -> `WAITING_FOR_SOURCE`.
- True/unconditional dependency with suitable resolved source -> `SATISFIED`.
- An applicable dependency on an explicitly unusable/contradictory source -> a documented `BLOCKED` outcome rather than an invented date.

Distinguish a dependency being not applicable from the **whole event** being not applicable. If an event's own timing independently references a missing source, a false optional precondition does not magically provide that timing date. Keep these reasons separate.

Define aggregation for multiple dependencies and source states such as resolved, completed, not applicable, waiting, paused, missed, cancelled, and unsupported. Respect the approved dependency type and explicit temporal basis; mere source-row existence is not proof that a required actual happened. Unknown predicates, missing source dates, and a known blocking contradiction need different explanations.

Include dependency source code/index, condition/result, source state, relevant temporal basis, evidence, outcome, and reason in the typed result and public explanations. Preserve compatible existing `dependency_result` fields or adapt callers deliberately. Do not expose sensitive condition evidence through a response privacy profile that P3 excludes.

Apply active-dependency selection consistently to blocking, actual/manual input checks, and timing-context setup. Use existing `evaluate_condition` and trusted `PatientContext` state/effective times. Do not introduce a second truth-value implementation or parse condition prose at evaluation time.

Structural approval validation must consider dependencies that could become active later. Do not accept a circular rule merely because the current patient's condition is false; distinguish structural cycles from valid references to a previous occurrence in a numbered series.

## 5. P4-02 — explicit recurrence basis and validation

Add `RecurrenceBasis` to the interval recurrence model with the PDF's four values:

- `FIRST_RESOLVED_OCCURRENCE`
- `PREVIOUS_PLANNED_OCCURRENCE`
- `PREVIOUS_ACTUAL_OCCURRENCE`
- `FIXED_ANCHOR_SERIES`

State exactly which value drives occurrence n and which fields/references are required. Keep `RecurrenceBasis` distinct from P1 `TemporalBasis`: one defines recurrence progression; the other selects the referenced event's date source. Validate their consistency without collapsing them.

For actual-based recurrence, require an explicit actual-visit or actual-treatment source where that distinction matters; attendance with treatment withheld must not generate a treatment-dependent next occurrence. Missing actuals must wait unless an explicit approved fallback exists. Do not infer basis from event names, statuses, dependency mode alone, or whether an interval uses months.

Model validation must reject impossible combinations. Unstated but representable draft semantics may remain review-blocked through coded validation; explain the strict-model versus incomplete-draft boundary rather than silently defaulting.

Update JSON schemas/serialization, API inputs, persistence read/write mapping, extraction compiler mapping, review editing/presentation, and frontend types as needed. Extraction can set a basis only from evidence-supported interpretation; otherwise mark it unconfirmed/unresolved and block approval.

Persisted schedules predate this field. Design an explicit compatibility path that records legacy-equivalent provenance where existing behavior is known, or requires review where it is ambiguous. Do not retroactively claim the protocol explicitly stated a basis. Do not mutate approved historical JSON or silently move enrolled patients' dates because a new enum is introduced. New corrected schedules must carry supported explicit semantics; any required change to an existing approved schedule needs a controlled version/review path.

Do not edit an already-applied migration and assume it changes storage. Use a new migration only if required; explain how old records round-trip and whether database validators/indexes need updating.

## 6. P4-03 — derive the series from approved first timing and basis

Resolve the first occurrence through the event's ordinary timing logic, including P1 temporal basis, offsets, precision, windows, and dependency outcomes. Derive later occurrences from the declared basis. Do not restart an offset-first series at a raw anchor or apply a first offset twice.

Mandatory PDF scenario with fixed dates:

**Baseline 1 January 2026; first event +14 days; recurrence every 28 days -> 15 January, 12 February, 12 March 2026.**

Prove this without duplicating the same offset into two fields just to make the test pass. If `start_offset` and event timing both exist, detect disagreement and use an explicit validated authority, not whichever field happens to be read first.

For each basis:

- First-resolved series derives later targets from the resolved first occurrence and the approved interval/calendar policy.
- Previous-planned series derives from the previous logical occurrence's planned date according to the defined evaluation semantics.
- Previous-actual series derives from the previous logical occurrence's required actual. Do not invent later actuals to fill a horizon. Preserve a pending next occurrence with a reason where appropriate.
- Fixed-anchor series uses the explicit approved anchor grid. Define how an offset first event aligns with that grid and validate inconsistent combinations rather than overriding occurrence 0 silently.

Explain the distinction between first-anchored and previous-planned monthly recurrence. For example, an anchored January-31 series may recover March 31 after February clamping, whereas repeated addition from February 28 can yield March 28; do not select either interpretation accidentally. Preserve existing declared month-end policy and expose policy provenance.

Use existing calendar arithmetic: months/years are calendar units, not 30/365-day approximations. Preserve date-only clinical values and supported timed activity/timezone behavior. Do not guess a site timezone.

Keep occurrence indexes stable when earlier occurrences are missing, missed, paused, excluded, or outside the projection horizon. Define `include_start=False` explicitly, including whether it excludes an anchor/start slot or the first clinically defined visit; do not silently renumber recorded history.

Preserve completed actuals and original planned dates across reevaluation. Only explicitly actual-relative future targets move when an actual changes; independent baseline imaging remains fixed. Reuse P1 reconciliation, diffs, idempotency, and immutable evaluation history.

Use the same shared pure evaluator for SQL and Mongo callers. Extend phased/weekday/cycle/episode recurrence only when required to preserve compatible references to the changed model; do not impose interval semantics on those distinct models.

## 7. P4-04 — actionable approval validation and safe termination

Extend the existing validator with stable issue codes and field paths for missing/incompatible basis, interval, termination, reference, condition source, effective time, structural cycle, contradictory start/offset, and safety bounds. Approval must recompute blocking issues through every SQL and Mongo approval entry point while preserving P3 grants/maker-checker checks.

Separate structural rule incompleteness (approval-blocking) from a valid protocol awaiting a patient's actual/condition/anchor (evaluation waiting). Missing patient-specific input must not make the protocol itself invalid.

Verify existing termination behavior rather than rewriting it blindly:

- COUNT counts the explicitly defined occurrence semantics, respecting start inclusion and existing count-basis policies where applicable.
- DATE uses the stated boundary and supported inclusion semantics.
- EVENT uses the required source occurrence/basis; unresolved termination does not silently become a horizon termination.
- CONDITION uses three-valued logic and recorded effective time; unknown values/times cannot be guessed.
- HORIZON bounds the operational projection without asserting that the protocol stops permanently at that date.
- RELATIVE preserves the required end reference, offset, and explicit inclusive/exclusive boundary.

All expansion paths must remain finite and bounded. Distinguish a protocol-stated maximum from an engine resource-safety cap. Do not invent a clinical maximum from a hardcoded loop limit or truncate a series as “complete” without explaining the safety boundary. Nonprogressing intervals, inconsistent date/datetime comparisons, missing effective times, and limit exhaustion require explicit safe outcomes.

Expose enough validation/explanation in review surfaces to correct the rule. New field edits must update P3 material-editor provenance and semantic revision/fingerprint so an old approval preview cannot approve changed recurrence semantics. Do not bypass evidence requirements by bulk-confirming an ambiguous rule.

## 8. P4-05 — exact regression matrix and workflow proof

Use fixed dates, explicit state, stable occurrence indexes, and injected clocks. Extend existing meaningful suites and add targeted tests for confirmed gaps; do not merely mirror helper implementation.

Prove at minimum:

1. Pregnancy false/unknown/true with absent, waiting, resolved, and blocked sources yields the five dependency outcomes as specified. A false dependency cannot block through later actual/manual checks.
2. Mixed dependencies aggregate deterministically; inactive dependency and event-level applicability/timing remain distinguishable.
3. All four recurrence bases have distinct exact-date/status/explanation tests, including missing actuals and visit-versus-treatment actuals.
4. Baseline **1 Jan 2026 -> first +14d -> every 28d** yields **15 Jan, 12 Feb, 12 Mar** without duplicating first offset. Conflicting offsets block review/approval rather than silently moving dates.
5. COUNT/DATE/EVENT/CONDITION/HORIZON/**RELATIVE** terminations cover first/last boundaries, inclusion, missing source/effective time, and limit exhaustion.
6. Calendar month/year arithmetic covers January 31, February in leap/nonleap years, leap-day anniversaries, and declared clamping policies for anchored versus previous-occurrence bases.
7. Missing/missed occurrence 0 and out-of-order actual dates do not shift occurrence 1's identity. Self-reference to a previous numbered occurrence is handled distinctly from a structural circular dependency.
8. A recorded actual changes only required future actual-based occurrences; parallel baseline events and completed history remain intact. Repeated reevaluations do not mutate old evaluations.
9. JSON/API/persistence round trips preserve basis, provenance, offsets, termination aliases, and dependency explanations. Legacy payloads load according to the documented compatibility policy; ambiguous new drafts cannot be approved.
10. Extraction without evidence does not invent the recurrence basis; reviewed explicit correction can resolve it, updates material-editor provenance, and invalidates stale approval previews.
11. Actual authorized SQL and Mongo workflows evaluate the same explicit rules and expose matching applicable projections/explanations. Invalid rules fail clinical validation for a correctly granted actor, not merely an authorization error that hides untested validation.
12. Every relevant approval alias and direct service boundary blocks unsafe structures; P3 self-approval restrictions and P1/P2 behavior remain intact.

Suggested suites to inspect/select based on changes:

- `test_uctsm_domain.py`, `test_uctsm_occurrence_history.py`, `test_uctsm_temporal_basis.py`.
- `test_relative_recurrence.py`, `test_engine_recurrence_calendar.py`, `test_engine_calendar_recurrence.py`.
- Conditional, rolling-horizon, activity/cycle, extraction/compiler, review, API-contract, Mongo patient/review/parity tests.
- P1 occurrence/recalculation/API/concurrency and P3 authorization/maker-checker regressions for touched boundaries.

Run local real-Mongo checks for affected serialization/workflow behavior using the repository fixtures and `UCTSM_REQUIRE_MONGO=1`. Verify local hosts and isolated test database configuration; never run mutation tests against a shared cluster. SQLite tests can establish SQL API/model behavior but do not prove PostgreSQL locking; keep that limitation explicit.

Follow `AGENTS.md`: targeted isolated pytest invocations where needed, then the applicable curated handoff gate with file-existence checks and deleted extraction/footnote modules omitted. Do not run unrestricted whole-backend pytest. Existing event-loop harness changes do not revoke that instruction.

If frontend code changes, run relevant UCTSM scripts and a viable scoped TypeScript check preserving module/JSX/path settings. There is no plain `npm test`; Expo lint lacks `yarnpkg`; whole-project typechecking is known to exhaust memory. State browser/device verification limits separately from unit/typecheck success.

## 9. Completion and reporting

Implement in reviewable increments: confirm failures -> decision tables/schema -> dependency evaluation -> recurrence expansion -> validation/compatibility -> API/persistence/review integration -> regression verification.

Run `git diff --check`, compare the final diff against the initial working tree, and update the P4 plan with actual outcomes. Do not count line-ending notices as whitespace errors or change unrelated P1/P2/P3 work.

Final report must include:

- P4-01–P4-05 status table with code and actual test evidence.
- Dependency outcome table and recurrence-basis examples/semantics.
- Proof of **15 January, 12 February, 12 March 2026** and missing-actual waiting behavior.
- Serialization/legacy compatibility, approval-blocking issue codes, termination/safety policies, and completed-history preservation.
- Reachable SQL/Mongo workflow evidence, actual verification commands/results, skipped/environment limitations, and relevant P1/P2/P3 regression results.
- Any unresolved clinical semantics and their precise blocked behavior. P3 role-map approval remains a separate owner decision.

Do not claim P4 complete if only standalone model tests pass, extraction/review can bypass explicit-basis validation, an active dependency still ignores its predicate, or actual recurrence invents dates for absent actuals. Start with inspection and the evidence matrix, then complete the authorized work.
