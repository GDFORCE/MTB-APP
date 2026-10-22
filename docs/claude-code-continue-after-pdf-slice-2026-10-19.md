# Continuation prompt: remaining PDF work after the 19 October slice

Saved 19 October 2026. This supersedes [claude-code-continue-current-tree-2026-10-19.md](claude-code-continue-current-tree-2026-10-19.md) as the starting point; that file and the earlier prompts are preserved for scope only. Saving this prompt does not authorize deployment, clinical policy changes or data migration.

```text
Read AGENTS.md, docs/claude-code-continue-after-pdf-slice-2026-10-19.md and
docs/pdf-requirements-acceptance-matrix.md, then continue the PDF work from the actual
uncommitted tree. Check git status yourself; nothing was committed.
```

## State at hand-off

All rows of the 70-task matrix carry evidence; none is `VERIFIED_ACCEPTANCE`. The 19 October slice added (each with memory and real-Mongo tests, listed in `MTB_IMPLEMENTATION_HANDOFF.txt` section E for the curated gate):

- P9: context-read indexes with explain tests (0014), encrypted hot/cold archive with holds (0015), write-volume/idempotency test.
- P7: temporal boundary matrix, evidence-backed conflict resolution (service, plane routes, `ConflictPanel`), revalidation at preview/commit/adoption.
- P8: transactional sequence scenarios in the quality gate; a hash-determinism bug (unordered sets) fixed.
- P10: patient allowlist calendar, reminder synchronisation with explicit preferences (0019), schedule reports reconciled with the calendar.
- P11: legacy consumer inventory, resumable legacy-patient migration job (0017), pilot cohort cutover state and rollback (0018), per-patient legacy write guard, runbook.
- P12: server.py ratchet, plane-boundary write test, assessment (`docs/p12-modularization-assessment.md`).
- P13: correlated redacted telemetry and health, alerts/routes/runbooks as code, inbox and audited dead-letter replay (0016), restore-drill RTO/RPO evidence, release manifest and canary rules.
- P14: collection catalog, connection-security posture and least-privilege roles, transaction/concurrency/unknown-commit tests, command-ledger contract.

## What remains, in order

1. **Run the gate** (`backend/scripts/check_pdf_gate.py`, one process per file, `UCTSM_REQUIRE_MONGO=1`, local replica set) after any change, plus `npm run test:uctsm` and `npm run typecheck:uctsm-amendments`. Report failures; do not drop files.
2. **Decisions the code deliberately does not take** (need a named owner): sub-day offsets from a date-only anchor (today floored to the date at EXACT precision, pinned by a test); patient display policy for overrides (DEC-15); who owns each legacy-inventory row; approved alert thresholds, RTO/RPO, canary numbers.
3. **Parity blocker for cutover:** the parity gate refuses to pair repeated visit names (`AMBIGUOUS_MATCH`), so a schedule with recurring same-name visits cannot be reconciled or promoted. Propose a distinguishing key with the legacy owner; do not weaken the gate.
4. **UI and browser evidence:** the conflict panel, report, reminder preferences and patient calendar have no browser or accessibility walkthrough; staff calendars still read legacy data and the three protected screens need the handoff review (C3/D-4) first.
5. **Product surface for operations:** HTTP/UI for dead-letter replay, cohort cutover and reminder delivery (an outbox consumer to the notification store or a provider); a worker that calls `publish_queue_gauges` and `ReminderSyncService.sync`.
6. **Evidence nobody here can create:** a reviewed licensed/de-identified gold corpus with signatures, authentic amendment and mislabelled-upload pairs, hosted backup/PITR and a drill on production-sized data, a real canary, remote CI run. Do not fabricate or relabel synthetic fixtures.

## Boundaries

No push, deployment, real-patient migration, `UCTSM_AUTHORITATIVE`, `DEV_OTP_MODE` or per-trial read-mode change. Preserve the six pre-existing scratch files in `backend/`. Do not migrate `sponsor/visit-schedule.tsx`, `clinical/schedule-review.tsx` or `clinical/trial-summary.tsx` without the handoff review.
