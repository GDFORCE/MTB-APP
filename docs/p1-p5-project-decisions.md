# P1-P5 project decisions under owner delegation

Decision date: **15 October 2026, Asia/Calcutta**.

Authority: the user instructed **"take the decsion which is best for proejct"** after reviewing the outstanding choices. This records the selected architecture and target application policy under that delegation. It does not assert an external clinician's approval, regulatory certification, implemented acceptance or deployment.

These choices supersede the corresponding owner-choice blockers in earlier P1-P5 prompts/plans within the scope below. Audit defects, evidence requirements and unrelated instructions remain valid.

## 1. Architecture and selected pilot

**Choose Mongo-authoritative canonical patient scheduling using the existing repository ports. Do not build a SQL command adapter for this phase.**

The existing ledger/application service already executes on Mongo, and branch history includes Mongo ports for extraction, authoring, enrolment, parity and related services. A second SQL UoW/repository implementation would add work and maintenance while still needing frontend integration. Keep the pure evaluator and shared application algorithms. This selects the canonical command plane, not merely the unrelated legacy operational Mongo collections.

Pilot: **`MTB-UCTSM-PILOT-001`**, a **new isolated local/development trial with synthetic patients only**. This is a chosen trial code/name, not an invented existing UUID or real registered study. Generate IDs normally and persist an environment/pilot marker; refuse a conflicting existing non-test trial instead of repurposing it.

Implement the generic product workflow and demonstrate it through ordinary navigation. Do not wait for a real trial name or build a fixture-only workbench. Reuse `app/application/operational_bridge.py` and existing native ports where appropriate; distinguish the live SQL bridge at `app/services/operational_bridge.py`.

The pilot's definition/version, enrolment, actuals, anchors, conditions, state/assignment facts, projection, commands, audit and outbox need explicit coherent ownership. Show the committed Mongo evaluation/calendar and reload from that same authority. Immutable protocol evidence may be shared through verified identity/content mappings; do not mix mutable SQL patient state with Mongo commands.

Persist explicit `MONGO` authority through trusted native enrolment. Unclassified imported records are not automatically native: handle historical None values through evidence-backed compatibility classification, not arbitrary mass relabelling. Every equivalent mutation/correction/replay entry point, including the old actual route, must enforce authority.

**Existing SQL/legacy patients keep their current authority.** This does not authorize live migration/mirroring, clinical patient cutover, `UCTSM_AUTHORITATIVE`, production read-mode changes, deployment or real-user grants. Their later migration needs an actual mapped cohort, enforced SQL/legacy write freeze, race handling and comparison evidence. Preserve the existing legacy-engine parity gate separately from SQL/Mongo comparison.

Prefer forward repair within the authoritative plane. Do not promise SQL rollback for commands SQL cannot represent. Emergency containment disables writes while preserving facts, commands, audit and notification effects; unpinning or deletion is not clinical rollback.

## 2. Selected target role/capability matrix

This is the target application policy selected by owner delegation, ready for implementation/local verification. Version and reference it in code. Record selected, implemented and active configuration separately; do not invent a named clinical signatory or grant rights to live users.

Defaults never replace persisted organization/trial/site/patient relationships, resource ownership or privacy checks. Unknown roles/capabilities/routes deny. Patient grants cannot create staff rights. Sponsor/CRO patient responses stay de-identified, including explicitly granted history access. No platform-admin bypass.

| Capability | Selected default holders |
|---|---|
| schedule.read | Sponsor, CRO, PI, CRC, Site, SMO |
| schedule.evidence.read | Sponsor, CRO, PI, CRC |
| schedule.export | Sponsor, CRO, PI |
| schedule.author | Sponsor, CRO |
| schedule.extract | **Sponsor, CRO, PI** |
| schedule.validate | Sponsor, CRO, PI |
| schedule.correct | Explicit grant only |
| schedule.review | Explicit grant only |
| schedule.approve | Explicit grant only, maker-checker enforced |
| schedule.reject | Explicit grant only |
| patient.enrol | PI, CRC, Site, SMO |
| patient.schedule.read | PI, CRC; Sponsor/CRO de-identified; patient own record only |
| patient.schedule.evaluate | PI, CRC |
| patient.occurrence.record | PI, CRC |
| patient.anchor.record | PI, CRC |
| patient.state.record | PI, CRC |
| patient.unscheduled.create | PI, CRC |
| patient.impact.preview | PI, CRC |
| patient.impact.confirm | PI; high-impact operations additionally need the high-impact grant |
| patient.command.preview | PI, CRC; explicit read grants retain recipient privacy restrictions |
| patient.command.high_impact | Explicit grant to an assigned PI only; no role default |
| amendment.adopt | No role default; reserved, explicit assigned-PI grant when a supported workflow exists |
| protocol_identity.read | Sponsor, CRO, PI, on permitted uploads/trials |
| protocol_identity.review | **Explicit grant only; remove automatic org-admin privilege** |
| trial.read_mode.read | Sponsor, CRO, PI |
| trial.read_mode.set | Explicit grant only; existing parity/cutover checks still required |
| parity.run | Sponsor, CRO, PI |
| parity.accept | Explicit grant only; no bypass of non-waivable gates |
| dashboard.read | Sponsor, CRO, PI, CRC with role-safe responses |
| demo.seed | Explicit grant plus local/demo configuration |
| authorization.grants.manage | Organization admin within their own organization |

Protocol correction/review/approval grants designate authorized trial reviewers; administrative status or a role name does not establish clinical qualification. Preserve accountable grantor, reason, scope and provenance. This does not assign a real person to a reviewer position.

Patient clinical data-entry grants are limited to assigned PI/CRC; scoped Site/SMO enrolment is retained for administration. Sponsor/CRO must not acquire patient clinical-write rights through explicit grants. Apply eligibility centrally, including alias/replay paths. High-impact authorization must follow the proposal's actual operation, including older impact APIs, rather than a weaker generic confirm capability.

Identity-review grants may designate appropriately scoped Sponsor/CRO/PI users. Use separate synthetic local editor/reviewer/approver actors as required for tests; no production accounts receive grants from this document.

## 3. Self-grants and maker-checker

**Choose the stricter self-grant restriction.** Nobody grants clinical write/decision rights to themselves, including occurrence, state and anchor recording, enrolment, unscheduled creation, other clinical mutations, correction/review/approval, amendment adoption and high-impact commands. Protect identity resolution, authority/read-mode changes and parity acceptance from self-escalation too.

Legitimately assigned PI/CRC role defaults continue to work; this does not require a second person to countersign every ordinary data entry. Keep the self-grant-protected set distinct from capabilities requiring maker-checker. Administrative grant management is not clinical authority.

**Maker-checker is ON with `LAST_EDITOR` for new approval actions in the selected workflow.** Approver differs from last material editor; record policy/provenance at shared approval boundaries. No administrator exemption or request override. Existing approved versions remain immutable. `ANY_EDITOR` can remain available as a stricter option but is not the selected default.

Use explicit rollout/grant readiness for an existing environment. Selecting this policy does not silently change a running production configuration.

## 4. Explicit dependency semantics

**TRIGGER/PRECONDITION labels alone do not mean actual attendance or treatment.** Actual visit, actual treatment, manual anchor or other evidence must be explicitly represented in timing/dependency mode/condition. A state condition may be the trigger.

An explicit actual requirement waits for the selected occurrence's own required actual. No substitution from another occurrence, a planned date or the wrong actual type. False dependency condition deactivates that dependency; unknown waits; independent event timing requirements still apply. Missing/conflicting protocol instructions remain unresolved and block approval where required.

**Deprecate ambiguous `Dependency.source_occurrence=CURRENT` in newly approved definitions.** New/edited drafts require FIRST, LAST or NUMBER explicitly, or a supported reviewed occurrence mapping. Never silently translate CURRENT to LAST. Target-relative current, when supported, means a source occurrence explicitly mapped to the target logical occurrence; do not infer it from closest date, generation horizon or equal indexes of unrelated series.

Approved legacy definitions keep their pinned CURRENT=LAST historical semantics and are flagged for clarification in the next reviewed version. Do not move enrolled patients through a silent evaluator upgrade. Implement explicit supported selectors and actionable validation first; a new mapping operator is not required merely to close P1-P5.

This settles application interpretation/validation policy without inventing clinical instructions for a protocol.

## 5. PI upload/extract/consume

**PI retains upload/extraction and draft-consume rights for an authorized existing trial.** Centralize this as `schedule.extract` plus trusted relationship, draft ownership and P2 identity/quarantine checks. Keep approved versions immutable and audit attachment/new-draft creation.

Pre-trial extraction can create a user-owned non-authoritative draft; attachment still requires target-trial authorization. This does not grant PI general `schedule.author`, correction, approval, adoption or patient-command privilege. Centralize all legacy wrapper/alias checks; a role-only handler must not bypass the capability service.

## 6. Registry and genuine document evidence

**Choose the official public ClinicalTrials.gov v2 API for NCT checks.** Do not wait for private registry credentials for this integration. Use the existing registry port, bounded timeout/retry/backoff/cache, typed outcomes and provenance. Send registry identifiers only, never patient data or uploaded PDF contents. Outage is UNAVAILABLE, not a fabricated match/conflict; unsupported registries stay explicitly unverified.

Primary sources: [NLM open-access API guidance](https://www.nlm.nih.gov/pubs/techbull/ja25/ja25_clinical_trials_screen-scraping.html), [official API reference](https://clinicaltrials.gov/data-api/api).

Independently verified on the decision date: unauthenticated GET `https://clinicaltrials.gov/api/v2/studies/NCT02088554` returned HTTP 200, title **PERIGON Pivotal Trial**, with its published protocol metadata. Downloaded the linked genuine official protocol:

- Local: `docs/evidence/protocol-identity/NCT02088554/Prot_003.pdf`; adjacent `source-manifest.json`.
- Source: `https://cdn.clinicaltrials.gov/large-docs/54/NCT02088554/Prot_003.pdf`.
- Registry document date 2019-03-13, uploaded 2019-08-27T12:20.
- 96 pages, 1,099,656 bytes; valid parsed PDF, PERIGON present in first five pages.
- SHA-256 `142fd9b3cbc461da523e3edd30bad33ce33c58e79a3cdec20474dff658226f96`.

Use this genuine fixture. A deliberately renamed copy can exercise filename NCT02088541 versus PERIGON content, but identify it as a constructed mislabelling of genuine content, not recovery of the historical upload. A renamed file or fabricated text is not a genuine amendment. Retrieve authentic earlier/amended versions from authorized sources where available, with provenance/hashes. Exact original-upload and real-version-pair evidence remains open until obtained; do not ask the user for the official file already retrieved here.

No extraction result was reclassified or attached by this read-only evidence acquisition.

## 7. Engineering and verification proceed

Authority bypass, controller cancellation/disposal repairs, production relationship-provider tests, pagination, component/binding tests and local browser setup do not require further product permission.

Select stable scoped cursor pagination, 50 default/200 maximum per page, explicit continuation, authorization-aware selection and a compatible client/API rollout. No silent first-500 truncation, unauthorized totals or unbounded in-memory trial loading.

Add the actual controller script to package scripts/gates. Verify proposal/action identity, disposed actions, unknown outcomes, React setup/cleanup binding and mounted stop-policy control. Preserve the passing R6 order/correction/replay tests. Typechecking is not component/browser proof.

Set up a suitable local renderer/browser harness and use the selected synthetic pilot through real navigation. Report exact attempted commands/blockers when environment checks cannot run. PostgreSQL locking matters for changed/supported SQL behavior relying on it; it is not blanket grounds to withhold unrelated domain-only acceptance.

## 8. Status and rollout

Replace superseded owner-choice blockers with **DECISION_RECORDED_IMPLEMENTATION_PENDING** or the actual verified state, linked here. Distinguish selected policy, implemented code, verified behavior and active deployment. Do not claim external clinical sign-off or completed corpus/tests because a decision exists.

The local synthetic pilot and reversible engineering work are selected. Real patient migration, production user grant rollout, deployment, commit/push and live authority/read-mode flips are not performed or implied. P1-P5 acceptance still needs actual row-level evidence; decisions do not convert 25/70 coverage into verified completion.
