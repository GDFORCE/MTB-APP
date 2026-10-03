# Protocol Extraction — Pipeline, Process and Case Handling

*How MTB reads a protocol PDF and turns it into a draft visit schedule.
Written 29 Sep 2026 against the code in the working tree (reader v2, compiler v2).*

Companion to [schedule-engine-case-handling.md](schedule-engine-case-handling.md),
which explains what the engine does with a schedule *after* it is approved. This
document covers everything *before* that: upload → AI reading → compiling →
checking → review.

---

## Contents

- [1. The idea in one paragraph](#1-the-idea-in-one-paragraph)
- [2. The pipeline at a glance](#2-the-pipeline-at-a-glance)
- [3. Where it starts in the app](#3-where-it-starts-in-the-app)
- [4. Stage by stage](#4-stage-by-stage)
- [5. One statement, followed all the way through](#5-one-statement-followed-all-the-way-through)
- [6. How each kind of protocol statement is handled](#6-how-each-kind-of-protocol-statement-is-handled)
- [7. What is never guessed](#7-what-is-never-guessed)
- [8. When things go wrong](#8-when-things-go-wrong)
- [9. Issues a reviewer sees](#9-issues-a-reviewer-sees)
- [10. Cost, limits and configuration](#10-cost-limits-and-configuration)
- [11. Known limits](#11-known-limits)
- [12. Code map and tests](#12-code-map-and-tests)

---

## 1. The idea in one paragraph

**The AI reads; plain code decides.** The AI's only job is to write down what
the protocol *says* about timing, as small **facts**, each with the page number
and the exact quote it came from. It never calculates a date. A deterministic
**compiler** (no AI) turns those facts into the app's canonical schedule using
fixed rules. Anything the protocol does not state, or states in two
contradictory ways, is left **unresolved** and becomes a review item. Nothing
reaches a patient until a person reviews and approves the draft.

The same facts always compile to the same schedule, so every rule in a draft can
be traced back to a quote plus one known mapping.

---

## 2. The pipeline at a glance

```text
 Protocol PDF (or .txt)
   │
   ▼
 0. UPLOAD CHECK        PDF/text only, not empty, ≤ 25 MB                server.py
   │
   ▼
 1. READ PAGES          Text per page, page numbers kept; each page        documents.py
   │                    tagged "text" / "sparse_text" / "image_or_empty"
   ▼
 2. PICK PAGES          Keyword scoring: schedule tables AND narrative     reader.py
   │                    (eligibility, treatment, toxicity, follow-up, QoL)  select_pages
   ▼
 3. OVERVIEW CALL  (AI) Trial details, separate schedules, what Day 1 is,  reader.py
   │                    arms/parts/cohorts, treatment blocks, visit names   read_metadata
   ▼
 4. FACT CALLS     (AI) One call per batch of ≤ 8 pages → cited facts;     reader.py
   │                    quotes checked against the page text                _read_batch
   ▼
 5. COMPILE      (code) Facts → canonical schedule (events, windows,       compiler.py
   │                    cycles, follow-up plans, conditions, anchors);
   │                    conflicts and gaps → UNRESOLVED + issues
   ▼
 6. VALIDATE     (code) Engine validator adds blocking checks              validator.py
   │
   ▼
 7. CACHE + PREVIEW     Stored 2 h in Mongo; preview table returned        server.py
   │                    to the Add Trial screen
   ▼
 8. BUILD DRAFT         Trial saved → cached result becomes a DRAFT        native_import.py
   │                    schedule version (status VALIDATION_REQUIRED)
   ▼
 9. HUMAN REVIEW        Resolve issues, confirm fields, approve            Protocol review screen
   │
   ▼
10. ENROL + DATES       Patients enrolled onto the APPROVED version;       evaluator.py
                        the engine calculates their dates
```

Steps 3 and 4 are the only AI steps. Everything else is ordinary code.

---

## 3. Where it starts in the app

| Where | Endpoint | What it does |
|---|---|---|
| **Add Trial** → "Upload protocol" | `POST /api/protocols/extract` | Full pipeline. Returns trial details (to pre-fill the form) + a schedule preview. Cached for 2 hours. |
| **Add Trial** → details only | `POST /api/protocols/extract-details` | Overview call only (one AI call) → trial details. |
| **Trial page** → Protocol & Schedule Versions → upload | `POST /api/protocols/extract`, then `…/consume` | Same pipeline for an existing trial / new protocol version. |
| Trial-scoped upload | `POST /api/trials/{id}/extract-schedule` | Same pipeline, saved straight onto that trial as a draft definition. |
| Background run | `POST /api/uctsm/protocols/{id}/versions/{vid}/extract-schedule` | Queued run; the worker calls the same `extract_schedules`. |

After the trial is saved, `POST /trials/{id}/protocol-extractions/{eid}/consume`
reuses the cached result (**no second AI call**) and
`POST /trials/{id}/uctsm-schedule` turns it into a draft schedule version.

---

## 4. Stage by stage

### Stage 0 — Upload check (`server.py`)
- Accepts `application/pdf` or UTF-8 `.txt`; rejects empty files and files over 25 MB (HTTP 400 / 413).
- If no AI provider is configured → HTTP **503** with a clear message. Nothing else runs.

### Stage 1 — Read pages (`documents.py`)
- Each PDF page's text is extracted with its **original page number** (every citation points at it).
- Each page is tagged:
  - `text`: normal page
  - `sparse_text` / `image_or_empty`: a scan or picture; the PDF itself is attached to the AI client so the model can read the page image.
- A document over 600,000 characters is rejected loudly instead of being silently cut. Cutting it could drop visits.

### Stage 2 — Pick pages (`reader.select_pages`, no AI)
Each page gets a score from weighted keyword matches (each capped at 10 hits):

| Signal | Weight |
|---|---|
| "Schedule of assessments / activities / events…" | 25 |
| "Study flowchart / calendar", "time and events" | 20 |
| "Pre-treatment evaluation", "pre-study assessment" | 15 |
| "Within N days before registration/randomisation" | 12 |
| "Treatment plan / dosing schedule", dose modification / hold | 10 |
| "End of treatment", QoL / questionnaires | 6 |
| Eligibility, discontinuation, follow-up, "cycle N", "every N weeks", windows (±) | 4 |
| Toxicity/grade, visit N | 3 |
| Day N, week N, registration/baseline, survival/progression | 2 |

Rules:
- A page scoring **≥ 8** is read.
- A page that *starts* a schedule table also pulls in the **next page**, because tables span pages.
- Scanned pages can't be scored, so they are **always kept**.
- At most **60 pages**. Above that, the highest-scoring pages win.
- If no page qualifies, extraction stops with "no page looks like it describes a schedule". This correctly rejects documents that aren't protocols, such as a GCP checklist.

Narrative sections score on purpose. Many protocols put timing in the text
rather than the table, e.g. "PET within 6 weeks before entry" in section 3.1.11.

### Stage 3 — Overview call (AI, 1 call)
Sends the first 4 pages plus the best-scoring pages (up to 14) and asks for one JSON object:

| Field | Used for |
|---|---|
| `trial` (title, phase, drug, indications, CTRI number, enrolment…) | Pre-filling the Add Trial form |
| `schedule_options` | Only when the protocol prints **more than one independent schedule** (e.g. substudies); each becomes its own draft |
| `day_numbering` (which date is Day 1, and Day-1 vs Day-0 counting) | Dating "Day N" rules; **null if not stated** |
| `arms`, `parts`, `cohorts` (with parent), `populations` | Branching / applicability |
| `blocks` (treatment blocks, with how later cycles are dated and how a hold resumes) | Cycle plans, pause/resume |
| `visit_names`, `anchor_names` | Passed to every fact call so facts from different pages use the **same names** |

### Stage 4 — Fact calls (AI, 1 call per batch)
- Selected pages are split into batches of **≤ 8 pages or ≤ 60,000 characters**.
- Each batch is sent with the shared names from Stage 3, the fact format and these **hard rules**:
  1. Never compute or write a patient date.
  2. Every fact cites the page number and the **exact quote**.
  3. Never guess. A window, boundary, count, Day 1 or counting convention that isn't stated is left **null**. Never invent "±3 days".
  4. A statement that fits no pattern → `UNRESOLVED` with a reason.
  5. Two places that disagree → write **both** facts in the same `conflict_group`; don't choose.
  6. Pre-treatment tests "within N days before registration" are counted from registration, **never from first dose**.
  7. Trial or site rules (accrual suspension, DSMC review) → scope `TRIAL` / `SITE`, not `PATIENT`.
- **Checks on the reply:**
  - Parsing is strict. A malformed reply gets **one** repair retry.
  - Each fact is validated on its own; a bad one is dropped with an issue and the rest are kept.
  - Each quote is checked against its page's text, tolerating PDF noise such as hyphenation and line breaks. A quote that isn't found → warning `EVIDENCE_QUOTE_NOT_FOUND`.

**What one fact looks like:**
```json
{
  "what": "PET scan",
  "kind": "visit",
  "event_type": "IMAGING",
  "timing": {"pattern": "RANGE", "from": {"name": "End of therapy", "kind": "ANCHOR"},
             "start": {"value": 12, "unit": "WEEK"}, "end": {"value": 16, "unit": "WEEK"}},
  "window": null,
  "applies_to": {"arms": []},
  "scope": "PATIENT",
  "evidence": [{"page": 41, "section": "10.3",
                "quote": "PET scan 12-16 weeks after completion of therapy"}],
  "conflict_group": null
}
```

Fact kinds: `visit`, `activity` (an assessment done at a visit), `anchor` (a
date others count from), `condition` (toxicity, response, progression rule),
`milestone` (end of a treatment phase).

Timing patterns (closed list): `ABSOLUTE`, `PROTOCOL_DAY`, `OFFSET`, `RANGE`,
`WITHIN_BEFORE`, `WITHIN_AFTER`, `NO_LATER_THAN`, `TRIGGERED`, `APPROXIMATE`,
`CYCLE_DAY`, `REPEAT`, `PHASED_REPEAT`, `HOLD_RESUME`, `DISCONTINUE`,
`REPEAT_UNTIL_RESOLVED`, `ON_DEMAND`, `UNRESOLVED`.

### Stage 5 — Compile (`compiler.py`, no AI)
Runs in this fixed order:

1. **Collect names**: every visit, anchor and condition gets a stable code.
2. **Groups**: arms, parts, cohorts (nested under their parent), populations.
3. **Cycle plans**: cycle facts are grouped per treatment block into one plan (length, how starts are dated, what ends the cycles).
4. **Visits**: facts about the same visit are grouped. The group becomes one event, or `CONFLICTING` if the facts disagree.
5. **Activities**: assessments are attached to their visits, with "Cycle 1 only" / "every 2nd cycle" rules.
6. **Conditions**: toxicity, response and discontinuation rules become actions (pause, resume, stop, cancel, add, repeat, manual review).
7. **Triggered visits** are linked to the condition that adds them.
8. **Anchors**: recorded by the site, or derived from a visit (first/last) or a condition.
9. **Condition definitions and blocks**, with scope.
10. **Day numbering**: set only if both "which date is Day 1" and the counting convention are stated.
11. **Reader issues** are carried over.
12. **Evidence**: every entity gets `Evidence` and claim links pointing at page + quote.

### Stage 6 — Validate (`validator.py`)
The engine's validator runs over the draft and adds its own checks: unknown
anchor, no Day 1, unclear dependency mode, unresolved boundary, missing evidence,
and so on. Issues raised by extraction are tagged `origin: EXTRACTION` so they
survive every re-validation until a reviewer resolves them.

### Stage 7 — Cache and preview (`server.py`)
- The PDF is saved to file storage; each schedule is cached in Mongo `protocol_extractions` for **2 hours** with the draft schedule, its issues, the storage key and the document hash. An audit entry is written.
- The response carries the trial details, the visit count, a **preview table** (schedule + projections + issues), and `verification.status` = `needs_review` whenever there are issues. The Add Trial screen shows this table and "review notes".

### Stage 8 — Build the draft
When the sponsor saves the trial:
1. `consume` copies the cached result onto the trial. No AI call.
2. `uctsm-schedule` → `native_import.py` creates the protocol version, pointing at the real stored PDF by its hash, and a **draft schedule version** with status `VALIDATION_REQUIRED`.

If this fails, the sponsor is told why and can retry from the trial page.

### Stage 9 — Human review
On the protocol review screen the reviewer:
- sees every visit, window, cycle plan, follow-up plan and conditional rule, each with its page + quote
- resolves each blocking issue: choose between conflicting readings, state a missing window or boundary, set Day 1, state a cycle rule
- confirms fields, then **approves**

Only an approved version can have patients enrolled on it.

### Stage 10 — Enrol and dates
Covered in [schedule-engine-case-handling.md](schedule-engine-case-handling.md).

---

## 5. One statement, followed all the way through

> Protocol, page 12, section 3.1: *"Laboratory studies must be performed within
> 28 days prior to registration."*

| Stage | What happens |
|---|---|
| 2 Pick pages | Page 12 matches "within 28 days prior to registration" (weight 12) and "eligibility" (4) → selected |
| 3 Overview | `anchor_names` includes "Registration" |
| 4 Fact | `{what: "Laboratory studies", kind: "visit", timing: {pattern: "WITHIN_BEFORE", from: {name: "Registration"}, duration: 28 DAY}, evidence: [{page: 12, section: "3.1", quote: "…within 28 days prior to registration"}]}` |
| 4 Quote check | Quote found on page 12 ✓ |
| 5 Compile | Event `LABORATORY_STUDIES`, timing `WITHIN 28 DAY BEFORE REGISTRATION`, anchor `REGISTRATION` (recorded by site), evidence + claims linked |
| 6 Validate | No issue: the anchor is defined and the evidence is present |
| 9 Review | Reviewer sees "Within 28 days before Registration · p.12 §3.1" |
| 10 Patient | Registered Thu 01-Oct-2026 → window **Thu 03-Sep → Thu 01-Oct-2026** |

---

## 6. How each kind of protocol statement is handled

"Compiles to" is the canonical rule. The last column shows what happens when
the protocol leaves something out.

### 6.1 Single visits

| Protocol says | Fact pattern | Compiles to | If something is missing |
|---|---|---|---|
| "Close-out on 15-Jan-2027" | `ABSOLUTE` | fixed date | — |
| "Day 8" | `PROTOCOL_DAY` | Day 8 from the stated Day 1 anchor (clinical-day counting) or +8 days (Day-0 counting) | No Day 1 stated, or no counting convention → **unresolved** |
| "Day 0" when the protocol counts from Day 1 | `PROTOCOL_DAY` | — | **Unresolved** (contradiction) |
| "4 weeks after registration" | `OFFSET` | offset +4 weeks | — |
| "12–16 weeks after end of therapy" | `RANGE` | range; weeks become days only when exact | Start after end, or units that can't be converted exactly → **unresolved** |
| "Within 28 days before registration" | `WITHIN_BEFORE` | within, before that anchor (never re-pointed at first dose) | — |
| "Within 7 days after surgery" | `WITHIN_AFTER` | within, after | — |
| "No later than 30 days after last dose" | `NO_LATER_THAN` | latest date only | — |
| "30 days after progression" | `TRIGGERED` | dated from the condition's date + an "add visit" action on that condition | — |
| "Approximately 3 months" | `APPROXIMATE` | kept as approximate | The engine refuses to date it → **review** |
| "As clinically indicated" | `ON_DEMAND` | available on demand, never due | — |
| Anything that fits no pattern | `UNRESOLVED` | unresolved timing with page + quote | **Review** |

### 6.2 Windows

| Protocol says | Compiles to |
|---|---|
| "Week 4 ± 3 days" | Window −3/+3 on the event; re-applied to every repeat |
| "Day 15, up to 3 days late" (both sides stated: −0/+3) | Window −0/+3 |
| Only one side stated ("+3 days") | **Not applied**; kept as a note with a warning. It is never completed with a guessed other side |
| A window on a range or "within" rule | Not applied; the range already *is* the window. Kept as a note |
| No window stated | **Empty**, never defaulted |

### 6.3 Repeats and follow-up

| Protocol says | Compiles to | If something is missing |
|---|---|---|
| "Every 3 weeks × 6" | repeat, 6 times | — |
| "Monthly until 30-Jun-2028" | repeat until date | — |
| "Every 12 weeks until progression" | repeat in a block that the progression condition **stops** | — |
| "Every 3 months for 2 years" | repeat ending 24 months after the anchor | Whether the 24-month visit is included isn't stated → **review** |
| "While on study" | open-ended repeat (rolling window of upcoming visits) | Only if the protocol says it continues; otherwise the end is **unresolved** |
| "Every 3 months for 2 y, then every 6 months to 5 y, then yearly" | **one** follow-up plan with 3 segments | Boundary inclusion, "does the first visit fall on the start date", or the final end not stated → **review** |
| A repeat on a range/within rule | — | **Unresolved** (no single first visit) |

### 6.4 Treatment cycles

| Protocol says | Compiles to | If something is missing |
|---|---|---|
| "21-day cycles, Days 1, 8, 15, for 6 cycles" | one **cycle plan** (length 21 days, 6 cycles) + cycle-day visits | Cycle length or number of cycles not stated → **unresolved** |
| "Cycles repeat every 21 days from C1D1" | cycle plan, **fixed grid** (a late cycle doesn't move later ones) | — |
| "Each cycle starts 21 days after Day 1 of the previous cycle" | cycle plan, **follows the actual previous cycle** | — |
| Neither stated | cycle plan, mode **UNCLEAR** | **Blocks approval** until the reviewer says which |
| "Cycles until progression" | open-ended plan + progression stops the treatment block | — |
| "C3D1 MRI" (one named cycle) | visit on cycle 3 | With no cycle plan it is only dated if the grid is stated as fixed; otherwise **unresolved** |
| "MRI every 2nd cycle", "PK Cycle 1 only", "even cycles" | an occurrence rule on the **activity** (MRI only on C2, C4, C6…) | Needs a fixed number of cycles and an unambiguous selector; otherwise a **review** note |

### 6.5 Anchors and milestones

| Protocol says | Compiles to |
|---|---|
| "Registration", "randomisation", "surgery" | Anchor recorded by the site |
| "First dose" / "last dose" | Anchor derived from the dosing visit (first/last). If the visit or first/last isn't stated → **unresolved** |
| "Date of progression" | Anchor set when the condition is confirmed |
| "End of concurrent therapy", "end of all therapy" | **Milestone** anchor of its own. A stated expected window becomes a separate "expected" visit that never dates the milestone itself |
| "Within 14 days before the **planned** surgery" | Rule marked `PLANNED_OR_ACTUAL`, so it can use the planned date |
| "30 days after surgery" | Default `ACTUAL`: waits for the real surgery date |
| A rule counts from an anchor nothing defines | Anchor recorded by the site + warning `ANCHOR_SOURCE_NOT_STATED` |

### 6.6 Branching

| Protocol says | Compiles to |
|---|---|
| "Arm A: …, Arm B: …" | Arms + per-visit/activity arm rules |
| "Part A → Cohort 1, 2" | Parts + cohorts nested under their part (Add Patient offers only the right ones). Unknown or ambiguous parent → **blocking** issue |
| "Women of childbearing potential", "age ≥ 65" | Attribute rules on the activity/visit |
| Several independent schedules (substudies) | **One draft per schedule**. Shared facts go into each; a fact naming an unknown schedule goes into all of them with a warning |

### 6.7 Conditions and toxicity

| Protocol says | Fact | Compiles to |
|---|---|---|
| "Grade 3: hold until ≤ grade 1, then resume" | `HOLD_RESUME` | Pause the block + a separate "resumed" condition that resumes it. Resume on the original grid vs from the resume date comes from the protocol, else **UNCLEAR** |
| "Discontinue for progression / pregnancy / withdrawal" | `DISCONTINUE` | Stop the treatment block (or cancel the visit if it isn't in a block) |
| "Repeat CBC weekly until ANC ≥ 1500" | `REPEAT_UNTIL_RESOLVED` | Repeat visit + repeat action + the resolution threshold. No checkable threshold → **unresolved** |
| "If CR, start maintenance" | condition with `ADD` | Add-visit action |
| "Fatal toxicity: accrual suspended, DSMC review" | scope `TRIAL` | Recorded as a trial-level rule (info issue). If it names patient visits, it becomes a trial-scoped definition that the engine **refuses** to apply to patients |
| A condition with no stated effect | — | **Unresolved** |

### 6.8 Contradictions

| Situation | Result |
|---|---|
| Two facts marked as conflicting (e.g. table vs footnote vs narrative) | Visit becomes `CONFLICTING`, timing unresolved and lists **every reading with its citation**, + blocking `CONFLICTING_SOURCE`. The compiler never picks |
| Two facts about the same visit that compile differently, even if not marked | Same as above |
| Two facts, one readable and one not | Readable one used + warning `UNRESOLVED_STATEMENT` |
| Cycle facts that disagree on length or end ("3 weeks" vs "21 days" is **not** a disagreement) | Blocking `CONFLICTING_SOURCE` |

---

## 7. What is never guessed

Each of these becomes an unresolved rule or a blocking review item, never a
default:

- which date is Day 1, and whether counting starts at Day 1 or Day 0
- a visit window, or the missing side of a window
- whether a boundary visit belongs to the earlier follow-up phase
- whether the first repeat falls on the start date
- the end of a repeat or of the last follow-up phase
- cycle length, number of cycles, and whether cycles follow the grid or the actual previous cycle
- how a held treatment resumes
- first vs last occurrence for a derived anchor
- a condition's effect or resolution threshold
- approximate timing
- which reading is right when the protocol contradicts itself

---

## 8. When things go wrong

| Problem | What the system does |
|---|---|
| No AI provider configured | HTTP 503: "Automatic protocol extraction is not configured…" |
| PDF unreadable | HTTP 400 |
| No page looks like a schedule | HTTP 502 "no page … describes a schedule" |
| AI unreachable (network/quota) | HTTP 502; **not** retried, not hidden |
| AI reply malformed | One repair retry; still bad → that batch is skipped with blocking `EXTRACTION_PAGES_UNREAD` naming the pages |
| **Every** batch unreadable | Extraction fails (502), rather than returning an empty schedule that would look like "the protocol has no visits" |
| Overview unreadable | Continues; blocking `EXTRACTION_OVERVIEW_UNREAD` ("Day 1, arms and blocks must be entered by a reviewer") |
| One fact malformed | Dropped with `EXTRACTION_FACT_INVALID`; others kept |
| Quote not on the cited page | Warning `EVIDENCE_QUOTE_NOT_FOUND` |
| Cited page wasn't among those read | Warning `EVIDENCE_PAGE_NOT_READ` |
| Cached result older than 2 h at save time | "Upload the PDF again" (404) |
| Draft build fails after the trial is saved | Sponsor is told why and can retry from the trial page |

---

## 9. Issues a reviewer sees

**Blocking** issues must be resolved before approval. The others are review notes.

| Code | Blocking? | Meaning |
|---|---|---|
| `CONFLICTING_SOURCE` | Yes | The protocol says two different things; pick one |
| `UNRESOLVED_STATEMENT` | No | One of several statements about a visit couldn't be mapped |
| `UNRESOLVED_SCHEDULE_ORIGIN` | Yes (validator) | Day 1 or the counting convention not stated |
| `UNCLEAR_DEPENDENCY_MODE` | Yes | Cycles/repeats: grid vs actual previous not stated |
| `UNRESOLVED_RECURRENCE_BOUNDARY` | Yes | Follow-up boundary / first-visit rule not stated |
| `PHASED_SEGMENT_UNBOUNDED` | Yes | A follow-up phase has no stated end |
| `UNRESOLVED_REFERENCE` / `UNKNOWN_ANCHOR` | Yes | Points at something the protocol never defines |
| `AMBIGUOUS_WINDOW` | Yes | A window with no single target date |
| `EXTRACTION_PAGES_UNREAD` | Yes | Pages the AI couldn't read; visits may be missing |
| `EXTRACTION_OVERVIEW_UNREAD` | Yes | Overview missing |
| `EXTRACTION_FACT_INVALID` | No | A statement was dropped |
| `EVIDENCE_QUOTE_NOT_FOUND` / `EVIDENCE_PAGE_NOT_READ` | No | Check the citation |
| `NON_PATIENT_RULE` | No (info) | Trial/site-level rule recorded, not applied to patients |
| `ANCHOR_SOURCE_NOT_STATED` | No | Anchor assumed to be recorded by the site |
| `CONDITION_BLOCK_MODE_UNSTATED` | No | Explains a block that exists only so a condition can pause/stop it |
| `UNKNOWN_SCHEDULE_OPTION` | No | A fact names a schedule the overview didn't list |
| `PLANNED_ANCHOR_AFTER_EVENT` | No | A post-event visit is allowed to use a planned date; check it |

---

## 10. Cost, limits and configuration

**AI calls per protocol:** 1 overview call + 1 call per batch of ≤ 8 selected
pages. Each call can be retried once, only for a malformed reply.

| Selected pages | Calls (normal) | Calls (worst case) |
|---|---|---|
| 8 | 2 | 4 |
| 24 | 4 | 8 |
| 60 (maximum) | 9 | 18 |

`extract-details` alone is 1 call. Consuming the cached result makes **no** call.

**Configuration** (`backend/.env`):
```dotenv
UCTSM_EXTRACTION_PROVIDER=gemini        # or anthropic — must be set explicitly
GEMINI_API_KEY=...                      # or ANTHROPIC_API_KEY
UCTSM_EXTRACTION_MODEL=                 # optional model override
```
The provider must be named explicitly: the same keys are used by remote-audit
features and must not switch extraction on by accident.

> ⚠ This machine's `backend/.env` currently selects Gemini with a key, so an
> upload makes real, paid calls.

Limits: 25 MB per file, 600,000 characters per document, 60 pages read, 8 pages
or 60,000 characters per batch, 16,000 output tokens per fact call.

---

## 11. Known limits

- **Accuracy is not yet measured on real protocols.** The pipeline's behaviour is tested with scripted AI replies; how well a real model fills in facts is not. Next step: hand-checked answer keys for a set of real protocols, scored per category (visits found, timing, windows, arms, conditions).
- **"Every 2nd cycle" with open-ended cycles** can't be expressed yet (no "even cycles" rule in the engine), so it goes to review.
- **Occurrence rules on plain repeats**, or on a visit's own conditions (not an activity's), stay review notes.
- **A plain repeat inside a block that follows the actual previous cycle** gets a blocking `UNSUPPORTED_TIMING`, because the engine dates plain repeats on their own grid.
- **Cross-page contradictions** are only caught when both statements use the same visit name. The shared names from the overview call reduce, but don't remove, this risk.
- **Pages scoring below 8** are not read. A schedule described only in unusual wording could be missed; the page list used is kept in the trace (`reader.select_pages`).

---

## 12. Code map and tests

| File | Role |
|---|---|
| `backend/server.py` (`/protocols/extract`, `/extract-details`, `/trials/{id}/extract-schedule`, `_run_protocol_extraction`) | Upload checks, caching, preview, audit |
| `backend/app/extraction/documents.py` | PDF/text → pages |
| `backend/app/extraction/reader.py` | Page selection, prompts, AI calls, quote checks |
| `backend/app/extraction/facts.py` | The fact schema |
| `backend/app/extraction/compiler.py` | Facts → canonical schedule (deterministic) |
| `backend/app/extraction/runner.py` | Provider choice; `extract_protocol` / `extract_schedules` / `execute_run` |
| `backend/app/extraction/llm_client.py` | Anthropic and Gemini clients |
| `backend/app/services/native_import.py` | Cached result → draft schedule version |
| `backend/app/domain/schedule/validator.py` | Blocking checks |
| `frontend/app/(app)/sponsor/add-trial.tsx`, `src/features/uctsm/ExtractionPreview.tsx`, `ProtocolUploadReview.tsx`, `ProtocolReviewScreen.tsx` | Upload, preview, review screens |

Tests (no real AI calls; scripted clients):
- `tests/test_extraction_compiler.py`: every mapping above, including end-to-end compile → approve → enrol → exact dates
- `tests/test_extraction_reader.py`: page selection, prompts, parsing, repair retry, quote checks
- `tests/test_extraction_endpoints.py`: endpoint responses, caching, consume without a second AI call (run this file on its own)
- `tests/test_extraction_ingestion.py`, `tests/test_native_import.py`: draft creation

Versions recorded in every trace: reader `uctsm-fact-reader.v2`, compiler `uctsm-fact-compiler.v2`.
