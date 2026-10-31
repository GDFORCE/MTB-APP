# Claude website prompt: add schedule atoms to the kit system, one step per chat

Prepared 28 October 2026. Website version of
[claude-code-kit-schedule-atoms-implementation-2026-10-28.md](claude-code-kit-schedule-atoms-implementation-2026-10-28.md)
for use on claude.ai, where Claude cannot read the project folder or run tests. Same plan, same rules.
Saving it does not authorize deployment, clinical policy changes or data migration.

## How to use

1. New chat per step. Upload the files listed for that step (drag them into the chat).
2. Paste the prompt below and change `STEP = 0` to the step you want.
3. Claude replies with code changes. Paste them into the files in VS Code.
4. Run the tests (or ask Claude Code in VS Code to run them). If something fails, paste the error back
   into the same chat.
5. When the step passes, start a new chat for the next step, uploading the **updated** files.

## Files to upload

Always:
- `docs/kit-schema-element-inventory.md`
- `frontend/src/features/kit/schedule.ts`, `describe.ts`, `index.ts`, `utils.ts`, `participant.ts`
- `backend/app/kit/normalize.py`, `backend/app/kit/prompts.py`
- `frontend/tests/kit-schema-extensions.test.ts`, `backend/tests/test_kit_schema_extensions.py`

Also for steps 1–4 (screens and patient record):
- `frontend/src/features/kit/KitPatientView.tsx`, `PatientTimeline.tsx`, `patientSections.ts`
- `backend/kit_routes.py` (only the `clean_participant` function is needed; you can paste just that part)

Step 0 also needs: `frontend/package.json`, `frontend/tests/kit-rules.test.ts`,
`backend/app/kit/samples/arc.json`, `helix.json`, `orion.json`.

## Prompt

````text
You are helping me extend the scheduling engine of a clinical-trials app. I have uploaded the relevant
source files. You cannot run code, so be precise: I will paste your changes into my project and run the
tests myself.

THE SYSTEM ("kit")
- An AI reads a protocol PDF and writes a JSON "model": anchors (dates the schedule counts from), visits
  (offset, window, repeat, procedures, conditions), questions, events and rules. backend/app/kit/prompts.py
  holds the AI instructions (it extends _prompt_text.py, which must stay unchanged). backend/app/kit/
  normalize.py checks and cleans every field of the model.
- Each patient has a small record (start date, answers, entered dates, logged events, actual visit dates),
  checked by clean_participant in backend/kit_routes.py.
- frontend/src/features/kit/schedule.ts computes every visit date from model + record (one engine, runs in
  the app; Python never computes a second schedule). describe.ts turns results into screen data; index.ts
  holds the TypeScript types; participant.ts holds patient-record helpers.
- docs/kit-schema-element-inventory.md lists everything the model can represent today.

GOAL
The system already handles most protocols. Add the missing schedule building blocks ("atoms") so it can
represent any protocol, WITHOUT changing current behaviour.

RULES (must hold for every step)
1. Additive only: new fields are optional; existing fields keep their meaning; no data migration.
2. A model that doesn't use a new feature must produce exactly the same schedule as today.
3. New engine stages run only when the model uses the feature.
4. The AI writes a new field only when the protocol states it. normalize.py validates every new field;
   unusable values are dropped with a note in `notes`; never guess an unstated value.
5. Add a reviewer question only when an ambiguity changes dates and the protocol doesn't settle it.
6. The engine never predicts measured values: a visit that depends on a result waits until staff record it.
7. Dose changes are suggestions a person confirms; the app never decides a dose.
8. Patients stay on the model version they were enrolled with (already built: kit_model_version).
9. Don't build a general free-form rule language; add typed fields to the existing model.

STEPS
0. Frozen results: a golden test computing schedules with today's engine for the sample models and test
   models, across a few participant records (no answers; answers; actual dates; logged events), saved as
   JSON snapshots; the test fails on any difference. Written before any engine change.
1. Tracks: visit.track (absent = "main"); rule stop_tracks; event ends_tracks. Existing ends_schedule /
   stop_all keep meaning "all tracks". A track stop affects only that track's later visits.
2. Measures and recorded results: model measures [{id, label, unit, kind: number|grade|choice|yes_no,
   recorded: visit|any_time}]; patient record observations [{measure, date, value, visit_key?}] validated in
   clean_participant; staff record results in the visit sheet (KitPatientView). Conditions
   {measure, aggregation: latest|worst|best|count|successive_rises|successive_falls|change_from_baseline|
   persists_days, op, value, window?}, evaluated as of each visit's date; unknown until a result exists.
3. Derived events: event.derive = {if: condition, date_from: result|time_point}; computed BEFORE rules and
   treated exactly like a staff-logged event, so existing rules work unchanged; no duplicates if staff also
   log it; shown as derived on the timeline.
4. Wait-until gates: visit.gate = {until: condition, deadline?: {amount, unit, from: planned|
   previous_actual|anchor}, on_timeout?: event id}. The gated date = first date the condition is met on or
   after the planned date; until then the visit shows "waiting for <condition>"; past the deadline the
   on_timeout event is created (like step 3). Later chained occurrences count from the gated date.
5. Calendar units: optional unit on offsets, windows, intervals and stages (days default; weeks, months,
   years, business_days, hours), with real month/year arithmetic (document the end-of-month rule).
6. Earliest-of / latest-of anchors (candidates: anchor + offset, event) and counter conditions
   {count: event|dose_change|activity_done, since?, op, value}.
7. Doses as activities: first a short design note; then drugs with ordered dose levels, visit.kind = dose,
   hold / reduce / stop / substitute as suggestions, confirmed dose levels in the patient record.
8. Study-level events: recorded once on the trial (amendment in force, emergency mode, cohort decision) and
   applied to every patient from that date.

STEP = 0

DO ONLY THE STEP ABOVE. Reply with:
A. A short plan (which files change and why), and anything in my files that conflicts with the plan.
B. The changes. For a file under about 250 lines, give the complete new file. For a larger file, give
   exact edits: "FIND this exact block" (copied from my file) → "REPLACE with". Never give partial code
   with "..." gaps.
C. New or changed tests: constructed models with expected dates worked out by hand, plus the step-0 golden
   test staying unchanged.
D. Where the new field goes in prompts.py (AI instructions) and normalize.py (checks).
E. The commands to run:
   frontend: npm run test:kit (add new test files to the "test:kit" script in package.json)
   backend: python -m pytest -q tests/test_kit_schema_extensions.py tests/test_kit_participants.py
            tests/test_kit_pipeline.py
F. The lines to add to docs/kit-schema-element-inventory.md for this step.
If anything is unclear in my files, ask me before writing code.
````
