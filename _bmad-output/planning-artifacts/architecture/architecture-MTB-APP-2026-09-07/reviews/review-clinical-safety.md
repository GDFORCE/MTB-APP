# Clinical Safety and Regulatory Review — MTB Scheduling Subsystem Architecture Spine

- **Artefact reviewed:** `ARCHITECTURE-SPINE.md` (status: final, dated 2026-09-07)
- **Lens:** clinical safety, silent failure, auditability/reconstruction, human-in-the-loop integrity, clinical-vs-technical decision boundary
- **Method:** the spine read in full, then every AD tested against the code it claims to bind (`backend/app/domain/schedule/`, `backend/app/services/`, `backend/app/db/models.py`, `backend/app/api/uctsm.py`, `backend/server.py`) and against `AGENTS.md`'s verified wiring facts. A rule the code cannot honour is treated as a spine defect, not only an implementation defect: the spine is the thing that is supposed to be obeyed.
- **Review date:** 2026-09-08

---

## Verdict

**The spine is a genuinely strong piece of safety engineering that is one review-gate defect away from being unsafe.** Its core intuitions are correct and unusually well chosen for this domain: no defaulting (AD-5), single-writer date ownership (AD-4a), version pinning as the outermost scope (AD-4d), append-only clinical history (AD-9), extraction that can never emit a patient date (AD-2b). Where the code exists, those are largely real.

But three things break the safety story:

1. The **approval gate that stands between an LLM-extracted timing rule and a real patient's calendar can be satisfied by one button press**, while the spine spends an entire AD (AD-1a) forbidding bulk acceptance of a *single patient's* parity difference. The strictness is inverted relative to blast radius.
2. **Re-evaluation — the operation that actually moves patient dates — is ungated, unattributed and unaudited.** Every confirmation gate in the system guards *inputs*; nothing guards the write.
3. The two controls that carry the most safety weight — *preview → confirm* and *every state transition writes an AuditEvent* — are **not ADs**. They are cells in the "Consistency Conventions" table, with no `Binds`, no `Prevents`, and no enforcement. The spine's own structure signals they are conventions, and the code treats them as such.

Recommendation: **do not treat this spine as `final` for a system that will schedule real patients.** It is final-quality as an engineering substrate and provisional as a clinical control document. Nine amendments are listed at the end; three of them (AD-10, AD-11, AD-12) should be authored before any trial passes the AD-1 parity gate.

---

## What the spine gets right

Stated up front because the findings below should not be read as a general indictment.

- **AD-5 (nothing unstated is ever defaulted)** is the correct organising principle for this domain and is visibly load-bearing in the codebase's own history (`AGENTS.md` records repeated regressions and fixes against exactly this rule). It is the single best decision in the document.
- **AD-2b (extraction never produces a patient date)** eliminates an entire class of failure — a protocol-level artefact carrying a date only a patient fact can justify — at the type level rather than by discipline.
- **AD-4a's single-writer table** makes "why is this date what it is" answerable in principle, and the field split is clinically sensible (Anchor owns dates; Conditional owns activation; Event Type owns attendance).
- **AD-4d (version is the outermost scope)** prevents the classic amendment disaster — an approved amendment silently re-dating patients already on study. `Patient.current_schedule_version_id` genuinely is the pin.
- **The ImpactService preview→confirm implementation is better than the spine describes it.** `preview()` evaluates against a throwaway copy of the patient so an abandoned preview leaves nothing behind, `confirm()` fingerprints the patient's inputs and refuses with *"patient inputs changed; generate a new impact preview"* if anything moved between preview and confirm, and `POST /patients/{id}/states` is hard-disabled with a 409 pointing at the gated route. This is real staleness safety. The spine undersells it by relegating it to a table row.
- **`operational_read.apply_engine_dates` is conservative in the right direction** — it overlays dates only, creates and deletes nothing, and leaves an unmatched instance visibly on its operational date rather than blanking a visit off a site's list.
- **`bulk_confirm_all_fields` is honest in its audit trail** — it stamps every generated `ReviewDecision` with *"this field was not reviewed individually"* and writes a distinct `SCHEDULE_BULK_CONFIRMED` event. The code knows what it is. The spine does not mention it exists.
- **The Deferred section names the extraction-accuracy gap without spin** ("until it exists, no accuracy claim is available"). That honesty is what makes the rest of this review possible.

---

## Findings

Severity is assigned by patient consequence, not engineering effort.

### F1 — CRITICAL — An entire trial's schedule can reach APPROVED on one click
**Concerns:** the Compile phase's "Human approval" node; AD-1a (by asymmetry); AD-5

`ScheduleReviewService.approve()` (`backend/app/services/schedule_service.py:286`) enforces a real-looking gate: it re-validates, refuses on any blocking issue, and then requires a `ReviewDecision` for every required field of every event (`display_name`, `timing`, plus `activities`/`conditions`/`applicability`/`recurrence` where present). Good.

`bulk_confirm_all_fields()` (`:77`) writes **exactly those rows**, for every field of every event, from a single call. The gate `approve()` checks is therefore satisfiable by one button plus a free-text reason string.

**Failure mode.** An LLM-extracted schedule of assessments for a 40-visit oncology protocol — every window, every day offset, every conditional pathway — becomes the authority for every patient's real calendar without a single timing rule having been individually looked at. Nothing downstream re-examines it: AD-4c retires the qualifier engine at Compile, AD-3 forbids backward edges, and AD-8 provenance faithfully records the *wrong* rule.

**Two aggravating factors.**

- `approve()` filters `ReviewDecision` rows by `reviewer_id == the approving user`. So the same human both confirms the fields and approves the version. There is **no second-person rule anywhere in the spine** — no separation between the person who ran extraction, the person who confirmed, and the person who approved. For a system whose output drives protocol-window compliance, single-person approval of protocol meaning is the finding a regulator opens with.
- **The severity inversion is explicit in the document.** AD-1a spends a whole decision forbidding auto-accept, bulk accept, and accept-without-reason for a `ParityRun` — blast radius: one patient, one version, and only during migration. Nothing in the spine constrains bulk confirmation at approval — blast radius: every patient on the trial, permanently. The spine applied its strictest human-in-the-loop language to its smallest risk.

**What would close it.** An AD stating that field-level confirmation and version approval are distinct acts by distinct accountable humans; that bulk confirmation is either removed or cannot satisfy the approval gate for *timing* and *applicability* fields (the two that decide dates and who gets them); and that `approve()` must reject when the confirming reviewer and approving reviewer are the same identity.

---

### F2 — CRITICAL — Re-evaluation moves patient dates with no gate, no actor and no audit event
**Concerns:** AD-3, AD-9, and the Conventions rows "Impactful changes go preview → confirm" and "Every state transition writes an `AuditEvent`"

Every confirmation gate in this system guards an **input**: anchors land as `PENDING_CONFIRMATION` (`api/uctsm.py:1500`), conditions and assignment and state changes route through `PatientScheduleImpactService` with fingerprinted staleness checks, and direct state writes are 409'd. That is well built.

But `POST /patients/{patient_id}/schedule/evaluate` (`api/uctsm.py:833` → `PatientScheduleService.evaluate`, `schedule_service.py:694`) is the **write** — and it is ungated. It re-runs the engines, creates a new `ScheduleEvaluation`, makes it current, and materialises a fresh generation of `PatientEvent` rows in which every unprotected event's `nominal_start_date`, `earliest_date` and `latest_date` may change. The reconciliation counter it builds literally has a `MOVED` bucket.

**Three compounding gaps:**

- **No accountable human.** `ScheduleGenerationRun` (`db/models.py:431`) and `ScheduleEvaluation` (`:458`) have **no `actor_id` column**. Neither does the endpoint record one.
- **No audit event.** `evaluate()` writes no `AuditEvent`. Contrast `ScheduleReviewService`, `ImpactService`, `ParityService` and `OperationalBridgeService`, which all do. So the single most consequential patient-facing operation in the subsystem is the one operation that leaves no audit row — directly contradicting the Conventions rule *"Every state transition writes an `AuditEvent` carrying before and after."*
- **No preview.** The spine's preview→confirm rule, being a convention rather than an AD, binds nothing and is not applied here.

**Failure mode.** "Who moved patient 0142's Day 28 visit from 14 March to 21 March, when, on whose authority, and why?" is **not answerable** from this system. The `supersedes_patient_event_id` chain and `generation_reason.reconciliation` tell you *that* it moved and *what the engine computed*; nothing tells you *who caused it* or *whether a human agreed*. In a GCP inspection that is a finding on its own; in operation it means a patient's date can change between two reminder-worker passes with no human ever having decided it should.

**What would close it.** Promote preview→confirm to an AD that binds `evaluate` itself, not only its inputs: a re-evaluation that produces any `MOVED` or `CANCELLED` row for a future event must land as a proposal requiring confirmation, not as the current evaluation. Add `actor_id` and `trigger_reason` to `ScheduleGenerationRun`. Make `evaluate()` write an `AuditEvent` carrying the reconciliation counts.

---

### F3 — HIGH — AD-8 provenance cannot actually be read from the row
**Concerns:** AD-8

AD-8's rule: *"Each materialized row stores `schedule_version_id`, the source definition id, and `protocol_version_id`. Deviation, audit and reconstruction read provenance from the row, never from the trial's current state."*

Checked against `backend/app/db/models.py`:

| AD-8 requires | `uctsm_patient_events` has |
| --- | --- |
| `schedule_version_id` | **absent** — reachable only via `patient_schedule_id` → `PatientSchedule.schedule_version_id` |
| source definition id | present (`event_definition_id`) |
| `protocol_version_id` | **absent — `protocol_version_id` appears on no patient-scoped table at all** (only on `uctsm_protocol_versions`, `uctsm_schedule_definitions`, `uctsm_evidence`) |

`uctsm_patient_activities` carries even less (`patient_event_id` + `activity_definition_id`).

The join-through is not a technicality. AD-8's own sentence forbids exactly this: reconstruction must not walk back up to a parent row whose state can have changed. `PatientSchedule.status` is mutated (`schedule_service.py:752` sets it `ACTIVE`) and `current_evaluation_id` is repointed on every evaluation.

**And AD-8 binds an entity that does not exist.** It binds "confinement episodes". There is **no confinement-episode table** — the full table list in `db/models.py` contains none. Confinement episodes are recomputed at read time (`reminder_delivery.compute_patient_projection` calls `service.evaluate_result(...).confinements` live, against a horizon of `today + 365d`). So a patient's admission and discharge dates are never materialised, never carry provenance, and are **recomputed against whatever the inputs are at the moment someone looks** — meaning the confinement dates the reminder worker used at 09:00 and the ones a CRC sees at 14:00 can differ with nothing recording that they did.

**Failure mode.** A regulator asks: *"On 14 March 2026, what schedule was patient 0142 on, under which protocol amendment, and what confinement was she expected to be in?"* The first two require a join through a mutable parent; the third is unanswerable at any price, because it was never stored.

**What would close it.** Denormalise `schedule_version_id` and `protocol_version_id` onto `PatientEvent`, `PatientActivity` and the confinement rows — and materialise confinement episodes, since AD-8 already assumes they are materialised.

---

### F4 — HIGH — Append-only in principle, `DELETE ... CASCADE` in practice
**Concerns:** AD-9

AD-9: *"Recorded actuals are never overwritten. A correction writes a superseding row; the prior row remains readable."* The superseding mechanics are genuinely built — `PatientCondition`, `PatientActivityRecord.superseded_by_id`, `PatientEvent.supersedes_patient_event_id`, `protected_history`. Correct as far as it goes.

What the spine never states is **what enforces append-only against deletion**, and the schema answers: nothing.

- `PatientEvent.patient_schedule_id` and `.schedule_evaluation_id` — both `ondelete="CASCADE"`
- `PatientActivity.patient_event_id` — `CASCADE`
- `PatientActivityRecord.patient_id`, `PatientCondition`, `PatientAnchor`, `PatientUnscheduledVisit`, `PatientState` — all patient-scoped `CASCADE`
- `ScheduleEvaluation.patient_schedule_id` — `CASCADE`

Deleting one `PatientSchedule` row erases every evaluation, every generated event, every activity and every superseding correction beneath it. The append-only chain is intact right up to the moment anyone deletes a parent — including a GDPR/"remove this patient" flow, a data-fix script, or a cleanup migration. Nothing in the spine forbids that; AD-9 speaks only about *overwrites*.

`uctsm_audit_events` is a plain table in the same Alembic-owned schema — **no immutability trigger, no revoked DELETE/UPDATE grant, no hash chain, no external sink**. The application never deletes from it today; the architecture does not prevent it from doing so tomorrow, and cannot demonstrate to an inspector that it never did.

**Failure mode.** The record of what actually happened to a patient is destructible by a single ordinary operation, and its destruction is itself undetectable.

**What would close it.** AD-9 should say *how* append-only is enforced: patient-history tables are soft-delete only (`CASCADE` removed or downgraded to `RESTRICT`), `uctsm_audit_events` is insert-only at the database grant level, and audit rows are chained or mirrored so a gap is detectable.

---

### F5 — HIGH — Reminder generation fails silently, per patient, indefinitely
**Concerns:** no AD — this is the largest uncovered surface in the spine

The spine's Deferred section says: *"Reminder and calendar delivery. The projection is complete; no consumer reads it."* **That is stale.** `backend/server.py` runs `reminder_worker_loop()` as a startup task on a `REMINDER_WORKER_INTERVAL_SEC` (default **900s**) cycle, and `app/services/reminder_delivery.py` is precisely the consumer. Reminders are live. The spine's own risk register understates its live blast radius, which means anyone reasoning from the spine will mis-assess this area.

The delivery path has **five independent silent-failure surfaces**:

1. `sync_all_patient_reminders()` wraps each patient in `try/except Exception: logging.exception(...)` and continues. A patient whose evaluation raises — an unresolved anchor, a corrupt `timing_resolution`, a schema drift — **simply stops receiving reminders**, forever, with a log line no clinical role reads.
2. `reminder_worker_loop()` catches the whole-pass exception too. The worker can be failing every pass and still report as running.
3. A patient with no `uctsm_patient_id`, or whose invite has not been accepted so no login `user_id` resolves, `return`s early and silently. From the patient's phone, "not linked to the engine" and "no visits due" are the same screen.
4. `db.patients.find(...).to_list(5000)` — the cohort is **silently truncated at 5000 patients**. Patient 5001 never enters the loop and nothing says so.
5. `db.notifications.find(...).to_list(500)` caps the already-delivered keys at 500, so a long-running patient's `existing_keys` becomes incomplete and previously-withdrawn reminders can be regenerated.

There is **no "reminders last computed at" timestamp** on a patient, no per-patient delivery health, and no surface where a CRC, PI or sponsor can see *"these 7 patients have not had their reminders recomputed since 3 March."*

**Failure mode.** A patient stops being reminded about protocol visits and every human in the chain believes the system is reminding them. This is the purest form of the failure this review exists to find: invisible to everyone responsible.

**What would close it.** An AD covering delivery health — every patient carries a last-successful-projection timestamp; a per-patient failure is a first-class, queryable state surfaced to the site, not a log line; pagination is exhaustive or explicitly refused; and the spine's Deferred entry is corrected to reflect that a consumer now exists.

---

### F6 — HIGH — "Today" is server UTC, for a global patient population
**Concerns:** the Conventions row on data & formats; AD-4a (Event Type owns reminder wording); no AD owns calendar-day semantics

`server.py:55`: `def now(): return datetime.now(timezone.utc)`. The reminder worker computes each patient's projection with `today=now().date()`. `notification_projection.project_notification_candidate` then compares that UTC date against `earliest_date` / `latest_date` to produce `UPCOMING` / `DUE_WINDOW` / `OVERDUE`.

There is **no patient timezone and no site timezone anywhere in the reminder path**. The spine's convention — *"Day-level timing is `date`; intra-day is timezone-aware `datetime` in UTC"* — silently assumes a `date` is universal. It is not: a protocol window is a *local calendar* window at the site the patient attends.

Concretely: for a patient in UTC−7, from 17:00 local the server is already on tomorrow's date; a visit whose window closed "today" reads `OVERDUE` while the patient still has seven hours to attend, and an `UPCOMING` reminder can fire a day early. For UTC+10 the error runs the other way. `operational_read._as_datetime` compounds it by coercing a bare `date` to **UTC midnight** before it is written over `scheduled_date` — an operational date that meant a local calendar day becomes a UTC instant, and any downstream local-time rendering shifts it a day.

**Failure mode.** A one-day error at the timezone boundary — a patient travels on the wrong day, or a within-window visit is flagged as a protocol deviation that never occurred.

**What would close it.** An AD naming the authoritative calendar (site-local, from the patient's assigned site) for all day-level comparisons, and forbidding UTC `date()` as a stand-in for "today" in any patient-facing projection.

---

### F7 — HIGH — The cutover overlay can render a schedule that is half engine, half legacy, matched on a free-text visit name
**Concerns:** AD-1

AD-1 asserts an absolute: *"The canonical engine is the sole authority for schedule meaning and dates."* `apply_engine_dates` cannot honour that absolute and is right not to try — an unmatched instance keeps its legacy date with `uctsm_source_of_truth: False` and a `uctsm_read_note`. The mechanic is conservative and correct.

The safety problem is what the spine does *not* say:

- **A mixed list is a valid output, and the spine never says what a human must do with one.** A CRC looking at ten visits where seven are engine-dated and three are legacy-dated has no instruction, no blocking behaviour and no alert. Whether the note is even rendered depends on a UI that, per `AGENTS.md`, does not exist — `getReadMode`/`setReadMode` have **zero callers**, and the three legacy schedule screens have **zero UCTSM imports**.
- **Identity is a normalised free-text visit name.** `by_key` is built from `normalize_key(row["name"] or row["event_code"])`. Whether two rows are *the same protocol visit* — a clinical equivalence judgement — is decided by string matching on a label. An amendment that renames "Week 4" to "Visit 4", or a site that edited a visit name in the legacy editor, silently converts every affected visit to unmatched-legacy. The parity gate that "was supposed to have caught" this ran against the *previous* version.
- **AD-1 says reverting a trial to `LEGACY` "stays ungated forever."** Reverting is a clinically significant act — every patient's displayed dates change back in one step — and the spine deliberately leaves it with no gate, no reason and no approval. Instant revert is the right *capability*; ungated revert is not the right *control*.

**Failure mode.** During migration a site works from a visit list silently composed of two different sources of truth, with no rule about which to trust, and a rename can move the boundary without anyone being told.

**What would close it.** AD-1 should state that a mixed-source visit list is itself an alert condition requiring resolution, that overlay identity is a stable id and not a name (or that name-based matching is an explicitly time-boxed migration compromise with a named owner), and that `LEGACY` reversion is audited with actor and reason even though it is ungated.

---

### F8 — MEDIUM — Which protocol amendment a new patient is enrolled onto is decided by `date.today()`
**Concerns:** AD-4d, AD-5

AD-4d correctly protects *existing* patients: every engine resolves from `Patient.schedule_version_id`, never "the trial's current version." Nothing in the spine governs which version a **new** patient is pinned to.

The code decides it in `api/uctsm.py:449`: among approved versions, those where `effective_from is None or effective_from <= today`, most recent wins. And the model comment at `db/models.py:152` states the default: *"NULL means 'in force once approved'."*

So an amendment approved without an explicit effective date becomes, instantly and by default, the version every newly enrolled patient is placed on. **AD-5's no-defaulting rule does not reach `effective_from`** — an unstated in-force date resolves to "now" rather than to `UNRESOLVED` or a blocking issue. That is precisely the class of default AD-5 exists to forbid, sitting on precisely the field that determines which protocol version a patient is consented to.

This is also a clinical/regulatory determination masquerading as a nullable column. When an amendment comes into force at a given site depends on IRB/EC approval, site activation and re-consent — not on when a reviewer clicked Approve. Layer on F6 and the comparison is against a **UTC** `today`.

**What would close it.** Extend AD-5 explicitly to `effective_from`: approval must state an in-force date or record why it is immediate; and add an AD covering new-enrolment version selection with the same seriousness AD-4d gives to existing patients.

---

### F9 — MEDIUM — Two unreconciled audit trails, one of them in a store with no migration tool
**Concerns:** AD-1, AD-8, AD-9, and the Conventions row on audit events

The spine says *"Every state transition writes an `AuditEvent` carrying before and after"* — singular, as if there were one trail. Per `AGENTS.md` (verified against `a5d0a4e`): there are two — Mongo `audit_logs` and Postgres `uctsm_audit_events` — and **neither writes the other**.

AD-1 makes this permanent by design, not transitional: `visit_instances` *"remains the permanent system of record for operational workflow state — comments, tasks, completion, site edits — and the engine never acquires equivalents."* So the record of **what actually happened to the patient** (completed, missed, edited by the site) audits into one log, while the record of **why the date was what it was** audits into a different log, in a different database, with no shared correlation id.

Two further problems the spine states as envelope facts rather than risks:

- *"The operational store has no migration tool and is schema-on-read."* The permanent system of record for clinical workflow state has no schema enforcement and no migration history. An inspector cannot be shown what shape a document had in March 2026.
- AD-8 (provenance) and AD-9 (append-only) **bind only engine-side rows**. Neither reaches `visit_instances`, even though AD-1 designates it permanent. Site edits and completion records — the highest-value audit objects in the whole system — are governed by no AD at all.

**Failure mode.** Reconstructing a single visit for an inspection requires manually joining two logs with no correlation key, one of which has no schema history.

**What would close it.** Either a correlation id written into both trails at every transition, or a single canonical audit trail; and extension of AD-8/AD-9 to the operational store that AD-1 makes permanent.

---

### F10 — MEDIUM — Extraction accuracy is deferred while its only compensating control is one-click
**Concerns:** the Deferred section; AD-2a; AD-4c; interacts with F1

The Deferred entry is honest and correct as a *measurement* statement: no eval set exists, so no accuracy claim is available, revisit before first cutover under AD-1.

What makes it a safety finding rather than a scheduling one is the interaction with F1. Between an LLM's reading of a schedule-of-assessments table and a real patient's calendar there is **exactly one control**: the field-level review gate. F1 shows that gate collapses to a single button. Deferring accuracy measurement is defensible when the human backstop is strong; it is not defensible when the human backstop is one click, and the spine does not connect these two facts anywhere.

Two narrower gaps:

- AD-2a prevents *"a confident schedule from an empty text layer"* — good, and the mandatory image fallback is the right mechanism. Nothing prevents a confident schedule from a **populated but misread** text layer, which is the far more common failure with real protocol PDFs.
- AD-4c and `extraction/completeness.py` block approval on unresolved markers, orphan footnotes and unresolved scope — structural completeness. There is **no per-field extraction confidence and no evidence-completeness threshold** that blocks approval. A timing rule with a weak or ambiguous evidence anchor is indistinguishable, at the gate, from one read cleanly off a table.

**What would close it.** Field-level confidence and evidence sufficiency as blocking conditions under AD-5 semantics; and an explicit statement that the accuracy deferral is conditional on the F1 fix.

---

### F11 — MEDIUM — Compile failure silently routes authoring back to the path AD-1 exists to delete
**Concerns:** AD-1, AD-2

`AGENTS.md`, verified: *"If the canonical-schedule build throws during Add Trial, `add-trial.tsx`'s catch block (`:305–310`) silently drops the sponsor onto the legacy `sponsor/visit-schedule` editor with no error shown."*

AD-1's `Prevents` clause is *"two schedule-authoring paths surviving indefinitely."* The live failure path does the opposite: every canonical build failure silently produces a legacy-authored schedule, and the sponsor never learns the engine failed. The trials most likely to end up legacy-authored are exactly the ones whose protocols the engine could not handle — the hardest and most error-prone ones.

The spine has **no rule about what happens when Compile fails**. AD-2a covers ingestion failure (empty text layer must route to image) and the operational envelope says extraction *"records failure on the run rather than raising into the upload"* — but nothing says the failure must become **visible to the human who initiated it**. Recording a failure on a run nobody reads is the same as not recording it.

**What would close it.** An AD stating that a Compile failure is surfaced to the initiating human and never silently degrades to legacy authoring.

---

### F12 — MEDIUM — `requires_attendance` defaults to false for any event type the projection has not seen
**Concerns:** AD-4a, AD-5, AD-6

AD-4a assigns `reminder wording` and `requires_attendance` to the Event Type engine as a **field-ownership** matter. Both are patient-facing clinical instruction: whether a patient travels to site, and what the message tells them to do (fast, hold a medication, expect a phone call).

`schedule_projection.py` computes `requires_attendance = event.event_type.upper() in ATTENDANCE_EVENT_TYPES` — set membership, so an event type not in the set yields **False**. `_wording()` has a documented generic fallback for any type it has no specific wording for. The module comment shows the team already hit this once (*"'PHONE_CONTACT' here and silently fell through to the generic wording"*).

So an unrecognised or newly added protocol event type produces generic text and `requires_attendance: False` — **a default, on the one field that decides whether a patient physically travels.** AD-6 says *"an unrecognised value raises a review warning rather than being coerced"*, and AD-5 forbids defaulting outright; the projection layer coerces anyway, and the spine's own Capability map routes ENG-10 through AD-6 as if that were settled.

**Failure mode.** A patient is not told to attend a visit that required attendance, or is told to attend one that was a phone contact. AD-5's writ needs to reach Project, not stop at Evaluate.

---

### F13 — LOW — The idempotency guard is stated as universal and is optional in code
**Concerns:** the Conventions row *"Idempotency keys guard every generation and extraction run"*

`PatientScheduleService.evaluate(..., idempotency_key: str | None = None)` and the endpoint reads it from an **optional** `Idempotency-Key` header. Omit it and the replay branch is skipped entirely: every call creates a new `ScheduleGenerationRun` and a new `ScheduleEvaluation`. "Current" is then resolved by `PatientSchedule.generated_at.desc()` / `current_evaluation_id`.

A retried request from a flaky mobile connection produces duplicate evaluations for the same patient — harmless if deterministic, not harmless if any input moved between the two, and noise in exactly the audit trail F2 already shows is thin. A guard described as universal should not be opt-in via a header a client may forget.

---

### F14 — LOW/MEDIUM — A future date already communicated to a patient is not protected, and its withdrawal is auto-marked read
**Concerns:** AD-9, AD-4a; no AD covers "a date a patient has already been told"

`protected_event_ids()` protects events that are `COMPLETED` or `MISSED`, carry `protected_history`, or have a recorded `actual_date` — i.e. **things that already happened**. Correct and necessary.

Nothing protects, or even flags, a **future** visit whose reminder has already been delivered to the patient's phone. On the next re-evaluation it silently becomes a `MOVED` row. The delivery path then does this (`server.py`, `_sync_reminders_for_one_patient`):

```
{'$set': {'withdrawn': True, 'read': True}}
```

The stale reminder is marked **`read: True`** on the patient's behalf. So the sequence is: patient is told 14 March → the date changes with no human confirming it (F2) → the old reminder is withdrawn and marked as though the patient had read it → a new one is created, subject to F5's silent-failure surfaces and F6's timezone error.

**Failure mode.** This is the concrete path by which a patient travels on a date that is no longer their visit date, and no record shows they were ever informed of the change. The spine has no concept of "a date that has been communicated to a patient", which is a materially different object from "a date the engine computed" and should probably be one.

---

## Decisions the spine treats as technical that are clinical

Each of these is written in the register of an engineering trade-off and should carry a named clinical owner and a documented rationale.

1. **AD-4e, the fixed engine composition order.** Presented as preventing order-dependent results, which it does. But the *particular* order encodes clinical semantics: Conditional resolves WHETHER before Anchor resolves WHEN, so a condition whose occurrence should itself shift an anchor cannot express that; Confinement groups after dates are fixed, so an episode cannot pull its member visits. The spine argues the order is *consistent*. Whether it is *clinically correct* for the protocol constructs in scope is an unexamined question, and AD-4e's `no other` phrasing makes it unrevisitable without amending the spine.
2. **AD-4b, planned vs actual dependency mode.** Correctly refuses to default `UNCLEAR` — the single best safety line in the ADs. But calling it *"a parameter, not an engine"* frames the highest-leverage clinical choice in the system (does the next visit hang off the scheduled date or the date the patient actually attended — the thing that decides whether deviations compound or self-correct) as a configuration value. The mechanism is a parameter; the decision is protocol interpretation.
3. **AD-7's `AGE` dimension with `LT/LTE/GT/GTE`.** "Subjects aged ≥65" is the worked example. Age **at what date** — consent, enrolment, first dose, or the visit itself? A patient aged 64 at consent and 65 at Visit 12 is included or excluded depending on an answer the spine does not give. The operator list looks complete and hides an unspecified clinical definition, and AD-5's no-defaulting rule does not currently reach it.
4. **`effective_from` / version-in-force (F8).** When an amendment applies at a site is an IRB, consent and site-activation determination, expressed here as a nullable date compared to `today`.
5. **`normalize_key` visit-name matching (F7).** "Are these two rows the same protocol visit?" is a clinical equivalence judgement implemented as string normalisation, and it is the identity rule the whole cutover rests on.
6. **`requires_attendance` and reminder wording (F12).** Assigned by AD-4a as field ownership; they are patient instructions.
7. **The rolling horizon bound (ENG-3 / AD-3).** How far ahead an open-ended protocol shows visits shapes patient expectation and site workload. Currently `today + 365d` in the projection path. That is a clinical/operational policy sitting in a function default.

---

## Recommended amendments

Ordered by urgency. The first three should exist before any trial passes the AD-1 parity gate.

- **AD-10 — Approval is a two-person act.** Field confirmation and version approval are performed by distinct accountable identities; bulk confirmation cannot satisfy the gate for `timing` or `applicability`; `approve()` rejects when confirmer and approver are the same. *(Closes F1.)*
- **AD-11 — Evaluation that moves a communicated future date requires confirmation.** Promote preview→confirm from convention to AD, binding `evaluate` itself: any re-evaluation producing `MOVED`/`CANCELLED` rows for future events lands as a proposal. `ScheduleGenerationRun` carries `actor_id` and a trigger reason; `evaluate()` writes an `AuditEvent`. *(Closes F2, mitigates F14.)*
- **AD-12 — Silence is a defect.** Every projection, reminder and Compile failure has a queryable, patient-scoped state and a human-visible surface for the site; no clinically relevant failure may terminate in a log line; pagination is exhaustive or explicitly refused. *(Closes F5, F11.)*
- **AD-13 — Provenance is denormalised onto the row.** `schedule_version_id` and `protocol_version_id` on every patient event, activity and confinement row; confinement episodes are materialised. *(Closes F3.)*
- **AD-14 — Append-only is enforced, not merely intended.** No destructive `CASCADE` on patient-history tables; `uctsm_audit_events` insert-only at the grant level and gap-detectable. *(Closes F4.)*
- **AD-15 — The site's calendar day is authoritative.** No UTC `date()` stands in for "today" in any patient-facing projection or window comparison. *(Closes F6.)*
- **AD-16 — A mixed-source visit list is an alert.** Overlay identity is a stable id, not a normalised name; unmatched instances raise rather than merely annotate; `LEGACY` reversion is audited with actor and reason. *(Closes F7.)*
- **AD-5a — No-defaulting extends to `effective_from`, `requires_attendance` and the `AGE` reference date.** *(Closes F8, F12, and item 3 above.)*
- **Correct the Deferred section.** "No consumer reads the projection" is false — `reminder_worker_loop` has consumed it on a 900s cycle since the reminder-delivery work landed. A stale risk register is itself a safety defect, because everyone downstream reasons from it. Also state explicitly that the extraction-accuracy deferral is conditional on AD-10.

---

## Regulatory reconstruction test — summary answer

*Could an inspector reconstruct exactly what schedule a given patient was on at a given date, and why?*

| Question | Answer today |
| --- | --- |
| Which schedule version was the patient pinned to? | Yes — `Patient.current_schedule_version_id`, but only *currently*; historical pinning is inferable, not stored on the row (F3). |
| Which protocol amendment did that correspond to? | **Not from any patient row** — `protocol_version_id` is on no patient table (F3). |
| What dates was the patient on, on a given past date? | Mostly — via the `supersedes_patient_event_id` chain and `ScheduleEvaluation.evaluated_at`, provided nothing upstream was ever deleted (F4). |
| Why did those dates change? | Partially — `generation_reason.reconciliation` says *what* changed; nothing says *who* or *on what authority* (F2). |
| Who approved the underlying schedule, and did they review it? | Recorded, and creditably honest — but "reviewed" may mean one bulk click by the same person who approved (F1). |
| What confinement was the patient expected to be in? | **No** — never materialised (F3). |
| What was the patient actually told, and when? | Partially, in a second audit trail with no correlation id (F9), where withdrawn reminders are auto-marked read (F14). |

Four of seven are "no" or "partial". The architecture is close — most of the missing pieces are columns and one AD each — but as written, the spine promises a reconstruction capability it does not yet deliver.
