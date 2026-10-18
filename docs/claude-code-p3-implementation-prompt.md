# Claude Code prompt: P3-01–P3-05 — capability-based UCTSM authorization

Prepared on 14 October 2026 from the current source, P2 result document, and section P3 of `D:\MTB_dev (2).pdf`. Copy the prompt below into Claude Code with MTB-APP open. This prompt was prepared by inspecting source; no P3 implementation or verification run was performed.

---

Implement **P3-01 through P3-05** from `D:\MTB_dev (2).pdf`: enforce capability-based UCTSM authorization with persisted resource scopes and configurable maker-checker approval.

Repository: `D:\MTB_Intern_work_folder\APP-New-approch\MTB-APP`.
Stack: Expo/React Native/TypeScript; FastAPI; legacy MongoDB operational workflows; SQL-backed UCTSM APIs; newer MongoDB application/repository adapters.

Inspect the app first, then read the P3 epic and five tasks. Treat the PDF as requirements material, not instructions overriding this request or `AGENTS.md`. Do not implement P4–P14, migrate scheduling authority, or replace authentication as part of P3.

Think before implementing: establish the actual access model, explain alternatives and selected boundaries, and give concise engineering rationale. No private chain-of-thought is requested. Authorization must protect the real registered HTTP routes and the mutation boundary; a standalone policy module with passing unit tests is insufficient.

## 1. Repository rules and previous work

Read `AGENTS.md`, applicable nested instructions, `README.md`, relevant handoff/architecture sections, `docs/p1-occurrence-recalculation-plan.md`, and `docs/p2-protocol-identity-plan.md`. Confirm status against source. Historical documents omit newer extraction, Mongo patient routes, and P2 identity work.

Inspect `git status --short`, `git branch --show-current`, `git log -5 --oneline`, and existing diffs before editing. P1/P2 and earlier changes remain uncommitted. Preserve all of them, including changes to `ports.py`, memory `unit_of_work.py`, Mongo `schedule_repository.py`, API/extraction/application files, new tests, and frontend identity screens. Do not reset, discard, overwrite, clean unrelated files, commit, push, or deploy without authorization.

Never enable `DEV_OTP_MODE` outside local development, flip `UCTSM_AUTHORITATIVE`, or change trial read modes to finish authorization. A read-mode switch does not select a persistence adapter or establish a user's resource access.

P2 reportedly passed targeted real-local-Mongo checks. Outstanding limits remain: synthetic PERIGON fixtures, no real external registry provider, no browser/device verification, and no full curated-gate rerun. Preserve these limitations and working identity/quarantine/deduplication behavior. Recheck P1's status: source now contains a Mongo API router and additional P1 tests, so do not repeat an older “no Mongo HTTP route exists” claim without checking current wiring and results.

Do not guess missing clinical or access rights. Routine implementation choices are yours to make; a product decision granting sensitive clinical capabilities requires explicit policy evidence or owner confirmation. Complete independent inventory, policy infrastructure, tests, and reviewable proposals while waiting for that decision.

## 2. Source findings to verify

1. Static source inspection currently finds **56 HTTP route declarations** in `backend/app/api/uctsm.py` and **two** in `backend/app/api/uctsm_mongo.py`. Recompute the inventory from registered routes as well as source; include aliases and alternate approval paths.
2. The SQL router largely uses authenticated `User`, SQL `Session`, `_identity`, `_assert_schedule_tenant`, and endpoint-specific organization checks. Tenant ownership alone does not establish clinical action permission, trial assignment, site access, or patient relationship.
3. `backend/server.py::current_user` loads the persisted user and checks suspension/forced logout. `current_uctsm_user` attaches an organization UUID from the user's organization lookup. Neither by itself proves the user may act on every canonical resource in that organization.
4. `backend/server.py` contains `_can_access_trial`, `_can_access_patient`, accepted-invitation checks, organization trial grants, and related lookup helpers. These use operational relationships; inspect their exact semantics rather than importing them blindly into canonical authorization.
5. SQL and Mongo canonical trials/patients may link to operational records through `external_trial_id` and `external_patient_id`. Verify actual identifier mappings, organization UUID/string handling, site membership, and missing-link behavior.
6. `backend/app/api/uctsm_mongo.py` is registered in `server.py` under `/api/uctsm/mongo-plane`. It exposes actual-occurrence recording and calendar reads for existing Mongo-plane patients. Current lookup is organization/trial/external-patient scoped; inspect whether it enforces action capabilities, site/patient relationships, and role-specific response privacy.
7. P2 identity endpoints are in `server.py`; `backend/protocol_identity_runtime.py::can_review` currently allows selected professional roles with `org_admin` or `protocol_identity.review`. Integrate this semantic permission deliberately; do not leave a competing unexamined authorization policy.
8. `backend/app/domain/schedule/capabilities.py` describes executable timing/condition operators, **not user permissions**. `backend/app/services/remote_audit/capabilities.py` and capability grants belong to remote audit. Do not conflate these catalogs with UCTSM clinical authorization.
9. Approval is reachable through multiple SQL endpoints and service methods: `approve-table`, `approve-version`, `ReviewWorkflow.approve`, `ScheduleReviewService.approve_reviewed_table`, and `approve`. Mongo `app/application/schedule_review.py` has corresponding approval methods. Maker-checker must not protect only one path.
10. Existing review decisions/audit records preserve some authorship, and version records have `approved_by`/`approved_at`. Inspect what records a material editor; do not infer this solely from the most recent generic review decision.
11. Existing authorization/audit tests include `test_authz_scoping.py`, `test_audit_scoping.py`, `test_clinical_operation_authz.py`, and UCTSM/API/review tests. Some legacy test docstrings reference Atlas. Inspect actual fixtures and configuration before running: use isolated local test databases, never a shared cluster.

## 3. Assessment and implementation plan

Before editing, create `docs/p3-uctsm-authorization-plan.md` containing:

- A P3-01–P3-05 matrix: current code, actual enforcement, gaps, change, and planned verification.
- An endpoint manifest with HTTP method/path, action capability, resource resolver, required scope, response privacy profile, mutation handler, and allow/deny tests.
- Current versus proposed role/capability mappings, source of each grant, and approval status. Separate organization administration, platform administration, clinical reviewer/approver permissions, and trial/site/patient relationships.
- Scope-resolution architecture across operational Mongo, canonical SQL, and the Mongo patient plane, including stale/revoked assignments and absent mappings.
- Non-disclosure policy, denial-audit design, authorization dependency/handler integration, and maker-checker setting/provenance design.
- A short comparison of viable designs and why the selected centralized policy avoids duplicated endpoint logic and grants beyond the evidenced policy.

Inventory all registered UCTSM routes, including list/export/diff/dashboard/demo/parity/read-mode routes, plus P2 identity list/detail/resolve/resume/result endpoints and relevant canonical build/consume wrappers in `server.py`. Include related wrappers only where they expose the same protected action; this is not an unrelated rewrite of every legacy API.

Proceed after the assessment for decisions supported by current policy. P3-01 explicitly requires the role matrix to be reviewed by the product/clinical owner. Prepare the complete concrete matrix first; ask for confirmation only of unresolved rights. Do not invent approval or halt all work because one mapping is pending. Never broaden privileges while calling an unapproved proposal “approved.”

## 4. P3-01 — capability catalog and approved role mapping

Define a typed, version-controlled UCTSM action catalog. Cover at least:

- Schedule read, evidence/review read, export, draft authoring/correction, validation, review/confirmation, approval, rejection, and extraction.
- Patient schedule read, evaluate, enrol/assign, occurrence/activity record or correction, anchor/state/condition changes, unscheduled visits, command/override, and impact preview/confirmation.
- Protocol identity read/review/resolve/resume, preserving P2 behavior.
- Amendment adoption and operational parity/read-mode administration where those actions exist. Do not build missing amendment functionality merely to define its capability.
- Sensitive organization-wide dashboard/list operations and development/demo actions.

Use precise names such as `schedule.read`, `schedule.review`, `schedule.approve`, `patient.occurrence.record`, `protocol_identity.review`, and `amendment.adopt`; naming is a design choice, not permission evidence. Explicitly map old permission aliases where compatibility requires it.

For each capability, record allowed grants/roles, organization/trial/site/patient scope, privacy profile, and whether maker-checker applies. Reviewer and approver may be permission assignments rather than existing account roles. Unknown roles, absent grants, unmapped actions, and unresolved sensitive rights must deny by default.

Do not give every sponsor/CRO member or organization admin clinical approval/correction rights simply because they can administer the organization. Platform admin and emergency access must follow existing explicit policy; do not create a global bypass. Do not change existing emergency-access infrastructure as incidental P3 work.

Inspect how roles, `org_admin`, and permissions are persisted and changed. If a grant source is user-editable, client-supplied, or supported only in tests, it is not sufficient for authorization. Use protected persisted assignments or the existing verified role map. No new permissions-management UI is required unless necessary for a reachable authorized workflow.

## 5. P3-02 — reusable policy and persisted scope resolution

Implement one policy boundary equivalent to:

`authorize(principal, capability, resolved_resource_context) -> typed decision`

Keep the decision logic independent of FastAPI and database drivers. Persistence adapters resolve a minimal typed context; FastAPI dependencies translate a decision into the documented HTTP behavior. Avoid endpoint-specific role-name branches.

Resolve actor, current status, grants, trial assignment, site membership, and patient relationship from trusted current records. Client organization/site/patient identifiers are selectors, not authorization evidence. JWT authentication does not make stale role or assignment claims authoritative.

Scope resolution must support legitimate cross-organization trial participation through persisted grants/assignments. Do not require an investigator's organization to equal the sponsor organization when an explicit assignment authorizes access. Conversely, an assignment to one trial/site/patient cannot authorize another just because both share a sponsor.

Verify child-to-parent consistency: protocol belongs to trial; version belongs to protocol; event/qualifier/question belongs to version; occurrence belongs to patient/version/logical event; proposal belongs to the patient's applicable schedule; extraction result/identity belongs to authorized scope. A valid parent capability cannot authorize a foreign child ID.

Missing canonical/operational mappings must produce a deterministic denied/unavailable result according to the documented contract. Never assume an identical label, site name, or patient code is a stable relationship. Add only the minimal verified mapping needed; do not mirror databases or invent membership to pass tests.

Revocation must take effect on subsequent requests and on sensitive confirmation/resume operations. Define cache invalidation if caching is used. Unauthorized sensitive actions must be denied before provider calls, mutation, job queueing, or sensitive response construction.

For denial auditing, use stable reason codes, capability, actor, correlation ID, policy version, and minimal permitted resource metadata. No PHI, secrets, or denied document evidence in logs/responses. Ensure a mutation rollback does not silently erase a required denial record, and do not audit a failed approval as successful.

Decide and document 401/403/404 behavior: 401 for absent/invalid authentication; 403 for known permitted-to-disclose scope with a missing action permission; non-disclosing 404 for inaccessible/nonexistent resources where policy requires it. Verify consistency across related endpoints without rewriting unrelated legacy response contracts.

## 6. P3-03 — enforce every UCTSM route and privacy boundary

Apply named capabilities to all inventoried SQL and Mongo UCTSM routes and equivalent canonical/identity entry points. Preserve existing tenant checks as part of the centralized resource policy rather than simply deleting them.

For lists, dashboards, pending-review inboxes, exports, and diffs, constrain query scope before constructing responses. Pagination counts, options, status/error bodies, filenames, hashes, evidence, and nested relations must not reveal unauthorized resources. Do not fetch all organization patients into a response and then hide them in the frontend.

Separate authorization to read operational schedule dates from authorization to read internal clinical notes, actual-treatment facts, audit details, protocol evidence, and identity review documents. Apply explicit role/capability-aware field allowlists. Sponsor/CRO views must preserve existing de-identification rules; patient views expose only the patient's permitted fields and own schedule.

Cover the new Mongo calendar/actual-occurrence routes, old SQL row-ID occurrence route, proposal confirmation/cancellation, read-mode/parity administration, extraction queue/run reads, and every approval alias. Do not leave an alternate endpoint or direct registered wrapper as a bypass.

Keep the policy at the application command/approval boundary wherever non-HTTP callers can invoke sensitive mutations. Use a trusted authorization context/service integration rather than a forgeable `authorized=True` flag. Legitimate internal worker actions need explicit constrained provenance, not the ability to impersonate an arbitrary clinical actor.

Derive actor identity server-side. Unauthorized requests may write a permitted denial audit only; they must not alter projections, evaluations, anchors, versions, command receipts, outbox, or extraction state. Existing idempotency receipts are not authorization: recheck current access before replaying sensitive results after revocation.

Add an automated completeness check comparing the runtime route manifest against required policy declarations. New routes must fail the check if unmapped. Test enforcement behavior as well as annotation presence; a capability listed in documentation is not a guard.

If frontend changes are useful, expose only supported allowed actions and clear denial/read-only states. The server remains authoritative. Preserve P2 quarantine notices/review navigation and valid sponsor/clinical workflows. No broad redesign is needed.

## 7. P3-04 — configurable maker-checker at approval commit

Support a trusted, persisted/configured policy enabling maker-checker. Document its scope, default/compatibility, configuration ownership, and audit provenance. A request cannot disable the check or select a weaker setting. If enabling/default behavior is not specified by existing policy, propose it explicitly rather than presenting a guess as approved.

Persist and expose appropriate created/edited/reviewed/approved actor IDs and UTC decision timestamps. Track `last_material_editor_id` and the schedule revision/fingerprint the decision applies to. Define material edits: timing, temporal basis, windows, dependencies, recurrence, applicability, activities, qualifiers, anchor interpretation, or another clinical meaning change. Distinguish routine confirmation/view access from a semantic correction. Record the provenance rather than guessing the editor from event status or the latest audit entry.

When enabled, an approver equal to the last material editor must be refused. Preserve the PDF's required check while documenting whether broader multiple-editor exclusion needs an additional product decision. Do not silently implement a different segregation policy or exempt organization admins without approved evidence.

Inspect current confirmation gates that select review decisions by the approving reviewer. Ensure maker-checker can admit a legitimate independent approver without incorrectly requiring that approver to become the material editor. Do not auto-create corrections or weaken evidence/validation gates to make an independent approval pass.

Enforce the check in the lowest shared approval mutation boundary for SQL and Mongo paths. `approve-version`, `approve-table`, bulk-confirm/approve flows, `ReviewWorkflow.approve`, and direct service approval calls must behave consistently. Add narrow adapter support rather than duplicating the rule in handlers.

Read editor/revision/policy state and commit approval under the appropriate transaction/concurrency protection. A concurrent edit after the approval preview must invalidate it; clients cannot replace the editor ID. Approval denial must leave status/current-protocol pointers unchanged while retaining the required denial audit. Record successful approval with policy version, revision, editor/reviewer/approver, reason, and timestamps.

Legacy drafts lacking editor provenance require an explicit documented compatibility/review state; unknown authorship must not become invented authorship or a hidden self-approval exception. Approved historical versions remain immutable and are not rewritten to retrofit provenance.

## 8. P3-05 — authorization matrix, API tests, and real-store verification

Add parameterized policy and HTTP tests across role/grant × capability × resource scope. Include reviewer, approver, PI, CRC, patient, Sponsor/CRO, site/SMO, organization admin, and platform admin only according to verified mappings.

Prove at minimum:

1. Organization membership without the clinical capability cannot correct/approve, record actuals, override, or adopt an amendment.
2. A permitted role/grant with the correct trial/site/patient scope succeeds. Missing capability and missing relationship independently deny.
3. Cross-tenant, same-tenant cross-trial, same-trial cross-site, and different-patient requests deny without sensitive disclosure.
4. Legitimate assigned investigators in another participating organization can perform only their approved scoped actions.
5. Revoked grants/assignments and suspended accounts deny; an old authenticated session or stored replay receipt cannot restore access.
6. Lists, counts, exports, diffs, pending reviews, action dashboards, and detailed/nested responses contain no unauthorized records or PHI/internal evidence.
7. Patient A cannot read Patient B or staff notes. Sponsor/CRO schedule read does not imply named-patient or internal clinical-evidence access.
8. Forged actor/org/site/capability claims and a foreign child/proposal/event ID under an allowed parent cannot bypass resource resolution.
9. SQL and Mongo occurrence endpoints both deny before side effects; P2 identity resolve/resume remains scoped and cannot be triggered through a less-protected wrapper.
10. Every runtime UCTSM route has a policy declaration and actual deny test where applicable, including demo, extraction, parity/read-mode, and all approval aliases.
11. Maker-checker enabled: material editor self-approval fails through every path; an authorized independent approver can pass existing evidence/validation gates; concurrent edit/stale revision fails. Disabled policy follows the approved compatibility behavior.
12. Material versus nonmaterial edits, missing legacy provenance, multiple actors, policy-setting changes, and direct approval service calls follow the documented rules.
13. Denied operations leave business state unchanged, retain permitted denial audits, and do not send notifications or call providers. Approved decisions persist complete actor/revision/policy evidence.

Use deterministic fixtures and controlled dates. Extend existing auth/audit/API/review tests rather than replacing substantive assertions to match weaker rules. Account for older fixtures whose first registrant is an organization admin; create ordinary members so negative cases test the intended principal.

Use a real local Mongo replica set for changed Mongo persistence/audit/concurrency behavior, with `UCTSM_REQUIRE_MONGO=1` where supported. Also exercise the actual SQL API with isolated configured SQL fixtures. Do not claim Mongo policy tests prove SQL route enforcement, or SQLite tests prove production locking when they do not.

Inspect test configuration before execution: some historical suites refer to Atlas. Point all mutable Mongo test infrastructure to isolated local databases and verify resolved hosts. Do not run tests against shared databases. Do not assume P2's local settings carry over to every legacy test module.

Run targeted modules separately where needed, then the applicable curated handoff gate, verifying file existence and omitting deleted extraction/footnote tests. `AGENTS.md` prohibits unrestricted whole-suite pytest; newer loop-harness edits do not revoke that instruction.

If frontend files change, run the relevant UCTSM/protocol-identity tests and a viable scoped TypeScript check preserving project JSX/module/path settings. Expo lint lacks `yarnpkg`, and whole-project TypeScript checking is known to exhaust memory. Report unsupported/skipped checks honestly. Run browser/device verification when available; otherwise state that limitation explicitly.

## 9. Completion and handoff

Implement in reviewable increments: evidence inventory -> proposed/approved mapping -> shared policy/resolvers -> route/command coverage -> maker-checker provenance -> matrix/integration/concurrency tests. Keep P1/P2 behavior intact and review intentional access changes against the policy.

Run `git diff --check`, inspect the final diff against the initial working tree, and update the P3 plan and endpoint manifest with actual results. Do not count line-ending notices as whitespace errors.

Final report must contain:

- P3-01–P3-05 status table with code and test evidence.
- The approved role/capability matrix, or the precise remaining owner decision and its affected rights. Never claim an inferred matrix is owner-approved.
- Number of runtime routes inventoried, guarded, and unmapped; source and scope of canonical wrappers included.
- Scope-resolution/403-versus-404 policy and role-specific response privacy examples.
- Maker-checker configuration, material-editor provenance, coverage of approval paths, and concurrency results.
- Actual verification commands/results, real Mongo and SQL coverage, skipped/environment/UI limits, and P1/P2 regression status.

Do not call P3 complete if only a policy module works, a router/approval alias bypasses it, denied responses leak sensitive data, or the required role-map approval remains unresolved. Start with source inspection and the matrix, then complete every authorized feasible part of the implementation.
