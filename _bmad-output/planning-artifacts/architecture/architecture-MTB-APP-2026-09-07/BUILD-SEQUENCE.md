---
name: 'MTB Scheduling Subsystem — Build Sequence'
type: build-sequence
companion_to: ARCHITECTURE-SPINE.md
altitude: initiative
status: draft
created: '2026-09-08'
---

# Build Sequence — MTB Scheduling Subsystem

Derived from `ARCHITECTURE-SPINE.md`. Every item traces to an `AD` or a Deferred entry; nothing here is new architecture. This orders the work, it does not re-decide it.

**Ordering rule:** a phase may not start until the phases it reads from can be *verified*, not merely written.

**Gate on the whole document:** AD-1 is `[PROPOSED — awaiting ratification]`. Phases 0–3 hold regardless of how AD-1 resolves. Phase 4 exists only if AD-1 is ratified as written.

---

## Start on day one — the two long-lead items

Zero code dependencies, longest lead time, both hard blockers on Phase 4. Starting them when Phase 4 begins is how this slips a quarter.

**L-1 · Extraction accuracy eval set** — Deferred #1, named blocker for the first cutover. Collect real protocols spanning the hard cases: scanned PDFs with no text layer, footnote-dense SoA tables, multi-arm/multi-cohort, confinement periods, amendments. A clinician produces the reviewed answer key. Then score. No architecture depends on this; Phase 4 cannot proceed without it. It is also the only evidence that will ever exist for "handles all cases" — that is a measurement claim, not a design claim.

**L-2 · Approval governance decision** — Deferred #2, AD-11. One approver or two, and may the approver be the person who entered the field decisions? A sponsor call, not an architectural one. AD-11 fixes the shape but cannot be built against a policy nobody has set.

---

## Phase 0 — Make the ground verifiable

Every later phase ships migrations and concurrency assumptions. None of them can be trusted until these land.

| Item | Source | Why here and not later |
| --- | --- | --- |
| `emergentintegrations==0.2.0` 404s on PyPI (`backend/requirements.txt:31`) | gate finding | A clean checkout does not install today. No onboarding and no CI is possible until this resolves. |
| Dev schema provenance | Structural Seed `[TARGET]` | `app/db/base.py` calls `Base.metadata.create_all` on SQLite under `UCTSM_DEMO_MODE`. Opt-in, but wherever it is on, `SELECT … FOR UPDATE` is a silent no-op — the exact lock AD-1's cutover depends on. Demo mode runs the migration chain, or it is labelled explicitly as not a rehearsal. |
| `motor==3.3.1`, vendor EOL 2026-05-14 (already past) | Stack table | AD-1 makes Mongo the *permanent* operational store. The pymongo async driver migration is named a prerequisite. Do it before Phase 3 writes more code against motor. |
| Close unbounded version floors: `anthropic`, `pypdfium2`, `pydantic`, `google-genai` | Stack table | Each resolves across a major boundary today. Phase 2 rewrites the extraction layer directly on top of `anthropic` and `pypdfium2` — pin before building on them, not after a silent major bump breaks the rebuild. |

**Exit:** clean checkout installs; dev and production schemas have one origin; no unbounded floor under a library Phase 2 touches.

---

## Phase 1 — Fix the model before anything writes into it

The decisive dependency in this plan. AD-2 requires extraction to emit *every* semantic the ten engines consume. If extraction is rebuilt first, it emits into enums and table shapes that Phase 1 then changes — and the rebuild is written twice.

| AD | Work | Note |
| --- | --- | --- |
| AD-6 | Close the taxonomies | `event_type` free string → closed taxonomy; `QualifierScope.BLOCK` splits into `VISIT_BLOCK`/`SCHEDULE_BLOCK`; `QualifierCategory` gains the three dimensional restrictions; `ScheduleStatus` gains enrolment-lifecycle states; `ConfinementStatus` gains `DISCHARGE_PLANNED`. Each with an explicit escape member. |
| AD-7 | Applicability becomes `(field, operator, value)` | Operators `IN/NOT_IN/EQ/NE/LT/LTE/GT/GTE`, `AND`/`OR` composable, open dimension set. Until this lands, "subjects aged ≥65" is unexpressible and therefore silently applies to everyone. |
| AD-8 | Provenance columns + persisted confinement episodes | `schedule_version_id`, source definition id, `protocol_version_id` on every materialized row. The confinement episode table this AD requires **does not exist** — new schema, not a column add. |
| AD-4f | One identity function for a materialized occurrence | `logical_key` is built in four places today. Consolidate before Phase 2 adds a fifth caller. |
| AD-4d | Collapse the version handles | `Patient.current_schedule_version_id` resolved once at the top of Evaluate and passed down. No function reads two. |
| AD-9 | Enforce append-only at the database | Drop cascading deletes from history and audit tables; make `uctsm_audit_events` insert-only at the grant. Application-level append-only that a cascade defeats is not append-only. |
| AD-5 | Remove model defaults that carry protocol meaning | Declare protocol-meaning fields without defaults so an unstated value is structurally absent. Replace `schedule_origin_anchor()`'s name-matching Day 0 recovery — AD-2 names it explicitly as a prohibited inference. |

**Runs in parallel here, no model dependency:** AD-2a *salvage only* — lift `_render_pdf_pages` and the Gemini/Ollama provider adapters out of `protocol_extraction.py` into the ingestion port. This must precede any deletion in Phase 2; it is the entire scanned-PDF capability and the offline dev path.

**Exit:** the model can represent every semantic the ten engines need; one identity function; one version handle; append-only enforced below the application; salvage complete.

---

## Phase 2 — Rebuild extraction, then delete the old

Build alongside, cut over, then delete. Never delete first — the three legacy modules are the only extraction the live Add-Trial flow calls.

1. **Ingestion port** (AD-2a). Two implementations behind one interface: embedded text, and page-image rendering. A page with an empty text layer *must* route to image. Extraction may never proceed on empty text — that is how you get a confident schedule from a blank page. Providers sit behind the same boundary; no engine or service imports a provider directly.
2. **Canonical-native extraction** (AD-2). Emits `UniversalSchedule` directly. One schedule shape, no translation. Every engine semantic is an extraction *output* with an evidence ref — dependency mode, conditional definitions, scoped qualifiers, activity anchors, confinement structure, dimensions, event mode. AD-2b holds throughout: no extraction output may contain a patient date. AD-5 holds throughout: unstated resolves to `UNRESOLVED` / `AWAITING_ANCHOR` / a blocking validation issue, never to a plausible value.
3. **Run both pipelines on the same protocols and diff.** This is the only moment the old pipeline is worth anything — as a reference implementation. Feeds L-1's eval set.
4. **Cut Add-Trial over.** Fix `add-trial.tsx:305–310` in the same change — the catch block that silently drops the sponsor onto the legacy editor with no error shown. Surface the failure.
5. **Delete.** Only once nothing produces a `CanonicalSchedulePlan`:

   | File | Lines |
   | --- | --- |
   | `protocol_agent.py` | 2,246 |
   | `protocol_extraction.py` | 2,097 |
   | `schedule_schema.py` | 1,827 |
   | `canonical_import.py` | 1,342 |
   | `canonical_bridge.py` | 297 |
   | | **~7,809** |

**Not deleted, not rebuilt:** `app/domain/schedule/`. Nine of the ten engine gaps are "extraction does not emit this," not "the engine cannot do this." The engines get Phase 1's targeted amendments; they are not re-derived.

**Exit:** one extraction pipeline; scanned protocols read; the five files gone; Add-Trial fails loudly.

---

## Phase 3 — Close the write path

Who may move a patient's dates, and whether a patient can silently fall out of delivery. Independent of Phase 2 in principle, but AD-10 touches the materializer that AD-4f consolidates — so after Phase 1.

| AD | Work |
| --- | --- |
| AD-10 | One gated chokepoint. `POST /patients/{id}/schedule/evaluate` (`api/uctsm.py:833`) commits and returns moved dates with an authenticated user but **no reason, no confirmed impact proposal, and no audit event of the move**. Route it through the chokepoint: actor + reason + confirmed impact, or preview-only persisting nothing. Every date move writes an `AuditEvent` with before/after. |
| AD-4a | Enumerate every status member with its owner, before any further write code. `PatientEventStatus`, `PatientActivityStatus`, `ConfinementStatus` — one owner per *member*. Members that are operational reality (completion, missed) belong to the operational store and are written by no engine. Until a member appears in that table, no code may write it. |
| AD-1b | Close the operational write set. `scheduled_date`, `window_start`, `window_end`, `uctsm_*` markers — and a canonical→operational payload must not contain a workflow key *at all*, not even null. This is what stops an upsert blanking a site's comment thread. |
| AD-12 | Per-patient failures in the reminder worker and calendar projection become patient-visible delivery state surfaced to the responsible CRC/PI. A loop with a row cap reports that it truncated. |
| AD-11 | Build to L-2's answer once it arrives. Bulk approval may not satisfy a per-field review requirement without per-field decisions. |
| Audit | Reconcile the two logs per the Consistency Conventions: assignment by phase, joined on canonical event id. Neither log may be read alone and presented as complete. |

**Exit:** no unowned field; no unattributed date move; no silently absent patient.

---

## Phase 4 — Cutover *(exists only if AD-1 is ratified as written)*

| Item | Note |
| --- | --- |
| AD-1a — `ParityRun` carries `ACCEPTED` with actor and reason; `readiness()` passes on `MATCH ∨ ACCEPTED`. No auto-accept, no bulk accept, no accept without a reason. | Without this a *correct* `ENGINE_UNDATED` difference is permanently unclearable, and the migration is unachievable by construction. |
| L-1 eval set scored to an agreed threshold | Deferred #1 — explicit blocker for the first cutover. |
| Deployment topology, environments, CI | Deferred #6 — "revisit **before** the first trial cuts over," not when a second environment appears. AD-1 moves real patient dates with no automated gate in front of it today. |
| Product surface for read-mode | `ParityService.set_read_mode` and `POST /api/uctsm/trials/{id}/read-mode` are complete and proven bidirectionally by `test_uctsm_cutover.py`, with **zero callers**. `getReadMode()`/`setReadMode()` exist in `frontend/src/features/uctsm/api.ts:505,515` unused. The mechanism is built; the screen is not. |
| Migrate the three fully-legacy screens | `sponsor/visit-schedule.tsx`, `clinical/schedule-review.tsx`, `clinical/trial-summary.tsx` — zero UCTSM imports today, and each carries visit-comment/task/edit features the engine has no equivalent for *by design* (AD-1). |
| First trial cutover, then the rest | One trial at a time through the parity gate. |
| Flip `UCTSM_AUTHORITATIVE`, delete legacy schedule authoring | Terminal state. Only after every trial is on `ENGINE`. Reverting one trial to `LEGACY` stays ungated forever. |

---

## What "perfect" means here — the finish line, as testable statements

Not a feeling. Seven checks:

1. **One schedule shape.** No `CanonicalSchedulePlan`, no translation layer, one extraction pipeline. *(AD-2)*
2. **Nothing unstated is ever defaulted.** Every protocol-meaning field is declared without a default; the validator's blocking checks are the test. *(AD-5)*
3. **Every field has exactly one writer, per status member.** *(AD-4a)*
4. **Every date move has an actor, a reason, and an audit row.** *(AD-10)*
5. **Every materialized row can be reconstructed as it stood on a past date** — provenance on the row, confinement episodes persisted, no cascade deleting history. *(AD-8, AD-9)*
6. **No patient is ever silently absent from delivery.** *(AD-12)*
7. **Extraction accuracy is measured against a reviewed answer key.** *(L-1)*

Checks 1–6 are architecture and are finishable. Check 7 is a measurement that never finishes — it gets a number and a threshold, and the number moves. Any claim that the system "handles all cases" is check 7 and nothing else.
