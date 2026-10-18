# Claude Code: implement the selected decisions and finish P1-P5

The project owner delegated the pending choices. Read **`docs/p1-p5-project-decisions.md` fully**: architecture, pilot, 31-capability target matrix, self-grants/maker-checker, dependency semantics, PI upload, registry/evidence and rollout scope are selected there.

Do not ask the same SQL-versus-Mongo, trial-name, role-default, self-grant, CURRENT/trigger or PI-upload questions again. The pilot is a **new isolated local/development trial `MTB-UCTSM-PILOT-001` with synthetic patients**, not an existing live trial. Build the generic product path and prove it through normal local navigation; do not hardcode a fixture-only workbench or move real patients.

Read AGENTS.md, nested instructions, the PDF's P1-P5 rows, `docs/p1-p5-latest-repair-review.md`, the full closure contract `docs/claude-code-p1-p5-final-closure-prompt.md` and current source. Earlier instructions awaiting these choices are superseded within the new decision's scope. Other audit, compatibility and acceptance requirements remain valid.

## Scope clarification after the partial P3 update

**Continue the complete P1-P5 closure; do not rebuild P3 from scratch or stop after changing its defaults.** Read `docs/p3-selected-policy-followup-review.md`. The first policy changes are present, but CRC high-impact grants and Site/SMO patient data-entry grants still authorize contrary to the selected eligibility rules. Add central recipient eligibility and policy/HTTP/confirm/replay regressions. Keep Site/SMO administrative enrolment legitimate.

Runtime policy version/status still say v1 / PROPOSED_PENDING_OWNER_APPROVAL; update provenance and the tests that pin it to the actual delegated selection without inventing external clinical sign-off. Replace the general suite's import-time maker-checker-off baseline with correctly separated editor/approver fixtures for the selected journey; scope any intentional legacy OFF behavior to its own tests. Verify the full selected journey with maker-checker enabled by default, including denied self-approval and successful independent approval. Preserve meaningful ON/OFF compatibility checks.

The remaining steps below still apply. Legacy wrapper mapping, the authority/controller defects and the selected native pilot are not additional owner-decision questions.

## Implementation sequence

### Latest review: native pilot and registry pass

Read `docs/p1-p5-pilot-registry-review.md` before continuing. Earlier grant-eligibility, explicit-SQL-authority and controller cancellation/disposal regressions now pass; preserve them. Do not stop at another P3-only update or describe the remaining work as browser evidence alone.

Prioritize these independently reproduced/source-verified gaps, without asking for new owner decisions:

1. Fresh pilot seed with the production relationship provider returns 200, but its creator sees an empty patient list and a 404 calendar: native patients have no operational relationship records and the provider does not consult native assignments. Implement a trusted native relationship contract and verify the entire fresh-seed/list/calendar/command/reload path with that provider. No blanket pilot-mode access bypass.
2. Registry corroboration currently accepts an unrelated protocol when a background mention matches an acronym and only the organization's first word overlaps. Keep ambiguous identity UNCERTAIN with authorized review, or prove exact trusted official-document/version/hash linkage. Do not weaken identity acceptance to make genuine PERIGON tests green. Add false-positive, citation, shared-prefix and conflicting-identifier cases.
3. Injected first-evaluation failure leaves one unevaluated patient; reseed incorrectly returns success. Make patient plus first evaluation atomic and overall seed completeness recoverable/idempotent, with failure/retry/concurrent regressions on memory and Mongo.
4. Unknown authority None still permits preview/confirm through `UNCLASSIFIED_COMPAT` without evidence. Require explicit trusted native authority or auditable evidence-backed classification; preserve read access/history and update fixtures deliberately, not by silently relabelling live data.
5. The older SQL impact API still permits hold/resume/stop through generic PI confirmation without the selected high-impact grant. Classify typed preview and trusted persisted confirm/replay operations centrally and enforce the extra grant. Cover normal PI denial, assigned granted PI success, ineligible CRC, revocation and replay without changing SQL patient authority.

After these repairs, perform the real UI/binding walkthrough and remaining document evidence work. A synthetic seed with a server-generated second actor is not itself a two-user review/approval UI walkthrough. Update stale original report sections as well as appendices. The detailed sequence below remains applicable where not already completed.

1. Inspect status/log/diffs, preserve all unrelated work, and make a concise plan linking the decision. Distinguish target policy from active configuration. No commit, push or deployment.

2. Repair the authority bypass first: SQL-authoritative preview=409 but older Mongo actual route=201. Add red memory/Mongo/HTTP/service tests; enforce one trusted authority decision on equivalent writes, correction and replay. Prove no partial facts/ledger/evaluation/receipt/outbox writes on denial. Classify historical None authority deliberately; do not trust request-body authority or mass-relabel patients.

3. Repair the controller: delayed OLD cancellation cannot clear NEW preview; disposed public actions/probes cannot start API requests. Verify action/proposal identity, cancel/confirm races, lost-confirm retry, patient/version switching, saved-but-refresh-failed and React setup/cleanup binding. Add the missing package script/gate. Preserve the independently passing R6 recording-order/correction/replay/reload tests.

4. Implement the selected target capability matrix and centralized eligibility/privacy. PI extraction is retained; identity review becomes grant-only; high-impact commands require an assigned PI's explicit grant, even through weaker aliases. Extend self-grant restrictions to clinical writes without requiring two-person approval for every data entry. Enable LAST_EDITOR maker-checker for new pilot approvals, with separate local actors. Version/document owner-delegated policy selection without inventing a clinical signatory or granting production users rights.

5. Map all live upload/consume wrappers centrally. Preserve scoped PI extraction/draft consume and user-owned pre-trial drafts; require authorized target attachment, trusted draft ownership and P2 identity/quarantine rechecks. Do not grant general authoring/approval/adoption or mutate approved schedules. Exercise actual persisted assignment/invitation providers, not only FakeOperationalAccess.

6. Enforce explicit dependency semantics. TRIGGER/PRECONDITION alone do not demand an actual; explicit actual mode demands the selected occurrence's own correct actual type. New/edited drafts must replace ambiguous CURRENT with FIRST/LAST/NUMBER or supported reviewed mapping. Preserve approved legacy semantics/dates with explicit compatibility; do not invent cycle mappings.

7. Build the Mongo-native pilot workflow using the existing repository-port bridge and services. Supply coherent approved version, enrolment, anchors/conditions/state/facts, displayed calendar/evaluation, history, preview/confirm and reload. One Mongo transaction owns clinical command effects. Existing SQL/legacy cohorts stay unchanged and their mixed-plane commands stay disabled. Do not confuse the live SQL bridge or legacy-engine parity with this plane integration.

8. Implement the ClinicalTrials.gov v2 registry adapter behind the existing port. Public GET access was verified; do not retain a blanket private-credential blocker. Use bounded requests/cache/retry and honest outage states; no patient data. Add genuine-document tests using `docs/evidence/protocol-identity/NCT02088554/Prot_003.pdf` plus its manifest. Label a deliberately misnamed copy as constructed from genuine content; acquire authentic amendment/version evidence where available, never fabricated text presented as real.

9. Add stable scoped cursor pagination (50 default, 200 max), explicit continuation and compatible API/UI callers. No silent 500 cutoff or unauthorized totals. Complete real provider tests, mounted stop-policy tests, scoped typechecks and local browser walkthrough with synthetic pilot users/data.

10. Run affected regressions, then the applicable curated gate in appropriate separate processes. Use explicit local replica-set Mongo and isolated databases, with required real-store variants failing rather than silently skipping. Cover policy/manifest, identity, history/occurrence, dependency, commands, review/maker-checker, parity and journey as affected. Run actual frontend controller/component tests and the walkthrough. Do not copy old totals or sum overlaps as unique coverage. Preserve SQL regressions; PostgreSQL locking proof is needed only for supported/changed SQL behavior relying on it.

## Handoff

Update original closure tables, integration implementation status and affected P1-P5 plans consistently. Remove the now-resolved owner-choice blockers, retaining genuine engineering/environment/corpus gaps with exact PDF-row impact. Domain-only criteria do not all require browser proof; production UI criteria do.

Demonstrate normal navigation: upload/identity, recurrence/track review, two-person approval, native enrolment/display, treatment delay with fixed imaging, separate actual dates, inside/outside/unknown-window appointments, supported pause/resume/rebase/stop/cancel/reversal, lost-response retry, stale refusal, privacy/denial and persisted reload from the committed plane evaluation.

Continue independent work; do not stop at architecture selection or request a live trial to begin. If real historical documents/amendments or an environment remain unavailable, report actual attempts, missing evidence and acceptance impact. Do not fabricate a pass or universal protocol support.

This authorizes the selected isolated pilot and reversible engineering work. It does not authorize live migration/mirroring, real-user grant rollout, production authority/read-mode changes, commit/push or deployment.
