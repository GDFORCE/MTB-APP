# Claude Code implementation prompt: add schedule atoms to the kit system without changing current behaviour

Prepared 28 October 2026. **Current implementation prompt for the kit schedule engine.** Saving it does
not authorize deployment, clinical policy changes or data migration.

Background: [kit-schema-element-inventory.md](kit-schema-element-inventory.md) (what the kit model can
represent), [protocol-schedule-atoms-prompt-v4-2026-10-28.md](protocol-schedule-atoms-prompt-v4-2026-10-28.md)
(the atom list used by the protocol study) and the 5-protocol pilot findings summarised in
[protocol-design-pattern-prompt-v3-2026-10-28.md](protocol-design-pattern-prompt-v3-2026-10-28.md).

## Prompt to paste

````text
Read AGENTS.md first, then docs/kit-schema-element-inventory.md and
docs/claude-code-kit-schedule-atoms-implementation-2026-10-28.md (this file; the plan below is the task).

GOAL
The kit scheduling system (backend/app/kit/*, backend/kit_routes.py, frontend/src/features/kit/*) already
represents most protocols. Extend it with the missing schedule "atoms" so it can represent any protocol,
WITHOUT changing any current behaviour. Every addition is an optional field: a model that doesn't use it
must produce exactly the same schedule as today.

START FROM THE ACTUAL TREE
Check git status and git log yourself. Branch kit/protocol-rules has a large uncommitted working tree
(28 Oct 2026: date ranges, repeat stages/limits/delays, richer conditions, what-happened conditions,
model versions and patient pinning, stored protocol PDFs, patient visit detail). It belongs to the owner:
preserve it, never reset or revert it. Branch backup/kit-tree-work-2026-10-28 holds an older reverted
"tree" rule engine; do NOT bring it back or build a general free-form rule interpreter. Commit only if the
owner asks.

NON-NEGOTIABLE RULES
1. Additive only. New fields are optional; existing fields keep their meaning; no data migration.
2. Step 0 (frozen results) comes first and must stay green after every step.
3. New engine stages run only when the model uses the feature; otherwise today's code path runs unchanged.
4. The AI (prompts.py, which extends the untouched _prompt_text.py) writes a new field only when the
   protocol states it. normalize.py validates every new field; unusable values are dropped with a note in
   `notes`, never guessed (AGENTS.md: don't default an unstated value).
5. Reviewer load stays low: add review questions only when an ambiguity changes dates and the protocol
   doesn't settle it (extend normalize._prune_review if needed). The one known case: repeats where the
   protocol doesn't say whether they chain from the previous actual date or sit on a fixed grid, or whether
   a hold shifts later visits — that is a date-changing decision and may be a review question.
6. The engine never predicts measured values. A visit that depends on a result waits ("waiting for ANC
   result") until staff record it.
7. Dose changes are suggestions only; a person confirms them. The app never decides a dose.
8. Patients stay pinned to their kit_model_version; nothing here moves existing patients.
9. One engine: extend frontend/src/features/kit/schedule.ts (split it into modules first if it helps,
   keeping the tests). Python validates; it does not compute a second schedule.

STEPS (build, test and finish each before the next; don't pause for approval between steps)

Step 0 — Frozen results (safety net).
 Add a golden test that computes schedules with today's engine for every fixture and sample model
 (backend/app/kit/samples/*.json normalized, frontend/tests fixtures, tests/kit-schema-extensions models),
 across representative participant records (no answers, answers, actuals, logged events), and stores the
 serialized output (computeKitSchedule) as JSON snapshots under frontend/tests/golden/. The test fails on
 any difference. Snapshots are written once, before any engine change, and only updated deliberately.

Step 1 — Tracks.
 Model: visit.track (optional; absent = "main"); rule stop_tracks: [track]; event ends_tracks: [track].
 Existing ends_schedule / stop_all keep meaning "all tracks". Engine: a track stop affects only that
 track's later visits; visits counted from the anchor an ending event sets still stay (current behaviour).
 UI: timeline groups or labels visits by track when a model has more than one. Prompt + normalize.
 Typical use: treatment ends at progression or discontinuation, follow-up and survival continue.

Step 2 — Measures and recorded results.
 Model: measures: [{id, label, unit, kind: number|grade|choice|yes_no, recorded: "visit"|"any_time"}].
 Patient record: observations: [{measure, date, value, visit_key?}] validated by clean_participant
 (known measure, valid date, value matches kind). Staff UI: record results in the visit sheet
 (KitPatientView) and list/edit them; patient-facing screens don't need to show raw values.
 Conditions: {measure, aggregation: latest|worst|best|count|successive_rises|successive_falls|
 change_from_baseline|persists_days, op, value, window?} evaluated as of each visit's date (same approach as
 the {event, happened} conditions). Unknown until a result exists.

Step 3 — Derived events.
 Model: event.derive = {if: condition (step 2 forms), date_from: "result" (which result's date) |
 "time_point"}. Engine stage BEFORE rules: create the derived event exactly like a staff-logged event, so
 all existing event machinery (rules, sets_anchor, ends_schedule, event visits) works unchanged. Timeline
 marks it as derived ("Progression derived from PSA, 12 Mar"). Staff can still log it manually; don't create
 duplicates.

Step 4 — Wait-until gates with deadlines.
 Model: visit.gate = {until: condition, deadline?: {amount, unit, from: "planned"|"previous_actual"|anchor},
 on_timeout?: event id}. Engine: the gated occurrence's date = the first date the condition is met on or
 after its planned date; before that the row is waiting ("waiting for ANC > 1,000"); past the deadline the
 engine creates the on_timeout event (like step 3), which existing rules act on (for example stop the
 treatment track). Later chained occurrences count from the gated actual date.

Step 5 — Calendar units.
 Offsets, windows, repeat intervals and stages get an optional unit (days default; weeks, months, years,
 business_days, hours). Real month/year arithmetic (end-of-month rule documented and tested) and business
 days (weekends; no holiday calendar unless a model supplies one). Normalize no longer needs to convert
 months to days; keep existing day fields working.

Step 6 — Earliest-of / latest-of and counters.
 Anchor kind "earliest_of"/"latest_of" with candidates (anchor + offset, event). Condition
 {count: event|dose_change|activity_done, since?, op, value}.

Step 7 — Doses as activities (design first, then build).
 Write a short design note in docs/ before coding. Model: drugs [{id, label, dose_levels [ordered]}];
 visit.kind = "dose" with drug and level; actions hold / reduce one level / stop one drug / substitute,
 always as suggestions shown to staff; patient record keeps confirmed dose levels per occurrence.
 Co-medication / premedication schedules switched on by a fact or event.

Step 8 — Study-level events.
 Trial-level events (amendment in force, emergency mode, cohort decision) stored on the trial (not the
 patient), recorded once by staff with a date, applied to every patient from that date. Respect model
 version pinning; document how they interact.

FOR EVERY STEP
- Update: prompts.py, normalize.py (+ backend tests), schedule.ts / describe.ts / index.ts types (+ engine
  tests), kit_routes.py clean_participant if the patient record changes, and the UI where staff enter or
  see the new data.
- Tests: a constructed-model test per behaviour (expected dates worked out by hand), the golden test from
  step 0 unchanged, and the existing suites.
- Docs: update docs/kit-schema-element-inventory.md (mark the atom supported) and the kit bullet in AGENTS.md.

VERIFY (machine limits apply — see AGENTS.md)
- Frontend: npm run test:kit. If the machine is low on memory, run each test file alone:
  node --max-old-space-size=256 --experimental-strip-types --import ./tests/kit-resolve-hook.mjs --test <file>
- Typecheck only touched files with a temporary tsconfig that extends tsconfig.json (whole-project tsc runs
  out of memory).
- Backend: tests/test_kit_pipeline.py, test_kit_participants.py, test_kit_tables_route.py,
  test_kit_progress.py, test_kit_schema_extensions.py and the new ones. Set the LOCAL Mongo variables first
  (never the Atlas URL in backend/.env): MONGO_URL and UCTSM_TEST_MONGO_URL =
  mongodb://127.0.0.1:27017/?replicaSet=rs0, DB_NAME=<local test db>, TEMP/TMP on D:. Stop mongod afterwards
  if you started it.

FINISH
Report per step: what was added, tests run and results, anything skipped or blocked and why, and any
review question added. Save a completion record under docs/ and update docs/claude-code-prompts-index.md
and docs/claude-code-next-prompt.md. If context runs low, save an exact checkpoint and continuation prompt
under docs/ first.
````
