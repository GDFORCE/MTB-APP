# P1–P5 closure plan (14 Oct 2026)

Written against `docs/p1-p5-consolidated-repair-audit.md`, the PDF's P1–P5 rows and the current uncommitted tree. Outcomes are in `docs/p1-p5-closure-report.md`.

## 1. Design decisions (engineering, taken without owner approval)

| Decision | Choice | Why |
|---|---|---|
| R1 fencing | The job's `attempt` number is the claim token; complete / fail / renew require `state=CLAIMED` **and** `attempt == plan.attempt`, inside the same transaction as the write | A takeover bumps the attempt, so "latest revision" is no longer mistaken for ownership. A retry by the *winning* attempt replays its stored result |
| R2 owner | The guard asks a plane locator for the persisted canonical owner (`find_owner_by_external_id`, scoped by the canonical trial id, ambiguity refused) **only after** the operational relationship holds; the resolved owner becomes the tenant for every read and write | Fixes cross-org investigators without dropping tenant filters or trusting body ids |
| R3 history | A `STAFF` / `RESTRICTED` profile is applied before serialisation; RESTRICTED never contains payload, reason, authority, actor or proposal. Preview/confirm diffs lose override authority/reason for non-staff | The explicit grant opens the endpoint, not the clinical fields |
| R4 roles | `SUPPORTED_ROLES` (derived from the catalog + patient) is checked before grants | A grant extends a supported role; it cannot create standing |
| R5 windows | Only stated bounds count. Neither bound → `None` ("cannot be evaluated"); one bound → evaluated on that side only, never "proven inside" | A nominal target is not a zero-width window; a known deviation is still flagged for an authorised override |
| R6 treatment | New fact kind `TREATMENT`: stores `treatment_date`, no visit date, no completion, ignored as an outcome/booking | The PDF separates visit and treatment; nothing is copied between them |
| R7 plane | Commands are enabled only when the Mongo plane's pinned schedule version **equals** the version the screen displays; otherwise visibly disabled | Existence in both stores is not parity; no mirror or cutover was done |
| R8/R9 | Stop-policy tri-state on process-linked tracks; request generation guard, state reset on identity change, 404/403/other classified with retry | See report |

## 2. R1–R9

| # | Reproduced | Fixed | Regression | Store |
|---|---|---|---|---|
| R1 | yes (audit) | yes | `test_protocol_identity_store` ×5 (late failure, late success, renewal, winner retry, Mongo race) | memory + real Mongo |
| R2 | yes (audit) | yes | `test_uctsm_authz_mongo_plane` ×4 (cross-org assigned, unassigned/revoked, forged id/foreign trial, preview/confirm owner) | memory + real Mongo, HTTP |
| R3 | yes | yes | `test_p5_command_api::…withholds_every_private_field…` (sentinels across body + headers) | HTTP |
| R4 | yes | yes | `test_uctsm_authz_policy` unknown/malformed roles + supported roles still allowed | pure policy |
| R5 | yes | yes | two tests in `test_p5_patient_commands`; override/appointment verdict `None` + `protocol_window` | memory + Mongo |
| R6 | yes; **reopened** (recording-order erasure) | yes | independent-fact + order tests in `test_p5_patient_commands` and one HTTP test | memory + Mongo, HTTP |
| R7 | source-confirmed | **unresolved**: disabled on SQL-reading screens + backend `authority_plane` refusal | controller + backend tests | memory/Mongo, unit |
| R8 | source-confirmed | control added | `tsc` + builder tests; sheet not walked through | — |
| R9 | source-confirmed | yes (state machine) | 9 controller behaviour tests; no React render/browser test | unit |

## 3. Live path (as built)

Upload → `/protocols/extract…` (identity gate P2) → review (`ProtocolScheduleScreen`, SQL) → track review sheet → approve → enrol (SQL canonical + operational bridge) → patient schedule screen (`clinical/patient-schedule`, SQL) → **commands panel (Mongo plane, only if versions match)**.

## 4. Decision table

| Item | Kind |
|---|---|
| Plane authority for SQL-only patients (mirror/SQL adapter/migrate) | **Owner decision** |
| P3 matrix and defaults, high-impact grant, maker-checker scope | **Owner decision** (still `PROPOSED_PENDING_OWNER_APPROVAL`) |
| Self-grant of occurrence/state recording | Owner decision; code unchanged (only clinical-decision capabilities are self-grant-refused) |
| CURRENT selector | Engineering default kept: alias of LAST, documented |
| TRIGGER/PRECONDITION imply actual | Owner decision |
| PERFORMED/PERIGON corpus + amendment PDFs | **External evidence missing** |
| Live registry provider | **External evidence/credentials missing** |
| Browser/device walkthrough, PostgreSQL locking | **Environment unavailable** |
