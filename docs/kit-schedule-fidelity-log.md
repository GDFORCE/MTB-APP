# Kit schedule fidelity: running log

Started 10 November 2026 from [the fidelity prompt](kit-schedule-fidelity-prompt-2026-11-10.md). Newest entry last.
A later session can continue from the last entry; the tools and where results live are listed first.

## Tools (all added in this work)

| What | Where |
|---|---|
| Run the pipeline on one file, keeping every prompt, reply and clean-up pass | `backend/scripts/kit_bench.py` (`--replay` re-runs on recorded replies, no credits) |
| Compare a model's schedules with an expected answer (words, not ids) | `backend/scripts/kit_bench_compare.py` |
| Run or re-score every case, print the totals | `backend/scripts/kit_bench_all.py <run> [--run] [--replay <run>] [--hand] [--details]` |
| Schedule engine on the command line | `frontend/scripts/kit-schedule-cli.ts` |
| What clean-up changed (before/after diff) | `backend/app/kit/ledger.py` |
| BP11-301 expected answer in bench form | `docs/protocol-benchmarks/bp11-301-v2.1/kit-expected.json` (from `build_kit_expected.py`) |
| Constructed fixtures (PDF + expected + hand-written model) | `backend/tests/kit_bench/fixtures/`, built into `docs/protocol-benchmarks/synthetic/` by `python -m tests.kit_bench.build` |

Run results are kept outside the repo in `D:/mtb_tmp/kit-bench/<case>/<run>/` (`KIT_BENCH_DIR`).
Each pipeline run of all seven cases takes about 15 minutes and roughly 25 model requests.

## Entry 1: baseline measurement (run `run-01-baseline`, code as of commit `b711a8c`)

Seven cases: the real BP11-301 benchmark and six constructed fixtures (SYN-01 to SYN-06), 38 patient
scenarios, 1,176 checks (visit dates, windows, visit mode, each assessment present or absent, visits that
must be gone, points that must be raised with the reviewer).

| Case | Passed | Wrong or missing |
|---|---|---|
| BP11-301 (real) | 422 / 437 | 15 |
| SYN-01 parallel, ruled table | 168 / 173 | 5 |
| SYN-02 oncology cycles | 141 / 141 | 0 |
| SYN-03 vaccine, prose only | 53 / 61 | 8 |
| SYN-04 crossover, borderless table | 99 / 101 | 2 |
| SYN-05 long two-part, tick-mark table | 230 / 234 | 4 |
| SYN-06 beyond the vocabulary | 16 / 29 | 13 |
| **Total** | **1,129 / 1,176** | **47** |

What this shows:

1. **The plain visit grid is right.** Every nominal visit date, every stated window and all 312 cells of
   the BP11-301 assessment table came out correct. The same holds for the fixtures' tables, including a
   borderless one and one whose tick marks are a symbol font.
2. **What goes wrong is conditional detail and "if this happens" behaviour**, and where each was lost can
   now be read from the recorded stages:

| Cause | Count | Examples |
|---|---|---|
| The vocabulary could not hold it, so the AI wrote it as text the engine ignores or forced it into another field | 15 | Sub-study-only PK time points shown to everyone (time points had no condition); fed/fasted instructions by sequence written as notes for everyone; "no window stated" stored as ±0; an injection that stops while its visit continues; calendar months as 30 days |
| Open point only in the free-text notes, or not raised at all (no question, no passage, no page) | 17 | 9 of the 10 BP11-301 source conflicts were one line among 30 notes; a review question the AI did write was deleted by clean-up because its answers carried no change the app could apply |
| The AI understood but did not use a field that exists | 4 | Efficacy assessments still listed after Week 12 for a patient who stopped before it (stated in a note) |
| Clean-up removed it | 3 (plus 4 rules in SYN-05) | Rules written as `{"timing": {...}, "max_days": -1}` (the shape the prompt itself shows) were dropped because clean-up reads the limits inside `timing`; the events pass then rebuilt them differently |
| The engine calculated it wrong from a reasonable model | 8 | Visits after a date that has a stated day *and* a tolerance ("Day 28, up to 7 days later") lost all planned dates; the last-dose date an event set was overwritten by the last visit; a visit conditional on `not event` was never scheduled; the Day 8 of a cycle that was never given stayed on the schedule |

So the hypothesis in the prompt holds in part: meaning is lost at the vocabulary boundary and in clean-up,
and it was invisible. It is not true that schedules are wrong across the board.

Engine-only run of the hand-written models (no AI) found four of the engine faults above before any
pipeline run: that level is worth keeping as a regression test.

## Entry 2: backlog recheck and generated-code proposal (14 November 2026)

Checked the implementation at commit `766d933`. Found two recorded runs that Entry 1 did not document:
`run-02-replay` and `run-03-prompts`. Re-scored the saved `run-03-prompts` models against the current
engine and expected files, without new LLM calls: **1,162 / 1,196 scored checks passed** across BP11
and six synthetic fixtures. There were 34 unsuccessful checks and one additional answer-mapping
finding. Counts are not a clinical accuracy estimate: matching/display checks need adjudication, and
the number of scored checks changes when a missing visit skips its downstream assessment checks.

The **754 / 769** result from the GitHub-update verification is a separate, hand-model-only experiment;
its 15 findings are not the full-pipeline backlog. Two of those findings reflect a missing
`window_stated: false` declaration in the hand-written fixture, which the current engine supports.

The latest saved models contain 83 open points. SYN-06 explicitly describes rules it understands but
cannot execute exactly (calendar months, business days, weekday selection, next-visit laboratory
carryover). Other failures involve supported actions, record inputs, source questions and benchmark
blind spots. The crossover's breakfast instructions are present, for example, but the comparator only
looks for assessments/timepoints when checking that expectation.

Saved the evidence, prioritized backlog and a proposed experiment in
[Protocol-generated scheduling code assessment](protocol-generated-code-assessment-2026-11-14.md).
This evaluates the owner's idea of allowing the LLM to write protocol-specific computations. It is
not an adopted architecture or a replacement implementation prompt. No runtime or app behavior was
changed. Detailed local re-score reports are under `.tmp/protocol-code-assessment/`.
