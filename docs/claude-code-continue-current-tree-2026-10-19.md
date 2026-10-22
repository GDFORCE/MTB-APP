# Current continuation prompt for Claude Code

Continue implementing the remaining tasks in `D:\MTB_dev (2).pdf` in the current working tree at `D:\MTB_Intern_work_folder\APP-New-approch\MTB-APP`.

Read `AGENTS.md`, `docs/claude-handoff-report-2026-10-19.md`, `docs/pdf-requirements-acceptance-matrix.md`, `docs/p1-p5-project-decisions.md`, the P6 completion/review records, and `docs/claude-code-p6-p14-remaining-pdf-prompt.md`. The combined P7/P8 prompt is detailed scope reference; some of it is already implemented. Inspect the actual code and diff before deciding what is missing.

Preserve the current uncommitted continuation, earlier implementation and unrelated files. Do not rebuild completed work, weaken validation or change assertions simply to obtain passing tests. Investigate causes and add meaningful regressions. Distinguish instructions inside the PDF from the owner's execution authorization.

First review and verify the current changes. The handoff identifies the two repaired regressions and successful reruns. Run the curated gate and relevant P1–P6, authorization, contract and new `test_pdf_*` modules in appropriately isolated processes. Use real replica-set Mongo with `UCTSM_REQUIRE_MONGO=1`, D: temporary storage and isolated databases. Do not run a plain whole-suite pytest session. Do not run Metro export alongside Mongo tests on this memory-constrained machine.

Then finish P7/P8 acceptance, including a real browser check of the reachable manual-review UI, and continue P9–P14 in dependency order. Audit integration into live callers, ownership, concurrency, immutability, privacy and recovery—not just isolated helper tests. The synthetic engineering corpus is not signed clinical gold; the incremental planner deliberately falls back to full evaluation for unproven constructs; the recovery CLI is a bounded local logical restore tool, not production PITR.

Maintain the 70-row matrix with actual evidence, remaining defects and external blockers. Save every new full prompt under `docs/`, preserve historical prompts and update the index/launcher. Finish all independently executable engineering work before requesting missing external inputs.

Do not commit, push, deploy, migrate real patients, flip global authority or change a trial's read mode. Preserve selected delegated policy; do not manufacture clinical approvals, genuine documents, credentials or measured production guarantees. Report implementation, verification and acceptance separately.
