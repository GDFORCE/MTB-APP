<!-- bmad:context -->
<!-- Verified 2026-09-07 against a5d0a4e6eb99a1979ed17d97be40b0dc4ca7566f. Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## MTB-APP

Clinical-trials platform: Expo/React Native frontend (`frontend/`) + FastAPI/MongoDB backend (`backend/`). Setup and run instructions are in `README.md`. Two scheduling systems coexist by design — see Where things are.

## Policy

- Never enable `DEV_OTP_MODE` outside local dev — it accepts a fixed OTP for any unconfigured channel, leaving signup open. (`backend/server.py:1318`)
- Never flip `UCTSM_AUTHORITATIVE` (off by default, `backend/.env.example:111`) without confirming per-trial parity first — it activates a middleware (`server.py:10380–10400`) that returns HTTP 410 on every legacy schedule-authoring route, and both code paths are live simultaneously today.
- Never migrate `sponsor/visit-schedule.tsx`, `clinical/schedule-review.tsx`, or `clinical/trial-summary.tsx` to read from the UCTSM engine without checking `MTB_IMPLEMENTATION_HANDOFF.txt` (section C3/D-4) first — they carry legacy visit-comment/task/edit features the engine has no equivalent for. Zero UCTSM imports in any of the three today.

## Where things are

- Authoritative store per concept: Trial, Protocol/schedule-definition, Patient, and visit **writes** are Mongo only (`backend/server.py`); visit **reads** follow `Trial.schedule_read_mode`, defaulting to `LEGACY` and changeable only through the gated `ParityService.set_read_mode` (`app/services/parity_service.py:206`), which refuses `ENGINE` unless every enrolled patient's dates already match — no trial has been moved yet. Condition state exists *only* in Postgres (`uctsm_patient_conditions`, no Mongo equivalent). The audit trail is two unreconciled logs — Mongo `audit_logs` vs. Postgres `uctsm_audit_events` — neither writes the other.
- Protocol extraction has no engine right now (both previous pipelines were deleted 2026-09-23; the pre-deletion snapshot is commit `0dd7c67`, pushed to `testing/universal-schedule-engine`). Upload endpoints (`/protocols/extract`, `/protocols/extract-details`, `/trials/{id}/extract-schedule`) validate the file then return 503 via `raise_extraction_unavailable()` in `server.py`; UCTSM runs fail immediately. The new engine plugs into `extract_schedules` in `backend/app/extraction/runner.py`, consuming `DocumentPage`s and returning `ExtractionResult`s (`app/extraction/contracts.py`). `protocol_extraction.py` now holds only the legacy `ExtractedSchedule` model and its deterministic expansion — no model calls. The curated gate in `MTB_IMPLEMENTATION_HANDOFF.txt` section E still lists the deleted `test_uctsm_extraction.py` and `test_uctsm_footnote_completeness.py`; drop them when running it.
- If the canonical-schedule build throws during Add Trial, `add-trial.tsx`'s catch block (`:305–310`) silently drops the sponsor onto the legacy `sponsor/visit-schedule` editor with no error shown — if you touch this path, surface the failure instead of preserving the silence.
- `clinical/add-patient.tsx` calls legacy endpoints directly *and* renders a live UCTSM component (`ProtocolAssignmentFields`) that silently renders nothing when the trial has no canonical schedule yet — there's no visible signal distinguishing "not wired" from "no schedule exists."
- Orphaned but present, don't assume reachable: `sponsor/trial-detail.tsx` (dead redirect, zero inbound refs), `clinical/invite-patient.tsx` (unreferenced), and the `ProtocolActivityTable`/`DayScheduleTable`/`QualifierPanel` exports from `ScheduleTable.tsx` (zero importers).
- `ParityService.set_read_mode` (`app/services/parity_service.py:206`) and its HTTP endpoint (`POST /api/uctsm/trials/{id}/read-mode`, `backend/app/api/uctsm.py:1139`) are a complete, tested, bidirectional per-trial cutover mechanism — proven correct in both directions by `test_uctsm_cutover.py` — but have zero product surface. `getReadMode()`/`setReadMode()` wrappers exist in `frontend/src/features/uctsm/api.ts` (`:505`, `:515`) with no callers. No trial has ever been cut over outside tests. This is designed-and-verified, not designed-and-untested — treat it as a strong candidate architecture decision input, not a risky unknown.
- `MTB_IMPLEMENTATION_HANDOFF.txt` (root) and `docs/audits/mtb-app-architecture-audit.md` are the current status sources — both supersede `docs/mtb-requirements-traceability.md`'s status column.
- `memory/PRD.md` is stale (e.g. its "admin portal excluded" line no longer holds) — don't treat it as current.

## Running and verifying

- Backend: don't run plain `pytest` across the whole suite — several TestClient-based integration modules (e.g. `test_schedule_edit.py`, `test_schedule_definition_api.py`, `test_protocol_creation.py`) error on teardown when run together in one session (an anyio/starlette event-loop issue), though each passes alone. Use the curated 34-file gate listed in `MTB_IMPLEMENTATION_HANDOFF.txt` section E.
- Frontend: there is no plain `npm test`; use `npm run test:uctsm` or a single `test:uctsm-*` script.
- `npm run lint` cannot run in this environment — Expo shells out to `yarnpkg`, which isn't installed here.
- `npx tsc --noEmit` over the whole frontend project runs out of memory on this machine, and no scoped tsconfig exists to work around it — typecheck only the files you touched instead (e.g. `npx tsc --noEmit <files>`).

## Known pitfalls

- Don't default or guess an unstated value (visit mode, dependency mode, recurrence maximum, footnote scope, etc.) — mark it UNRESOLVED / AWAITING_ANCHOR / blocked-pending-review instead. Repeated historical fixes exist because this was violated (e.g. commits `e2b812e`, `bfad44c`, `94084d0`).
- Don't trust a status doc's claim about what's committed — check `git status`/`git log` yourself. (An architecture audit dated the same week claimed 370 uncommitted lines in `schedule_schema.py`; all of it was committed by the next session.)
- Two independent reminder systems share no code: patient medication alarms (`db.reminders`) vs. the engine that bridges UCTSM projections into visit-reminder notifications (`server.py:8695–8714` vs. `5544–5633`).
- Push-token registration (`POST /push/register`) exists but nothing sends an actual push (no FCM/APNs/Expo integration), and the frontend never registers a token either — don't assume push notifications work end-to-end.
- "Messages" refers to two unrelated systems — 1:1/group WebSocket chat and an admin one-way broadcast with its own scheduler worker — check which one a task means.
- Trial/team invites, admin-issued platform invitations, and org-admin member invites are three independent handlers with separate token schemes and no shared code — a fix to one doesn't apply to the others.
- `POST /auth/register` (single-call) and the OTP wizard (`/auth/register/start → verify → complete`) are both live; only the OTP wizard is what the app actually uses.

<!-- /bmad:context -->
