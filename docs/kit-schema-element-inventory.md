# Kit schema element inventory

Prepared 28 October 2026 from the working tree on `kit/protocol-rules` (uncommitted). It lists every
building block the kit protocol model can represent today, so the protocol corpus study can mark each
element it finds as **supported**, **partly** or **missing**.

Sources of truth: the AI schema (`backend/app/kit/_prompt_text.py` extended in `prompts.py`), the checks
in `backend/app/kit/normalize.py`, the engine in `frontend/src/features/kit/schedule.ts`, and the patient
record checks in `backend/kit_routes.py` (`clean_participant`). Elements marked *new* were added on
28 October 2026. They pass constructed tests only and have not been run against a real protocol yet.

Each element has an ID (kind letter + number) for the study to reference.

## 1. Design structure (S)

| ID | Element | Field | What it represents | Status and limits |
|---|---|---|---|---|
| S-01 | Protocol identity | `protocol.title/number/sponsor/phase/indication/version/design` | Descriptive metadata | Descriptive only |
| S-02 | Arms | `arms[]` (id, name, description) | Treatment arms | **Descriptive only.** Arm-specific schedules work through a participant question plus visit conditions (S-03) |
| S-03 | Arm / cohort / part / sequence choice | participant question + `include_if` | Which visits apply to which group | Supported |
| S-04 | Sections / phases | visit `phase` | Screening, treatment, follow-up… for grouping the timeline | Free text, grouped by `sections.ts` |
| S-05 | Periods (crossover, extension) | one anchor per period start, relative to the previous one | Period 1 → washout → Period 2 | Supported |
| S-06 | Crossover switch | rule `set_answers` *new* | From a date on, the participant follows another arm's visits | Supported (answers only; no new randomisation) |
| S-07 | Treatment cycles | visit `repeat` with `chain` | Cycle n+1 counts from cycle n's actual date | Supported |
| S-08 | Days within a cycle | visit `follows` (same occurrence) | C1D8 follows C1D1 | Supported |
| S-09 | After the last occurrence | `follows_occurrence: "last"` | Safety follow-up 30 days after the last dose | Supported |
| S-10 | Sub-study / basket assignment | decision question | Assigned after screening | Supported as a decision; no assignment logic |
| — | Randomisation, stratification, allocation ratios | — | — | **Missing** (not needed for dates so far) |
| — | Adaptive design changes (arm dropping, sample-size changes) | — | — | **Missing** |

## 2. Time points / anchors (A)

| ID | Element | Field | What it represents | Status and limits |
|---|---|---|---|---|
| A-01 | Start date | anchor `kind: "start"` | First date the site knows (usually screening) | Exactly one; required at Add Patient |
| A-02 | Counted date | anchor `kind: "relative"`, `relative_to`, `offset_days` | Fixed gap from another date | Supported, both directions |
| A-03 | Entered date | anchor `kind: "ask"`, `question`, `optional` | Date the site enters (surgery, randomisation) | Supported; asked at Add Patient when at or before Day 1, otherwise on the patient's schedule |
| A-04 | Date with a range | anchor `range` *new* | "Within 28 days before Day 1" | Entered per patient, warned when outside, never refused |
| A-05 | Planned estimate | anchor `estimates[]` (`when`, `relative_to`, `offset_days`) | Expected timing of an entered date, used only to order visits | Supported; can depend on answers |
| A-06 | Set by a visit | anchor `set_by_visit` | Day 1 = the actual date of C1D1 | Supported |
| A-07 | Set by an event | event `sets_anchor` | Progression date = the logged event's date | Supported |
| A-08 | Study day numbering | anchor `is_day1`, `day_zero` | Day 1 vs Day 0 conventions | Supported |

## 3. Timing patterns (T)

| ID | Element | Field | What it represents | Status and limits |
|---|---|---|---|---|
| T-01 | Fixed day | visit `anchor` + `offset_days` | "Day 15" | Calendar days only |
| T-02 | Window | `window_before`, `window_after` | "±3 days" | Days only |
| T-03 | Duration | `duration_days` | Confinement, diary period | Supported |
| T-04 | Repeat, fixed grid | `repeat.every_days`, `chain: false` | "Every 8 weeks from Day 1 regardless of delays" | Supported |
| T-05 | Repeat, chained | `repeat.chain: true` | Next cycle from the previous actual date | Supported |
| T-06 | Repeat count | `repeat.count` | "6 cycles" | Supported; no count stated → shows 6 ahead with a note (never 1) |
| T-07 | Count from an answer | `repeat.count_question` | Number of cycles depends on cohort | Supported |
| T-08 | Open-ended repeat | `repeat.open_ended` | "Until progression" | Shows `count` ahead |
| T-09 | Until a date | `repeat.until_anchor` | No occurrence after progression | Supported |
| T-10 | Interval changing over time | `repeat.stages` *new* | "Every 8 weeks for year 1, then every 12" | Days from the first occurrence |
| T-11 | Time limit | `repeat.max_days` *new* | "Up to 2 years" | From the first occurrence; with count, whichever comes first |
| T-12 | Allowed delay | `repeat.max_delay_days`, `delay_effect` *new* | "May be delayed up to 21 days" | **Warning only**; the effect text is not executed |
| T-13 | Minimum gap | visit `min_gap` *new* | "At least 14 days between doses" | **Warning only** on recorded dates |
| T-14 | Within-day timepoints | visit `timepoints[]` (`offset_min`, windows in minutes) | PK samples 2 h post-dose | Clock times once the dose time is recorded |
| T-15 | Occurrence numbering and names | `repeat.start_n`, `label_pattern`, `code_pattern` | "Cycle {n} Day 1" | Supported |
| T-16 | Weekend handling | engine | Flags weekend dates, suggests the nearest weekday in the window | Automatic |
| — | Calendar months / years, business days, clock-time windows across days | — | — | **Missing** (months are 30 days) |

## 4. Events (E)

| ID | Element | Field | What it represents | Status and limits |
|---|---|---|---|---|
| E-01 | Event definition | `events[]` (id, label, `repeatable`, `note`) | Progression, AE of special interest, withdrawal, pregnancy | Logged by staff with a date |
| E-02 | Event questions | event `questions[]` | Grade, confirmed yes/no, asked when logging | Choice / yes-no |
| E-03 | Event-triggered visits | event `visits[]` | Visits counted from the event date (can repeat) | Supported |
| E-04 | Ending event | `ends_schedule` | Withdrawal cancels every later visit | Visits counted from the anchor it sets stay |
| — | Unscheduled visit | modelled as an event | — | Partly (no dedicated visit kind) |

## 5. Patient facts and conditions (F / C)

Patient facts (questions):

| ID | Element | Field | What it represents | Status and limits |
|---|---|---|---|---|
| F-01 | Participant fact | question `scope: "participant"` | Known at enrolment: arm, cohort, age, childbearing potential | Required at Add Patient; answered **once** |
| F-02 | Site setting | question `scope: "study"` | Same for every patient at a site (country, which part the site runs) | Answered once in Add Trial |
| F-03 | Decision | question `scope: "decision"`, `ask_at` | Known later at a visit or date: run-in success, entering an extension | Answered once, on the timeline |
| F-04 | Event answer | event question | Known when the event is logged | Per logged event |
| F-05 | Answer types | `kind`: choice / boolean / number | — | No units, no date-type answers, no free text |
| F-06 | Values measured at each visit | model `measures` + check `limits`; record check entries `values`, `baselines` *new 30 Oct* | ANC, creatinine, a toxicity grade entered at each check; the Yes/No is worked out | Only at visit checks on scheduled visits; values are not usable in visit `include_if` |
| — | Standard fact library (common measures, units, normal ranges) | — | — | **Missing** |

Conditions (where they apply: visit `include_if`, procedure `if`, estimate `when`, rule `if`):

| ID | Element | Form | Example | Status and limits |
|---|---|---|---|---|
| C-01 | Answer is one of | `{question, in}` | Arm = A | Supported everywhere |
| C-02 | Comparison | `{question, op, value}` *new* | Age ≥ 65 | Single answered value; no units or change from baseline |
| C-03 | Or / not | `{any: [...]}`, `{not: ...}` *new* | Arm A **or** prior therapy | Two levels |
| C-04 | Event happened by the date | `{event, happened}` *new* | Visits only after progression | Visits and procedures only |
| C-05 | Visit done / missed / not done | `{visit, n, status}` *new* | Retest if screening was missed | Visits and procedures only |
| C-06 | Answer logged with the event | rule `{event_question, in}` | Grade 3 branch | Rules only |
| C-07 | Trigger timing | rule `{timing: {anchor or visit, min_days, max_days}}` | Before Cycle 3 vs after | Rules only |
| C-08 | Nth occurrence | rule `{occurrence: n}` | Second AE only | Rules only |
| C-09 | Unknown until answered | engine state | Visit shows "depends on…" while unanswered | Supported |
| C-10 | Against baseline, worst so far | limit `baseline_factor`, `of: worst_here / worst_so_far` *new 30 Oct* | Creatinine ≤ 1.5 × baseline; worst neuropathy grade ≥ 3 | Check limits and dose-change conditions only; baseline entered by staff |
| — | Count of events in a period, time since an event in visit conditions | — | — | **Missing** |

## 6. Effects (R) — what a rule does

A rule (`rules[]`) has a trigger `when` (an anchor's date, or an event), optional conditions `if`, and:

| ID | Element | Field | What it represents | Status and limits |
|---|---|---|---|---|
| R-01 | Stop listed visits | `stop_visits` | Remaining treatment visits stop at progression | Supported |
| R-02 | Stop everything | `stop_all` | Withdrawal / death | Visits counted from the anchor it sets stay |
| R-03 | Same-day handling | `same_day` keep / cancel | A stopped visit on the trigger date | Supported |
| R-04 | Start a new part | `starts` | Period 2 opens from its anchor | Supported |
| R-05 | Rule out another rule | `blocks` | An AESI rules out Injection 2 | Warning when it happens anyway |
| R-06 | When the trigger may happen | `allowed` (`relative_to`, `min_days`, `max_days`, `not_on_visit`) | "4–20 weeks after Injection 1" | **Validation only**; an impossible date is refused |
| R-07 | Change answers | `set_answers` *new* | Crossover to Arm B | From the trigger date on |
| R-08 | Pause and resume | `pause` (`visits`, `resume`, `mode` shift / skip) *new* | Treatment interruption | Visits wait for the resume date |
| R-09 | Plain-words effect | `effect`, `label` | Shown to staff | Text only |
| R-10 | Change a dose level | check / rule `dose_changes` (`step`, `to`, `stop`; check ones with `if`, `held_days`, `occurrence`) *new 30 Oct* | Resume at level −1 after a grade 4 hold; discontinue below the lowest level | Below lowest stops only when the protocol says so |
| — | Delay a single visit by N days, add a one-off visit on a rule, replace an assessment set | — | — | **Missing / partly** (only through events or stops) |

## 7. Targets (G) — what can be affected

| ID | Element | Field | Status and limits |
|---|---|---|---|
| G-01 | Visit definitions | visit `id` in rules, conditions, `follows` | Supported |
| G-02 | Visit occurrences | `#n` keys, `occurrence`, overrides `n` | Supported |
| G-03 | Assessments | `procedures[]` (`name`, `if`) | Supported |
| G-04 | Assessments at some occurrences | `repeat.overrides` (`n` list, or `every`/`from`/`to` *new*; `add`, `remove`, `note`) | Supported |
| G-05 | Assessment need | procedure `need` (required / if_indicated / optional) *new* | Display only |
| G-06 | Reusable results | procedure `reuse_within_days` *new* | Display only |
| G-07 | Visit modality | `modality` (site / phone / home / remote) | Supported |
| G-08 | Preparation | visit `prep` (`fasting_hours`, `notes`) *new* | Display only, also to patients |
| G-09 | Dose levels | model `doses` (levels, `start`, `visits`, `below_lowest`); record `doseLevels` for staff changes *new 30 Oct* | Level shown per dose; no amounts computed (mg/m², weight-based) |
| — | Kits and supplies | — | **Missing** |

## 8. Records, review and provenance (P)

| ID | Element | Field | Status and limits |
|---|---|---|---|
| P-01 | Patient record | `startDate`, `answers` (incl. `@anchor` dates), `events`, `actuals`, `doseTimes` | Validated by `clean_participant`; dates computed on the device |
| P-02 | Model version per patient | `kit_model_version` *new* | Patients never move on re-save; staff move them |
| P-03 | Review questions | `review_questions` with patches (visit offset / window / duration / repeat fields; anchor offset / relative_to) | Pruned automatically when they don't change dates (*new*) |
| P-04 | Source citations | `source` on visits and rules; on procedures *new* | No quote text or page number fields |
| P-05 | Notes | `notes[]` | Free text |
| P-06 | Analysis record | `file_sha256`, `analysis_warnings`, `corrections`, stored PDF, history *new* | Supported |
| P-07 | Frozen schedules (regression baseline) | `frontend/tests/golden/` (`models/` from `backend/tests/test_kit_golden.py`, `schedules/` from `frontend/tests/kit-golden.test.ts`) | Step 0 of the schedule atoms work: the schedules today's engine gives for the 3 sample models and 11 constructed test models over 54 patient records. Every later step must keep them passing; output fields a step adds are listed in the test's `ADDED_*_KEYS`, never by rewriting the snapshots |

## Known gaps to check first in the study

1. ~~Values measured at each visit~~ — done 30 Oct (F-06); still not usable in visit conditions.
2. Calendar months / business days.
3. ~~Doses and dose modification~~ — dose levels done 30 Oct (G-09, R-10); dose amounts are not computed.
4. ~~Change-from-baseline and "worst so far"~~ — done 30 Oct for checks and dose changes (C-10).
5. Randomisation, stratification and adaptive design changes (whether they affect schedules at all).
6. Executable effects for delay limits and minimum gaps (today warnings only).
7. Quote text and page numbers as separate provenance fields.
