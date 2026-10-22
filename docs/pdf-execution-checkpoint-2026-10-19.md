# PDF implementation checkpoint — 19 October 2026

Source: `D:\MTB_dev (2).pdf`, read again on 19 October. It contains **70 numbered tasks: P1–P14, five per section**. This checkpoint records implementation and verification separately. It is not a completion certificate or clinical sign-off.

## Current position

| Section | Implementation position | What remains |
|---|---|---|
| P1–P5 (25 tasks) | Existing implementation, selected delegated policy and native Mongo pilot preserved. | Close the exact outstanding UI, authentic-document and operational acceptance gaps in the closure report. The synthetic pilot does not establish support for every legacy patient. |
| P6 (5 tasks) | End-to-end implementation already exists. F1–F4 repairs are present; all 34 repair regressions now pass on memory and real Mongo. | Requalify the complete P6 suite after the new shared-validator changes. Preserve unsupported transfer cases as explicit blockers. Authentic amendment-pair evidence remains unavailable. |
| P7 (5 tasks) | In progress: graph/source checks and an independently approved manual-only workflow, with service/API and immutable review-decision verification. | Reachable manual-review UI, browser evidence, full evidence/conflict workflow and cross-feature coverage. Audit persistence paths that discard dangling dependencies before validation. |
| P8 (5 tasks) | In progress: versioned gold schema, cryptographic review verification, field scorer, deterministic evaluator scenarios, explicit engineering/clinical modes and CI workflow. | Real licensed/de-identified, independently reviewed gold corpus; full command/adoption scenario execution; approved thresholds, trends and release integration. Engineering fixtures are synthetic and are not extraction-accuracy evidence. |
| P9 (5 tasks) | No completion claim; existing evaluator/storage foundations need assessment. | Incremental/full parity, compiled artifacts, bounded indexed context queries, measured scale and archive/restore policy. |
| P10 (5 tasks) | A committed-snapshot calendar service and role-aware ETags are implemented and tested. | Complete operational/patient UI, consent-aware reminders and consistent reports/exports. Snapshot work does not close the section. |
| P11 (5 tasks) | Existing bridge/parity/cutover foundations remain; full PDF acceptance has not been established. | Consumer inventory, resumable migration/reconciliation, synthetic rehearsal, all-writer authority fencing and rollback evidence. No real migration or authority flip was performed. |
| P12 (5 tasks) | Existing domain/application/port separation remains; architecture tests pass in focused checks. | Assess every row, complete service extraction and characterize/reroute live consumers while preserving legacy features. |
| P13 (5 tasks) | A transactional outbox claim/ack foundation now has fenced leases, bounded retries and terminal dead state, tested on memory and real Mongo. | Production consumer/inbox, authorized audited dead-letter replay, observability/alerts, backup/restore drills and release manifests. No external notification was sent. |
| P14 (5 tasks) | Existing Mongo contracts/migrations/transaction foundations remain; an additive outbox migration was added. | Full row-level schema/index/security assessment, portable export/import and restore proof. Local replica-set tests are not production security or disaster-recovery evidence. |

**Count interpretation:** P1–P6 account for **30/70 tasks with established implementations**, not 30 fully accepted tasks. P7/P8 are actively being completed and selected P10/P13 foundations are built. Do not publish an overall completion percentage from these mixed states. P9–P14 account for another 30 tasks whose sections remain open, even where prior foundations exist.

## Evidence from this execution

All Mongo runs below used a real local replica set, `UCTSM_REQUIRE_MONGO=1` and isolated test databases. Test totals overlap; do not add them into a unique coverage count.

| Check | Result | Qualification |
|---|---|---|
| P6 repair module | 34 passed | Fixed test-clock injection so the frozen fixture date does not conflict with the production current-date default. |
| Curated gate plus quality, graph and P6 repair modules, each file in a separate process | 428 passed across 36 files | Two historical deleted files explicitly reported as `DOCUMENTED_DELETED`. This run preceded the later manual-rule/shared review-view changes; it is not a final gate for the current tree. |
| Outbox delivery contracts plus Mongo platform | 21 passed | Includes lease expiry/reclaim, stale acknowledgment, retry/dead state and tenant isolation. |
| P6 API plus architecture checks after snapshot changes | 22 passed | Before the later manual-rule changes. |
| Latest manual-rule, snapshot and authorization-manifest checks | 22 passed | Memory + real Mongo; scope, maker-checker, forged metadata, stale revisions, role-aware cache and conditional-request authorization. |
| Quality/graph/architecture checks in fresh minimal-dependency environment | 23 passed | Local fresh environment; the GitHub workflow has not run remotely. |
| Engineering corpus CLI | PASS in engineering mode | Ten synthetic construct cases; explicitly `clinical_release_qualified=false`. No claim of signed clinical quality. |

Raw curated-gate output remains at `D:\mtb_tmp\pdf-gate-2026-10-19\report.json` and the sibling per-file logs. This is a local scratch evidence path, not a portable committed artifact.

## Immediate engineering continuation

1. Finish and exercise the manual-rule review UI; the backend currently has three declared routes. The authorization manifest now covers 79 routes. Manual handling must never fabricate an automatic protocol target.
2. Reconcile persisted validator-version metadata with the new validator and audit malformed-reference persistence. Do not weaken validation to make older fixtures pass.
3. Finish P7 evidence/conflict and cross-feature acceptance; triage the wider integration failures recorded in the P6 completion record with genuine red/green evidence.
4. Finish the P8 executable scenario boundary and gate documentation; keep synthetic engineering coverage separate from signed clinical gold. Recheck corpus regeneration determinism.
5. Re-run affected P6/P1–P5 modules and the curated gate after the shared changes, then continue the remaining PDF rows in dependency order.

## Boundaries and remaining external inputs

- Prior P6 browser-journey evidence exists in `docs/evidence/p6/`; it does not verify the newly built P7/P8 changes. No new browser/device walkthrough was run in this execution.
- Genuine PERIGON protocol evidence exists, but there is still no authentic mislabelled upload/amendment pair or signed representative clinical gold corpus. Do not manufacture sign-off or change synthetic labels.
- The latest execution changes are **uncommitted**. Earlier implementation is committed in `e85a99e`; the old statements that all P1–P6 work was uncommitted are historical.
- No push, deployment, real-patient migration, global-authority flag or per-trial read-mode change was performed. Preserve the six pre-existing scratch/log files.
- Mongo was restarted locally before real-store checks. A passing local transaction test is not proof of hosted security, backup, PostgreSQL locking or production performance.

Related records: [70-row acceptance matrix](pdf-requirements-acceptance-matrix.md), [P1–P5 closure](p1-p5-closure-report.md), [P6 record](p6-completion-record.md), [P6 independent findings](p6-end-to-end-independent-review.md), [current detailed continuation](claude-code-p7-p8-after-p6-prompt.md), [remaining-PDF master](claude-code-p6-p14-remaining-pdf-prompt.md).

## Second slice (19 October, later): what was added and verified

Added, each with memory and real-Mongo tests and listed in the curated gate (`MTB_IMPLEMENTATION_HANDOFF.txt` section E): P9 context indexes/explain tests, encrypted archive with holds and write-volume test; P7 temporal matrix, evidence-backed conflict resolution (plane routes + panel) and revalidation tests; P8 transactional sequence scenarios; P10 patient allowlist calendar, reminder sync with explicit preferences, reconciled reports; P11 inventory, resumable migration job, pilot cohort cutover/rollback and legacy write guard; P12 ratchet and boundary tests; P13 observability, alerts/routes/runbooks as code, inbox and dead-letter replay, drill RTO/RPO, release manifest and canary rules; P14 collection catalog, security posture and roles, transaction/concurrency tests, command-ledger contract. Migrations 0014-0019 were added; the authorization manifest now declares 84 routes.

| Check | Result | Qualification |
|---|---|---|
| Curated gate via `backend/scripts/check_pdf_gate.py`, one process per file, local replica set, `UCTSM_REQUIRE_MONGO=1` | PASS, complete: 66 files, 713 tests passed, 2 historical deleted files reported `DOCUMENTED_DELETED` (report in `D:\mtb_tmp2\gate-final\`, scratch, not committed) | Started before four later test files existed |
| Four later files (server ratchet, boundary writes, temporal matrix, write volume) | 2 + 3 + 17 + 1 passed | Run individually after the gate |
| Frontend `npm run test:uctsm` | 200 passed | Includes the new conflict tests |
| `npm run typecheck:uctsm-amendments` | clean | Scoped config only |
| Fail-point test of an unknown transaction commit | passed on a throwaway mongod (port 27018, `enableTestCommands`) | Skips with a reason on the normal local mongod |
| Real CLI snapshot + isolated restore drill | reconciled; RTO/RPO fields reported `MET` against test targets | Tiny dataset, test targets only |

### Findings worth the owner's attention

- A test failed about one run in four because unordered sets made a gold record's source-manifest hash change after a reload; fixed (sorted serialization) with a regression test.
- Parity refuses to pair repeated visit names (`AMBIGUOUS_MATCH`), so a recurring same-name schedule cannot be reconciled or promoted until legacy rows carry a distinguishing key.
- A sub-day offset from a date-only anchor floors to the date at `EXACT` precision (midnight assumed) with no validator finding; pinned by a test, decision needed.
- A contract test showed the in-memory store accepted any command type while Mongo validated the set; the record now validates it.
- Evaluation, preview, commit and adoption already revalidate the approved schedule through the verified compiled artifact; a stricter validator is refused at each boundary (now tested).

### Still not established

Reviewed clinical gold corpus and signatures, authentic amendment/mislabelled documents, hosted backup/PITR and production-sized drills, a real canary/pager, browser and accessibility walkthroughs of the new UI, a delivery consumer for reminders/outbox, owner approvals (legacy inventory, thresholds, RTO/RPO, patient display policy), and remote CI. Nothing was committed, pushed, deployed or migrated; no authority flag or read mode was changed.

