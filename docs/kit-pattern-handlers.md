# Kit pattern rules and blockers

Added 18 November 2026. This is the kit-engine version of the "blocker" approach proposed for PR #2 on `GDFORCE/MTB-APP-TESTING`. That PR built the same idea on the old UCTSM engine, which no longer schedules patients.

## How it works

1. **The AI tags the rule.** If a protocol rule about *when* a visit happens doesn't fit any kit field, the analysis writes it to `model.pattern_rules` instead of squeezing it into the nearest field. Each entry has:
   - a name for the rule's general shape (`UPPER_SNAKE_CASE`)
   - the visits it concerns
   - its params
   - the protocol's own words and the page number

   The prompt lists every pattern that already has a handler, so the AI reuses those names. For anything else it makes up a new name that describes the shape, not the protocol. See `prompts.py` and `patterns.prompt_text()`.
2. **Clean-up keeps it.** `normalize` never drops a pattern rule. `patterns.apply_status` marks a rule `blocked` in two cases:
   - no handler exists for its name (`"no_handler"`)
   - its params can't be used, for example a value the protocol doesn't state

   The quote is looked up in the PDF text (`quote_found`), the same way it is for rules and open points.
3. **The trial is locked for enrolment.** A model with blockers can still be reviewed and saved. The saved document stores `blockers`. While any blocker is open, the server refuses:
   - enrolment: `POST /patients/invite` returns 409
   - moving a patient to the new model: `POST /patients/{id}/kit/move-to-latest` returns 409

   The screens say the same thing:
   - Add Trial shows a red row, and its saved screen no longer says "ready for patients".
   - The saved schedule screen shows a warning.
   - Add Patient stops at the trial step.

   Blocked rules come first in the list of points to check, and a reviewer can't tick them off.
4. **The admin sees them.** Admin → *Protocol Rules Waiting* (`admin/protocol-rules.tsx`, `GET /api/admin/kit/blockers`) groups the blockers by name. The name with the most waiting trials comes first, and each entry shows the protocol's words and page.
5. **A developer adds a handler.** See below.
6. **The admin re-checks.** *Re-check* (`POST /api/admin/kit/blockers/recheck`, all trials or one pattern) re-evaluates every saved current model against the server's handlers. Every trial whose rules now pass is unblocked at once.
7. **Later protocols** with that shape are calculated as soon as they are analysed.

Calculated pattern rules are also listed for the reviewer, as *Calculated by a protocol rule*, and can be ticked off. The engine applies a rule only when it isn't `blocked` and its name has a frontend handler.

## Handlers so far

| Name | What it calculates | Params |
|---|---|---|
| `CALENDAR_MONTHS` | Same day of the month, k calendar months on | `months`, `every_months` (repeats), `missing_day`: `last_day_of_month` / `first_day_of_next_month` / `not_stated` (no date; staff enter it) |
| `BUSINESS_DAYS` | N business days (Mon–Fri) after what the visit counts from | `business_days`, `within` (window up to that day), `excludes_holidays` (shown as a note, because the app doesn't know holidays) |
| `WEEKDAY` | The given weekday on or after / on or before the planned day | `weekday`, `direction` |

Shapes the kit already holds without a pattern rule:

| Shape | Kit field |
|---|---|
| A minimum gap after an event | visit `min_gap` |
| Same-day merging | rule `same_day` |
| A delay after a hold (the next cycle counts from the recorded date) | visit `check` (a "No" holds the visit), rule `pause`, and `repeat.chain` |

These cover PR #2's three handlers, so they aren't needed as patterns here.

## Adding a handler

Both halves must use the same name. `tests/kit-patterns.test.ts` fails if the two lists differ.

1. **Backend: `backend/app/kit/patterns.py`.** Add a check function `(params, model, visits) -> (clean_params, None) | (None, "why")`. Fill in no value the protocol didn't state; return a reason instead. Then add a `Pattern(title, prompt, check)` entry to `PATTERNS`. The `prompt` text tells the AI when to use the pattern and what its params are.
2. **Frontend: `frontend/src/features/kit/patterns.ts`.** Add an entry to `PATTERN_HANDLERS` with `date(params, ctx)`. `ctx` gives you:
   - the occurrence index
   - what the occurrence counts from (`anchor`, `follows` or `repeat`) and that date
   - the anchor date
   - the date so far
   - `chain`

   Return `{date}`, and optionally `windowBefore`/`windowAfter`, `rel` (how the date was worked out), `note` (shown once) and `pending` (why there is no date). Return `date: null` rather than guess.
3. **Tests.** Add backend cases to `backend/tests/test_kit_patterns.py` and engine cases with dates worked out by hand to `frontend/tests/kit-patterns.test.ts`. Run `npm run test:kit`, then run each backend kit file separately.
4. **Deploy, then re-check.** After the deploy, press *Re-check* on the admin page. Models analysed before the handler existed may have params in a different shape. Those stay blocked, with the reason shown, until the protocol is analysed again.

Only rules about **dates** can be handlers today. The hook runs for each scheduled-visit occurrence in `schedule.ts`, before windows, stops and checks are applied. A rule that changes *whether* visits happen will need another hook in `schedule.ts`, for example `CONSECUTIVE_MISSED_VISITS_WITHDRAW` or `MISSED_VISIT_TESTS_CARRIED_OVER` from the SYN-06 fixture, which both stay blockers. Rule-condition timing (`plannedVisit` in `schedule.ts`) still uses plain day offsets.

## Measured

On the hand-written benchmark models (`python scripts/kit_bench_all.py <run> --hand`):

| Benchmark | Before | After |
|---|---|---|
| SYN-06 beyond-vocabulary | 19/31 | 48/48 |
| All six fixtures | 754/769 | 783/786 |

The other fixtures are unchanged. No AI run has been made with the new prompt yet.

## Generated calculations (20 November 2026)

A rule with a new name no longer always waits for a handler: when the protocol states it clearly, the analysis also writes its calculation (`program`, `output`, `inputs`, `tests`), which the app validates and tests (`backend/app/kit/calc.py`) and the study team confirms in workflow review. Write a handler here when a shape recurs across protocols or needs facts the calc language does not expose. See [kit-generated-calculations-2026-11-20.md](kit-generated-calculations-2026-11-20.md).
