# Claude Code continuation: close authority bypass and controller races

Read `docs/p1-p5-latest-repair-review.md` and the full requirements contract in `docs/claude-code-p1-p5-final-closure-prompt.md` before editing. This continuation supersedes the earlier instruction to reopen the treatment-first R6 defect: those checked cases now pass. Preserve its regressions and all other existing work. Focus on remaining repairs and evidence; do not restart the implementation or call P1-P5 closed prematurely.

## A. Repair the schedule-authority bypass

Reproduce this red HTTP/service case on memory and real local Mongo:

- Patient is persisted with `authority_plane="SQL"`.
- Command preview correctly returns 409 PLANE_NOT_AUTHORITATIVE.
- The same patient's older Mongo `actual-occurrences` route currently returns 201 and writes an occurrence plus ledger row.

Centralize the authority decision in the shared application mutation boundary so every equivalent schedule-changing entry point checks it. Protect old actual recording, corrections, normal commands, confirmation and replay. Identify other writers by tracing source rather than applying a check only to the one reproduced route.

Denied requests must leave no new facts, ledger/evaluations, business audit, receipts or outbox effects. Readable retained history should stay available to appropriately authorized users. Preserve safe error bodies and permission/resource scope checks. Add regressions for direct services, HTTP, authority changes between preview and confirm, and replay after authority changes.

Define typed authority/provenance and trusted native/import enrolment semantics. None is currently treated as native Mongo; do not confuse missing data with proof of native authority. Preserve justified compatibility through evidence/classification, not an arbitrary mass relabel or a new default that silently moves patients. Request bodies must not choose or change authority.

No migration, mirror, authority switch or policy approval is authorized here. Fixing the denial invariant does not depend on choosing a pilot.

## B. Repair same-patient cancellation/proposal races

Add a failing controller test:

1. Get preview OLD.
2. Begin Discard OLD and delay its API cancellation.
3. Obtain preview NEW for the same patient/version.
4. Resolve OLD cancellation.
5. NEW must remain the active preview.

Bind asynchronous proposal actions to exact proposal identity and action generation, or serialize them using a documented action state. Identity generation alone is insufficient. Test command-type switching, reversal previews, cancel failure, rapid discard, cancel/confirm races, and new preview failure. Do not allow an older result to replace/clear a newer intent. Preserve stable keys and unknown-confirm handling.

## C. Prohibit new actions after disposal and verify the binding

The actual controller currently sends confirm after `dispose()` if it retains a ready preview. Check disposed state before starting every public action or probe, not only before publishing responses. Test no new calendar/history/preview/confirm/cancel requests after disposal.

Handle already dispatched work honestly: ignoring a response does not undo a committed server operation. Preserve patient isolation and unknown-outcome recovery.

Add a meaningful React binding test or real browser/device check for setup/cleanup/setup, patient/version switches and action handlers. The ref-owned controller is permanently disposed on effect cleanup; ensure the binding creates/reinitializes the proper live instance. Do not claim the plain controller suite proves React mount behavior.

Wire the new suite into package scripts and the relevant frontend gate. `test:uctsm-command-controller` is currently missing; use/add the actual script rather than documenting an imaginary invocation.

## D. Finish independent closure work while owner inputs are pending

- Cover the production persisted assignment/invitation provider behind R2, rather than only FakeOperationalAccess.
- Give bounded cross-org lists stable pagination or explicit truncation visibility; deterministic silent truncation is still incomplete.
- Exercise the mounted stop-policy sheet and production workflow in a browser/device when available.
- Preserve the independently passing R6 memory/Mongo/HTTP order, correction, replay and reload tests.
- Reassess legacy wrapper central mapping with the owner policy packet; do not silently expand or remove PI rights.
- Obtain genuine PERIGON/amendment/provider evidence when available; retain explicit blocked rows otherwise.

For the authority decision, distinguish the live SQL bridge (`app/services/operational_bridge.py`) from the repository-port bridge (`app/application/operational_bridge.py`) already present. Reuse the latter where appropriate. Finish the concrete enrolment/display/fact/condition ownership design, enforced SQL write freeze and migration race handling. Demonstrate how rollback represents every committed command's semantics and reconciles reminders/outbox while retaining history. Do not promise a SQL import of facts can reverse track commands without verifying SQL support.

Complete reviewable independent repairs before requesting the essential choice of an approved pilot cohort versus SQL adapter. A named cohort does not itself implement the supported production path. Keep SQL display/Mongo commands disabled until one consistent active-plane workflow exists.

## E. Verify and update evidence

Follow AGENTS.md, preserve unrelated uncommitted changes, run targeted red/green regressions and then the applicable curated gate. Use explicit local replica-set settings and isolated databases; do not skip real-Mongo variants or infer SQL/PostgreSQL proof from a fake.

Run frontend controller/component tests, applicable existing scripts and meaningful scoped typechecking. Report actual counts without summing overlapping suites or repeating previous results as new runs. If a browser, document or owner decision remains unavailable, name the exact acceptance rows blocked and continue independent work.

Update original closure plan/report tables, including stale P1-05/P5-05 version-only eligibility and the plane contract. Keep per-row PDF acceptance criteria separate from overall production approval: domain-only criteria do not automatically need a browser walkthrough.

Deliver the resolved-finding table, current acceptance matrix, source references, exact commands/results and remaining essential decisions/evidence. No commit, push, deployment, live enrolment or authority flip without authorization.
