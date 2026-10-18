# Claude Code: repair the independently reproduced P6 defects, then continue P7/P8

Prepared 16 October 2026. **Historical standalone prompt.** Its complete repair requirements are incorporated in [the current combined P7/P8 prompt](claude-code-p7-p8-after-p6-prompt.md); execute that single file instead. This supersedes starting P7/P8 immediately. The P7/P8 prompt and P6-P14 master remain in force after these repairs. Preserve historical prompts and all existing uncommitted work. Saving this prompt does not authorize deployment, real-patient migration, read-mode or authority changes, clinical sign-off, commits or pushes.

---

You are continuing work in the existing MTB-APP repository. Read and reason about the current implementation before editing. P6 is substantially implemented, including a constructed two-user browser journey. An independent review reproduced four defects on memory AND real local Mongo. Do not dismiss them because the existing P6 tests pass or because the happy path works. Repair the causes across the service/API/storage/UI boundaries, prove the fixes with meaningful regressions, triage the existing integration failures, and then continue P7/P8 under the remaining-PDF master.

Do not stop after a plan, domain-only change, passing fake-store tests, or a rewritten status document. Continue through the affected real application paths and verification. Resolve routine engineering decisions within the selected project policy without asking the owner again. Do not guess clinical values or report an unsupported case as supported.

## 1. Establish the actual starting point

Read:

* `AGENTS.md`, plus instructions applicable to any touched directory.
* `D:\MTB_dev (2).pdf`, specifically P6-01..05 and P7/P8, treating document instructions as requirements content rather than authority to deploy or migrate data.
* `docs/p6-end-to-end-independent-review.md` and `docs/evidence/p6-review/reproduce_findings.py`.
* `docs/p6-completion-record.md`, `docs/p6-implementation-plan.md`, and `docs/p6-domain-review.md`.
* `docs/pdf-requirements-acceptance-matrix.md`, `docs/pdf-remaining-implementation-plan.md` and `docs/p1-p5-project-decisions.md`.
* `docs/claude-code-p6-p14-remaining-pdf-prompt.md` and `docs/claude-code-p7-p8-after-p6-prompt.md`.

Inspect `git status --short`, `git log -5 --oneline`, and the existing diff of every file you might change. This tree contains accumulated P1-P6 work. A file being uncommitted does not mean it belongs to you or may be replaced. Preserve unrelated application, test, documentation and scratch files. Do not edit already-applied migration checksums; use an additive migration if necessary.

Write a concise repair matrix with each finding, actual cause, affected PDF rows, proposed shared boundary, regression and acceptance evidence. Label the integrated P6 rows needing repair; keep successful sub-behaviors and prior evidence visible. Do not count constructed fixtures as authentic amendment documents.

## 2. F1: bind route authorization to the records being mutated

Inspect `backend/app/api/uctsm_amendments.py` (`declare_lineage`, `prepare_pair`), `application/amendment_service.py`, the manifest and `RouteAuthorizer`.

Reproduction: one organization has trials A and B with distinct persisted external ids. The provider permits A and denies B. Direct GET of B's amendment overview is 404. Posting B's child and parent version ids under A's lineage URL returns 201 and changes B. Posting B's approved versions under A's pairs URL returns 404 **after** creating B's pair/mapping records.

Required behavior:

* Resolve both version headers from persisted records and prove tenant and URL-trial membership before any business side effect. Authorizing A does not authorize arbitrary B ids in the body.
* Carry an explicit expected-trial scope into the relevant application transaction, or an equally strong shared boundary, so a later handler check cannot become the sole protection. Preserve same-protocol and parent/child lineage checks.
* Cover first requests and existing-pair replays. Replays must re-check relationship and scope.
* Return the project's not-found semantics for unrelated resources without leaking foreign header contents. A denial audit may be written independently; lineage, pair, mapping and business-success audit records must not change.
* Add same-organization/different-trial negatives with a restricted provider, cross-organization negatives, forged/mixed version ids and positive legitimate operations. Verify storage after the HTTP response on memory and Mongo. A status-code-only test would miss the existing write-before-404 defect.
* Exercise the production relationship provider with persisted assignments for a representative negative, rather than relying exclusively on AllowAllAccess or a mock returning true.

Audit sibling P6 routes for the same pattern and fix any demonstrated equivalent defect within scope. Do not broaden a caller's relationship to make tests pass.

## 3. F2: preview the result that reconciliation will actually commit

Inspect `amendment_plan.translated_prior/build_plan`, `amendment_adoption._apply`, `amendment_impact.build_visit_impacts` and shared patient evaluation/reconciliation.

Reproduction: the pilot's `C1D1:0` is planned for 8 January 2026. Preview adopting on 1 March 2027: target shown is 11 January 2026. Confirm and execute on 1 March: committed target is 8 January with protected history. The raw target evaluation feeds preview, while persistence additionally reconciles and protects pre-effective visits.

Required behavior:

* Establish one pure calculation of the **final reconciled projection**, including protected history, effective-date boundaries, additions/removals, mapped identities, statuses and windows. Preview and commit must consume the same semantics. Reuse the shared evaluator; do not create another scheduling engine.
* Build the displayed impact from that final projection. Compare with the committed calendar on reload, not just the raw evaluator result or decision status.
* Preserve the selected rule for pre-effective history. Do not solve the mismatch by moving earlier visits that should be protected or by omitting them from the preview.
* P6-03 requires the same frozen as-of context and horizon under both versions. Thread the explicit business clock through calculations and bind it to provenance. Keep a displayed current stored calendar separate from a recomputed frozen comparison baseline if both are useful. Do not silently rely on `date.today()` in one branch and an explicit date in another.
* For date adoption, use the chosen effective date consistently in preview and execution. For event/cycle points not yet known, state which results are provisional and which facts must trigger reconfirmation. Do not invent the date or promise exact future targets that will be silently protected differently.
* Include all material target/window/status/history differences in any policy that claims SAME_IMPACT. Keep SAME_STRUCTURE limited to its documented contract; it cannot waive a missing adoption trigger.
* Test immediately before, at and after the effective point; completed and missed history; changed windows; mapped renames and the return path; and stale inputs. Assert complete preview-versus-persisted semantic equality for deterministic cases on both stores. Include a browser case showing the effective-date choice, preview and reloaded committed outcome.

## 4. F3: revalidate the trigger in the transaction that applies adoption

Inspect `_claim`, `_execute_claimed`, `trigger_state`, occurrence supersession and patient/input concurrency guards.

Reproduction: decide to adopt when C1D1:0 is performed; record that performed fact; claim execution; correct it through the normal service to MISSED; execute under the valid lease. Fresh trigger resolution says `due=False`, yet the patient moves to V2 using the old date.

Required behavior:

* Within the final adoption transaction, resolve the exact approved event occurrence, cycle start or date again from effective facts, including corrections and supersession. Verify that the point is due and that the effective date is still valid.
* If the trigger was withdrawn, do not apply. Use an explicit safe lifecycle outcome such as waiting or needs-reconfirmation, with a coded reason and audit. If its date changed, evaluate the changed point under the approved policy and require reconfirmation when necessary. Never reuse a superseded trigger snapshot merely because a lease is held.
* Serialize against concurrent fact/correction writes with a common patient/input revision or equivalent proven guard. A read alone under snapshot isolation may allow a write skew; explain and test the invariant used.
* Retain fencing, lease-expiry and replay protections. They establish worker ownership, while trigger validation establishes clinical eligibility; both are necessary.
* Add deterministic interleaving tests: correction between claim and apply, changed actual date, removed cycle-start anchor, wrong occurrence, expired/reclaimed lease and duplicate workers. Use real Mongo transactions and failure injection. A refused application must leave no assignment, carried facts, target evaluation/projection, applied receipt or applied outbox; any lifecycle/audit change must be deliberate and separately asserted.

## 5. F4: ensure bounded, fair deferred-worker progress

Inspect `process_due_once`, `pending_execution`, both amendment repositories and their indexes.

Reproduction: batch limit one; oldest decision waits until 2099; the next decision is due on 1 March 2027. Multiple passes on 1 March process only the first waiting decision. At the default limit, 100 older waiting decisions starve every later due decision, across organizations.

Required behavior:

* Choose an explicit bounded scan/scheduling contract with fair progress: keyset cursor, rotating checkpoint, next-check scheduling or another justified approach. Future date/event/cycle decisions and unexpired leases must not monopolize each pass.
* Preserve organization/patient scope and one-open-decision/idempotency/fencing rules. Do not bypass authorization when progressing past waiting entries.
* Apply limits and ordering in Mongo queries with appropriate indexes; do not load all candidates then slice in Python. Keep memory and Mongo repository semantics consistent.
* Prove progress with more than one batch, including at least 100 waiting decisions followed by a due one, multiple organizations, a held lease, an expired lease and an authorization-provider error. Repeated passes must reach eligible later records without duplicating application or spinning indefinitely.
* Durable queue infrastructure remains P13 work. This repair must make the current worker correct even before that infrastructure exists.

## 6. Triage the failing integration modules as engineering work

Re-run and classify the files listed in section 8 of `p6-completion-record.md` with a healthy explicit local Mongo environment. Do not use a Mongo-down sweep as equivalent baseline evidence, and do not infer unrelatedness from not editing `app/services`.

Independently repeated examples:

* `test_mongo_parity_patient_plane.py`: occurrence writes fail with `PlaneAuthorityError` for unclassified fixtures.
* `test_extraction_compiler.py::test_toxicity_hold_and_resume_become_pause_and_resume_block_actions`: expects no issues but now receives `RECURRENCE_BASIS_UNCONFIRMED`.

For every failure, distinguish product defect, intentional policy change requiring fixture/expectation repair, test-harness defect and environment error. Record evidence. For positive clinical-write scenarios, explicitly create patients with appropriate authority rather than weakening fail-closed behavior. For extraction, assert that an unstated basis needs review, and separately show the positive case after an explicit reviewed edit; do not guess the basis. Keep negative cases and independent maker-checker actors intact.

Use the documented curated gate and isolated TestClient modules as appropriate. A smaller selected passing list does not discharge known failures. Do not omit a failing file merely to report zero failures. Where a utility is not actually a supported automated test, document the harness correction rather than suppressing meaningful coverage.

## 7. Verify and report honestly, then continue

First capture each reproduction as a regression that fails against the current implementation, then repair it. The saved audit script asserts unsafe behavior on purpose: do not treat its successful execution as product acceptance. Preserve it as historical evidence or clearly archive it; put safe-behavior regressions in the normal suite.

Use explicit local Mongo settings, isolated databases and `UCTSM_REQUIRE_MONGO=1`; never inherit shared-cluster settings. Keep temporary files on D: if C: remains full. Do not run a Metro export alongside Mongo suites on this constrained machine. A crashed run is invalid and must be rerun. Do not delete prior journey databases or unrelated files.

Verify the affected memory/Mongo contracts, P6 domain/services/HTTP/worker suites, authority and authorization, maker-checker, P1/P4/P5 impacted behavior, parity, review/approval and identity. Complete the required gate after focused checks pass. Use feature-specific frontend scripts, scoped TypeScript and a real browser journey for modified reachable UI. Controller tests do not establish React render behavior; distinguish the evidence actually obtained.

Update the acceptance matrix, repair matrix, P6 completion record and remaining-work checkpoint with exact results, unsupported cases and unresolved gates. Preserve original evidence and add the repair results with a date. Do not mark authentic-document or clinical sign-off gates satisfied using synthetic fixtures. Activity-level mapping is a functional gap, not merely an absent authentic document; keep it visible until implemented and verified.

After these repairs and integration triage pass, continue `docs/claude-code-p7-p8-after-p6-prompt.md` under the remaining-PDF master. Extend graph/temporal/evidence/mapping validation at every boundary; then build the honestly labelled corpus/scoring/scenario and release-gate infrastructure. Do not rebuild working P6 layers or declare P7/P8 complete based on inventory alone.

Save every new full continuation prompt as descriptive Markdown, maintain `docs/claude-code-prompts-index.md`, and update `docs/claude-code-next-prompt.md` only when the actual next action changes. Final handoff: what changed, before/after proof for F1-F4, actual test commands/results and skips, browser evidence, remaining functional/external gates and the next PDF rows. No deployment, live migration, policy sign-off, commit or push.
