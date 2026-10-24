# Kit protocol coverage review — 24 October 2026

## Verdict and scope

The current kit can express many visit-scheduling patterns, but it cannot reliably handle every protocol. Its strongest scope is a reviewed schedule made of calendar-day offsets, fixed-interval repeats, explicit anchors, choice-based conditions and simple events. Supporting a pattern in the JSON schema does not prove that extraction, scheduling, patient display and operational follow-through all work for it.

This review inspected the current working tree, including uncommitted kit implementation files. HEAD at review time was `2a6be25`. It covered `backend/app/kit`, `backend/kit_routes.py`, patient integration in `backend/server.py`, `frontend/src/features/kit`, Add Trial and existing kit tests. It is a software capability review, not a clinical interpretation of any particular protocol. No live model requests, deployment, data migration or application-code changes were performed. The only added artifact is this report.

## What the representation can express

| Scheduling pattern | Current representation | Qualification |
| --- | --- | --- |
| Fixed screening, baseline and follow-up visits | Anchors, offsets and asymmetric windows | Requires correct source extraction and anchor choice |
| Arms, cohorts, optional sub-studies | Questions and `include_if` | Equality/membership conditions; all listed conditions must hold |
| Treatment cycles with delays | Repeats with `chain`, dependent visits with `follows` | Limited finite expansion; does not provide a general treatment state machine |
| Imaging independent of treatment delays | Anchor-based repeats with `chain: false` | Fixed calendar-day interval |
| Surgery, infusion and other dates entered later | Ask anchors and actual-visit anchors | Missing dates can remain pending |
| Crossover periods and washout | Separate anchors, visits and sequence choices | Fixed modeled paths, not arbitrary adaptive behavior |
| Withdrawal, progression, flare and unscheduled visits | Logged events with visit definitions and anchor effects | Event completion currently fails validation; ending semantics are broad |
| PK/PD and other intra-day samples | Timepoints and dose-time offsets | Calculation exists; current staff visit sheet lacks dose-time entry and timepoint display |

These are representation capabilities, not validated accuracy percentages for real documents.

## Confirmed defects and important gaps

### 1. Event-triggered visit completion cannot be saved

`frontend/src/features/kit/schedule.ts:192` generates event keys such as `ev.e1.ev#1`. `backend/kit_routes.py:351` splits the occurrence key at `#` and checks the entire prefix against bare visit IDs. It therefore rejects the generated event key as an unknown visit. This affects event-triggered actual dates and dose times.

Reproduction using the actual validator functions: a model with event `e`, event visit `ev`, and logged event key `e1` rejects actuals `{ "ev.e1.ev#1": "2026-10-25" }` with HTTP 422. Conversely, a nonexistent occurrence `v#999` is accepted for a single-occurrence visit `v`. Event linkage and occurrence bounds need validation together.

### 2. Unknown facts are silently converted into schedule meaning

`backend/app/kit/normalize.py:99` defaults an unspecified modality to `site`, and missing offsets and windows to zero. At line 140 an unknown anchor becomes the start anchor. Invalid visit dependencies and stopping anchors are removed; follow cycles are broken by removing dependencies. Duplicate visit IDs are accepted.

Reproduction: `{id: "v", name: "Visit", anchor: "nonexistent"}` becomes a site visit on the start date with a zero-day window, without a blocking unresolved state. This conflicts with the repository instruction to preserve missing clinical meaning as unresolved. A plausible date is especially difficult for a reviewer to recognize as an error.

### 3. Scanned and mixed PDFs have major page-coverage limits

`backend/app/kit/pdf_input.py:188` supplies only pages 1–8 as scan images. A generated 12-page image-only PDF with its schedule on page 10 produced `scanned=True`, image pages `[1,2,3,4,5,6,7,8]` and no table pages. Neither extraction nor review receives that schedule.

Scanned status is determined across the entire document rather than per page. A readable text protocol containing image-only schedule pages can therefore also miss those images. Schedule detection requires at least five recognized tick marks plus a recognized heading or continuation. It caps detected table pages at 10 and rendered table images at 5. Alternative table symbols, languages, dense layouts and small footnotes need explicit coverage testing.

Long text protocols normally send the full text up to the configured character limit. Larger documents use selected pages; a model context rejection also falls back to selection. The latter emits a warning, but neither path proves every relevant footnote was retained.

### 4. A successful second model call does not establish completeness

`backend/app/kit/prompts.py:48` asks the model to check existing visits. `backend/app/kit/analyze.py:23` only applies corrections to already-known scheduled visit IDs. It cannot add an omitted visit, event or question, and the review prompt does not include the full event definitions or visit inclusion conditions.

Reproduction: a second model reply of `{}` yields `corrections: []`, no warnings and stage text `all visits match`. The Add Trial screen then uses `Matches the tables` for an empty correction list. A skipped cross-check is warned about but still returns a usable model, and the save endpoint does not require verification to have succeeded.

The question modal does require deliberate answers on the first review, which is useful. However, it only asks questions the model generated. The prompt requests at most six review questions. The API independently accepts absent review answers by using defaults when the answer dictionary is empty; it does not validate full review-answer completeness when the dictionary is partial.

### 5. Open-ended treatment does not extend its planning horizon

`frontend/src/features/kit/schedule.ts:132` expands a fixed count, clamped to 1–200 for scheduled visits. `open_ended` marks the final row but does not move or expand the horizon after completion. Event repeats use a separate 1–60 cap.

Reproductions:

- A six-occurrence, open-ended, 21-day repeating treatment still has only six rows after all six have actual dates. No seventh occurrence is generated.
- A 365-occurrence daily repeat returns only 200 rows.
- A repeat count of zero returns one occurrence.

`frontend/src/features/kit/participant.ts:72` can classify a schedule whose displayed visits are all done as completed. Thus exhausting an open-ended preview can appear to finish participation even without an ending event.

### 6. Unanswered decisions can appear as scheduled appointments

`computeSchedule` retains visits whose condition is unknown and supplies `dependsOnDecision: true`. `frontend/src/features/kit/describe.ts:105` still gives such a visit status `scheduled` when its anchor date is known. The detailed timeline adds a conditional tag, but `frontend/src/features/kit/patientVisits.ts:14` flattens the visit to the legacy patient row shape and drops the decision flag.

Reproduction: a visit gated by an unanswered `ok = yes` decision has a date, status `scheduled` and `dependsOnDecision: true`. The simplified patient dashboard/calendar representation cannot convey the unresolved dependency. Event visits also set their row condition to `yes` after only excluding a definitely false condition.

### 7. Treatment discontinuation and study termination are not separate states

`frontend/src/features/kit/schedule.ts:98` selects the earliest event marked `ends_schedule`. At line 168 it cancels every later uncompleted scheduled visit, without a phase or visit-class exemption.

Reproduction: treatment on day 30 and safety follow-up on day 60 are both cancelled by an ending event on day 14. A model can explicitly add replacement follow-up visits under the event, but the engine does not infer which follow-up must continue. The representation needs separate meanings for stopping treatment, stopping selected assessments, withdrawing consent and ending all follow-up.

### 8. Calendar and conditional logic are limited

The prompt in `backend/app/kit/_prompt_text.py` says to use 30 days per month unless defined, a typical screening-to-Day-1 offset when needed, and six preview cycles by default. These are assumptions, not universally correct protocol rules.

The engine adds whole calendar days. It has no native calendar-month recurrence, holiday calendar or business-day operator. Its weekend suggestion is a nearby weekday within the existing window, not a site holiday policy.

`condState` supports membership in enumerated values, combined with AND. Numeric thresholds, compound OR expressions across different questions, calculated laboratory criteria and time-varying clinical states are not general native predicates. A reviewer can reduce some rules to a manually answered boolean, but that does not automate the rule. Repeated re-randomization, dose holds/restarts, adaptive cohorts and rescreening episodes need additional modeling and evidence before claiming support.

### 9. Intra-day functionality is incomplete in the active workflow

`frontend/src/features/kit/describe.ts:77` calculates timepoints relative to one dose time per visit occurrence. Its procedure-name mapping does not evaluate procedure conditions. A conditional PK procedure remained in the day plan in a local function check.

The current `KitPatientView` visit sheet records a date and displays visit-level assessments. It does not expose dose-time entry or the computed timepoint plan. Multiple dose anchors per day and sample-level completion tracking are not represented by the current record.

### 10. Replacing a trial model can change existing patient schedules

`backend/kit_routes.py:254` replaces the trial's single model document. `_kit_payload` at line 376 always reads that latest model for every patient. Patients are not pinned to an immutable kit model version or content hash, and this path does not use the existing UCTSM amendment adoption mechanism.

Consequently, changing the model can reinterpret existing answers and actual keys or change future dates for all enrolled patients without individual reviewed adoption. This is inferred directly from the storage/read path; no database mutation was performed to test it.

### 11. Expected protocol identity is recorded but not checked in this path

The analysis endpoint stores `expected_protocol_id` at `backend/kit_routes.py:171`; no later use exists in that module. Add Trial passes the entered identifier, but only replaces it with the extracted number when the entered value is empty. The kit save path does not compare the extracted protocol number with the target trial either.

This allows a mismatch to pass through the kit path on static inspection. It needs a deterministic identity/version check before a schedule is attached to a trial. Existing identity protections elsewhere in the application should not be assumed to cover this independent route.

### 12. Patient display and backend operations have different schedule sources

Kit dates are computed on the client from the patient record and trial model. The legacy materializer skips kit patients (`backend/server.py:1607`), while visit reminders and several backend reports continue to read `visit_instances`. A correct patient timeline therefore does not establish correct backend reminders or visit-based reporting.

`frontend/src/features/kit/patientVisits.ts:24` also catches all kit-fetch failures and falls back to legacy visits; schedule computation errors return an empty list. These behaviors can make an unavailable or invalid kit schedule appear empty. Actual downstream failure rates were not measured.

## Verification performed

- `frontend`: `npm.cmd run test:kit` — **14 passed**.
- `backend`: `python -m pytest tests/test_kit_pipeline.py -q` — **18 passed**.
- `backend`: `python -m pytest tests/test_kit_participants.py -q` — **6 passed**, in a separate process with Mongo environment variables pointed at a local test name. These tests do not access the database.
- Additional read-only Python and Node stdin experiments executed the actual normalization, analysis and scheduling functions. Pure validator functions were compiled from their source AST to avoid importing database application state for the targeted checks. The existing participant test module was separately run through its normal import path.
- PDF page coverage was checked with an in-memory generated image-only PDF. Model responses were stubbed for verification-result handling; no paid model call was made.

Passing these 38 existing tests does not establish extraction accuracy. The pipeline tests use fake model replies, shipped sample models and generated documents. This review did not run an independent, clinician-reviewed real-protocol benchmark across trial designs, languages and PDF layouts, so no defensible universal success percentage can be given.

## Recommended order of work

1. Fix event occurrence validation, silent semantic defaults, unresolved decision propagation and protocol identity checks. Require explicit unresolved/error states instead of generating a confident schedule from missing facts.
2. Replace the current match claim with a coverage result: every schedule page, visit column, marked assessment and footnote must be accounted for. Preserve page evidence and block approval for missing pages, malformed verification results or unresolved critical rules.
3. Correct repeat horizons and distinguish treatment stop from follow-up termination. Expose unsupported recurrence and condition types for review rather than approximating them.
4. Pin each patient to an immutable model version and connect changes to reviewed adoption. Connect kit schedules to backend reminders/reporting through an explicit shared evaluation or projection boundary.
5. Add representative protocol benchmarks with reviewed expected models and patient scenarios, including late dosing, progression, missing dates, withheld consent, repeatable events, long follow-up, amendments and scanned/mixed PDFs.

Keep the kit as a candidate scheduling foundation for explicitly supported patterns. Widen support based on demonstrated document and patient-scenario coverage; do not describe the current implementation as handling any protocol.
