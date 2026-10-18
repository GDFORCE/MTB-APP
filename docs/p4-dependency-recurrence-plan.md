# P4 â€” dependency outcomes and explicit recurrence basis: design and verification record

Scope: P4-01â€“P4-05 of `D:\MTB_dev (2).pdf`. Not in scope: P5â€“P14, authorization, extraction/identity rewrites. P3's role matrix is still `PROPOSED_PENDING_OWNER_APPROVAL`; nothing here changes grants, the maker-checker default, or rights.

## 1. Requirement matrix

| Task | Confirmed gap (source) | Change | Evidence |
|---|---|---|---|
| P4-01 dependency outcomes | `evaluator._evaluate_event` blocked on `prior` source resolution and never read `Dependency.condition`; the later ACTUAL_PREVIOUS_EVENT / MANUAL loops iterated *all* dependencies, so an inactive one could still block | new pure `domain/schedule/dependency.py` (`DependencyOutcome`, 5 kinds, aggregation); condition decided first with the existing `evaluate_condition`; active-source list drives the actual/manual/timing-context steps | `test_p4_dependency_recurrence.py` (dependency section) |
| P4-02 explicit basis | `RecurrenceRule` had no basis; extraction and review could not say what a repeat counts from | `RecurrenceBasis` (4 values), `basis_status` (STATED/UNCONFIRMED), `actual_source`; model validation of impossible combinations; legacy = `basis is None` | model/round-trip tests; `test_p4_persistence_and_approval.py` |
| P4-03 series from first timing | `_expand_recurrence` ignored its `first` argument and counted from `start_reference + start_offset` | `_expand_basis_recurrence`: occurrence 0 is the event's own resolved timing; later ones follow the basis | 15 Jan / 12 Feb / 12 Mar test and basis-distinguishing tests |
| P4-04 approval validation | no check that timing and recurrence start agree, none for unconfirmed bases; safety cap silent | `_interval_recurrences` in the existing validator (codes below); engine safety cap reported on the last occurrence | validator tests, both approval planes |
| P4-05 regression matrix | â€” | 48 new tests + journey updated | Â§6 |

## 2. Dependency decision table

Condition is decided **before** the source (so a false condition never asks for a source, an actual or a manual date).

| Condition | Source state (all occurrences of the source) | Outcome | Event effect |
|---|---|---|---|
| FALSE | not consulted | `NOT_APPLICABLE` | dependency skipped |
| UNKNOWN | not consulted | `WAITING_FOR_CONDITION` (lists missing fields / unsupported predicates) | status `WAITING_FOR_CONDITION`, no date |
| TRUE or none | resolved or completed | `SATISFIED` | proceeds |
| TRUE or none | waiting for anchor/condition/assignment, blocked, paused, on-demand, or not yet evaluated | `WAITING_FOR_SOURCE` | status `BLOCKED`, `blocked_by` lists it (existing contract) |
| TRUE or none | not applicable, missed, cancelled, unresolved | `BLOCKED` | status `BLOCKED` with the reason |

Aggregation, strongest first: `BLOCKED` > `WAITING_FOR_CONDITION` > `WAITING_FOR_SOURCE`; otherwise satisfied (a `NOT_APPLICABLE` dependency is just skipped).

* **Dependency not applicable â‰  event not applicable.** If the event's own timing references the same source, that timing still waits (`WAITING_FOR_ANCHOR`); a false optional precondition does not supply the date.
* `dependency_type` is carried into each outcome for explanation. It does **not** by itself require an actual: that is the event's explicit `dependency_mode` (ACTUAL_PREVIOUS_EVENT / MANUAL), never inferred from the type. *Owner decision:* whether TRIGGER/PRECONDITION should imply "must have actually happened".
* Public shape: `dependency_result` keeps `mode`, `blocked_by`, `waiting_for_actual`, `waiting_for_manual_date` and gains `outcome` and `dependencies[]` (`source_event_code, dependency_type, outcome, reason, condition_result, source_status, missing_inputs`). No condition evidence beyond field names.
* Structural validation is unchanged and condition-blind: a cycle through a conditional dependency is `CIRCULAR_DEPENDENCY` even if the condition is false today; a self-dependency is a cycle; "previous occurrence of a numbered series" is expressed by `RecurrenceBasis`, not by a dependency.

## 3. Recurrence bases

`RecurrenceBasis` says how the *series* progresses; `TemporalBasis` (P1) says which *date* of a referenced visit a rule counts from. They are separate.

| Basis | Occurrence 0 | Occurrence n | Requires |
|---|---|---|---|
| `FIRST_RESOLVED_OCCURRENCE` | the event's own resolved timing | occurrence 0 + n Ã— interval (always from 0, so 31 Jan â†’ 28 Feb â†’ **31 Mar**) | exact first timing; `start_offset` absent or equal to it |
| `PREVIOUS_PLANNED_OCCURRENCE` | event's own timing | planned(n-1) + interval (31 Jan â†’ 28 Feb â†’ **28 Mar**) | as above |
| `PREVIOUS_ACTUAL_OCCURRENCE` | event's own timing (planned) | actual(n-1) + interval; **waits, undated**, until that actual exists | `actual_source` = ACTUAL_VISIT or ACTUAL_TREATMENT |
| `FIXED_ANCHOR_SERIES` | grid slot 0 = anchor (+ `start_offset`) | slot n of the grid | anchor reference; the event's timing must fall on slot 0 |

Exact examples (tested): baseline 1 Jan 2026, first +14 d, every 28 d, `FIRST_RESOLVED_OCCURRENCE` â†’ **15 Jan, 12 Feb, 12 Mar 2026**, with the offset neither doubled nor restarted at the anchor. With no actual: occurrence 0 = 15 Jan, occurrence 1 = `WAITING_FOR_ANCHOR` (no date, `waiting_for_actual_occurrence`), nothing beyond it. Actual visit on 20 Jan â†’ occurrence 1 = 17 Feb. Visit without treatment does not date an ACTUAL_TREATMENT series.

`include_start=False` excludes only the start slot; emitted occurrence indexes still begin at 0 and are never renumbered by history. Months/years are calendar arithmetic (leap February and leap-day anniversaries tested); the declared month-end policy and whether it was stated are exposed per occurrence.

## 4. Compatibility and validation

* **Legacy** (`basis is None`, i.e. every schedule persisted before P4): dates are computed exactly as before and explained as `UNSTATED_LEGACY`. If the event's timing disagrees with where the series starts, the dates are **not** moved; `explanation.recurrence.basis.start_conflict` says so. The original defect (offset on the event only â†’ series restarted at the anchor) is therefore *flagged, not silently repaired*, for enrolled patients; a new or corrected version must resolve it.
* **Evaluating an approved schedule** re-runs the validator with `evaluating_approved=True`: legacy-basis findings are advisory there so an approved schedule keeps dating (NF-08). Explicit-basis conflicts and unconfirmed bases still fail integrity.
* **No migration.** Fields live inside the event JSON (SQL `JSON`, Mongo document) with defaults; old payloads load with `basis=None`. No stored value is rewritten; approved versions are untouched. No database validator needed changing.
* **Extraction**: the compiler marks every interval repeat `basis_status=UNCONFIRMED` (proposing `FIRST_RESOLVED_OCCURRENCE` only when the fact's own timing *is* the first occurrence). It is never dated and approval is blocked until a reviewer states it.
* **Review**: the row summary shows the basis (or "Needs review"); the reviewer states it with the existing typed edit (`changes.recurrence`), which is a *material* edit (P3 provenance, revision/fingerprint change, stale approval previews refused).

Validator codes (field path in `details.field_path`):

| Code | Blocks approval? | Meaning |
|---|---|---|
| `RECURRENCE_BASIS_UNCONFIRMED` | yes | basis proposed without evidence, or status unconfirmed |
| `RECURRENCE_BASIS_UNSTATED` | yes when ambiguous (timing not structurally comparable, conflicting, or actual-previous dependency mode); warning when unambiguous (`legacy_equivalent = FIXED_ANCHOR_SERIES`) | repeat predates explicit bases |
| `RECURRENCE_START_CONFLICT` | yes (advisory only for a legacy rule while evaluating an approved schedule) | event timing and `start_offset` / grid put occurrence 0 on different dates |
| model `ValueError` | rejected at load | actual source without/with the wrong basis; FIXED_ANCHOR_SERIES from a visit |

## 5. Termination and safety

Unchanged and re-verified: COUNT, DATE, EVENT (earliest source date), CONDITION (three-valued; true needs an effective time, else `UNRESOLVED`; unknown â‡’ `WAITING_FOR_CONDITION`), HORIZON (bounds projection only) and RELATIVE (explicit offset and inclusive flag). Expansion is bounded by the engine's 10 000-occurrence safety cap, which is a *resource* limit, not a protocol maximum: when a non-COUNT series ends on it, the last occurrence carries `engine_safety_cap {limit, note}`.

## 6. Verification (14 Oct 2026)

Local replica set `rs0` on 127.0.0.1, `UCTSM_REQUIRE_MONGO=1`, `DB_NAME=mtb_p4_test`; SQLite in memory for SQL. Modules run one at a time (TestClient caveat); gate run as one session.

| Suite | Result |
|---|---|
| `test_p4_dependency_recurrence.py` (dependency outcomes, bases, terminations, safety cap, round trip) | 35 passed |
| `test_p4_persistence_and_approval.py` (SQL + memory + real Mongo round trip; both approval planes refuse unsafe repeats) | 13 passed |
| Curated gate Â§E, 33 files present (2 deleted modules omitted) | 375 passed |
| recurrence/calendar/temporal-basis/occurrence-history suites | 11 + 11 + 26 + 35 + 12 passed |
| review suites, Mongo parity review/extraction, extraction endpoints, protocol creation | all passed |
| P1 (api, recalculation, concurrency) | 17 + 56 + 6 passed |
| P2 identity workflow | 22 passed |
| P3 authz http / mongo-plane / maker-checker SQL + port | 42 + 16 + 18 + 16 passed |
| journey upload â†’ review â†’ approve â†’ enrol â†’ amend (real server) | 14 passed, after the reviewer states the dose repeat's basis (pending reason appears for `VELTRACIN_DOSE`, cleared by the typed edit) |

## 7. Intentional behaviour changes

1. Newly extracted interval repeats are blocked from approval until a reviewer states the basis (journey updated accordingly).
2. A dependency with a FALSE condition no longer blocks; one with an UNKNOWN condition now yields `WAITING_FOR_CONDITION` instead of being ignored.
3. `COMPLETED` sources now satisfy a dependency (previously only `RESOLVED`).
4. Approving a new version with a legacy ambiguous repeat is blocked; evaluating existing approved versions is not.

## 8. Limits and open decisions

* *Owner:* default basis semantics per protocol wording are never guessed; the compiler proposes at most one value and always leaves it UNCONFIRMED.
* *Owner:* should TRIGGER/PRECONDITION dependency types require an actual by themselves (Â§2)?
* Weekday (`calendar_recurrence`), phased `recurrence_plan`, cycle and conditional-repeat models are intentionally untouched; they keep their own semantics.
* The review UI has no dedicated "basis" control: it is set through the existing typed recurrence edit (API). A purpose-built control is not built.
* SQLite proves SQL model/API behaviour, not PostgreSQL locking. No browser/device verification. The frontend was not changed.
* P3 follow-ups (legacy upload wrappers not in the capability catalog, bounded cross-org lists) and P2's real PERIGON/registry limits are separate and unchanged.
