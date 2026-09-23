---
name: 'Adversarial review — MTB Scheduling Subsystem architecture spine'
type: architecture-review
lens: adversarial
target: ARCHITECTURE-SPINE.md
created: '2026-09-08'
---

# Adversarial Review — Architecture Spine, MTB Scheduling Subsystem

**Method.** For each AD I built pairs of concrete units one level down that each obey
every AD *to the letter* and still build something incompatible. Where the repository
already contains one of the two units, I name the file and line so the pair is not
hypothetical — several of these divergences are not future risks, they are already
shipped, and both halves are currently legal under the spine.

**Verdict up front.** The spine is unusually strong on *phase* separation (AD-3) and
*field* ownership (AD-4a) but weak on three things that turn out to matter more:

1. **AD-4a binds "engines", and almost nothing that writes patient rows is an engine.**
   The services layer, the HTTP layer and the projection layer are all outside its scope
   by the literal text, and all three currently write fields AD-4a assigns to a sole
   engine writer.
2. **Several rules have no named enforcement point.** "Impactful changes go preview →
   confirm", "every state transition writes an AuditEvent carrying before and after", and
   "nothing unstated is ever defaulted" are each stated once and enforced by hand, per
   call-site. The repository already enforces each of them in some places and not others.
3. **AD-3's "no backward edges" governs *data* direction but not *invocation* or
   *derivation* direction.** Project is allowed to call Evaluate, and Project is allowed
   to derive scheduling semantics as long as it doesn't persist them. Both loopholes are
   currently in use.

Findings are D-1..D-16, severity-tagged. Proposed ADs are collected at the end.

---

## D-1 — CRITICAL — Two writers of `nominal_start_date`, and neither is breaking AD-4a

**Unit A — the Anchor engine** (`backend/app/domain/schedule/timing.py`, `anchor_status.py`).
AD-4a names it sole writer of `nominal_start_date`, `earliest_date`, `latest_date`.

**Unit B — the patient-row materializer**, `PatientScheduleService._persist_events`
(`backend/app/services/schedule_service.py:769-790`).

**ADs both obey.** AD-4a (it binds *"every engine that touches a patient event"* — the
materializer is a service in `app/services/`, a different layer under both the Layer map
and the Naming convention, so it is not bound), AD-3 (writes only patient rows), AD-4d
(version pinned upstream), AD-8 (writes provenance), AD-9 (it is *preserving* recorded
history, which AD-9 positively requires).

**How they diverge.** The materializer computes a `protected` flag from
`protected_event_ids()` (`schedule_service.py:403-413`: status in `{COMPLETED, MISSED}`,
or `protected_history`, or any occurrence with an `actual_date`) and then does:

```python
status         = previous.status              if protected else item.status.value
nominal_start  = previous.nominal_start_date  if protected else as_date(timing.nominal_start)
earliest       = previous.earliest_date       if protected else as_date(timing.earliest)
latest         = previous.latest_date         if protected else as_date(timing.latest)
```

So on any protected row the *stored* date is the old date, not the date Anchor computed.
Concretely: an amendment moves Cycle 3 Day 1 from 2026-02-28 to 2026-03-10. The visit
already has an `actual_date`, so it is protected. `DeviationService.report` reads the
materialized row and reports **no deviation** (actual matches stored nominal).
`PatientScheduleImpactService.preview` runs `evaluate_result` and reports **a +10 day
move**. Two units, both correct under the spine, give the site two different answers to
"when was this visit due" — the exact "unexplainable schedule" AD-4a exists to prevent.

Worse, the substitution is invisible: nothing on the row records that the engine's value
was discarded. `explanation["reconciliation"]["change"] = "PROTECTED"` is a free-text
breadcrumb inside a JSON blob, not a queryable fact.

**Proposed fix — tighten AD-4a to AD-4a′ and add AD-4f.**

> **AD-4a′.** Sole-writer ownership binds *every code path that assigns the field on a
> persisted patient row* — engine, service, repository, HTTP handler or migration — not
> only modules named "engine". A non-owner may copy a value forward but may not compute or
> substitute one.
>
> **AD-4f — freezing is additive, never substitutive.** A row whose engine-computed value
> is deliberately not applied stores *both*: the engine value in the owned column, and the
> frozen value in `frozen_<field>` with `frozen_at`, `frozen_reason`, `frozen_by`. No
> reader ever sees an owned column holding a value its owner did not produce. Deviation,
> parity and impact then read the same pair and cannot disagree.

---

## D-2 — CRITICAL — `PatientEvent.status` has four legal writers and AD-4a names one

**Unit A — the Conditional engine** (`backend/app/domain/schedule/conditional.py`).
AD-4a: sole writer of "activated / cancelled / paused".

**Unit B — the HTTP occurrence recorder**, `record_occurrence`
(`backend/app/api/uctsm.py:1483-1484`):

```python
if body.status == "COMPLETED":
    event.status = "COMPLETED"
```

**ADs both obey.** AD-4a — to the letter. Its table row reads *"activated / cancelled /
paused"*: three specific values, not "the status field". `COMPLETED` is not one of them, so
writing it is not a violation. AD-1 — completion is operational workflow state, which AD-1
explicitly assigns to the operational store, so the endpoint is faithfully recording an
operational fact. AD-9 — nothing is destructively overwritten (an occurrence row is
appended). AD-8 — provenance is already on the row.

**And there are two more writers nobody owns.** `PatientEventStatus`
(`domain/schedule/models.py:163-179`) contains `WAITING_FOR_ANCHOR` (plainly Anchor's),
`NOT_APPLICABLE` (plainly Applicability's), `AVAILABLE_ON_DEMAND` (Event Type's), and
`BLOCKED` / `UNRESOLVED` (nobody's — they are the AD-5 escape values, written by whoever
gets there last). Five engines and one HTTP handler write one column, and AD-4a's table
assigns it to one engine for three of its eleven values.

**How they diverge.** Two teams, both reading AD-4a correctly:

- *Team Conditional* implements "an amendment retro-cancels Cycle 4" as
  `event.status = CANCELLED`.
- *Team Operational* has already written `event.status = COMPLETED` on that row, because
  the site did the visit before the amendment landed.

One of the two facts is destroyed on write, and which one survives depends on evaluation
timing. There is no representation for "completed, and subsequently cancelled by
amendment" — a real and reportable regulatory state. Then D-1 fires: because the row now
reads `COMPLETED`, `protected_event_ids` freezes its dates permanently.

**AD-1 also states a rule it cannot enforce.** *"The engine never acquires equivalents [of
comments, tasks, completion, site edits]."* `PatientEventStatus.COMPLETED` and `.MISSED`
already exist in the canonical model (`models.py:177-178`), and
`domain/schedule/deviation.py:35` treats `COMPLETED, MISSED, DUE, OVERDUE` as assessable
canonical statuses. The rule is stated in AD-1, contradicted in the canonical model, and no
test could ever notice.

**Proposed fix — split the field and give the rule a test.**

> **AD-4a-status.** `PatientEvent.status` carries *protocol-meaning lifecycle only*. Its
> members are exhaustively enumerated in the AD, each with exactly one owning engine:
> `RESOLVED`/`WAITING_FOR_ANCHOR` → Anchor; `NOT_APPLICABLE` → Applicability;
> `WAITING_FOR_CONDITION`/`CANCELLED`/`PAUSED` → Conditional; `AVAILABLE_ON_DEMAND` →
> Event Type; `UNRESOLVED`/`BLOCKED` → the validator, and only as an AD-5 escape.
> `COMPLETED` and `MISSED` are removed from the enum. Execution state lives in
> `PatientEventOccurrence` and in `visit_instances` per AD-1, joined at read time, never
> merged into this column.
>
> **Enforcement point (named, not implied):** a test asserting
> `set(PatientEventStatus) == set(AD_4A_STATUS_OWNERS)`, plus a ban on assigning
> `PatientEvent.status` outside `domain/schedule/`.

---

## D-3 — CRITICAL — Two legal routes to move every date on a patient, with different audit

**Unit A — the recalculation endpoint**, `POST /patients/{id}/schedule/evaluate`
(`api/uctsm.py:833-862`). Body is `{horizon, rolling}`. No actor recorded, no reason
required, no preview, no proposal. It calls `PatientScheduleService.evaluate`, which
*materializes* — every date on the patient can move, and does.

**Unit B — the impact-proposal path**, `POST /patients/{id}/schedule/impact-preview` →
`POST /schedule-impact-proposals/{id}/confirm` (`api/uctsm.py:1334`, `1361`;
`services/impact_service.py`). Requires `actor_id`, a mandatory `reason`, an expiry, a
staleness hash, and writes a full before/after AuditEvent.

**ADs both obey.** AD-3 (both are Evaluate writing patient rows), AD-4a, AD-4d, AD-8, AD-9,
AD-5. Nothing in any AD says a date move needs a reason. The only text that does is a
**Consistency Convention** row — *"Impactful changes go preview → confirm, never
apply-on-click"* — which is a convention in a table, binds nothing, and names no
enforcement point.

**Proof the rule is enforced by hand and inconsistently.** `POST /patients/{id}/states`
(`api/uctsm.py:1569-1576`) was *hard-disabled* specifically to force callers onto the
proposal path:

```python
raise HTTPException(409, "Direct schedule-input changes are disabled; use the state
                          impact-preview endpoint and confirm its proposal.")
```

`POST /patients/{id}/schedule/evaluate` — a strictly more impactful operation — was not.
Someone applied the convention to one endpoint and not to its neighbour, which is exactly
what "no enforcement point" produces.

**How two teams diverge.** Team Site-UI wires the "Recalculate schedule" button to
`/schedule/evaluate` — it is the obvious endpoint, it is documented, it works, and it obeys
every AD. Team Clinical-Ops wires "Record anchor date" through the proposal path. Both move
the same dates. Half the trial's date changes carry an actor and a reason; the other half
appear in the audit log as an unattributed evaluation with a fresh `evaluator_version`. At
inspection, nobody can say who moved Patient 0041's Cycle 2.

**Proposed fix — add AD-10, a single mutation gate.**

> **AD-10 — one write chokepoint for patient rows.** All materialization of patient events
> and activities passes through one function, `PatientScheduleService.materialize(...)`,
> which refuses to write without a `ScheduleImpactProposal` id, an `actor_id` and a
> `reason`. `evaluate` becomes *evaluate-or-propose*: if the computed result is identical to
> the current materialization it is a no-op read; if it differs it returns a proposal and
> writes nothing. "Impactful" is computed, not judged by the endpoint author. Direct
> construction of `db.PatientEvent` outside `materialize` is banned.

---

## D-4 — HIGH — Two anchor-candidate producers, one consumer, and their status sets do not intersect

**Unit A — the explicit anchor recorder**, `POST /patients/{id}/anchors`
(`api/uctsm.py:1500-1533`). Writes `PatientAnchor(status="PENDING_CONFIRMATION")` and
returns `impact_preview_required: True`.

**Unit B — the derived anchor producer**, the side-effect block inside `record_occurrence`
(`api/uctsm.py:1445-1483`). When an occurrence carries an `actual_date`, it silently creates
one `PatientAnchor(status="PENDING_CONFIRMATION")` *per matching anchor definition*.

**Unit C — the only consumer**, `PatientScheduleService._context`
(`schedule_service.py:466-475`):

```python
.where(db.PatientAnchor.status.in_(["CONFIRMED", "PROVISIONAL"]))
```

**ADs all three obey.** AD-2b (no extraction date), AD-5 (unresolved stays
`AWAITING_ANCHOR`), AD-8, AD-9 (append-only candidates), AD-4b (dependency mode selects
*which value*; it says nothing about which *status*).

**How they diverge.** There are three anchor statuses in play — `PENDING_CONFIRMATION`,
`PROVISIONAL`, `CONFIRMED` — and **no AD defines the lifecycle or names who may advance
it**. Both producers write `PENDING_CONFIRMATION`; the consumer accepts
`CONFIRMED | PROVISIONAL`. Nothing transitions the first into either except the anchor
impact-preview/confirm flow — and *"Auto-derived anchor confirmation (doc 1 s16) — the
confirm prompt does not exist"* is listed under **Deferred**. So today: a site records a
real actual date, the system dutifully derives the anchor candidate, and Evaluate never
sees it. The patient stays `AWAITING_ANCHOR` indefinitely while the data to date them sits
in the table.

Now the divergence proper. Two teams reading the spine:

- *Team A* builds the confirm UI and reads only `CONFIRMED`, treating `PROVISIONAL` as a
  legacy value.
- *Team B* builds patient-facing anchor entry and, seeing that `_context` accepts
  `PROVISIONAL`, writes candidates as `PROVISIONAL` so dates appear immediately — which is
  defensible, because no AD says an anchor must be human-confirmed before it dates a visit,
  and AD-5 bans only *inventing* a value, not *using an unconfirmed real one*.

Result: half the trial's schedules are driven by confirmed anchors and half by unconfirmed
ones, with no field on the resulting dates distinguishing the two.

**Proposed fix — add AD-11.**

> **AD-11 — anchor admissibility is a named lifecycle with one producer.** `PatientAnchor`
> has exactly three states with defined transitions: `PENDING_CONFIRMATION` →
> (`CONFIRMED` | `REJECTED`); `PROVISIONAL` is removed, or given an explicit definition, an
> owner, and a rule for how a provisional-derived date is *labelled on every event it
> dates*. **Evaluate reads `CONFIRMED` only**; a `PENDING_CONFIRMATION` candidate is
> invisible to the engines and the event remains `AWAITING_ANCHOR` per AD-5. Anchor
> candidates are created by one service function called by both endpoints — an HTTP handler
> never constructs a `PatientAnchor`. Because AD-11 makes the deferred confirm prompt
> *load-bearing rather than optional*, it moves out of Deferred and into the cutover gate.

---

## D-5 — HIGH — `requires_attendance` and reminder wording are computed in Project, by two projectors, differently

**Unit A — the Event Type engine.** AD-4a names it sole writer of *"reminder wording,
`requires_attendance`"*. Capability map: `models.py Event.event_type / visit_mode`.

**Unit B — the calendar/reminder projector**, `services/schedule_projection.py`. It owns
`PATIENT_WORDING` (14 entries, L51-66), `ATTENDANCE_EVENT_TYPES` (L42-46), `_wording()`
(L157), and computes `requires_attendance=event.event_type.upper() in ATTENDANCE_EVENT_TYPES`
(L232).

**Unit C — the notification projector**, `services/notification_projection.py`. Same domain,
44 lines, written later, and it has **no wording and no attendance concept at all**.

**ADs they all obey.** AD-3 — *"Project reads patient rows and writes nothing back"*.
Neither projector writes anything; they return response models. AD-4a — binds *engines*; a
projector is not an engine. AD-6 — both key on `CORE_EVENT_TYPES`, and `schedule_projection`
even asserts it at import time. AD-5 — `ProjectedEvent.event_type` deliberately has no
default.

**How they diverge — already live.** These are two implementations of one derivation:

| Derivation | `schedule_projection.py` | `notification_projection.py` |
| --- | --- | --- |
| due window start | `event.earliest_date or event.nominal_date` (L228) | `earliest_date or nominal_date` (L29) |
| policy state | `_policy_state()` (L151-157) | inline, re-implemented (L31-36) |
| actionable status | `ACTIONABLE_STATUSES` set (L48) | `event_status != "RESOLVED"` string compare (L26) |
| unscheduled visits | suppressed with a reason (L184-190) | **not suppressed** |
| confinement absorption | suppressed — an admitted patient is not told to travel (L214-221) | **no concept of confinement** |
| wording / attendance | full table | absent |

`schedule_projection`'s docstring states rule 3 as load-bearing: *"A continuous confinement
is ONE calendar entry. While the patient is admitted, internal study days must not generate
further arrival reminders."* `notification_projection` cannot honour that rule — it never
receives confinement data. A push-notification worker built on the newer, simpler projector
will therefore tell an admitted inpatient to travel to site on study day 4 of their own
admission. Both units obey every AD.

Root cause: AD-4a assigns `requires_attendance` and wording to an engine, and then nothing
materializes them. They are not columns on the patient event, so every projector must
re-derive them, and each re-derivation is legal.

**Proposed fix — tighten AD-4a and AD-3.**

> **AD-4a-materialization.** A field AD-4a assigns to an engine is a *stored column on the
> patient row*, written during Evaluate. `requires_attendance`, `reminder_text` and
> `attendance_mode` become columns on `uctsm_patient_events`, written by Event Type.
>
> **AD-3′ — Project may format, never derive.** Project may reorder, group, localize and
> render. It may not compute a scheduling predicate. Every predicate a projector needs —
> due/overdue state, window bounds, confinement absorption, unscheduled suppression — is
> either a stored field or a single shared function in `domain/schedule/projection.py` that
> every projector calls. A second implementation of a scheduling predicate anywhere under
> `app/services/*_projection.py` is a review-blocking violation.

---

## D-6 — HIGH — Four grammars for "the identity of one occurrence", and no AD covers keys

**Unit A — the operational overlay**, `operational_read.apply_engine_dates`. Matches by
`normalize_key(instance["name"])` — the visit's **display name**.

**Unit B — the reminder reconciler**, `schedule_projection.reconcile`. Keys on
`f"reminder:{logical_key}:{nominal_date.isoformat()}"` and strips the date with
`key.rsplit(":", 1)[0]`.

**Unit C — the materializer**, `schedule_service.py:769`:
`logical_key = f"{event_code}:{occurrence_index}"`.

**Unit D — the activity recorder**, `schedule_service.py:661`:
`logical_key = f"{event_code}#{occurrence_index}#{activity_code}"` — while the
*materializer's* activity key 95 lines earlier (L566) is
`f"{row.logical_key}#{activity_code}"`, i.e. `EVENT:1#ECG`, colon then hash.

**ADs they all obey.** Consistency Conventions, Data & formats: *"Ids are UUID."* None of
these is an id — they are keys, and the spine has no rule about keys. AD-8 is satisfied
(each row carries UUID provenance alongside). AD-6 is about enums, not key grammars.

**How they diverge, concretely.**

1. `record_activity` writes `PatientActivityRecord.logical_key = "C1D1#1#ECG"`. `_context`
   (L501-505) reads those records into `activity_statuses` keyed by that string, and the
   evaluator matches them against activities the materializer keyed as `"C1D1:1#ECG"`.
   **They never join.** A recorded activity actual is silently invisible to the next
   evaluation. Both formats are constructed in the same class.
2. The reminder key embeds `logical_key`, which itself contains a `:`, and is then split
   with `rsplit(":", 1)`. That survives today and stops surviving the day anyone adds a
   third key component or an ISO *datetime* (which contains colons). The reconciler's
   "moved visit ⇒ cancel + add" guarantee — a patient-safety rule from doc 1 s24 — depends
   on that split being right.
3. Name-based matching in the overlay means renaming a visit's `display_name` in an
   amendment breaks the operational date overlay for every existing patient, while parity
   stays green because parity ran before the rename. The overlay degrades *silently and
   safely* (`uctsm_source_of_truth: False`), so nobody notices the whole trial has quietly
   reverted to legacy dates.

**Proposed fix — add AD-12.**

> **AD-12 — one occurrence identity, constructed in one place.** `logical_key` has exactly
> one grammar, defined by constructor functions in the canonical model
> (`event_key(event_code, occurrence_index)`, `activity_key(event_key, activity_code)`),
> using a separator the model's validators forbid inside any code. Building a key by string
> interpolation outside those constructors is a review-blocking violation, and every
> key-consuming split goes through a matching `parse_*` function. **Display-name matching is
> confined to the parity gate** (where a human adjudicates under AD-1a) and is banned from
> every read and write path; the overlay joins on `uctsm_logical_key`, which the bridge
> already writes onto the operational document.

---

## D-7 — HIGH — The operational bridge emits blank workflow state; the operational read refuses to. Both obey AD-1

**Unit A — the read overlay**, `services/operational_read.py`. Its docstring is emphatic:
overlays `OVERLAID_FIELDS = ("scheduled_date", "window_start", "window_end")` **only**,
*"nothing is created and nothing is deleted... a read cannot lose a site's comment thread"*,
and `unmatched_engine_visits` deliberately returns names rather than creating documents.

**Unit B — the compatibility bridge**, `services/operational_projection.py:41-92`. It emits
*whole* visit documents including:

```python
"note": "", "clinical_tasks": [], "admin_tasks": [], "comments": [],
"updated_by": None, "status": operational_status,
"seq": sequence, "visit_number": sequence,
```

**ADs both obey.** AD-1 (the engine is authority for *dates*; `visit_instances` stays the
record for workflow — the bridge is literally described as a "compatibility projection for
legacy consumers"), AD-3 (`PROJECT --> OPS` is a declared edge in the dependency graph, and
the prohibition is on writing back into *Compile or Evaluate*, not into OPS), AD-8 (every
row carries `uctsm_schedule_version_id` and `uctsm_evaluation_id`).

**How they diverge.** **No AD decides whether the bridge's rows are persisted.** They are
`dict`s. If any caller upserts them into `visit_instances` — which is precisely what a
"compatibility projection for legacy consumers" is for — then `comments: []` overwrites a
site's comment thread, `clinical_tasks: []` erases the CRC's task list, and
`updated_by: None` erases attribution. That is the single failure AD-1 was written to
prevent, executed by a unit that obeys AD-1.

Three narrower collisions inside the same document:

- **`status`.** The bridge writes `planned` / `cancelled` / `paused` / `manual_review` into
  the same key the site workflow writes `completed` into. It also writes `canonical_status`
  and `operational_status` alongside — three status fields, one a merge of the other two,
  with no rule for which wins on read.
- **`seq` / `visit_number` = `enumerate(events, start=1)`.** A Project-invented ordinal for
  a concept AD-4a assigns solely to Repeating (`occurrence_index`). The projector's ordinal
  is *evaluation-order dependent*: it renumbers whenever the event set changes, so "visit 7"
  means a different visit after an amendment.
- **`scheduled_end = nominal`.** A multi-day confinement projects as a zero-length visit,
  while `schedule_projection` correctly emits a `CONFINEMENT` calendar entry spanning
  `start_date..end_date`. Two projectors, two shapes for one admission.

**Proposed fix — add AD-1b.**

> **AD-1b — the operational document has one writer per field, enumerated.** The canonical
> side may write only `scheduled_date`, `window_start`, `window_end` and keys prefixed
> `uctsm_`. It may not emit an operational-workflow key *at all* — not even empty — so an
> upsert is structurally incapable of blanking one. Operational status and canonical status
> never share a key: `status` is operational, `uctsm_status` is canonical, and no field
> merges them. Ordinals presented to users come from `occurrence_index` (AD-4a, Repeating);
> a projector may not number rows. Bridge output is `$set`-shaped by construction.

---

## D-8 — HIGH — AD-4d names a field that does not exist, and there are three version pins per patient

**AD-4d text:** *"Every engine resolves its rules from `Patient.schedule_version_id`."*

**What exists:** `Patient.current_schedule_version_id`,
`PatientSchedule.schedule_version_id`, `PatientEvent.schedule_version_id` (AD-8 provenance),
and `PatientSchedule.current_evaluation_id`. Four handles on "which version", and the AD
names none of them.

**Unit A — the reminder worker**, `reminder_delivery.compute_patient_projection`
(`services/reminder_delivery.py:38-95`). It reads *events* from
`patient_schedule.current_evaluation_id` — the version pinned when the evaluation ran — and
in the very next block computes *confinements* from
`service.evaluate_result(patient, patient.current_schedule_version_id, ...)` — the patient's
**current** pin.

**Unit B — the deviation service**, `deviation_service.report`. Reads events from
`current_evaluation_id` but stamps the report
`schedule_version_id=str(patient.current_schedule_version_id)`.

**ADs both obey.** AD-4d — neither reads "the trial's current version"; both read a
per-patient field. AD-8, AD-3, AD-9.

**How they diverge.** Between an amendment being pinned to a patient and the next evaluation
running — a window that exists by design, since D-3 shows evaluation is a separate call —
the reminder worker mixes v2 confinement structure with v1 events. A v2 confinement episode
lists `member_event_codes`, and `absorbed` then suppresses reminders for member codes that
*do* exist in v1. The patient receives **no reminder at all** for a real visit, and the
suppression is logged as *"inside confinement episode X"* — a correct-looking reason for a
wrong answer. Meanwhile the deviation report labels v1 rows as assessed under v2, defeating
AD-8's whole purpose (and its own docstring: *"a v1 patient keeps being assessed against v1
windows"*).

A second, quieter version leak: `_context` (`schedule_service.py:485-497`) collects
`actual_event_values` across **all** of a patient's `PatientSchedule` rows with no version
filter, so a v1 occurrence feeds a v2 anchor derivation. AD-4d scopes *rules* by version and
says nothing about whether *facts* cross versions — a deliberate-looking silence that two
teams will resolve in opposite directions.

**Proposed fix — tighten AD-4d.**

> **AD-4d′ — the pin is the evaluation, not the patient row.** Name the field exactly. Every
> read path resolves its version as `PatientSchedule.current_evaluation_id →
> ScheduleEvaluation.schedule_version_id`; `Patient.current_schedule_version_id` is the
> *intended* version and is read only by the impact/proposal path. A patient whose intended
> version differs from the current evaluation's version is `RE_EVALUATION_REQUIRED`, and
> **every projector refuses to produce output for such a patient** rather than mixing. Add
> explicitly: patient *facts* are version-scoped for anchor derivation — an actual date
> recorded under v1 is admissible under v2 only through an explicit carry-forward recorded
> as provenance.

---

## D-9 — MEDIUM/HIGH — AD-4e fixes the order but not the pass count, and the horizon is a request parameter

**Unit A — Repeating (HOW OFTEN)**, which AD-4e places *before* Anchor.
**Unit B — Anchor (WHEN)**, which runs after.

**ADs both obey.** AD-4e, exactly and literally.

**How they diverge.** A `RollingHorizon` — "every 4 weeks until 12 months after first dose"
— is a count that is a *function of dates*. Repeating runs before Anchor and therefore
cannot know it. Two legal implementations:

- *Team A* has Repeating emit a conservative fixed count; Anchor dates them; some land past
  the horizon and are trimmed by whoever notices. Occurrence count = 14.
- *Team B* has the evaluator re-enter Repeating after Anchor to extend to the true horizon.
  AD-4e forbids a different *order*; it says nothing about a **second pass in the same
  order**. Occurrence count = 13.

Same protocol, same patient, two schedules, both AD-4e-compliant. AD-4e's stated purpose —
*"Prevents order-dependent results"* — is defeated by a rule that pins order but not
iteration or fixpoint.

**Compounding: the horizon has no owner.** It arrives from the HTTP request body
(`EvaluateIn.horizon`, `api/uctsm.py:833`) and is passed straight through
`evaluate(... horizon=body.horizon ...)`. `occurrence_count` is a field AD-4a assigns solely
to Repeating — yet a **caller** determines it. Two callers with different horizons produce
two different materializations of one patient, last writer wins, with no record that the
horizon differed. The reminder worker uses `today + 365d` (`reminder_delivery.py:88`); a UI
might send 180 days; the deviation service uses its own.

**Proposed fix — tighten AD-4e and add a row to AD-4a.**

> **AD-4e′ — single pass, stated.** The composition is exactly one pass in the stated order,
> with no re-entry and no fixpoint iteration. A rolling horizon is expressed as Repeating
> emitting an *unbounded, lazily-materialized* occurrence generator with its termination
> predicate attached; Anchor materializes it against the horizon as it dates. If any engine
> genuinely requires a second pass, that is an architecture change, not an implementation
> choice.
>
> **AD-4a, new row:** `horizon_end` — sole writer **Repeating**, resolved from the schedule
> version and the patient's pin. `horizon` is **not** a request parameter; a caller may
> request a *view* window, which filters output and never changes what is materialized.

---

## D-10 — MEDIUM — AD-3's "never an edit" and AD-4c's "writes into definitions" are in direct tension

**Unit A — `ScheduleReviewService.correct_event`**, behind
`PUT /schedule-versions/{id}/events/{event_id}` (`api/uctsm.py:633-656`). Requires an
`X-Review-Reason` header and raises `ImmutableScheduleError` — so someone thought about it.

**Unit B — `ScheduleReviewService.resolve_qualifier`**, behind
`POST /schedule-versions/{id}/qualifiers/{qualifier_id}/resolve` (`api/uctsm.py:658-680`).
Its docstring: *"Without this endpoint nothing could ever clear that block, so a protocol
with a single footnote produced a draft that could not be approved at all."*

**ADs both obey.** AD-3: *"Compile writes an immutable `ScheduleVersion`"* — immutability is
asserted of the artifact, but the AD never says **when** it becomes immutable. AD-4c:
*"Qualifier resolution writes into the other engines' definitions during Compile — a timing
qualifier becomes an activity anchor, an applicability qualifier becomes an applicability
rule."* That is, by construction, a write that changes protocol meaning, and AD-4c
authorizes it.

**How they diverge.** AD-3's closing sentence — *"a correction to protocol meaning is a new
version, never an edit"* — carries **no `DRAFT` qualifier**. Two teams:

- *Team Compile* reads AD-3 literally: no edit ever, at any status. It implements
  `correct_event` to fork a new `ScheduleVersion` on every correction, producing a version
  per keystroke during review.
- *Team Review-UI* reads AD-4c: qualifier resolution legitimately writes into definitions,
  so definitions are writable during the review loop. It edits in place and guards only on
  `APPROVED`.

And here is where a backward edge sneaks in *legitimately*: a reviewer resolves a footnote
on a version that has already been **approved** — because AD-4c presents qualifier
resolution as a compile-time activity with no status precondition, and because the
resolution genuinely is compile-time work someone forgot to finish. That silently re-dates
every patient already pinned to that version, which is exactly what AD-4d exists to prevent
— and AD-4d cannot see it, because the patient's version id never changed.

**Proposed fix — tighten AD-3.**

> **AD-3′ — approval is the freeze, and it is named.** A `ScheduleVersion` and every
> definition it owns are mutable **only** while `status == DRAFT`. Approval is the
> immutability boundary. All AD-4c qualifier-resolution writes occur strictly before
> approval; a qualifier discovered after approval blocks the *next* version and never edits
> the approved one. **Enforcement point:** the guard lives in the repository/session layer
> (a `before_flush` hook rejecting any mutation of an approved version's object graph), not
> in each endpoint — endpoint-level checking is exactly what has already been applied to
> `correct_event` and not to `resolve_qualifier`.

---

## D-11 — MEDIUM — "Every state transition writes an AuditEvent carrying before and after" has no enforcement point

**Unit A — `record_activity`** (`schedule_service.py:679-695`). Correct:
`before={"status": superseded[-1].status, "actual_time": ...} if superseded else None`.

**Unit B — `record_occurrence`** (`api/uctsm.py:1484-1490`). Flips `event.status` to
`COMPLETED`, then writes
`db.AuditEvent(..., before=None, after={"actual_date": ..., "status": body.status})`.
The previous status was in memory, on the `event` object, one line above. It is dropped.

**ADs both obey.** The rule lives in the **Consistency Conventions** table, not an AD. It
binds nothing, names no owner and has no test. Both handlers write *an* AuditEvent with *a*
`before` key, so both comply with the text as written.

**How they diverge.** Half the audit trail can answer "what did it change from" and half
cannot, and which half is decided by whoever wrote the handler. `db.AuditEvent(...)` is
constructed directly in at least ten places in `schedule_service.py` alone plus every
endpoint — there is no single place a reviewer can look to know the invariant holds.

**Proposed fix — promote to AD-13 with a mechanical enforcement point.**

> **AD-13 — audit is written by one helper, and `before` is mandatory for updates.**
> `AuditEvent` is never constructed directly. All transitions go through
> `record_transition(entity, action, before, after, actor, reason)`, which reads `before`
> from SQLAlchemy attribute history rather than trusting the caller. `before` is
> non-nullable for any entity that already existed. Direct `db.AuditEvent(...)` construction
> outside that helper is a review-blocking violation.

---

## D-12 — MEDIUM — AD-1a adjudication survives the change that actually invalidates it

**AD-1a text:** *"Adjudication is per patient per schedule version and is invalidated by a
new version exactly as a run is."*

**Unit A — `ParityService.readiness`.** Passes a patient on `MATCH ∨ ACCEPTED`.
**Unit B — the legacy operational path.** AD-1 explicitly preserves `visit_instances` as the
permanent record for *"comments, tasks, completion, **site edits**"*.

**ADs both obey.** AD-1, AD-1a. Neither is doing anything wrong.

**How they diverge.** A reviewer adjudicates a `DATE_DIFFERS` as `ACCEPTED` with the reason
"legacy invented a date; the engine is right". Three weeks later a coordinator edits that
visit's date in the operational store — a first-class, AD-1-blessed operation. The
adjudication is not invalidated, because **no new schedule version was created**. The trial
then cuts over on a sign-off of a comparison that no longer describes either system.

Worse in the other direction: `apply_engine_dates` documents that ambiguous name matches
*"the parity gate refuses to pass in that case, so reaching here means something changed
since"* — the code already knows the gate's evidence can go stale, and handles it by
silently falling back to legacy dates for that visit. Cutover completeness is therefore
unobservable: a trial can be nominally in `ENGINE` mode with an arbitrary fraction of its
visits still showing legacy dates, and nothing counts them.

**Proposed fix — tighten AD-1a and AD-1.**

> **AD-1a′ — adjudication is keyed by what was compared.** A `ParityRun` stores a hash of the
> legacy rows it compared. Adjudication is keyed
> `(patient, schedule_version_id, legacy_snapshot_hash)` and is invalidated by *any* change
> to those rows, exactly as by a new version.
>
> **AD-1 addition — cutover is measured, not declared.** A trial in `ENGINE` mode reports a
> continuous `engine_backed_fraction` derived from `uctsm_source_of_truth`. A drop below
> 100% is an alert, not a silent read-note, and `schedule_read_mode` cannot reach its
> terminal state while the fraction is below 100%.

---

## D-13 — MEDIUM — AD-5's ban on defaults does not reach the projection layer, and both projectors default

**Unit A — `schedule_projection.project`** (L228-229):
`start = event.earliest_date or event.nominal_date`, `end = event.latest_date or event.nominal_date`.

**Unit B — `notification_projection.project_notification_candidate`** (L28-29): the identical
fallback, independently written.

**ADs both obey.** AD-5 binds *"extraction, every engine, every screen"*. A projection
service is not extraction, not an engine, and not a screen — it is the layer between. AD-3
protects it further: it writes nothing, so it cannot be said to have *stored* a default.

**How they diverge — and this one produces a wrong clinical statement.** A protocol that
**states no window** and a protocol that states **"day 15 exactly, ±0"** are represented
identically after this substitution: `earliest == latest == nominal`. Both then produce
`OVERDUE` on day 16. For the first protocol that is an invented assertion — the protocol
never said the visit could not happen on day 16 — and it is exactly the class of failure
AD-5's own text calls *"the failure the whole subsystem exists to avoid"*. Meanwhile
`schedule_projection` is scrupulous about *not* defaulting `event_type` (its comment on
`ProjectedEvent.event_type` explains at length why a default there is unacceptable), so the
same file both honours and violates AD-5 — which is what happens when an AD's binding list
omits a layer.

A third instance, in Evaluate rather than Project: `_context`
(`schedule_service.py:479-481`) resolves anchor selection as
`str((derivation_rule or {}).get("selection", "LAST")).upper()` — **an unstated selection
rule silently defaults to LAST**. For a repeating anchor event ("date of last dose" vs "date
of first dose") that is a direct AD-5 violation inside an engine's context builder, and AD-2
says this semantic must be an extraction *output*, never a downstream inference.

**Proposed fix — tighten AD-5.**

> **AD-5′ — AD-5 binds every layer, named exhaustively: extraction, the canonical model,
> every engine, every service, every projector and every screen.** Specifically: a missing
> `earliest_date`/`latest_date` projects as `window: UNSTATED` and can never produce
> `OVERDUE`; a missing anchor `selection` is a blocking validation issue at approval, not a
> runtime `.get(..., "LAST")`. **Enforcement point:** a lint rule banning `or <fallback>` and
> `.get(key, <non-None default>)` on any canonical scheduling field, plus a fixture protocol
> with no stated windows whose projection must contain zero `OVERDUE` rows.

---

## D-14 — MEDIUM — Project is allowed to call Evaluate, and one of the two entry points persists

**Unit A — `PatientScheduleService.evaluate_result`** (`schedule_service.py:416-434`). Pure:
builds a context, calls `ScheduleEvaluator().evaluate(...)`, returns. Writes nothing.

**Unit B — `PatientScheduleService.evaluate`** (L694). Materializes: writes
`PatientSchedule`, `PatientEvent`, `PatientActivity`, `AuditEvent`.

Two methods, adjacent in one class, seven characters apart in name, opposite in effect.

**ADs both obey.** AD-3 — *"Project reads patient rows and writes nothing back"*, and the
dependency graph declares `PROJECT --> EVAL` as a legal edge, so a projector calling Evaluate
is explicitly sanctioned. AD-4d, AD-8.

**How they diverge.** `reminder_delivery.compute_patient_projection` — a **Project** unit run
by a background worker — calls `evaluate_result`. Correct today. The next projector (a
dashboard, a calendar feed, a site worklist) that needs a *materialized* row and
autocompletes to `evaluate` turns a read path into a write path, executed by a background
worker, on a schedule, with no actor and no reason (see D-3, which makes that write legal).
AD-3's "no backward edges" prohibition is about *data*, and this is not a backward data edge
— it is Project causing Evaluate to write forward. Nothing in the spine forbids it, and the
naming actively invites it.

**Proposed fix — tighten AD-3.**

> **AD-3′′ — Project is call-pure with respect to Evaluate.** A module under Project may call
> only functions declared side-effect-free. The two entry points are renamed to make the
> difference unmissable (`compute_evaluation` vs `materialize_evaluation`); the materializing
> one is private to the service and reachable only through AD-10's chokepoint; the pure one
> is the only symbol Project may import. **Enforcement point:** an import linter —
> `app/services/*_projection.py` and `reminder_delivery.py` may not reference the
> materializing symbol.

---

## D-15 — LOW/MEDIUM — AD-2a's routing predicate is "empty text", which the realistic failure does not trigger

**Unit A — the embedded-text ingestion implementation.**
**Unit B — the page-image ingestion implementation.**

**AD both obey.** AD-2a: *"A page whose text layer is empty MUST route to page-image;
extraction may never proceed on empty text."*

**How they diverge.** The dangerous input is not an *empty* text layer — it is a **bad** one:
a scanned protocol with a low-quality OCR layer already embedded, which yields non-empty
garbage. The MUST never fires. Two teams: one implements the predicate literally
(`if not text.strip()`), one adds a quality gate (character confidence, or "does this page
contain a recognizable schedule-of-assessments grid"). The first produces *"a confident
schedule from an empty text layer"* — the exact outcome AD-2a's **Prevents** clause names —
via a route the **Rule** clause does not cover. AD-2a also does not say what happens when
*both* implementations yield nothing.

**Proposed fix — tighten AD-2a.**

> **AD-2a′ — route on extractable content, not on emptiness.** The routing predicate is "the
> page yields no *extractable schedule content*", evaluated by one shared function, not
> `text == ""`. A page that yields nothing on both implementations is a blocking extraction
> issue under AD-5 — never a silently skipped page — and the run records which implementation
> produced each page's content as AD-8 provenance, so a reviewer can see which pages were
> read by which route.

---

## D-16 — LOW — Internal inconsistencies that different readers will resolve differently

1. **"Eight ordered engines" vs ten bound engines vs seven nodes.** The paradigm mermaid says
   *"Eight ordered engines"*; `binds:` lists ten; AD-4e's chain shows **seven** nodes
   (Applicability, Conditional, Repeating, Anchor, Confinement, Activity, Event Type).
   Version (ENG-9) is the outer scope and Dependency mode (ENG-4) is a parameter (AD-4b),
   which reconciles to seven — so "eight" is simply wrong, and a reader trying to place the
   eighth will place it somewhere.
2. **AD-4a's table has seven rows for seven engines but omits `status` composition entirely**
   (see D-2) and omits `logical_key`, `logical_occurrence_id`, `protected_history`,
   `generation_reason` and `horizon_end` — all fields the materializer writes, none owned.
3. **AD-1's `UCTSM_AUTHORITATIVE` and `Trial.schedule_read_mode` are two switches for one
   concept** (global env flag, per-trial column) with no stated precedence. A trial in
   `ENGINE` mode with `UCTSM_AUTHORITATIVE=false` is a defined-looking state with no defined
   behaviour.
4. **"Reverting a trial to `LEGACY` stays ungated forever"** has no owner and no audit
   requirement — the one operation that instantly changes every date a site sees is the one
   operation with no gate, no reason and no named actor.
5. **AD-9's carve-out is broad enough to swallow the rule.** *"Expected values may be
   recalculated freely for deviation analysis — recalculating an expectation is not rewriting
   history."* Under D-1, `nominal_start_date` is simultaneously an expectation (recalculable
   freely) and the frozen historical value the deviation report is assessed against. Two
   teams will classify it differently, and AD-9 supports both.

---

## Proposed AD changes, collected

| # | Change | Closes |
| --- | --- | --- |
| **AD-4a′** | Sole-writer ownership binds *every code path* that assigns the field on a persisted row, not only "engines" | D-1 |
| **AD-4f** | Freezing is additive (`frozen_<field>` + `frozen_by`/`frozen_at`/`frozen_reason`), never substitutive into an owned column | D-1 |
| **AD-4a-status** | `PatientEvent.status` is protocol-lifecycle only; members exhaustively enumerated with one owner each; `COMPLETED`/`MISSED` removed | D-2 |
| **AD-4a-materialization** | Fields AD-4a assigns to engines are *stored columns*, not values projectors re-derive (`requires_attendance`, `reminder_text`) | D-5 |
| **AD-4a, new rows** | `horizon_end` → Repeating; `logical_key`, `protected_history`, `generation_reason` given owners | D-9, D-16 |
| **AD-10** *(new)* | One write chokepoint: all patient-row materialization requires a proposal id, actor and reason; `evaluate` becomes evaluate-or-propose | D-3 |
| **AD-11** *(new)* | Anchor admissibility lifecycle; Evaluate reads `CONFIRMED` only; one anchor-candidate producer; un-defers the confirm prompt | D-4 |
| **AD-12** *(new)* | One `logical_key` grammar via model constructors; display-name matching confined to the parity gate | D-6 |
| **AD-1b** *(new)* | Enumerated writable fields on the operational document; canonical side emits no workflow key, blank or otherwise; no projector-invented ordinals | D-7 |
| **AD-13** *(new)* | Audit written by one helper that reads `before` from attribute history; `before` non-nullable for updates | D-11 |
| **AD-3′** | Project may format, never derive; every scheduling predicate is a stored field or one shared function | D-5 |
| **AD-3′′** | Project is call-pure w.r.t. Evaluate; materializing entry point unreachable from Project | D-14 |
| **AD-3 (freeze boundary)** | Mutability ends at approval, stated explicitly; AD-4c writes are pre-approval only; guard in the session layer | D-10 |
| **AD-4d′** | The pin is the evaluation, not the patient row; projectors refuse mixed-version patients; facts are version-scoped | D-8 |
| **AD-4e′** | Single pass, no re-entry, stated; rolling horizons as lazy generators; horizon is not a request parameter | D-9 |
| **AD-5′** | AD-5 binds every layer including services and projectors; `window: UNSTATED`; lint against `or`/`.get` defaults on canonical fields | D-13 |
| **AD-1a′** | Adjudication keyed by `legacy_snapshot_hash`; invalidated by legacy edits | D-12 |
| **AD-1 (measured cutover)** | `engine_backed_fraction` gates the terminal state; a revert to `LEGACY` requires actor and reason | D-12, D-16 |
| **AD-2a′** | Route on "no extractable schedule content", not `text == ""`; both-empty is blocking; per-page route recorded as provenance | D-15 |

## Rules currently stated with no enforcement point

These are the highest-leverage gaps, because each is already enforced inconsistently in the
existing code:

| Rule | Where stated | Evidence of inconsistent enforcement |
| --- | --- | --- |
| Preview → confirm for impactful changes | Consistency Conventions | `/patients/{id}/states` hard-disabled; `/patients/{id}/schedule/evaluate` wide open |
| AuditEvent carries before **and** after | Consistency Conventions | `record_activity` correct; `record_occurrence` writes `before=None` over a known prior status |
| Nothing unstated is defaulted | AD-5 | `event_type` scrupulously undefaulted; anchor `selection` defaults to `"LAST"`; windows default to the nominal date in two projectors |
| The engine never acquires completion equivalents | AD-1 | `PatientEventStatus.COMPLETED`/`.MISSED` exist and are written by an HTTP handler |
| One canonical event-type vocabulary | AD-6 | Already fixed once in `schedule_projection.py` after a real divergence — the comment at L30-39 documents the exact failure this review predicts will recur elsewhere |
| Ids are UUID | Consistency Conventions | Silent on keys; four `logical_key` grammars in one service class |

## What the spine gets right

Worth stating, because the fixes above should not disturb it: the Compile/Evaluate/Project
split is real and load-bearing; AD-2b (extraction never produces a patient date) is a clean,
checkable invariant; AD-1's insistence that `visit_instances` keeps workflow state is the
decision that makes the cutover survivable, and `operational_read.py` implements it
faithfully; AD-1a's adjudication-with-reason is a genuinely good answer to an unwinnable
parity gate; and AD-5 is the correct organizing fear for this domain. Every finding above is
about *scope of binding* and *enforcement point*, not about the paradigm, which holds.
