# Claude Code continuation: P7 graph-wide validation, then P8 corpus and release gates

Prepared 16 October 2026, after the P6 slice ([completion record](p6-completion-record.md)). This is the next focused slice of the [P6-P14 master](claude-code-p6-p14-remaining-pdf-prompt.md); it does not replace it. Saving this prompt does not authorise deployment, clinical policy changes, real-patient migration, commits or pushes.

---

Continue from the current tree. Read `AGENTS.md`, `docs/pdf-remaining-implementation-plan.md` (resume checkpoint first), `docs/pdf-requirements-acceptance-matrix.md`, `docs/p6-completion-record.md` and the P7 and P8 rows of the master prompt. Run `git status --short`, `git log -5 --oneline` and inspect existing diffs for every file you touch; the worktree carries a large amount of uncommitted P1-P6 work that must be preserved.

## 1. First, inventory (the P7-P14 rows are NOT_ASSESSED)

For each P7 and P8 row write into `docs/pdf-remaining-implementation-plan.md`: the exact PDF acceptance, the existing source and tests that already cover part of it, the missing behaviour, the dependencies and the intended API/UI/store integration. Do not rebuild what exists (`domain/schedule/validator.py`, `constraint_validation.py`, `dependency.py`, `tracks.py`, `app/extraction/*`, the review view, the P6 pair/mapping/decision stores).

## 2. P7 - graph-wide clinical safety, at every boundary

Cover the supported graphs - event, activity, anchor, process, track, condition, mapping - with identity, duplicate, ownership/reference, acyclicity, reachability and orphan checks that return stable issue codes with exact paths. A legitimate conditional root is not an orphan. A malformed graph fails APPROVAL, before any patient executes it.

Apply the same validation at the boundaries P6 added, not only at schedule approval: an event mapping set (coverage, ownership, duplicate/overlapping sources, split/merge rules that name only listed visits), a change set, and a decision's adoption point against the patient's CURRENT version. Unsupported constructs stay visible and block; no date is invented.

## 3. P8 - corpus, field-level quality and release gates

Build the schema, corpus, scoring and scenario harness with honestly labelled deterministic engineering fixtures (the synthetic pilot and the P6 constructed amendment pair are fixtures, not a signed corpus). An unsafe invented date fails release; an empty, unsigned or zero-denominator corpus must not pass as a successful gate. Tie thresholds and sign-off to the P13 release manifests as the master specifies, and record the exact remaining external gates.

## 4. Carry-forward from P6 (do not reopen; extend where a row needs it)

* P6 limits kept visible: activity-level mapping rules, carry of patient-specific commands across versions, re-adoption onto a version with history, many-to-one reversal without a reviewed transfer policy.
* The deferred-adoption worker (`app/api/uctsm_amendment_worker.py`, `UCTSM_AMENDMENT_WORKER_INTERVAL_SEC`) is in-process; P13 owns a durable job queue.
* Mongo test runs: explicit local replica set (`UCTSM_REQUIRE_MONGO=1`, `UCTSM_TEST_MONGO_URL`, `MONGO_URL`, `DB_NAME`, `UCTSM_MONGO_*`), `TEMP`/`TMP` on `D:`. A crashed mongod invalidates the affected run - rerun it; do not relabel it. Do not run a Metro export alongside the Mongo suites on this machine (it exhausted memory and crashed mongod once).

## 5. Verification and completion

Focused defect tests first, then memory + real-Mongo contracts, isolated API modules, the production relationship provider where a route is added, scoped TypeScript for touched frontend files, and a browser check for any reachable screen you change. Update the matrix and the plan, add a completion record for the slice, keep this prompt and the index current, and continue with the next independent work under the master. No commit, push, live patient migration, read-mode/authority flip or deployment.
