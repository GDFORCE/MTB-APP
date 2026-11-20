# History-based visit intervals and confirmed study-wide events

20 November 2026. This report covers two new operations in the kit workflow, the synthetic scenarios run against them, and what remains open. It follows [kit-protocol-workflow-verification-2026-11-20.md](kit-protocol-workflow-verification-2026-11-20.md).

## Findings from the inspection

The earlier inspection was right on both counts:

- **History-based intervals were not supported.** A repeat could use fixed `every_days`, `stages`, `chain` and visit checks. Nothing could read a confirmed finding and the interval history, and nothing could apply a breakpoint. A treat-and-extend schedule could only be held as a blocked pattern rule.
- **Cross-site study events were partly supported.** Shared study dates (`scope: "study"` anchors) let a PI enter one date per workflow version, and that date released dependent visits. There was no audience selection by phase, no record of who confirmed the event and when it actually happened, no per-participant effect, no booking task, and no correction handling.

## What changed

### History-based intervals (`repeat.adaptive`)

An approved block can now compute each interval from the previous one and the visit history.

- **Inputs:** the finding confirmed at the previous occurrence, an optional external recommendation (for example a reading centre interval), and the history of earlier occurrences.
- **Calculation:** threshold steps (`add_days`, `set_days`, `keep`, optionally only after an extension), then the minimum and maximum limits, then a historical breakpoint. After N extension attempts, an extension is capped at the longest interval that ended with activity plus an offset. The cap is recorded the first time it is reached and continues to apply.
- **Target and window:** the next target counts from the previous occurrence's actual date or its target (`basis`). The window is applied separately. Target, booked date and actual date stay distinct, and the row reports `bookingStatus` and `deviation`.
- **Precedence:** when the calculated interval and the recommendation differ, the workflow picks `calculated`, `recommendation`, `shorter`, `longer` or `review`. `review` asks the PI to choose one of the two values. If the protocol does not say, clean-up leaves precedence unset and blocks the operation.
- **Missing inputs:** the affected occurrence is undated, shows as `waiting` and names what it needs, including the role allowed to enter it. Nothing after it is generated. Fixed visits stay dated.
- **Corrections:** a corrected or late input re-dates the dependent future occurrences. Recorded actuals are kept. A completed visit whose new target puts it outside its window is flagged as a deviation and never moved.

A visit can also carry `combine_with`. When one of its occurrences falls inside a fixed visit's window (for example Month 12), the two are listed as one visit with the union of their assessments, so tasks are not duplicated. The fixed visit keeps its own baseline-anchored target and window.

### Confirmed study-wide events (`phases`, `study_events`)

- **Confirmation:** an authorised study user (by default sponsor or CRO, set by `confirm_roles`) confirms the event with the date it actually happened and any fields the workflow defines, such as the final participant's subject ID. The server stores `effective_date` apart from `confirmed_at` and `confirmed_by`.
- **Audience:** the server reads each participant's phase on the effective date from recorded facts only, using their own workflow version.
  - The first matching action applies.
  - A participant whose facts are missing is listed as `unresolved` and is not guessed in or out. For example, Visit 7's window closed before the event but no date was recorded.
  - A participant whose version lacks the action is listed as `unmatched`.
  - Participants in a phase listed in `review_phases` are listed as `to_review`.
- **Storing the effects:** there is one effect per participant, keyed `<confirmation id>:<patient id>`. It records the state, the action, the reason and the calendar items changed.
  - Re-sending a confirmation or retrying processing never duplicates an effect or a reminder.
  - A failure for one participant is recorded, and `/process` finishes the rest.
  - Each closeout sends one in-app notification to that participant's PI and CRC. The notification ID is deterministic, so a retry does not resend it.
- **Calendar:** effects are delivered inside the participant record (`participant.studyEffects`) on every fetch, so a device that was offline gets them on its next fetch. The engine removes superseded pending occurrences and never removes a recorded one. An added visit waits for the site's booking of the next available date.
- **Permissions:** confirming, retrying and correcting need the study-level role and trial access. Booking needs PI or CRC access to that participant (`_require_patient`, site-scoped). PI and CRC listings show only their own participants.
- **Corrections:** `PUT …/kit-study-events/{id}` with `preview: true` returns who would be added, removed or kept, the completed closeouts that are kept, and any booked visits affected. A booked closeout that would no longer apply needs explicit acknowledgement. It is then kept as `review`, visible to its site with a note, until the site cancels it, which makes it `withdrawn` and brings Visit 8 back. Completed closeouts are never changed.

### Generation, review and approval

- **Extraction prompt:** `prompts.py` describes `repeat.adaptive`, `combine_with`, `phases` and `study_events`, and asks for automatic detection to be reported as `cannot_represent`.
- **Clean-up:** `normalize.py` calls `app/kit/adaptive.py` and `app/kit/study_events.py`. An operation that is missing a stated part keeps its source and gets `blocked` plus an open point quoting the protocol. `patterns.blockers` lists it as `ADAPTIVE_INTERVAL` or `STUDY_EVENT`, so approval fails, `/patients/invite` and move-to-latest return 409, and the engine never runs it. A `detect` description becomes a `cannot_represent` open point.
- **Workflow blocks:** `workflow.py` builds the blocks the study team reviews: visit blocks with a `summary` (condition, inputs, timing, branches, breakpoint, precedence, outputs), plus new `phase` and `study_event` blocks with role-specific prompts. `WorkflowReview` shows these summaries, and its sample calendar accepts sample findings.
- **Saved data:** existing approved workflows and patient records are unchanged. The new fields appear only when present. Versions, pinning and move-to-latest work as before, and recorded inputs travel with the patient.

### Files

| Area | Files |
| --- | --- |
| Engine (device) | `frontend/src/features/kit/adaptive.ts` (new), `schedule.ts`, `describe.ts`, `index.ts`, `participant.ts`, `patientRows.ts` (new pure patient-calendar consumer), `patientVisits.ts` |
| Screens | `KitPatientView.tsx` (inputs, waiting needs, deviations, closeout booking), `StudyEventsPanel.tsx` (new; on `sponsor/kit-schedule.tsx`), `WorkflowReview.tsx`, `clinical/kit-patient.tsx`, `api.ts` |
| Backend | `backend/app/kit/adaptive.py` (new), `study_events.py` (new), `normalize.py`, `workflow.py`, `patterns.py`, `ledger.py`, `prompts.py`, `kit_routes.py` |
| Routes | `POST /api/patients/{id}/kit/observations`; `POST/GET /api/trials/{id}/kit-study-events`; `POST …/{event}/process`; `PUT …/{event}` (correction, `preview`); `PUT /api/patients/{id}/kit/study-effects/{effect}/booking` |
| Data | `patients.kit.observations`; Mongo `kit_study_events`, `kit_study_effects`; reminders in `notifications` |
| Tests | `frontend/tests/kit-adaptive.test.ts`, `kit-study-events.test.ts`; `backend/tests/test_kit_adaptive_study.py`, `test_kit_adaptive_study_e2e.py`, `_kit_calendar.py`; fixtures `backend/tests/fixtures/kit_synthetic/` (+ `scripts/kit_synthetic_fixtures.py`) |

## Synthetic inputs and their assumptions

The protocol for NCT02130024 (RIVAL) is not in the repository; it was later found on this machine (see the offline upload section). Only its public registry summary was available, which confirms three monthly loading doses, 2-week extensions up to 12 weeks, and Month 12 and Month 24 reading-centre assessments. The treat-and-extend fixture is therefore **synthetic**. Its assumptions are written into `rival_treat_extend.raw.json`:

- One sign of activity shortens the interval by 2 weeks, and two or more signs return it to 4 weeks.
- The fourth-extension breakpoint caps the interval at the longest interval with activity minus 2 weeks.
- Injection windows are ±7 days. Month 12 is Day 364 and Month 24 is Day 728, each ±14 days.
- An injection due inside the Month 12 window is combined with it on the injection's own date.
- The calculated interval books the visit.

The P16 fixture is **synthetic (P16-style)**, built from the summary in `docs/protocol-schedule-schema-analysis.md` and the CBS corpus case (page 58, "next available date"). It records one open interpretation as an assumption: participants between Visit 6 and Visit 7 on the event date are listed for review, because the summary does not say whether they still enter extension.

All subject IDs, sites, dates and results are fictional. Expected values in the tests were worked out by hand from the fixture rules.

## Scenario results

Level: **U** engine unit test (TypeScript, the app's engine), **R** route test with an in-memory database and the real kit routes, **E** end to end on the local MongoDB replica set, using the real `server.app`, registration through the OTP routes, sign-in tokens and site-scoped access. In R and E, the calendars are computed by the app's engine and patient consumer (`kitVisitRows`) from the server's payload.

| ID | Synthetic input | Expected | Observed | Level | Status |
| --- | --- | --- | --- | --- | --- |
| A1 | Gap 8 wk, 0 signs at INJ 5 (5 Jun 2026 history) | 10 wk, INJ 6 on 2026-08-17, window 08-10 to 08-24 | as expected | U, R, E | Pass |
| A2 | Gap 8 wk, 1 sign | 6 wk, 2026-07-20 | as expected | U | Pass |
| A3 | Gap 8 wk, 2 and 3 signs | 4 wk, 2026-07-06 | as expected | U | Pass |
| A4 | 1 sign at 4 wk; 0 signs at 12 wk | 4 wk (limited by min); 12 wk (limited by max), 2027-02-01 | as expected | U | Pass |
| A5 | 3 earlier extension attempts, activity at 10 wk, 4th attempt | capped at 8 wk, 2026-12-07 | as expected | U | Pass |
| A5b | Activity at 4, 10 and 8 wk, then 4th attempt | cap from longest (10 − 2 = 8 wk): 8 wk, then extension to 10 wk held at 8 | as expected (4 or 6 wk would show first or latest was used) | U | Pass |
| A6 | 5th attempt after the cap; then 1 sign | 8 wk (recorded cap); 6 wk (shortening allowed) | as expected | U | Pass |
| A7 | No finding at INJ 5; recommendation precedence without recommendation; gap in finding history | INJ 6 waiting, needs the finding (PI) or the recommendation (PI/CRC); later intervals wait for the missing earlier finding; M12 and M24 stay dated; patient sees no undated visit | as expected | U, E | Pass |
| A8 | Calculated 10 wk vs recommended 12 wk | `calculated`: books 10 with a note; `review`: waits for PI choice of 70 or 84 days, then 84 → 2026-08-31; equal values ask nothing; `shorter` with 8 wk → 2026-08-03 | as expected | U | Pass |
| A9 | Correct INJ 5 finding 0 → 1 after INJ 6 was generated | INJ 6 target 2026-07-20 (window 07-13 to 07-27); INJ 1–5 actuals unchanged; if INJ 6 was already done on 08-17, it stays and is flagged outside the window | as expected | U, R | Pass |
| A10 | Book INJ 6 on 08-20 and 08-28; record 08-28 | target stays 08-17; `in_window`, then `outside_window`; actual 08-28 flagged; INJ 7 counts from the actual (2026-11-20) | as expected | U | Pass |
| A11 | INJ 8 due 2027-01-18 (inside Month 12 window) vs 2027-02-01 (outside) | One combined visit with the assessments listed once, Month 12 not listed again; otherwise Month 12 alone on 2027-01-04; Month 24 on 2028-01-03 either way; one imaging task | as expected | U | Pass |
| A12 | Inputs in any order; replay same id; older confirmation delivered late; earlier-visit input out of order | identical calendars; `replayed` (revision and audit unchanged); `ignored_older`; no duplicate visits; newer input kept | as expected | U, R, E | Pass |
| B1 | Sponsor confirms "final participant completed Visit 6", effective 2026-09-14, C-301 | closeout for exactly A-101, B-201, C-302 (three sites); C-301 gets Visit 7 then exit; one task each; 6 reminders | as expected | E | Pass |
| B2 | Finished A-102, withdrawn B-202, in treatment A-103 | excluded; C-301 keeps Visit 7 (2026-09-21), Visit 8 superseded, no closeout | as expected | E | Pass |
| B3 | Confirmed on the test day for 2026-09-14; B-203 entered extension 09-16; B-204 finished Visit 8 on 09-21 | B-203 listed for review (not closed out); B-204 `already_completed`, Visit 8 record intact, no task | as expected | E | Pass |
| B4 | A-101 closeout with no date | waiting, needs a booking (PI/CRC), hidden from the patient; site CRC books 2026-11-25 → staff and the patient's own `/me/kit` show it | as expected | E | Pass |
| B5 | Processing fails for B-201, then retry; replay the confirmation | `partial` with B-201 listed, then `applied`; 11 effects, 6 reminders after replay and reprocessing; same id with different content → 409 | as expected | E | Pass |
| B6 | Site device fetched C-302 before the event | next fetch shows Visit 8 superseded and the closeout | as expected | E | Pass |
| B7 | Confirm as site PI or CRC, or as another sponsor organisation; book A-101 as site B CRC | 403, 403, 403; booking 403; unknown subject and future date → 422; nothing stored | as expected | E | Pass |
| B8 | A-104 pinned to workflow v1 (no closeout section) | `unmatched` ("version has no action"); calendar unchanged | as expected | E | Pass |
| B9 | Correct effective date to 2026-07-01 after bookings and one completed closeout | preview lists C-302 booking and B-201 completed; refused until acknowledged; then C-302 `review` (Visit 8 back, booking flagged), B-201 kept, A-101 keeps its booking, A-102 `already_completed`, B-202 `ended`; site clears C-302 → withdrawn | as expected | E | Pass |
| B10 | Automatic final-participant detection | Not implemented: prerequisites absent (see below); a `detect` description becomes a review issue | review issue raised; approval refused until resolved | R | Not implemented (by design) |
| C1 | One synthetic protocol with both operations, uploaded through `/api/kit/analyze` with controlled provider responses | survives model build, cross-check, events check, draft, approval, enrolment, inputs and calendar; study closure supersedes the remaining injections and assessments; exit visit booked | as expected | R, E | Pass |
| C2 | Precedence unstated, unsupported rule shape, automatically detected event | open points with quotes and pages; approval 422; draft keeps the evidence; enrolment 409; blocked interval never dates anything; fixing precedence in the block leaves the unsupported rule blocking | as expected | R | Pass |
| C3 | Day 0, Day 28/56/70 ±4; rebook Day 28 inside and outside the window, then record it late | targets and windows of all three unchanged; booking and actual flagged outside window | as expected | R (engine) | Pass |
| C4 | Progression logged 2026-02-20 during 21-day cycles | no cycles after C3; follow-up on 06-22, 09-14, 12-07 kept | as expected | R (engine) | Pass |
| C5 | CRD/09: Period II booking at 20 and 21 days; dose times | waits until a booking at least 21 days out; sample clock dates only after the dose time is entered | as expected (also `test_kit_workflows.py`) | R | Pass |
| C6 | Amend max interval to 8 wk with a patient on v1 | patient pinned; preview shows 2026-08-03; move keeps inputs and actual versions; new calendar uses v2 | as expected | R | Pass |
| C7 | Both capabilities: approval → invite → OTP registration → PI/CRC inputs → study closure → booking → the participant's own `/me/kit` | as above, with role and site checks | as expected | E | Pass |

## Commands and results

Run from `frontend/`:

- `npm run test:kit`: 167 tests, 167 passed. This includes the new `kit-adaptive.test.ts` (17) and `kit-study-events.test.ts` (7). Three golden snapshots gained one `deviation` line each for visits already recorded outside their windows. Nothing else in the snapshots changed.
- Scoped typecheck: `npx tsc -p <tsconfig listing the touched files>` exited 0. The whole-project `tsc` runs out of memory on this machine.

Run from `backend/` with the local replica set (`MONGO_URL=mongodb://127.0.0.1:27017/?replicaSet=rs0`, `DB_NAME=mtb_kit_e2e`):

- `python -m pytest -q -p no:cacheprovider` over the 14 `tests/test_kit_*.py` files, including the new two: **201 passed**.
- `python scripts/kit_synthetic_fixtures.py --check`: the normalized fixtures are current.
- Server-wide checks: `test_pdf_server_ratchet.py`, `test_architecture_boundaries.py`, `test_pdf_security.py`, `test_admin_api.py`, `test_authz_scoping.py`, `test_uctsm_authz_manifest.py`, `test_mtb_backend.py` and `test_patient_phone_only.py`. Four failures, none from this change:
  - `test_pdf_server_ratchet.py`: `server.py` grew from 11,570 to 11,627 lines in earlier uncommitted work. This change does not edit `server.py`.
  - `test_admin_api.py` (2 tests) and `test_forgot_reset_flow`: they return 502 because outbound email is not configured here.
- The full curated UCTSM gate in `MTB_IMPLEMENTATION_HANDOFF.txt` section E was not run. This change does not touch UCTSM code.

Provider: every extraction run used controlled responses. No live model call was made, so no live extraction result is reported.

## Offline upload of the real NCT02130024 protocol (added later on 20 November)

The protocol PDF is on this machine at `D:\NCT02130024__Prot_000.pdf` (CRFB002AAU17, Version 02, 19 January 2015, 51 pages). It is not in the repository. Uploading it now works with no AI provider at all.

- **Offline mode** (`KIT_OFFLINE=1`, or `KIT_OFFLINE_DIR=<folder>`; `backend/app/kit/offline.py`). The upload runs every real step: PDF reading, crop handling, table location (it finds Table 6-1 on page 32), clean-up, quote verification and workflow building. Only the three requests a model would answer come from files prepared for that exact file, matched by SHA-256 in `backend/app/kit/offline_answers/manifest.json`. A file with no prepared answer is refused with `no_offline_model`. `backend/start-backend-offline.cmd` starts the local backend this way.
- **The prepared RIVAL analysis** (`offline_answers/nct02130024.model.json`) was written by the coding assistant from pages 8, 19 and 24–37, in the shape the model prompt asks for:
  - Treatment visits with the inject-and-extend rule from section 5.5.4.1:
    - 4 to 12 weeks; no sign extends by 2 weeks, one sign reduces by 2 weeks, two or more go back to 4-weekly.
    - Break point at the fourth extension.
    - The reading-centre interval replaces the investigator's interval when it arrives.
    - Last study injection at Week 103.
  - Plasma VEGF visits on Weeks 5 and 9, and Month 12 and Month 24 at ±6 days. An injection due in the Month 12 window is given at the Month 12 visit.
  - Withdrawal with an Early Discontinuation visit 30 ± 7 days after the last study visit.
  - Sponsor termination (section 5.5.11) as a study-wide event, with an Early Discontinuation visit as soon as possible.
  - Its seven interpretations are open points quoting the protocol, and every quote was found in the PDF text.
- **What the real protocol changed compared with the synthetic fixture:**
  - Month 12/24 windows are ±6 days, not ±14 (Amendment 2, page 8).
  - The reading centre's recommendation decides once given. That needed a new precedence, `recommendation_if_given`: the calculated interval books the visit provisionally until then.
  - The break point counts activity "in any of the three previous extensions". The breakpoint now states which earlier findings count (`count_activity_at`: `extended_intervals` or `any_interval`). The RIVAL analysis reads it as `extended_intervals`. Under the other reading, activity during the loading phase would cap the fourth extension at 4 weeks instead of letting it reach 12.
  - Injections stop at Week 103.
- **Test:** `backend/tests/test_kit_offline_rival.py`, 4 passed. It runs on the local replica set and skips if the PDF is absent. Building a provider client fails the test.
  1. Uploads the real PDF bytes through `/api/kit/analyze`.
  2. Checks the stage details (51 pages, tables on page 32) and that approval is refused until the seven interpretations are recorded, then approves.
  3. Downloads the stored PDF intact.
  4. Enrols a synthetic participant through OTP registration.
  5. Records the findings and the reading-centre interval, and checks the hand-worked dates on the staff calendar and the participant's own calendar.
  6. Checks the break point and Month 12 combination on the approved model.
  7. Confirms a sponsor termination and has the site book the Early Discontinuation visit.

  It writes no events-check answer, so the analysis notes that withdrawal and the checklist were not reported on.

## Open items

- **B10, automatic detection:** not implemented. The repository lacks three things it needs:
  - A reliable study-wide enrolment-closure record. `trials.recruitment_status` is free text that defaults to `recruiting`.
  - Server-side Visit 6 obligations for kit patients. Their dates are computed on devices, and no `visit_instances` exist, so the server sees only recorded dates and cannot tell an outstanding visit from late data entry.
  - Any per-site attestation that data is complete.

  A `detect` description is kept as a review issue, and the event is applied only on confirmation.
- **Protocol interpretations to confirm:**
  - The P16 participants between Visit 6 and Visit 7 on the event date (listed for review).
  - The seven RIVAL interpretations recorded as open points in the offline analysis (loading windows, break-point reading, reading-centre precedence, interval basis, Month 12 combination, VEGF visit dates, withheld doses). The synthetic RIVAL fixture remains a separate test input.
- **Not verified:**
  - Browser and mobile rendering of the new sheet sections and `StudyEventsPanel`. They are typechecked, not exercised in a browser.
  - Push or SMS delivery of the closeout reminders. Only in-app `notifications` rows are written, as for the rest of the app.
  - The server-side visit reminder worker, which still does not see kit patients.
  - A live provider run on a real protocol.
- **Limits:**
  - A phase can only start or end on a visit's first occurrence, an event or a date.
  - The server checks missing data only for visits it can date: those anchored to an entered or derived date, or following one.
  - History-based intervals and combined visits apply to scheduled visits, not to visits an event adds.
