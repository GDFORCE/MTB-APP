# MTB-APP Architecture Audit

*Engineering audit · whole-repo, evidence-based*

A complete map of what's old and what's new across the entire application — every backend subsystem, every frontend screen, and whether the protocol-extraction/scheduling rebuild is actually wired up or still riding on the legacy path. Compiled from five independent code-reading passes plus a live test run; every claim below is cited to a file and line.

- **Repo:** MTB-APP
- **Method:** 5 parallel agents, full-file reads, no doc taken at face value
- **Verified against:** live code + git history + a full test run

**Legend**

| Tag | Meaning |
|---|---|
| Old | legacy MongoDB path |
| New | UCTSM canonical engine (Postgres) |
| Bridge | translates old → new, one-way |
| Orphan | exists in code, unreachable in the app |
| Pass / Fail | test outcome |

**Contents**

- [00 — Executive summary](#00--executive-summary)
- [01 — Repo state](#01--repo-state-before-anything-else)
- [A — Backend: old vs new](#a--backend-old-vs-new-concept-by-concept)
- [B — Backend inventory](#b--backend-feature-inventory)
- [C — Frontend: schedule wiring](#c--frontend-schedule--protocol-screen-wiring)
- [D — Frontend inventory](#d--frontend-feature-inventory)
- [E — Test suite health](#e--test-suite-health--live-run-not-a-doc-claim)
- [F — This session's changes](#f--exactly-what-changed-this-session)
- [G — Open items](#g--open-items)

---

## 00 — Executive summary

Two direct answers to the questions that started this audit, then the evidence for everything else below.

> **⚖ Is protocol extraction "fully wired" to the new system, or still old?**
>
> **Both, in a specific and deliberate split.** The AI extraction itself — reading the PDF, finding visits, timings, activities — still runs entirely on the **Old** pipeline (`protocol_agent.py`, `protocol_extraction.py`, `schedule_schema.py`). A second, UCTSM-native extraction pipeline exists (`app/extraction/graph.py`) and is fully wired at the API level, but **zero screens in the app call it** — it's reachable only from backend tests. What *is* new: once the old pipeline produces a schedule, a **Bridge** (`canonical_import.py`) deterministically translates it into the new Postgres engine, and for a brand-new trial the sponsor is dropped onto the new UCTSM review screen, not the old one. So: extraction = old, translation + review screen = new, and this only happens for trials created after that bridge was wired — nothing already in the system was migrated.

> **Are the screens updated to match — the tabular, multi-table UI?**
>
> **Partially, and split by screen, not by feature.** The multi-table spec (master table, patient table, a separate conditional-requirements table, qualifier badges) is genuinely built and does render — on `sponsor/protocol-schedule.tsx` and `clinical/patient-schedule.tsx`. But three screens a sponsor/CRC uses just as often — `sponsor/visit-schedule.tsx`, `clinical/schedule-review.tsx`, `clinical/trial-summary.tsx` — still render their own old flat tables built from Mongo documents directly, with zero import of the new components. One screen, `clinical/add-patient.tsx`, is a genuine hybrid: legacy form fields sitting next to a live UCTSM widget in the same JSX file.

**Key numbers**

| Metric | Value |
|---|---|
| Mongo collections | 37 |
| Postgres/UCTSM tables | 34 |
| Frontend screens | 99 |
| Extraction pipelines (1 orphaned) | 2 |
| Backend tests passing | 992/992 |
| Frontend tests passing | 87/87 |

---

## 01 — Repo state, before anything else

> **⚠ This is a git repository with uncommitted work — contradicting the environment banner**
>
> The session environment reported "not a git repository," but `git rev-parse --is-inside-work-tree` returns true, HEAD is at `d895691`, and the working tree carries real uncommitted changes: two brand-new **untracked** files (`backend/app/services/canonical_import.py`, `canonical_bridge.py`), an untracked `MTB_IMPLEMENTATION_HANDOFF.txt`, and 370 uncommitted insertion lines in `backend/schedule_schema.py` alone. Nothing described in this report as "current code" is committed. **Worth deciding deliberately whether/when to commit** — see Part F and Part G.

---

## A — Backend: old vs new, concept by concept

Two schedule systems run side by side: the original app on MongoDB, and a newer "UCTSM" canonical scheduling engine on Postgres (SQLite in this dev environment, at `backend/uctsm-dev.db`). Below is which one is actually authoritative for each concept in the live, reachable app — not which one the docs say should be.

### A.1 Which store is authoritative

| Concept | Live authority | Evidence |
|---|---|---|
| Trial | **Old** Mongo `trials` | `server.py:2374` · `uctsm_trials` row only created lazily on bridge call |
| Protocol / schedule definition | **Old** authored & approved in Mongo `schedule_definitions` | `server.py:3773–3814` · UCTSM copy is a one-way derived draft |
| Patient | **Old** Mongo `patients` | `server.py:6054` · `uctsm_patients` only via separate router path |
| Visit / event occurrence — write | **Old** Mongo `visits` + `visit_instances`, exclusively | `server.py:5093–5286` · no reachable UCTSM write path |
| Visit / event occurrence — patient read | **Old** in practice | `_apply_canonical_dates` (`server.py:4377`) checks `Trial.schedule_read_mode`, default `"LEGACY"` · no code path ever sets a trial to `ENGINE` |
| Condition (protocol rule state) | **New** — no Mongo equivalent exists | `uctsm_patient_conditions`, reachable only via `/api/uctsm/patients/{id}/conditions*`, no legacy caller |
| Activity (per-visit assessment) | Split — Mongo embeds it in visit docs, UCTSM models it as a first-class table | `server.py` `_structured_visit_procedures:4372` vs. `uctsm_activities` |
| Audit trail | Split, unreconciled — two separate logs | Mongo `audit_logs` (`write_audit`) vs. Postgres `uctsm_audit_events`; neither writes the other |

<details>
<summary>Full store list — 37 Mongo collections, 34 Postgres/UCTSM tables</summary>

**Mongo (37, from `server.py`):** `app_content, audit_logs, conversations, dose_logs, faq, files, invitations, master_data_submissions, master_data_values, medications, messages, meta, notifications, org_sites, org_trial_access, organizations, otp_throttle, password_reset_tokens, patients, pending_contact_changes, pending_registrations, preferences, prelogin_support_requests, protocol_extractions, protocol_registry, public_api_throttle, push_tokens, refresh_tokens, reminders, role_reports, schedule_definitions, schedule_reviews, schedule_versions, shares, support_tickets, trials, users, visit_instances, visits`

| `uctsm_` table | Purpose |
|---|---|
| `trials` | One canonical trial per org; carries `schedule_read_mode` and bridge key |
| `parity_runs` | Recorded comparison of engine vs. Mongo schedule for one patient |
| `protocols` / `protocol_versions` | One protocol per trial, and one content-hashed uploaded version |
| `extraction_runs` | One LLM/deterministic extraction attempt, idempotency-keyed |
| `schedule_definitions` / `schedule_versions` | Named schedule series, and one versioned/statused schedule with JSON blobs |
| `epochs` / `arms` / `cohorts` / `populations` | Study period and dimension-value tables |
| `anchors` | Named date origins (baseline, surgery, ...) |
| `events` | Visit/contact definitions — timing, dependency, conditionals, qualifiers, confinement |
| `event_applicability` / `event_dependencies` / `event_recurrence` | Which arm an event applies to; dependency edges; recurrence rules |
| `activities` | Assessments/procedures within a visit |
| `evidence` / `claim_evidence` | Source citations and links from an extracted claim to them |
| `validation_issues` / `review_decisions` | Validator findings and human review decisions |
| `patients` / `patient_schedule_assignments` | Canonical patient, and history of which schedule version they were pinned to |
| `schedule_impact_proposals` | Preview of a proposed change awaiting confirm/cancel |
| `patient_anchors` / `patient_states` | Recorded anchor dates and generic state facts for one patient |
| `schedule_generation_runs` / `patient_schedules` / `schedule_evaluations` | Evaluation-run bookkeeping and input/output snapshots |
| `patient_events` / `patient_event_occurrences` | Materialized per-patient visits, and scheduled vs. actual dates |
| `patient_unscheduled_visits` | Site-created on-demand visits, never overwritten by regeneration |
| `patient_conditions` | Append-only lifecycle of one protocol condition for one patient |
| `patient_activities` / `patient_activity_records` | Materialized activity schedule, and append-only actual completion |
| `audit_events` | Canonical-engine audit trail, separate from Mongo's |

</details>

### A.2 Two extraction pipelines — only one is live

The repo genuinely contains two independent AI extraction implementations. Only one is reachable from any screen.

| | Pipeline A — legacy | Pipeline B — UCTSM-native |
|---|---|---|
| Files | `protocol_agent.py, protocol_extraction.py, schedule_schema.py` | `app/extraction/graph.py, llm_client.py, prompts.py, claude_provider.py` |
| Reachable via | `POST /protocols/extract`, `/extract-details`, `/trials/{id}/extract-schedule` | `POST /api/uctsm/protocols/{id}/versions/{id}/extract-schedule` |
| Called by any real screen? | **Yes** — `add-trial.tsx:209` | **No** — its frontend wrapper `queueScheduleExtraction` (`api.ts:436`) has zero callers anywhere in the app |
| Exercised by | The entire live Add-Trial flow | Backend tests only |

> **💬 Precise characterization**
>
> Pipeline B is not dead code — it's a functioning, mounted, tested API endpoint. It's **orphaned**: fully wired at the server, unreachable from the product.

### A.3 Full trace: PDF upload → schedule screen, for a new trial

1. **[Old]** User picks a PDF; multipart upload fires — `frontend add-trial.tsx:209 → POST /protocols/extract`
2. **[Old]** Server runs Pipeline A, the LangGraph-style extraction agent — `server.py:2216 extract_protocol_alias → pe.extract_protocol_bundle_all()`
3. **[Old]** Result cached — `server.py:2262 · db.protocol_extractions.insert_one`
4. **[Old]** Trial document created — `add-trial.tsx:262 → POST /trials → server.py:2374 db.trials.insert_one`
5. **[Old]** Cached extraction "consumed" into a persisted schedule definition — `add-trial.tsx:292 → server.py:3974 → _persist_schedule_definition (3773) → db.schedule_definitions`
6. **[Bridge]** Canonical draft built from that Mongo definition — reads old, writes new — `add-trial.tsx:294 buildCanonicalSchedule() → server.py:5908 → canonical_bridge.py:139 import_schedule_definitions`
7. **[Bridge]** Mongo `canonical_plan` deterministically translated into a `UniversalSchedule` — `canonical_import.py:696 universal_schedule_from_plan / :1309 extraction_result_from_plan`
8. **[New]** Persisted as a DRAFT schedule version + child rows in Postgres — `extraction_service.py:71 ExtractionService.complete() → ScheduleRepository.persist_draft()`
9. **[New]** Frontend redirected to the canonical review screen — `add-trial.tsx:297 → router.replace("/(app)/sponsor/protocol-schedule")`
10. **[New]** Screen renders, reading Postgres exclusively — `protocol-schedule.tsx → ProtocolScheduleScreen.tsx → GET /api/uctsm/schedule-versions/{id}`

> **↩ The fallback that explains the screenshot you originally saw**
>
> If step 6 throws for any reason (a construct the bridge can't yet translate, a missing Postgres session, etc.), `add-trial.tsx:305–310` catches it and routes to `sponsor/visit-schedule` instead — the fully **Old** editor, zero UCTSM imports. This is a silent, catch-all fallback: a sponsor has no way to tell, from the screen alone, whether they landed there because there was no extraction or because the new engine choked on their protocol.

### A.4 Handoff doc vs. current code

`MTB_IMPLEMENTATION_HANDOFF.txt` was read in full and checked line by line against the live code. Every claim checked out *except* one gap, which is a timing gap, not an error:

> **📋 The handoff predates today's range-cadence work**
>
> Neither `value_max` nor `QualifierCategory.REPEAT` appear anywhere in the handoff file, yet both are real, tested, and in the working tree right now. Git confirms the ordering: `schedule_schema.py` has 370 uncommitted insertions with no `value_max` in the last commit at all, and file mtimes put today's edits after the handoff's own last edit (6 Sep vs. 7 Sep). Not a documentation error — just work the handoff hasn't caught up to yet. See Part F.

Everything else audited — the two-systems framing, the "extraction doesn't emit dependency/conditional/qualifier data itself" claim, the "three screens kept on legacy for their write features" claim, the `schedule_read_mode` default, the "nothing has been cut over" claim — matched the code exactly.

---

## B — Backend feature inventory

Everything the backend does outside the scheduling engine — roughly 150 endpoints across 12 feature areas, all still on the **Old** Mongo path unless noted.

<details open>
<summary>Auth &amp; identity, trials, patients — the core</summary>

**Auth** runs two parallel registration flows simultaneously: a simple one-call `POST /auth/register`, and the one the app actually uses — a multi-step OTP flow (`/auth/register/start → verify → complete`). Forgot-password has *three* separate mechanisms: OTP-based self-serve, an admin-issued single-use link, and a pre-login "support" ticket path that never confirms whether the email exists. A `DEV_OTP_MODE` bypass exists with a fixed code, flagged in its own comment as "must be OFF in production." ~30 endpoints, collections: `users, pending_registrations, otp_throttle, password_reset_tokens, refresh_tokens, organizations`.

**Trials** (~25 endpoints): CRUD plus sponsor/SMO/site dashboards, all access-gated through `_can_access_trial` (`server.py:5398`), a single relationship-scoped helper used almost everywhere.

**Patients** (~10 endpoints): direct enroll and invitation-based enroll both exist; a `UCTSM_AUTHORITATIVE` env flag makes enrollment fail-closed with 409 if a trial isn't linked to a canonical schedule — live evidence the team is mid-cutover, not finished.

</details>

<details>
<summary>Legacy visits, dashboards, team management</summary>

**Legacy visits** (~25 endpoints) run a two-tier model: templates in `visits`, per-patient materialized copies in `visit_instances`, with a 14-value status enum (`server.py:7940`) and a separate derived/display status layer on top that never mutates the stored value.

**Dashboards**: sponsor, SMO/site (deliberately masked-subject, separate from org-admin), and a shared PI/CRC payload builder. An admin-side alerting surface lives in a separate router.

**Team management**: despite the name, there is *no* `team_capabilities` collection — permission scope (`can_edit`/`can_remove`) is computed inline per request, never stored.

</details>

<details>
<summary>Messaging, notifications/reminders, audit, admin</summary>

**Messaging**: one WebSocket endpoint (`/api/ws`, `server.py:7496`) with an in-process, single-worker connection map — not distributed-safe — plus ~16 REST endpoints for conversations/messages.

**Notifications** is the busiest area for old/new overlap — see the duplicate-systems list below. Push-token registration exists (`POST /push/register`) but nothing in the backend ever reads that collection to actually send a push.

**Audit**: `write_audit()` is called from ~49 sites across the app, all into one Mongo collection, with heavy role-scoped de-identification on read (sponsors see `SUBJ-xxx`, never real names).

**Admin** (~60+ endpoints across two routers): fully implemented, fail-closed by construction (every route in `admin_routes.py` requires the `admin` role at the router level). Covers users, orgs, master-data approval, tickets, alerts, audit, legal, reports, delegation, and an explicit "break-the-glass" emergency-access flow with mandatory session audit. This directly contradicts an old PRD note calling the admin portal "excluded" — see Part D.

</details>

### B.1 Other old/new splits found, beyond Mongo-vs-UCTSM

- **A kill-switch already exists for the legacy schedule-authoring surface** — An `@app.middleware` hook, gated on `UCTSM_AUTHORITATIVE`, returns HTTP 410 for every legacy schedule-authoring route when flipped on. Both paths are fully present and mounted today; the off-switch is built but unused. (`server.py:10380–10400`)
- **Two independent reminder systems** — Patient-set medication alarms (`db.reminders`) share no code with the newer engine that bridges UCTSM schedule projections into visit-reminder notifications. (`server.py:8695–8714 vs. 5544–5633`)
- **Push tokens are collected but never dispatched** — Registration exists; no code anywhere sends an actual push (no FCM/APNs/Expo integration found). Confirmed independently by the frontend audit: no push-token registration code exists client-side either. (`server.py:9740`)
- **Two systems both called "messages"** — 1:1/group WebSocket chat vs. an admin one-way broadcast system with its own scheduler worker — different collections, different delivery model, same word. Worth a rename somewhere.
- **Three separate invitation surfaces** — Trial/team invites, admin-issued platform invitations, and org-admin member invites — three handlers, three token schemes, no shared code.
- **Dual registration flows, both still live** — The simple single-call register endpoint appears unused by the current app (which clearly uses the OTP wizard) but remains fully reachable.

---

## C — Frontend: schedule &amp; protocol screen wiring

Every screen touching trials, protocols, schedules or visits, checked for three things: which API it calls, whether a real user can actually reach it, and whether it renders the old flat table or the new multi-table UCTSM UI.

| Screen | API | UI shape | Reachable? |
|---|---|---|---|
| `sponsor/add-trial` | Old + Bridge | Form | Normal nav |
| `sponsor/visit-schedule` | Old | Old flat table | Normal nav |
| `sponsor/protocol-schedule` | New | **New ScheduleTable** | Normal nav |
| `sponsor/schedule-versions` | Bridge | Version list | Normal nav, 1 entry point |
| `sponsor/trial-detail` | — | Dead redirect | Orphan — zero inbound refs anywhere |
| `clinical/protocol-review` | New | Custom qualifier list (not the shared component) | Normal nav, 1 entry point |
| `clinical/schedule-review` | Old | Custom diff list | Normal nav |
| `clinical/team-calendar` | Old | Calendar grid | Normal nav |
| `clinical/trial-summary` | Old | Old inline flat table — but forks users to both worlds | Normal nav, heavily (19 inbound refs) |
| `clinical/add-patient` | Old + New **in the same file** | Form | Normal nav |
| `clinical/patient-schedule` | New | **New ScheduleTable + ConditionalRequirementsTable** | Normal nav, 2 entry points |
| `clinical/visit-detail` | Old | Custom list | Normal nav |
| `clinical/action-board` | New | Dashboard cards | Normal nav |
| `patient/*` (5 screens) | Old | Cards / lists / calendar | Mostly normal nav |
| `dev/uctsm-workbench` | New | Full UniversalScheduleWorkbench | Dev-only, one guarded link |

> **🔗 The one true code-level hybrid**
>
> `clinical/add-patient.tsx` makes legacy calls directly (`GET /trials`, `POST /trials/{id}/schedule-preview`, `POST /patients/invite`) while also rendering `ProtocolAssignmentFields`, a live UCTSM component that calls the bridge (`getCanonicalSchedules`) and a true `/api/uctsm/...` endpoint for arm/cohort assignment. If the trial has no canonical schedule yet, that component silently renders nothing and the screen behaves exactly like the old version — no visible signal to the user either way.

<details>
<summary>Component-level orphan check: what's built but never mounted</summary>

- `ScheduleTable` and `ConditionalRequirementsTable` — genuinely mounted, on `ProtocolScheduleScreen` and `PatientScheduleScreen` respectively.
- `ProtocolActivityTable`, `DayScheduleTable`, `QualifierPanel` — exported from `ScheduleTable.tsx`, **zero importers** found anywhere else in the repo. `ProtocolReviewScreen.tsx` even reimplements its own inline qualifier list instead of using the ready-made `QualifierPanel`.
- `UniversalScheduleWorkbench` — real, but its only route in or out of it is dev-gated (`__DEV__`) and only shown after a UCTSM schedule fails to load.

Note on the three UCTSM Jest suites (`uctsm-tabular-ui`, `uctsm-presentation`, `uctsm-review-surfaces`): all 87 passing tests exercise pure row-shaping logic in `presentation.ts` only — none render a component, touch navigation, or call the API client. They're strong evidence the view-model math is correct; they are not evidence anything is actually on screen. Reachability had to be established separately, via the import/navigation grep audit above.

</details>

---

## D — Frontend feature inventory

The other 99−24≈75 screens: auth, dashboards, admin, patient-facing, messaging, settings — everything outside the schedule/protocol slice above.

> **👀 The admin portal is not excluded — a project note is stale**
>
> `memory/PRD.md` states "Admin portal (16 screens) — explicitly excluded per your request." That count (16) is accurate, but the portal is fully built and live: a responsive sidebar/drawer shell at `admin/_layout.tsx`, global cross-entity search, and every one of the 16 screens wired to real `admin_routes.py` endpoints.

<details open>
<summary>Folder map — frontend/app/(app)/*</summary>

| Folder | Purpose |
|---|---|
| `admin/` | Platform admin portal, 16 screens, sidebar/drawer shell |
| `clinical/` | Shared PI/CRC/SMO/Site screens, incl. a `profile/` settings sub-app |
| `org-admin/` | Per-organization admin console (site/smo/sponsor variants) |
| `patient/` | All 9 patient-role screens |
| `pi/, crc/, site/` | Role dashboards — `site/` is a bare re-export of `pi/dashboard` |
| `sponsor/` | Sponsor/CRO screens: dashboard, trials, patients, PIs, sites, profile |
| `conversation/, conversations/join/` | Chat thread info, shared files, invite-link joiner |
| `dev/` | `uctsm-workbench.tsx` only |
| loose files | `chat.tsx, notifications.tsx, audit-trail.tsx, data-policy.tsx, ownership-transfer.tsx` — shared across roles |

</details>

### D.1 Notable findings

- **No push-notification code on the frontend at all** — No `expo-notifications` import, no token-fetch, no call to `/push/register` anywhere in `frontend/`. Confirms the backend-side finding in Part B — this feature is entirely unbuilt on the client, not just unused.
- **`clinical/invite-patient.tsx` appears orphaned** — No `router.push`/`href` anywhere in the codebase points at this route.
- **No shared navigation primitive** — Every role gets its own hand-coded bottom nav (`PatientBottomNav`, `PiBottomNav`, `SponsorBottomNav`) — no expo-router `Tabs`/`Drawer` anywhere. `crc/dashboard.tsx` even hand-rolls its own tab bar instead of reusing `PiBottomNav`, a small inconsistency.
- **Several screens are thin redirects, not real screens** — `patient/medication-reminder.tsx`, `patient/messages.tsx`, `sponsor/trial-detail.tsx`, `site/dashboard.tsx` are all pure re-exports or bounce-redirects.

---

## E — Test suite health — live run, not a doc claim

Every check below was actually executed against the current working tree.

| Check | Result | Status |
|---|---|---|
| Curated UCTSM + canonical gate (34 files) | 387 passed · 38.2s | ✅ Clean |
| Full backend suite | 992 passed, 1 skipped, 2 xfailed · 26m 29s | ✅ Clean |
| Frontend UCTSM suite | 87 / 87 passed · 0.87s | ✅ Clean |
| Frontend full typecheck | Out of memory | ⚠️ Known env limit |

> **🔄 One documented issue did not reproduce**
>
> The handoff describes a known anyio/starlette TestClient teardown error when multiple integration modules run together in one session. This run — all 995 collected tests, in one session — completed with zero setup/teardown errors. Either it's been fixed since, is order-dependent, or this environment differs from when the doc was written. Flagging as a discrepancy worth a second look, not assuming it's simply stale.

> **📁 The handoff's documented typecheck workaround doesn't exist in the repo**
>
> It references "a scoped tsconfig covering `src/**`, `tests/**` and the edited app screens" that reportedly typechecks clean. No such file exists in the repo today — only the single project-wide `tsconfig.json`. Whoever wrote that workaround didn't commit it.

---

## F — Exactly what changed this session

All of it uncommitted (Part 01). In order.

**`backend/app/services/canonical_import.py` — qualifier scope + page numbers**
- Added a page-number parser for evidence IDs (`_page_number_from_evidence_id`) so evidence citations carry a real page.
- Event- and activity-level qualifiers now get scope `CELL` instead of always `VISIT`/`ACTIVITY` when the source narrows to specific occurrences or arms.
- +18 new tests across two new files proving CELL-scope bumping and page-number parsing, plus that cohort/substudy/population applicability already resolves correctly.

**Four legacy test files repaired (test-only, zero production risk)**
- `test_audit_scoping.py` — added a real accepted-invitation fixture matching production shape.
- `test_clinical_dashboards.py` — fixed a seeded fixture's `window_end` so the overdue-count assertion matches what the dashboard actually keys on.
- `test_sponsor_dashboard.py` — the "non-admin" test case was accidentally testing an org-founding admin; added a genuine second registrant.
- `test_mtb_backend.py` — rewritten off a dead remote URL onto the in-process pattern every other test file uses; 2 WebSocket tests marked `xfail` with a disclosed, real architecture limitation (Motor's event-loop pinning vs. TestClient's own portal thread) rather than hidden or deleted.

**The real-protocol bugs (from your NCT00129740 review)**
- **Day-5-shows-as-Day-4 bug, fixed**: the deterministic day-label cross-check regex was too strict to match a real label like "Day 5 +/- 3 days" — extended it to tolerate a trailing window annotation without loosening what counts as a match. (`schedule_schema.py, _DAY_LABEL_WINDOW_SUFFIX`)
- **Range cadences ("every 3–4 months"), no longer silently collapsed**: added `value_max` to `TemporalAmount`, taught the extraction prompt to capture both bounds instead of picking one, and added a deterministic structural check that flags any range stated in the source text with no `value_max` recorded.
- 6 new tests covering both fixes against the exact protocol language.

**Today — making the range-cadence fix actually visible, on both systems**
- **[Old path]** `schedule_schema.py::build_row()` — when a recurrence's captured frequency has `value_max`, the specific affected visit row (not just a global note) now gets an `operational_constraints` entry and a forced review flag. This is the field `visit-schedule.tsx` already renders per-row, so no frontend change was needed there.
- **[New path]** `canonical_import.py` — the same condition now attaches a `QualifierCategory.REPEAT` qualifier to the UCTSM event, using the identical unresolved-until-reviewed mechanism every other footnote/condition uses — meaning it **blocks schedule approval** until a reviewer addresses it, stronger than the old path's cosmetic warning.
- 4 new tests (2 per system) proving the range case gets flagged and the fixed-cadence case doesn't.

| Metric | Value |
|---|---|
| UCTSM gate, after today's edits | 387 |
| `canonical_import` tests, all passing | 20 |
| `schedule_schema_v2` tests, all passing | 18 |

---

## G — Open items

Everything surfaced across all five passes that isn't a completed fact — a punch list, not a plan.

- [ ] **Nothing from this session is committed to git** — Two new files are fully untracked; `schedule_schema.py` alone has 370 uncommitted lines. Worth a deliberate commit before it's at risk of being lost or conflicting with other work.
- [ ] **Three heavily-used screens still render the old flat table** — `visit-schedule`, `schedule-review`, `trial-summary` — the range-cadence fix and any future UCTSM-only capability won't reach these until they're migrated or the cutover (`UCTSM_AUTHORITATIVE`) is flipped.
- [ ] **The bridge's failure mode is invisible to the sponsor** — When the canonical build throws, the user is silently dropped onto the legacy editor with no indication anything failed — worth at least a toast or banner explaining why.
- [ ] **Pipeline B (UCTSM-native extraction) has no caller** — Either wire it up somewhere, or it's worth explicitly deciding it's not the direction and removing the dead surface area.
- [ ] **Push notifications are half-built on both ends** — Backend collects tokens it never uses; frontend never registers a token at all. Needs a decision: finish it or remove the registration endpoint.
- [ ] **Stale project docs** — `memory/PRD.md`'s "admin portal excluded" note and parts of `docs/mtb-requirements-traceability.md` no longer match reality — worth a cleanup pass so future audits don't re-derive this from scratch.
- [ ] **Orphaned screens/components to decide on** — `sponsor/trial-detail.tsx`, `clinical/invite-patient.tsx`, and three `ScheduleTable.tsx` exports (`ProtocolActivityTable`, `DayScheduleTable`, `QualifierPanel`) — either wire them in or remove them.

---

*Compiled from 5 independent agent passes + 1 live test run · every figure above is reproducible from the cited file:line*
*MTB-APP*
