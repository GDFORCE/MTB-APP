# Schedule Engine — How Every Case Is Handled

*Design reference for the in-app scheduling engine (UCTSM). Written 29 Sep 2026; updated the same day after all nine changes were implemented (uncommitted).*

This document explains, case by case, how MTB turns a protocol into a patient's
visit dates, and what the patient sees for every kind of protocol rule: fixed
visits, windows, surgery, repeats, cycles, branching, toxicity, confinement,
timed sampling, amendments and deviations.

Every case has a worked example with real dates. Each case is tagged:

| Tag | Meaning |
|---|---|
| ✅ **Today** | Works in the current engine and is covered by tests |
| ⚠️ **Today, untested** | The engine can express it, but no end-to-end test proves it yet |
| ✅ **Built (Change N)** | Implemented by change N (see [Part 5](#part-5--the-changes-to-build)) and covered by tests |

The system stays **inside the app**. There is no standalone engine. Everything
below runs in `backend/app/domain/schedule/` (the engine),
`backend/app/services/` (persistence and workflows), `backend/app/extraction/`
(reading protocols) and `frontend/src/features/uctsm/` (the screens).

---

## Contents

- [Part 1 — Principles](#part-1--principles)
- [Part 2 — The flow inside the app](#part-2--the-flow-inside-the-app)
- [Part 3 — Visit statuses](#part-3--visit-statuses)
- [Part 4 — Every case, with examples](#part-4--every-case-with-examples)
  - [A. Single-visit timing](#a-single-visit-timing)
  - [B. Windows](#b-windows)
  - [C. Anchors (the dates everything counts from)](#c-anchors-the-dates-everything-counts-from)
  - [D. Surgery and other procedures](#d-surgery-and-other-procedures)
  - [E. Repeating visits](#e-repeating-visits)
  - [F. Treatment cycles](#f-treatment-cycles)
  - [G. Depending on the previous visit](#g-depending-on-the-previous-visit)
  - [H. Branching](#h-branching)
  - [I. Conditions and toxicity](#i-conditions-and-toxicity)
  - [J. Inpatient confinement](#j-inpatient-confinement)
  - [K. Timed activities, PK and time zones](#k-timed-activities-pk-and-time-zones)
  - [L. Unscheduled visits and visit modes](#l-unscheduled-visits-and-visit-modes)
  - [M. Protocol amendments and reassignment](#m-protocol-amendments-and-reassignment)
  - [N. Deviations](#n-deviations)
  - [O. When the protocol contradicts itself](#o-when-the-protocol-contradicts-itself)
- [Part 5 — The changes to build](#part-5--the-changes-to-build)
- [Part 6 — Regression test matrix](#part-6--regression-test-matrix)

---

## Part 1 — Principles

1. **The AI understands; the engine calculates.** The AI reads the protocol and
   writes down *what it says*, with a page citation. It never produces a
   patient date. Deterministic code turns those statements into a schedule, and
   the engine computes dates from it.
2. **Never guess.** If the protocol does not state something — a window, which
   date counts as Day 1, whether a repeat restarts after a delay — the value is
   marked `UNRESOLVED` and a person decides. A visit with an unknown rule gets
   no date, so it can never be due, overdue or reminded about.
3. **Planned is not actual.** A planned surgery date may place the pre-op
   visits. It must never date the post-op visits as if surgery had happened.
4. **Combine rules, don't write one algorithm per study type.** Surgery,
   crossover, oncology cycles and long follow-up are all built from the same
   parts: anchors, timing, windows, repeats, applicability, conditions and
   blocks.
5. **History is protected.** Recalculating a schedule never moves or deletes a
   visit that already happened.
6. **Every change to a live patient is previewed, confirmed and audited.**

---

## Part 2 — The flow inside the app

```text
Sponsor uploads protocol PDF (Add Trial, or trial page → Protocol & Schedule Versions)
   │
   ▼
1. READ          Pages are read, with page numbers kept     app/extraction/documents.py
   │
   ▼
2. EXTRACT       AI writes cited "schedule facts"           app/extraction/  (to build)
   │             (what, when, relative to what, window,
   │              which arm, repeat, stop rule, page)
   ▼
3. COMPILE       Code turns facts into one canonical        app/extraction/  (to build)
   │             schedule; contradictions → UNRESOLVED
   ▼
4. VALIDATE      Blocking checks: unknown anchor, no        domain/schedule/validator.py
   │             Day 1, unclear dependency, no evidence…
   ▼
5. REVIEW        Sponsor/PI reviews the draft table,        sponsor/protocol-schedule
   │             resolves open items, approves              clinical/protocol-review
   ▼
6. ENROL         CRC adds patient, chooses arm/cohort,      clinical/add-patient
   │             records registration date                  (ProtocolAssignmentFields)
   ▼
7. EVALUATE      Engine computes every visit's status       domain/schedule/evaluator.py
   │             and dates for this patient
   ▼
8. LIVE          Site records actual dates, surgery,        clinical/patient-schedule
                 responses, toxicity → preview → confirm    services/impact_service.py
                 → schedule recalculates → reminders        services/reminder_delivery.py
```

All steps now exist. Steps 2–3 are the extraction layer (Change 8):
`app/extraction/reader.py` (AI → cited facts), `facts.py` (the fact schema) and
`compiler.py` (facts → canonical schedule, no AI).

---

## Part 3 — Visit statuses

Every visit, for every patient, has exactly one status.

| Status | Meaning | Has a date? | Reminders? |
|---|---|---|---|
| `RESOLVED` | Date calculated | Yes | Yes |
| `WAITING_FOR_ANCHOR` | The date it counts from has not happened yet (e.g. surgery) | No | No |
| `WAITING_FOR_CONDITION` | A clinical trigger has not happened (e.g. progression) | No | No |
| `WAITING_FOR_ASSIGNMENT` ✅ 7 | The patient has not been randomised or assigned yet | No | No |
| `BLOCKED` | The visit it depends on has not happened | No | No |
| `NOT_APPLICABLE` | Not for this patient (other arm, other population) | No | No |
| `PAUSED` | Its treatment block is on hold | No | No |
| `CANCELLED` | Stopped by a condition before it happened | No | No |
| `AVAILABLE_ON_DEMAND` | Unscheduled visit a site can create | No | No |
| `UNRESOLVED` | A person must decide the rule | No | No |
| `COMPLETED` / `MISSED` | Recorded by the site | Actual date | — |

**Precedence.** When several rules touch one visit, the engine applies them in
one fixed order (`ScheduleEvaluator._evaluate_event`, then `_apply_block_state`):

```text
not applicable → on-demand → cancelled by condition → manual review →
waiting for condition/assignment → blocked by dependency → waiting for anchor →
dated → then block state: stopped → paused → resumed
```

So "Arm A only" beats everything, and a stopped treatment block cancels a visit
that would otherwise have a date.

---

## Part 4 — Every case, with examples

All examples use one patient unless stated otherwise:

| Fact | Date |
|---|---|
| Registration | Thu 01-Oct-2026 |
| First dose (Day 1) | Mon 05-Oct-2026 |
| End of therapy | Mon 07-Dec-2026 |

---

### A. Single-visit timing

#### A1. Fixed calendar date ✅ Today
> "Study close-out visit on 15-Jan-2027."

- **Rule:** `ABSOLUTE 2027-01-15`
- **Result:** `RESOLVED` on 15-Jan-2027 for every patient.

#### A2. Protocol day (no Day 0) ✅ Today
> "Day 8: vital signs and CBC." "Day −7: screening labs."

- **Rule:** `PROTOCOL_DAY day=8 from FIRST_DOSE`, counting convention `CLINICAL_DAY` (Day 1 is the anchor itself, so there is no Day 0)
- **Example:** Day 1 = Mon 05-Oct-2026 → Day 8 = **Mon 12-Oct-2026**; Day −1 = Sun 04-Oct; Day −7 = **Mon 28-Sep-2026**
- A rule that says "Day 0" under this convention is rejected when the schedule is built.

#### A3. Day 0 convention ✅ Today
> "Day 0 is the day of first dose; Day 7 follow-up."

- **Rule:** counting convention `ELAPSED_DURATION`; `OFFSET +7 days from FIRST_DOSE`
- **Example:** Day 0 = 05-Oct → Day 7 = **12-Oct-2026**
- **Safety:** the Day 1 anchor and convention must be stated on the schedule. If they are missing, approval is blocked (`UNRESOLVED_SCHEDULE_ORIGIN`). The engine never picks the anchor by its name.

#### A4. Offset from an anchor ✅ Today
> "Treatment visit 4 weeks after registration."

- **Rule:** `OFFSET +4 WEEK from REGISTRATION`
- **Example:** 01-Oct-2026 → **Thu 29-Oct-2026**

#### A5. Negative offset ✅ Today
> "ECG 7 days before first dose."

- **Rule:** `OFFSET −7 DAY from FIRST_DOSE`
- **Example:** 05-Oct → **Mon 28-Sep-2026**

#### A6. Target with a window ✅ Today
> "Visit 3: 4 weeks after first dose ± 3 days."

- **Rule:** `OFFSET +4 WEEK from FIRST_DOSE`, window `−3 DAY / +3 DAY`
- **Example:** target **Mon 02-Nov-2026**, allowed Fri 30-Oct → Thu 05-Nov

#### A7. Range with no single target ✅ Today
> "PET scan 12–16 weeks after completion of therapy."

- **Rule:** `RANGE +12 WEEK … +16 WEEK from END_OF_THERAPY`
- **Example:** end of therapy 07-Dec-2026 → window **Mon 01-Mar-2027 → Mon 29-Mar-2027**
- The screen shows the window. Reminders use the window start.

#### A8. "Within N days before" ✅ Today
> "Labs within 28 days before registration."

- **Rule:** `WITHIN 28 DAY BEFORE REGISTRATION`
- **Example:** window **Thu 03-Sep-2026 → Thu 01-Oct-2026**
- This is how every pre-treatment and eligibility test is handled. None of them may depend on first dose, which has not happened at registration.

#### A9. "No later than" ✅ Today
> "Safety follow-up no later than 30 days after last dose."

- **Rule:** `NO_LATER_THAN +30 DAY from LAST_DOSE`
- **Example:** last dose 07-Dec-2026 → latest **Wed 06-Jan-2027**, no earliest date

#### A10. Triggered by a clinical event ✅ Today
> "Survival follow-up 30 days after disease progression."

- **Rule:** `TRIGGERED by PROGRESSION, +30 DAY`
- **Example:** before progression → `WAITING_FOR_CONDITION`. Progression confirmed 15-Mar-2027 → **Wed 14-Apr-2027**

#### A11. Approximate wording ✅ Today (refused safely)
> "Approximately 3 months after treatment."

- **Rule:** `APPROXIMATE` → the engine refuses to date it → `UNRESOLVED`
- A reviewer converts it to a real rule, for example `OFFSET +3 MONTH`, window ±2 weeks, before approval.

#### A12. Timing the AI could not read ✅ Today
- **Rule:** `UNRESOLVED` with the source page attached
- **Result:** blocks approval until a reviewer writes the rule.

---

### B. Windows

#### B1. Window on a single visit ✅ Today
See A6.

#### B2. Window on a repeating visit ✅ Built (Change 3)
> "Every 3 months ± 14 days" works today.
> "Quarterly follow-up, within weeks 11–13 of each quarter" used to lose its window.

- **Change 3:** windows become their own rule, separate from timing. Every occurrence re-applies the window from its own target date.
- **Example after change:** quarterly from 07-Dec-2026, first target Sun 07-Mar-2027 with window `−14 / +14` → 21-Feb → 21-Mar-2027. The same window is re-applied to every later occurrence.

#### B3. Window only after, or only before ✅ Built (Change 3)
> "Day 15 visit, may be up to 3 days late but not early."

- **Rule after change:** timing `PROTOCOL_DAY 15`, window `−0 / +3`
- **Example:** Day 15 = Mon 19-Oct-2026, allowed 19-Oct → 22-Oct

#### B4. No window stated ✅ Today
- The window stays **empty**. It is never defaulted to ±0 or ±3 days. The
  screen shows "no window stated", and the reviewer may add one citing the
  protocol.

---

### C. Anchors (the dates everything counts from)

#### C1. Anchor known at enrolment ✅ Today
> Registration, randomisation, consent.

- Entered by the CRC in Add Patient. The patient's schedule is calculated immediately.

#### C2. Anchor taken from a visit ✅ Today
> "Follow-up 30 days after the last dose."

- **Rule:** anchor `LAST_DOSE` is derived from event `TREATMENT_DOSE`, selecting the `LAST` occurrence (or `FIRST` for first dose)
- **Example:** the last dosing visit is dated 07-Dec-2026, so `LAST_DOSE` = 07-Dec-2026

#### C3. Anchor taken from a condition ✅ Today
> "Date of progression."

- The anchor is set when a PI confirms the condition "Progression, occurred 15-Mar-2027".

#### C4. Anchor that is never defined ✅ Today
- Any rule that points to an anchor the schedule does not define → `UNKNOWN_ANCHOR` → approval blocked.

#### C5. Correcting an anchor ✅ Today
> The site entered surgery as 17-Dec but it was actually 18-Dec.

- The change goes through preview → confirm. The anchor is shown as `CORRECTED` and the audit keeps the old value. Only visits that depend on that anchor move; completed visits never move.

#### C6. Planned vs actual anchors ✅ Built (Change 4, safety)
Every anchor record has a state: `PLANNED`, `ACTUAL`, `CONFIRMED` or `CORRECTED`.
Every rule declares which states it accepts:

| Rule | Accepts |
|---|---|
| Pre-op visit | `PLANNED_OR_ACTUAL` |
| Post-op visit | `ACTUAL` only |

The default is **`ACTUAL`**: a planned value is used only where a reviewer (or
the extractor, when the protocol says "planned") marks the rule
`PLANNED_OR_ACTUAL`. A date is planned when it is recorded with a planned source
type (`PLANNED_PROCEDURE`, `PLANNED`, `EXPECTED`, `SCHEDULED`...); the app's
"Record a Reference Date" card makes the site choose Planned or Actual.

> **Behaviour change:** an anchor derived from a visit (e.g. `LAST_DOSE`) is
> actual only once that visit is *recorded*. "Follow-up 30 days after last dose"
> therefore waits for the last dose to be recorded, instead of being dated from
> the calculated dose date, unless its rule accepts a planned date.

---

### D. Surgery and other procedures

Surgery is not special code. It is an anchor plus timing rules plus conditions.
One worked pathway (neoadjuvant → surgery → adjuvant):

| Protocol says | Rule |
|---|---|
| Last neoadjuvant dose | Anchor `LAST_NEOADJ_DOSE` from the last neoadjuvant dosing visit |
| "Surgery 3–6 weeks after the last neoadjuvant dose" | Surgery event: `RANGE +3 WEEK … +6 WEEK from LAST_NEOADJ_DOSE`; creates anchor `SURGERY` |
| "Pre-op assessment within 14 days before surgery" | `WITHIN 14 DAY BEFORE SURGERY`, accepts `PLANNED_OR_ACTUAL` |
| "Post-op visit 30 days after surgery" | `OFFSET +30 DAY from SURGERY`, accepts `ACTUAL` only |
| "Adjuvant therapy starts 4–8 weeks after surgery" | `RANGE +4 WEEK … +8 WEEK from SURGERY`, accepts `ACTUAL` only |
| "If progression before surgery, go to off-study follow-up" | Condition `PROGRESSION` → `CANCEL` surgery, post-op and adjuvant; `ADD` survival follow-up |

Last neoadjuvant dose: Mon 16-Nov-2026.

#### D1. Surgery not yet scheduled ✅ Today
- Surgery window shown: **Mon 07-Dec-2026 → Mon 28-Dec-2026**
- Pre-op, post-op and adjuvant: `WAITING_FOR_ANCHOR` — "waiting on surgery"

#### D2. Surgery planned for 14-Dec-2026 ✅ Built (Change 4)
- Pre-op window: **Mon 30-Nov → Mon 14-Dec-2026**, `RESOLVED`, reminders on
- Post-op and adjuvant: still `WAITING_FOR_ANCHOR`, because they need the actual date
- Anchor status shown: `PLANNED`

#### D3. Surgery happens 17-Dec-2026 ✅ Today
- Anchor → `ACTUAL` (shown as `CONFIRMED` once the site's date is confirmed)
- Post-op: **Sat 16-Jan-2027**
- Adjuvant start window: **Thu 14-Jan → Thu 11-Feb-2027**

#### D4. Surgery delayed to 04-Jan-2027 ✅ Today
- The site corrects the surgery date → preview shows what moves → confirm (anchor shows `CORRECTED`, old value kept in the audit)
- Post-op → **Wed 03-Feb-2027**; adjuvant → **Mon 01-Feb → Mon 01-Mar-2027**
- The pre-op visit already completed on 10-Dec stays completed.

#### D5. Progression before surgery ✅ Tested end to end
- The PI confirms progression on 01-Dec-2026
- Surgery, post-op and adjuvant → `CANCELLED` ("cancelled by condition PROGRESSION")
- Survival follow-up is added (A10): **Thu 31-Dec-2026**
- Note: pre-op is not in the cancelled list here, so if a planned surgery was already recorded, pre-op stays due. A protocol that cancels pre-op too must list it.
- Nothing is overdue and no reminders are sent for the cancelled branch.

The same pattern covers any planned procedure: transplant, device implant,
radiotherapy start, admission.

---

### E. Repeating visits

#### E1. Fixed count ✅ Today
> "Every 3 weeks for 6 cycles."

- **Rule:** repeat every 3 weeks, `COUNT 6`

#### E2. Until a date ✅ Today
> "Monthly until 30-Jun-2028."

- **Rule:** stop at `DATE 2028-06-30`

#### E3. Until an event or condition ✅ Today
> "Every 12 weeks until progression."

- **Rule:** stop on `CONDITION PROGRESSION`. Repeats stop from the confirmed progression date; earlier repeats stay.

#### E4. For a duration after an anchor ✅ Today
> "Every 3 months for 2 years after end of therapy."

- **Rule:** stop `RELATIVE +24 MONTH from END_OF_THERAPY`, with an explicit **inclusive** flag
- If the protocol does not say whether the 24-month visit is included, the flag is left unset and approval is blocked until someone decides.

#### E5. Open-ended ✅ Today
> "Every 12 weeks while on study."

- **Rule:** stop at `HORIZON`
- Only the next few visits are created (a rolling window). The screen says "continues every 12 weeks; no stated maximum". It never shows 12 rows as if the study ended there.

#### E6. Calendar month from a month-end date ✅ Built (Change 1)
> "Monthly from 31-Jan-2027."

| Occurrence | Before (wrong: each repeat was added to the previous date) | Now (counted from the start date) |
|---|---|---|
| 1 | 28-Feb-2027 | 28-Feb-2027 |
| 2 | **28-Mar-2027** ❌ | **31-Mar-2027** |
| 3 | **28-Apr-2027** ❌ | **30-Apr-2027** |
| 4 | **28-May-2027** ❌ | **31-May-2027** |

#### E7. "Every 4 weeks" is not "monthly" ✅ Today
> "Every 4 weeks from 31-Jan-2027."

- **Rule:** interval `4 WEEK` (fixed 28 days), not `1 MONTH`
- **Example:** 28-Feb → 28-Mar → **25-Apr-2027**

#### E8. Leap day ✅ Built (Change 1)
> "Yearly from 29-Feb-2028."

- Now: 28-Feb-2029, 28-Feb-2030, 28-Feb-2031, **29-Feb-2032**. It returns to the 29th because each date is counted from the original start date.

#### E9. Phased follow-up ✅ Built (Change 5)
> "Every 3 months for 2 years, then every 6 months to year 5, then yearly."

- **Rule:** `Event.recurrence_plan` with three segments. Each segment starts where the previous one ends. If the protocol doesn't say whether the boundary visit belongs to the earlier segment, approval is blocked (`UNRESOLVED_RECURRENCE_BOUNDARY`).

| Segment | Cadence | From → to | First dates |
|---|---|---|---|
| 1 | every 3 months | end of therapy → +24 months | 07-Mar-2027, 07-Jun-2027, 07-Sep-2027 … 07-Dec-2028 |
| 2 | every 6 months | +24 → +60 months | 07-Jun-2029, 07-Dec-2029, 07-Jun-2030 … |
| 3 | yearly | +60 months → end of study | 07-Dec-2032, 07-Dec-2033 … |

- The boundary visit (07-Dec-2028) belongs to exactly one segment, decided by the inclusive flag, so it is never duplicated and never dropped.

---

### F. Treatment cycles

21-day cycles, Cycle 1 Day 1 = Mon 05-Oct-2026. Nominal grid: C2 26-Oct, C3
16-Nov, C4 07-Dec.

#### F1. Nominal grid ✅ Built (Change 2)
> "Cycles repeat every 21 days from Cycle 1 Day 1."

- A delayed Cycle 2 does **not** move Cycle 3. C2 given 02-Nov (7 days late) → C3 stays **16-Nov-2026**.

#### F2. Next cycle follows the actual previous cycle ✅ Built (Change 2)
> "Each cycle starts 21 days after Day 1 of the previous cycle."

- Each cycle start becomes its own anchor: `CYCLE_1_START`, `CYCLE_2_START`, …
- **Rule:** C(n+1) = `ACTUAL(CYCLE_n_START) + 21 DAY`
- **Example:** C2 actual 02-Nov → C3 **Mon 23-Nov-2026**; C4 shows `BLOCKED` ("Waiting for Cycle 3 to be given") until C3 is recorded on 23-Nov → C4 **Mon 14-Dec-2026**
- Tip: record each cycle's start directly. Otherwise the k-th recorded Day-1 visit is taken as Cycle k, so a skipped recording mis-numbers later cycles.

#### F3. Cycle delay mode not stated ✅ Today
- Cycle dependency mode `UNCLEAR` → approval blocked (`UNCLEAR_DEPENDENCY_MODE`). The engine never assumes F1 or F2.

#### F4. Day within a cycle ✅ Today
> "Days 1, 8 and 15 of each cycle."

- Each day is a `CYCLE_DAY` rule inside the current cycle
- **Example:** C1: 05-Oct, 12-Oct, 19-Oct

#### F5. Activity only on some cycles ✅ Today
> "MRI every second cycle." "PK in Cycle 1 only."

- A rule on the *activity*, not a separate visit. C2 and C4 carry MRI; C1, C3 and C5 do not.

---

### G. Depending on the previous visit

| Mode | Protocol says | What happens |
|---|---|---|
| `NOMINAL` ✅ | "Visit 3 is 14 days after Visit 2's scheduled date" | Counts from the *planned* date |
| `ACTUAL_PREVIOUS_EVENT` ✅ | "14 days after Visit 2 is performed" | `BLOCKED` until Visit 2's actual date is recorded, then counts from it |
| `MANUAL` ✅ | "Timing per investigator" | `WAITING_FOR_ANCHOR` until a date is entered |
| `UNCLEAR` ✅ | Protocol doesn't say | Approval blocked |

**Example:** Visit 2 planned 19-Oct, done 22-Oct. Nominal mode → Visit 3 on
02-Nov. Actual mode → Visit 3 on **05-Nov-2026**.

---

### H. Branching

#### H1. Arm assigned at enrolment ✅ Today
> "Arm A (paclitaxel): Day 1 every 3 weeks. Arm B (nab-paclitaxel): Days 1, 8, 15; carboplatin Day 1."

- **Rule:** each visit carries `ARM IN [A]` or `ARM IN [B]`
- Patient in Arm B: the Arm A visits are `NOT_APPLICABLE` and never shown as due.

#### H2. Late randomisation (run-in first) ✅ Built (Change 7), tested end to end
> "4-week run-in; eligible patients are randomised at the end of run-in."

- Registered 01-Oct-2026. The run-in visits are dated normally.
- Before randomisation, **all arm visits are `WAITING_FOR_ASSIGNMENT`** ("Waiting for randomisation/assignment: Arm").
- Randomised to Arm B on Thu 29-Oct-2026 → preview → confirm:
  - Arm B C1: **29-Oct, 05-Nov, 12-Nov**; C2 D1 19-Nov-2026
  - Arm A visits → `NOT_APPLICABLE`
- For comparison, Arm A would have been 29-Oct, 19-Nov, 10-Dec.

#### H3. Response-based branch ✅ Today
> "CR → maintenance every 8 weeks; PD → off-study follow-up; SD/PR → continue."

- Conditions `RESPONSE_CR` and `PROGRESSION`, each with actions:
  - CR → `STOP_BLOCK` treatment, `ADD_EVENT` maintenance
  - PD → `STOP_BLOCK` treatment, `ADD_EVENT` survival follow-up
- Nothing changes until the PI **confirms** the response. A pending response does nothing.

#### H4. Nested groups ✅ Today
> "Part A → Cohort 1 or 2; Part B → Cohort 3."

- Add Patient offers only the cohorts under the chosen part.

#### H5. Population, age or sex rules ✅ Today
> "Pregnancy test for women of childbearing potential." "Extra ECG for patients ≥ 65."

- The rule is on the activity: `PATIENT_ATTRIBUTE wocbp = true`, `AGE GTE 65`
- An unknown attribute → `WAITING_FOR_CONDITION`, never assumed true or false.

#### H6. Investigator's choice ⚠️ Supported, not yet tested end to end
> "Surgery or definitive radiotherapy per investigator."

- Condition `LOCAL_THERAPY_CHOICE`. The PI records the choice; the other branch is cancelled.

#### H7. Crossover ✅ Tested end to end (AB and BA)
> "Sequence AB: Period 1 drug A, 14-day washout, Period 2 drug B. Sequence BA reversed."

- Periods are study periods (`epochs`); the sequence is a dimension (`SEQUENCE IN [AB]`)
- Period 2 Day 1 = Period 1 last dose + 14 days
- Identically named "Day 1" visits in each period stay separate.

---

### I. Conditions and toxicity

Conditions always go through: **pending → confirmed (active) → resolved**, and
may come back as **recurred**. A pending condition changes nothing.

#### I1. Hold, then resume on the original grid ✅ Today
> "Grade 3 toxicity: hold treatment until ≤ grade 1; resume on schedule."

- `PAUSE_BLOCK`, then `RESUME_BLOCK`, with resume mode `NOMINAL`
- **Example:** paused 26-Oct, resumed 09-Nov-2026. C2 (26-Oct) → `PAUSED`, not made up. C3 stays **16-Nov**.

#### I2. Hold, then restart from the resume date ✅ Today
- Resume mode `ACTUAL_RESUME`: everything after the resume shifts by the 14 paused days. C3 → **30-Nov-2026**, C4 → **21-Dec-2026**.

#### I3. Resume rule not stated ✅ Today
- Resume mode `UNCLEAR` → visits after the resume are `UNRESOLVED` until a person decides.

#### I4. Discontinue ✅ Today
> "Discontinue for progression, pregnancy, withdrawal, or an interruption over 2 weeks."

- `STOP_BLOCK` treatment from the confirmed date. Future treatment visits → `CANCELLED`; completed ones are kept.

#### I5. Repeat until resolved ✅ Today
> "If ANC < 1000, repeat CBC weekly until ANC ≥ 1500."

- Condition active 20-Oct → CBC 27-Oct, 03-Nov, 10-Nov … Resolved 05-Nov → no repeats after 05-Nov.

#### I6. Toxicity comes back ✅ Today
- State `RECURRED`: acts like active, but the reviewer sees it is a recurrence.

#### I7. Several conditions at once ✅ Tested end to end
- Every block transition is kept and ordered by date, then pause → resume → stop, so the result no longer depends on the order the rules are listed or confirmed.
- A pause now covers its own start day: a dose due on the hold date is `PAUSED`, not due.
- **Open decision:** if the PI marks the hold condition *resolved* (rather than recording a resume), the pause disappears and held visits come back as due/overdue. Decide whether "resolved" should mean "resume using the block's resume mode" or "stay held until an explicit resume" (recorded as an expected-failure test).

#### I8. Trial-level rules never touch patients ✅ Built (Change 6)
> "Fatal toxicity: accrual is suspended and the DSMC reviews."

- **Rule scope:** `TRIAL`. The patient engine refuses rules scoped `TRIAL`, `SITE` or `COUNTRY`; they go to a trial-level workflow instead.
- Only `PATIENT`-scoped conditions may add, cancel, pause or stop a patient's visits.

---

### J. Inpatient confinement

> "Admit Day −1, dose Day 1, discharge Day 3; extend by 2 days if a grade ≥ 2 adverse event occurs."

- **Rule:** a confinement episode (admission, dose and discharge events, day plan) plus an extension action
- **Example:** admit **Sun 04-Oct**, dose 05-Oct, planned discharge **Wed 07-Oct-2026**
- With the AE confirmed → planned discharge **Fri 09-Oct** (`EXTENDED`)
- Planned discharge date passes with no discharge recorded → `DISCHARGE_PLANNED`, not `DISCHARGED`
- One calendar entry for the whole stay; no "please travel" reminders while admitted
- Overlapping stays are flagged ✅ Today

---

### K. Timed activities, PK and time zones

#### K1. Relative to the actual dose time ✅ Today
> "PK sample 2 hours after dosing."

- **Rule:** `OFFSET +2 HOUR from ACTIVITY DOSE`, using the *actual* time only
- Dose recorded 09:12 → sample **11:12**. Before the dose is recorded the sample shows "waiting for dose".

#### K2. Date-only vs date-and-time ✅ Built (Change 9)
- Visits are stored as **dates**.
- Doses, samples, infusions and ECGs are stored as **date-and-time in the site's time zone**, e.g. `Europe/London` or `Asia/Kolkata` (`PatientContext.time_zone`).
- **Open decision:** nothing stores a site's time zone yet, so live evaluation keeps the old naive behaviour until the source is decided (e.g. a field on the site record).

#### K3. Daylight saving ✅ Built (Change 9)
> Dose at 00:30 on Sun 28-Mar-2027 in London; sample 4 hours after.

- Correct: 4 real hours later = **05:30 BST**, because the clocks jump forward at 01:00
- The old naive arithmetic gave 04:30, which is only 3 real hours → a PK sample 1 hour early. Change 9 calculates in UTC and converts back.

---

### L. Unscheduled visits and visit modes

#### L1. Protocol-defined unscheduled visit ✅ Today
> "Unscheduled visit as clinically indicated: vitals, labs."

- `AVAILABLE_ON_DEMAND`: never due, never overdue. The site creates one with a reason, and it appears dated with the protocol's activities.

#### L2. Visit mode ✅ Today
> "Week 12 follow-up by telephone."

- Mode `TELEPHONE`: the reminder says "your study team will contact you", never "travel to the hospital".

---

### M. Protocol amendments and reassignment

#### M1. New protocol version ✅ Today
- Amendment → new draft → review → approve. Existing patients **stay on their version**; new patients get the new one.

#### M2. Moving a patient to the new version ✅ Today
- Preview shows exactly which future visits change → confirm → audited. Completed visits never move.

#### M3. Changing arm or cohort ✅ Today
- Same preview → confirm. An arm the protocol doesn't define is refused. A preview made before the patient's data changed is rejected as stale.

---

### N. Deviations ✅ Today

> Visit 3 target Mon 02-Nov-2026, window 30-Oct → 05-Nov. Done Sat 07-Nov.

- **Late:** 5 days after target, **2 days outside the window**
- Computed live, never stored, so correcting an anchor updates it automatically
- A visit that was never required (other arm, condition never happened, cancelled, on-demand) is `NOT_ASSESSABLE`: it can never be "missed".

---

### O. When the protocol contradicts itself

Protocols often have a schedule table, footnotes, narrative sections and
amendments that disagree.

#### O1. Footnote narrows the table ✅ Today
> Table: MRI every cycle. Footnote b: "MRI every second cycle."

- The footnote's scope is clear (the MRI cell), so the footnote wins and the cell is linked to it. Unresolved footnotes block approval.

#### O2. Real contradiction ✅ Built (Change 8, extraction)
> Section 13.9: "QoL at 3 months post completion of treatment."
> Section 10.3: "QoL at the first follow-up visit."

- The extractor records **both** readings with their pages, sets status `CONFLICTING`, and does not choose.
- The reviewer picks one before approval. Until then, the QoL 3-month visit is `UNRESOLVED`.

#### O3. Missing information ✅ Built (Change 8)
> "PET/bone scan pre-treatment" — the timing is in section 3.1.11, not the table.

- The extractor must read eligibility and narrative sections, not only tables. If the timing truly cannot be found, the visit is `UNRESOLVED`, never "timing not specified" with a guessed date.

---

## Part 5 — The changes to build

All inside the existing app. Nothing is rewritten; each change extends the
current engine. **All nine are implemented** (uncommitted in the working tree).

| # | Change | Where | Priority | Cases |
|---|---|---|---|---|
| 1 | Calculate each repeat from the original start date, not the previous occurrence | `evaluator.py` `_expand_recurrence` | P0 | E6, E8 |
| 2 | Each cycle start is its own anchor; next cycle can follow the actual previous cycle | `timing.py` `CycleDayTiming`, `evaluator.py` | P0 | F2 |
| 3 | Windows as their own rule, applied to every occurrence | `models.py` `Event`, `evaluator.py` | P0 | B2, B3 |
| 4 | Anchor rules declare `PLANNED_OR_ACTUAL` or `ACTUAL`; planned values stop dating post-event visits | `schedule_service.py:632`, `timing.py` references | **P0 (safety)** | C6, D2 |
| 5 | Follow-up plan with segments (wraps chained repeats) | `models.py`, `evaluator.py` | P1 | E9 |
| 6 | Rule scope (`PATIENT` / `ARM` / `COHORT` / `SITE` / `COUNTRY` / `TRIAL`); patient engine refuses trial scope | `models.py` `ConditionalDefinition`, `conditional.py` | P1 | I8 |
| 7 | `WAITING_FOR_ASSIGNMENT` status | `models.py`, `evaluator.py` `_evaluate_event`, frontend presentation | P1 | H2 |
| 8 | Extraction layer: AI → cited facts → compiler → canonical schedule | `app/extraction/runner.py` `extract_schedules`, restore `/protocols/extract` in `server.py` | P0 (largest) | O2, O3, all |
| 9 | Site time zone; timed activities calculated in UTC | `models.py`, `evaluator.py` `add_amount` | P2 | K2, K3 |

### Change 8 in more detail — the extraction layer

**Step 1 — facts.** The AI reads the whole document (tables, footnotes,
eligibility, treatment, follow-up and QoL sections) and returns small cited
facts:

```json
{
  "what": "PET scan",
  "kind": "visit",
  "timing": {"pattern": "RANGE", "from": "END_OF_THERAPY", "start": "12 WEEK", "end": "16 WEEK"},
  "window": null,
  "applies_to": {"arm": "ALL"},
  "repeat": null,
  "scope": "PATIENT",
  "evidence": {"page": 41, "section": "10.3", "quote": "PET scan 12-16 weeks after completion of therapy"}
}
```

Each fact names one of the patterns in this document (`OFFSET`, `RANGE`,
`WITHIN_BEFORE`, `CYCLE_DAY`, `PHASED_REPEAT`, `HOLD_RESUME`, …). A statement
that fits no pattern becomes an `UNRESOLVED` fact with its quote.

**Step 2 — compile.** Plain code, no AI, turns facts into the canonical
schedule using fixed rules:

- The Day 1 anchor is the one the protocol names. Pre-treatment tests use `WITHIN … BEFORE REGISTRATION`, never first dose.
- End of each treatment phase becomes its own milestone event ("end of concurrent therapy", "end of all therapy").
- Follow-up with changing frequency becomes a segmented plan.
- Toxicity rules become pause, stop or manual review, with scope.
- Two facts that disagree become `CONFLICTING`, not a choice.

**Step 3 — validate and review**, as today.

**Step 4 — measure.** Each protocol in the test set has a hand-checked answer
key. Extraction is scored per category (visits found, timing, windows, arms,
conditions), never by an AI grading itself.

---

## Part 6 — Regression test matrix

Every case becomes a backend test that enrols a patient, records events and
checks exact dates and statuses.

| # | Scenario | Cases |
|---|---|---|
| 1 | Monthly from 31-Jan | E6 |
| 2 | Yearly from 29-Feb | E8 |
| 3 | Every 3 months from a month-end | E6 |
| 4 | Every 4 weeks ≠ monthly | E7 |
| 5 | Delayed C2 shifts C3 (actual mode) | F2 |
| 6 | Delayed C2 does not shift C3 (nominal mode) | F1 |
| 7 | Range window on every quarterly repeat | B2 |
| 8 | Pre-op dated from planned surgery | D2 |
| 9 | Post-op waits for actual surgery | D2, C6 |
| 10 | Surgery delayed | D4 |
| 11 | Surgery cancelled by progression | D5 |
| 12 | Neoadjuvant → surgery → adjuvant | D1–D3 |
| 13 | Run-in → late randomisation → arm B | H2 |
| 14 | 3-monthly → 6-monthly → yearly, boundary visit once | E9 |
| 15 | AB crossover | H7 |
| 16 | BA crossover | H7 |
| 17 | Timed PK across a daylight-saving change | K3 |
| 18 | Trial-level toxicity rule refused by patient engine | I8 |
| 19 | Two conditions firing on the same day | I7 |
| 20 | Amendment while the patient has future visits | M2 |
| 21 | Hold/resume: nominal, actual, unclear | I1–I3 |
| 22 | Protocol contradiction stays unresolved | O2 |

Plus randomised tests for the date arithmetic: many random start dates, and for
each one, occurrence IDs stay unique, dates stay in order, re-running gives the
same result, and correcting an anchor never moves completed visits or unrelated
branches.
