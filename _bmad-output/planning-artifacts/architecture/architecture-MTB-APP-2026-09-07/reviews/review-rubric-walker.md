# Rubric Walker Review — ARCHITECTURE-SPINE.md (MTB Scheduling Subsystem)

- **Target:** `_bmad-output/planning-artifacts/architecture/architecture-MTB-APP-2026-09-07/ARCHITECTURE-SPINE.md` (status: final, 2026-09-07)
- **Reviewer role:** rubric walker — the good-spine checklist, item by item
- **Grounding read:** `AGENTS.md`, `backend/app/domain/schedule/models.py`, `backend/app/services/parity_service.py`, plus corroborating reads of `backend/app/domain/schedule/evaluator.py`, `backend/app/db/base.py`, `backend/app/db/models.py`, `backend/app/extraction/`, `backend/app/services/`
- **Verdict:** **Strong spine, not yet final.** The paradigm is right, the ten capabilities are covered, and most ADs ratify real code. But the spine's load-bearing AD (AD-4a) is written at a field granularity the model it binds does not have; the operational/environmental envelope is deferred in a way that already permits divergence; and one known, documented divergence point (the two audit trails) is missed entirely.

---

## Checklist item 1 — Does it fix the real divergence points, and miss none?

**Mostly. Three misses.**

What it gets right, and these are the hard ones:

- **AD-1 / AD-1a** name the dual-store problem precisely and — unusually — fix the *right* thing. Splitting "schedule meaning and dates" (engine) from "operational workflow state" (`visit_instances`) is the distinction `AGENTS.md` says three legacy screens depend on, and it is the distinction that makes the cutover finishable rather than perpetual. AD-1a's adjudication path solves a real terminal-state bug: `ParityService.readiness()` today passes only on `ParityVerdict.MATCH`, so a trial whose legacy path invented a date can never reach `ENGINE`. That is a genuine architectural fix, not paperwork.
- **AD-4d** (version as outermost scope) and **AD-8** (row-level provenance) together close the amendment-divergence hole. Correct and enforceable.
- **AD-3 / AD-2b** close the phase-leak holes cleanly.
- **AD-4e** fixes composition order, which is the single most likely source of two implementers producing different schedules from the same inputs.

### Miss 1 — The two audit trails (HIGH)

Consistency Conventions state: *"Every state transition writes an `AuditEvent` carrying before and after."* Two unreconciled audit logs exist in this codebase and the spine never chooses between them:

- Postgres `uctsm_audit_events` — `backend/app/db/models.py:587`, written by e.g. `ParityService.set_read_mode`
- Mongo `audit_logs` — `backend/server.py:685`, `:7850`, read at `:9879` with its own fail-closed role scoping (`_scope_audit_logs`, `:9781`)

`AGENTS.md` calls this out explicitly: *"The audit trail is two unreconciled logs — Mongo `audit_logs` vs. Postgres `uctsm_audit_events` — neither writes the other."* AD-1 then guarantees the Mongo side keeps producing state transitions **permanently** (comments, tasks, completion, site edits stay there forever, and the engine is forbidden from acquiring equivalents). So the spine has decided that the divergence is permanent while its convention speaks as if one log existed.

Two units building against this spine will each pick a log, and no reviewer will be able to reconstruct a patient's history from either alone. The spine needs an AD that says which transitions land where, whether an operational transition must mirror into `AuditEvent`, or that the two are deliberately disjoint by concern with a stated join key.

### Miss 2 — Tenant scoping is a load-bearing invariant nowhere stated (MEDIUM)

`organization_id` scoping is pervasive and load-bearing in `parity_service.py` — every lookup filters on it, and `compare_patient` raises `KeyError` rather than leaking across orgs. Nothing in the spine requires this. Scope excludes "auth", but tenant scoping *on schedule reads and evaluations* is inside the subsystem, and it is exactly the kind of invariant a second implementer omits from a new repository method. One line in Consistency Conventions would close it.

### Miss 3 — The frontend read path is in scope and bound by no AD (MEDIUM)

Scope names *"the operational read path"*; the Structural Seed lists `frontend/src/features/uctsm/` and `frontend/app/(app)/`. No AD binds any screen behaviour. AD-5 binds "every screen" but supplies no screen-level mechanism. Meanwhile `AGENTS.md` documents live frontend divergences the spine is silent on:

- `add-trial.tsx:305–310` silently drops the sponsor onto the legacy editor when the canonical build throws — the exact class of silent fallback AD-5 exists to forbid, on the other side of the wire.
- `clinical/add-patient.tsx` renders `ProtocolAssignmentFields` which silently renders nothing when no canonical schedule exists.
- `getReadMode()` / `setReadMode()` exist with zero callers — a complete cutover mechanism with no product surface.

A spine that binds `schedule_read_mode` as its central migration device but never says how a screen must behave in either mode leaves each screen free to decide, which is divergence by definition.

---

## Checklist item 2 — Is every Rule enforceable, and does it actually prevent its stated divergence?

**Two ADs fail this. Several are exemplary.**

Exemplary (testable constraints a reviewer can check mechanically): AD-4b (`UNCLEAR` blocks approval, mode writes nothing), AD-4d (resolve from `Patient.schedule_version_id`, never "the trial's current version"), AD-4e (fixed order), AD-8 (three named columns on every materialized row), AD-1a (no auto-accept, no bulk accept, no accept without a reason; invalidated by a new version).

### Finding A — AD-4a's field-ownership table is not expressible on the model it binds (CRITICAL)

This is the spine's centrepiece and the one AD everything else leans on. The Rule is a table of *fields* with one writer each. The model has no such fields.

`EvaluatedEvent` (models.py) carries a single `status: PatientEventStatus`. Its members are owned by at least four different engines:

| Status member | Engine that owns the decision |
| --- | --- |
| `NOT_APPLICABLE` | Applicability |
| `CANCELLED`, `WAITING_FOR_CONDITION` | Conditional |
| `BLOCKED`, `PAUSED` | Conditional / block state |
| `WAITING_FOR_ANCHOR`, `UNRESOLVED` | Anchor |
| `AVAILABLE_ON_DEMAND` | Event Type / `activation` |
| `COMPLETED`, `MISSED` | operational, i.e. outside Evaluate entirely |

`evaluator.py` writes `PatientEventStatus.*` from eighteen distinct sites (lines 162, 181, 960, 966, 970, 1013, 1023, 1120, 1129, 1137, 1151, 1169, 1187, 1190, 1198, 1218, 1233, 1241, 1265). AD-4a's row *"activated / cancelled / paused → Conditional"* names a concept, not a field, and the field it actually lands in is written by everyone. So the rule as stated cannot be checked, and the divergence it exists to prevent — last-writer-wins on a shared field — is precisely what the ratified model permits.

Compounding this, the table:

- **Names two fields that do not exist:** `reminder wording` and `requires_attendance` appear nowhere in `EvaluatedEvent`, `EvaluatedActivity`, or the DB models.
- **Omits fields that do exist and are contested:** `visit_mode` / `allowed_visit_modes` (the Capability map assigns these to ENG-10, the table does not), `explanation` (written by every engine by construction), `unscheduled_reason`, `dependency_result`, `occurrence_index` on activities, and the four `EvaluatedConfinement` date fields (`planned_admission`, `actual_admission`, `planned_discharge`, `actual_discharge` — dates, which AD-4a gives to Anchor, on a confinement row, which AD-4a gives to Confinement).

The fix is not to delete the AD — the intent is exactly right. It is to restate ownership at a granularity the model has: either give each engine its own result slot on `EvaluatedEvent` that a final reducer folds into `status` in the AD-4e order, or make ownership a partition over *status members and explanation keys* rather than over fields, and name the reducer as the sole writer of `status`.

### Finding B — AD-5's Rule restates its Prevents without a testable constraint (HIGH)

> *Rule: An unstated value resolves to `UNRESOLVED`, `AWAITING_ANCHOR`, or a blocking validation issue naming what is missing. Never to a default...*

That is the Prevents ("a plausible-looking date, mode, cadence or maximum that no protocol states") restated as an imperative. It names no mechanism, no enforcement point, and — critically — no boundary between *protocol-stated* fields (which must never default) and *structural* fields (which may). Without that boundary the rule is simultaneously unenforceable and, read literally, already violated by the model the spine ratifies:

| Field | Default in `models.py` |
| --- | --- |
| `Activity.requiredness` | `REQUIRED` |
| `Event.activation` | `SCHEDULED` |
| `ApplicabilityRule.operator` | `"IN"` |
| `DayNumbering.counting_convention` | `"CLINICAL_DAY"` |
| `RecurrenceRule.include_start` | `True` |
| `ScheduleMetadata.schedule_type` | `"PRIMARY"` |
| `ScheduleMetadata.version_number` | `1` |

Several of these are exactly the "mode / cadence" class AD-5 says must never be guessed — a protocol that does not state whether an activity is required is not thereby stating `REQUIRED`. The model already contains the right pattern in one place: `RepeatBlock.resume_mode` defaults to `UNCLEAR`, and `Event.visit_mode` defaults to `None` with an explicit comment refusing to guess. AD-5 should mandate that pattern as its testable constraint — *no protocol-derived field may carry a pydantic default other than an explicit unresolved sentinel; the closed list of structurally-defaulted fields is enumerated here* — and reconcile the seven above.

### Finding C — AD-2a bundles two different ports into one Rule, and only one of them is specified (LOW/MEDIUM)

The ingestion port (embedded-text vs page-image, with the mandatory empty-text-layer routing) is specified well enough to test. The trailing sentence — *"Model providers sit behind the same port boundary — no engine or service imports a provider directly"* — is a second, different port with no named interface and no stated location. It happens to be partly ratified already (`LLMClient` Protocol at `backend/app/extraction/llm_client.py:40`, `claude_provider.py` behind it), which the AD does not mention. With two providers in the Stack (`anthropic`, `google-genai`) and no rule for selection or fallback between them, provider strategy is an undecided sub-dimension riding inside an AD about something else.

---

## Checklist item 3 — Could anything under Deferred let two units diverge?

**Yes, two of the seven.**

| Deferred item | Divergence-safe? |
| --- | --- |
| Extraction accuracy (no eval set) | Safe. Correctly framed as a measurement process, gated before first cutover. |
| **Reminder and calendar delivery** | **Not safe — see below.** |
| Auto-derived anchor confirmation | Safe. AD-5 covers the unresolved state in the interim. |
| Automatic condition detection from lab/EHR | Safe. Out of Phase 1 by spec; `ConditionState` already models the shape. |
| Existing-patient migration across versions | Safe. AD-4d explicitly keeps the model open to it. |
| **Deployment topology, environments, CI** | **Not safe — see item 6.** |
| WebSocket connection-map distribution | Safe. Correctly scoped out and flagged. |

### Finding D — Deferring reminder/calendar delivery defers a *contract*, not just a consumer (MEDIUM)

*"The projection is complete; no consumer reads it"* is true of the UCTSM projection, but not of the problem. Two independent reminder systems already exist and share no code — patient medication alarms (`db.reminders`, `server.py:8695–8714`) and the UCTSM projection-to-visit-reminder bridge (`server.py:5544–5633`) — per `AGENTS.md`. The spine also ships `notification_projection.py` and `reminder_delivery.py` in `app/services/`. Deferring the contract while two implementations are live means the third consumer (calendar) will be written against whichever of the two its author finds first. What is genuinely deferrable is *delivery* (FCM/APNs/Expo — `AGENTS.md` confirms nothing sends a push and the frontend never registers a token). What is not deferrable is which projection is the single source and what its shape is. Split the item.

---

## Checklist item 4 — Does it ratify rather than contradict the brownfield codebase?

**Largely yes — this is the spine's strongest quality.** Its factual claims check out against the tree: `canonical_import.py` and `canonical_bridge.py` exist and are still referenced from `server.py` and eight tests; `app/extraction/graph.py` emits `UniversalSchedule` natively; `_render_pdf_pages` is at `backend/protocol_extraction.py:1814` as stated; `app/services/schedule_projection.py`, `operational_read.py`, `operational_bridge.py` all exist; the parity/cutover mechanism is exactly as `AGENTS.md` describes. AD-1's treatment of `visit_instances` correctly ratifies the AGENTS.md policy line about the three legacy screens. AD-6's enum deltas are all real gaps against the current `models.py`.

Three contradictions, in descending severity.

### Finding E — AD-2's "never a downstream inference" is already violated by ratified code the spine does not name (HIGH)

`schedule_origin_anchor()` in `backend/app/domain/schedule/models.py` resolves the schedule's Day 0 anchor. Its primary path is correct and exactly what AD-2 wants — an explicit `SCHEDULE_ORIGIN_ROLE` recorded on `derivation_rule` by the producer, with a docstring that pointedly refuses to match on printed names. Then it falls through to:

```python
for anchor in schedule.anchors:
    if anchor.anchor_type.upper() == "BASELINE" or anchor.code == "BASELINE":
        return anchor
```

That is a name-matched downstream inference of the single most load-bearing value in the subsystem, living in the canonical model rather than in extraction. AD-2 says *"Every semantic the ten engines consume ... is an extraction output, never a downstream inference."* AD-5 says nothing unstated is ever defaulted; this defaults Day 0 to whatever is called BASELINE. The code's own comment is honest about why it exists (pre-origin persisted schedules, plus demo/native paths that mint a BASELINE anchor outright) — which means it is a **migration allowance**, and the spine should treat it the way AD-1 treats `schedule_read_mode`: a flag with a terminal state, a backfill, and a deletion. As written, the spine forbids something it also ratifies, and silently.

### Finding F — The "one engine per module" convention contradicts the spine's own Capability map (MEDIUM)

Consistency Conventions: *"One engine per module in `app/domain/schedule/`, named for the engine."*

Reality — and the spine's own Capability → Architecture Map — says otherwise. `app/domain/schedule/` contains `anchor_status.py, condition.py, conditional.py, deviation.py, diff.py, enrolment.py, evaluator.py, exceptions.py, models.py, parity.py, projection.py, timing.py, validator.py`. There is no `applicability.py`, `repeating.py`, `confinement.py`, `activity.py`, or `event_type.py`. `evaluator.py` is 1,270 lines and holds ENG-1, ENG-3, ENG-5 and most of ENG-2/6/8's evaluate-time behaviour; the Capability map itself points ENG-1, ENG-3 and ENG-5 at `evaluator.py`.

So the convention is either (a) an undeclared refactor mandate that no AD sizes or sequences, or (b) dead text. Either way, a reader following the convention and a reader following the Capability map will put new engine code in different files — a divergence produced by the spine itself. Note also that AD-4a's single-writer discipline is far harder to *review* while eight engines share one 1,270-line module, so (a) and Finding A are the same underlying problem.

### Finding G — AD-7 mandates a breaking model change without flagging it as one (LOW)

Ratified today: `ApplicabilityRule.operator: Literal["IN", "NOT_IN"]`, `values: list[str]`, `dimension` constrained by `^[A-Z][A-Z0-9_]*$`, no boolean composition. AD-7 mandates eight operators (`IN, NOT_IN, EQ, NE, LT, LTE, GT, GTE`) composable with `AND`/`OR`, plus a nine-member open dimension list. That is right — the "subjects aged ≥65" case is real and currently inexpressible — but it changes the shape of a field embedded in every persisted `UniversalSchedule`, `Event`, `Activity`, `ConditionalDefinition` and `ConfinementEpisodeDefinition`. AD-9 (append-only) and AD-4d (version pinning) mean old versions must stay readable. The AD should say whether this is additive-with-defaults or a schema_version bump; `schema_version: Literal["uctsm.v1"]` is a single literal today, so a second value has nowhere to live.

Same class, lower stakes: AD-6's `QualifierScope.BLOCK → {VISIT_BLOCK, SCHEDULE_BLOCK}` split is a *removal* of a live enum member, which every persisted qualifier with `scope=BLOCK` will fail to validate against. Worth one sentence about how those rows resolve — a reviewer decision, presumably, given AD-5.

---

## Checklist item 5 — Does it cover the driving spec's capabilities, ENG-1..ENG-10?

**Pass, and cleanly.** All ten are in `binds:`, all ten appear in the Capability → Architecture Map with a code location and governing ADs, and each has at least one AD that actually constrains it. The two engines that are *not* evaluate-time steps are handled correctly and deliberately: ENG-7 Qualifier is pinned to compile-time with no runtime authority (AD-4c), and ENG-9 Version is pinned as the outermost scope (AD-4d). ENG-4 is correctly demoted from an engine to a parameter (AD-4b), which is the right call and prevents a fifth date-writer.

Two clarity nits, neither substantive:

- The paradigm diagram says *"Eight ordered engines"*; AD-4e's diagram shows **seven** nodes; the spine binds **ten**. All three are reconcilable (10 − Qualifier − Version = 8 evaluate-time, − dependency-mode folded into Anchor = 7 nodes) but nowhere is that reconciliation stated, and a reader's first reaction is to look for a missing engine.
- ENG-5 Activity timing is governed by "AD-4a, AD-9" only. AD-5 arguably binds it harder than either — `_missing_activity_anchor` in `evaluator.py` exists precisely to refuse a guessed intra-day time. Worth adding.

---

## Checklist item 6 — Is every dimension this altitude owns decided, deferred, or an open question?

**This is the checklist item the spine fails hardest.**

Decided and well covered: persistence (dual store, ownership per concept), canonical data model and taxonomies, evaluation semantics and ordering, provenance and audit-of-record shape, extraction pipeline and ingestion, versioning, module dependency direction, stack pinning.

### Finding H — The operational/environmental envelope is deferred as a unit, and it is divergence-capable (HIGH)

> *Deferred: **Deployment topology, environments and CI.** No CI configuration exists in the repository; environment promotion is undefined. Not owned at this altitude yet — revisit when a second deployed environment exists.*

The Structural Seed's "Operational envelope" paragraph does real work — it fixes both stores, names Alembic as the canonical migration owner, states the operational store is schema-on-read, and puts extraction on a background worker keyed by idempotency key with failure recorded on the run. That much is decided and good.

What is left silent is not "topology and CI" as a nice-to-have. It is a set of contracts two units will each invent:

1. **Dev/prod schema provenance diverge silently.** `backend/app/db/base.py:23` — when the dialect is SQLite and `UCTSM_DEMO_MODE` is truthy, `build_session_factory` calls `Base.metadata.create_all(engine, checkfirst=True)`. Development schema therefore comes from the ORM; production schema comes from Alembic. Nothing compares them. A migration that is written wrong, or not written at all, passes every local run and fails only in the environment that matters. The spine names Alembic as the owner but does not state the invariant that makes that ownership real (*the ORM never creates schema in any environment that has a migration history; a model change without a migration is a build failure*).
2. **A concurrency guard that exists in prod and does not in dev.** `ParityService.set_read_mode` takes `with_for_update()` on the trial row — the lock that makes the cutover gate safe against a concurrent read-mode change. SQLite ignores `FOR UPDATE`. So the one mechanism AD-1 depends on for its terminal state is untested wherever the demo path is used, and the spine's only mention of SQLite ("in development only") reads as reassurance rather than as the caveat it is.
3. **No configuration/secrets contract.** `UCTSM_DATABASE_URL` is required-or-`RuntimeError` at first session; the Mongo URI, `UCTSM_AUTHORITATIVE`, `UCTSM_DEMO_MODE` and `DEV_OTP_MODE` are configured elsewhere with different conventions. `AGENTS.md` carries two standing "never enable X outside local dev" policies — `DEV_OTP_MODE` and `UCTSM_AUTHORITATIVE` — which are *environment* invariants with no environment model to hang on. AD-1 makes `UCTSM_AUTHORITATIVE` the terminal step of the entire migration; a spine that owns that flag's lifecycle cannot also disown the environment dimension it lives in.
4. **No verification contract.** `AGENTS.md` documents that plain `pytest` breaks across the suite (anyio/starlette teardown), that the working gate is a curated 34-file list in a text file, that `npm run lint` cannot run at all here, and that whole-project `tsc --noEmit` OOMs. That is a *shared build substrate* fact — the definition of "green" is currently tribal knowledge in a handoff document. Two units will disagree about whether their change passes.

"Revisit when a second deployed environment exists" defers the wrong trigger: the divergence is between *development and production today*, not between two future deployments. At minimum this should become an open question with named sub-dimensions (schema provenance, dialect parity, config/secrets, definition of green) rather than a single deferred line.

### Finding I — Calendar-day authority is undecided (LOW/MEDIUM)

Conventions fix representation — *"Day-level timing is `date`; intra-day is timezone-aware `datetime` in UTC"* — but not semantics: whose local day a `date` denotes. `PatientContext.anchors`, `event_values`, `activity_actuals` and `ResolvedTiming` all accept `date | datetime` interchangeably, and `evaluator._as_date` collapses one into the other. In a multi-country trial (AD-7 makes `COUNTRY` a first-class dimension, and AD-6 adds `COUNTRY_RESTRICTION`), a UTC-derived date and a site-local date differ for a real fraction of visits, and a window boundary computed either way will disagree. One sentence deciding site-local, patient-local, or UTC — and where the conversion happens — closes it.

---

## Summary of findings

| # | Sev | AD / section | Finding |
| --- | --- | --- | --- |
| A | CRITICAL | AD-4a | Single-writer table is written over fields the model does not have; `PatientEventStatus` is one field owned by four engines and written from 18 sites. Names two nonexistent fields, omits several contested ones. |
| E | HIGH | AD-2 / AD-5 | `schedule_origin_anchor()`'s name-matched BASELINE fallback is a downstream inference of Day 0 in the canonical model — forbidden by AD-2 and unmentioned by the spine. |
| H | HIGH | Deferred — envelope | Operational/environmental envelope deferred as one line; dev/prod schema provenance, dialect parity (`with_for_update` is a no-op on SQLite), config/secrets and definition-of-green are all undecided and already divergent. |
| B | HIGH | AD-5 | Rule restates the Prevents with no testable constraint and no protocol-stated/structural boundary; seven silent defaults in the ratified model contradict it. |
| — | HIGH | Conventions | Audit dimension: one convention sentence over two permanently-coexisting audit logs, with no assignment or join. |
| F | MEDIUM | Conventions vs Capability map | "One engine per module" contradicts the spine's own map and the tree; eight engines live in one 1,270-line `evaluator.py`. |
| — | MEDIUM/HIGH | AD-1 | Internal contradiction: legacy authoring is deleted permanently, yet reverting to `LEGACY` "stays ungated forever" — the escape hatch degrades to stale reads with no stated end to the revert window. |
| D | MEDIUM | Deferred — reminders | Defers a contract, not a consumer; two reminder implementations already exist sharing no code. |
| — | MEDIUM | Scope / frontend | The operational read path is in scope but bound by no AD; known silent-fallback screens go ungoverned. |
| — | MEDIUM | Conventions | Tenant (`organization_id`) scoping is load-bearing in the code and absent from the spine. |
| — | MEDIUM | AD-4a | "reminder wording" assigned to an Evaluate engine, contradicting the paradigm's placement of reminders in Project and AD-3's no-presentation-in-evaluation rule. |
| I | LOW/MEDIUM | Conventions | No decision on whose calendar day a `date` denotes, with `COUNTRY` a first-class dimension. |
| C | LOW/MEDIUM | AD-2a | Two ports in one Rule; the model-provider port has no named interface, and two providers ship with no selection rule. |
| G | LOW | AD-7 / AD-6 | Breaking model changes (operator set; `BLOCK` enum split) mandated without a migration or `schema_version` story. |
| — | LOW | Paradigm / AD-4e | "Eight ordered engines" vs seven diagram nodes vs ten bound engines, never reconciled. |

## Recommended minimum before `status: final` holds

1. Rewrite **AD-4a** at a granularity the model has — per-engine result slots plus a named reducer as sole writer of `status`, or ownership over status-members rather than fields. Reconcile with the Capability map's field assignments.
2. Add an AD (or a Conventions row) resolving the **two audit trails**.
3. Give **AD-5** a mechanism: no pydantic default on protocol-derived fields, with the structural-default exceptions enumerated; then fix the seven.
4. Promote the **operational envelope** from one deferred line to an open question with named sub-dimensions, and decide the two that already bite: ORM-never-creates-schema, and dialect parity for the cutover lock.
5. Name the **`schedule_origin_anchor` fallback** as a migration allowance with a terminal state, the way AD-1 treats `schedule_read_mode`.
6. Resolve **AD-1's revert contradiction** — state when the ungated revert window closes.
7. Decide whether the **one-engine-per-module convention** is a mandate (then size it) or drop it to match the map.
