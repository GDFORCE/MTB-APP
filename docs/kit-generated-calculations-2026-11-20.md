# Generated calculations: the analysis writes the missing logic

20 November 2026. Before this change, a protocol rule that no kit field and no named handler could hold was kept as a blocker until a developer wrote a handler. Now the analysis can write that calculation itself. The app checks it, tests it, shows it for review, versions it and runs it. No protocol-specific code is added.

## How it works

1. **The analysis writes the calculation.** The extraction prompt (`app/kit/patterns.py`, `GENERATED_PROMPT`) asks for this whenever a clearly stated timing rule fits neither the fields nor a named handler. The analysis writes a pattern rule with a new UPPER_SNAKE name plus:
   - `program`: the logic as an expression in the **calc v1** language.
   - `output.kind`: either `occurrences` (the rule's one visit gets exactly the occurrences the program returns) or `date` (each occurrence of the rule's visits is re-dated).
   - `inputs`: every fact the program reads.
   - `tests`: at least two cases with exact expected results, one of them a case where the condition does not hold.

   Ambiguous wording gets an open point and no program, as before.
2. **The language is data, not code.** `backend/app/kit/calc.py` and `frontend/src/features/kit/calc.ts` are two copies of one fixed interpreter.
   - What a program can use: arithmetic, dates, comparisons, `if`/`and`/`or`, `map`/`filter`/`let`, records.
   - What a program can read: each visit's occurrences (`planned`, `actual`, `given`, `status`, window), protocol dates, answers, event dates and today.
   - What it cannot do: loop without end, call out, read files or run instructions. Each run has a step budget and size limits.
   - Both copies agree on the shared cases in `backend/tests/fixtures/calc_vectors.json`, and both test suites run those cases.
   - Since nothing is executed as code, no isolated Python runner was needed.
3. **The app checks it before it can run** (`calc.generated_status`, run on every clean-up). The program must:
   - be well formed and within the size limits;
   - read only facts it declares and that exist in the model;
   - not read the visit it produces;
   - produce the declared output shape;
   - pass every one of its own tests.

   Any failure makes the rule a **blocker** with the reason (for example "its own test … fails: expected …, got …"). As for a missing handler, a blocker means no approval, no enrolment, and the visit is never dated from its nominal fields.
4. **The study team reviews it in the same workflow.** The pattern block's summary shows:
   - the inputs and output;
   - the logic in readable form;
   - each test's result;
   - the content hash.

   An open point of kind `generated` must be answered before the workflow can be approved. The sample calendar runs the program, including the new "dose not given" toggle. The reviewer can edit the program in the block editor, and the checks run again.
5. **The approved version is pinned.** The rule stores the calc version and the program's SHA-256, and the workflow digest covers the program. Patients stay on the version they were enrolled on. An amendment that changes the program is previewed and adopted like any other change.

**New engine fact: was the dose given?** Staff can record that a dose was withheld at a visit that took place, with the reason (`participant.withheld`, validated by `clean_participant`, set from the visit sheet). Generated calculations read `given = false` for a withheld dose, and also for a dose a check skipped. `given` is `true` once the visit is recorded, and `null` before that.

## The acceptance test

`backend/tests/test_kit_generated_calc.py::test_upload_generates_the_missing_calculation_and_dates_the_samples`.

**Input:** a synthetic protocol (`docs/protocol-benchmarks/synthetic/syn07-generated-calculation/protocol.txt`) states "A pharmacokinetic blood sample is taken 7 days (±1 day) after each injection that is given; no sample is taken after a withheld injection". No handler exists for this rule.

**Path:** the text is uploaded through `/api/kit/analyze`. The model provider is a controlled stand-in that returns what an analysis following the prompt returns, including the program and its two tests. Everything after that runs on the app's own code.

| Step | Result |
| --- | --- |
| Clean-up | runs both tests; both pass; the rule is not blocked |
| Workflow preview | the only problem left is the generated-calculation review point |
| Approval without confirmation | refused (422) |
| Approval after confirmation | accepted; the stored rule carries the program and its hash |
| Enrolment, then injections recorded on 5 Jan, 3 Feb (late) and 2 Mar 2026, the last withheld (IOP 32 mm Hg) | samples on 12 Jan (window 11–13 Jan) and 10 Feb (from the recorded, late date); none after the withheld dose; samples 4–6 wait for "7 days after injection N, once given"; the participant's calendar shows only dated samples |
| Amendment to 14 days | the enrolled patient stays at 7 days on version 1; the version 2 preview gives 17 Feb |

A second upload, whose program adds 6 days while its tests expect 7, is blocked with the failing test named. Its approval is refused and enrolment returns 409.

**Engine checks** (`frontend/tests/kit-generated.test.ts`):
- a dose skipped by its check also counts as not given;
- rules and ending events still stop generated occurrences, and recorded ones stay;
- a blocked calculation never dates its visit;
- a generated date calculation ("first Wednesday at least 10 days after each injection") re-dates an existing repeating visit;
- results are deterministic.

**Try it without a provider:** start `backend/start-backend-offline.cmd`, create a trial with protocol ID `SYN-GEN-PK`, and upload `protocol.txt`.

## Results

- `cd frontend && npm run test:kit`: 185 passed, 0 failed (includes `kit-generated.test.ts`, 18).
- `cd backend && python -m pytest -q -p no:cacheprovider` over the 16 `tests/test_kit_*.py` files, on the local replica set: 229 passed. This includes `test_kit_generated_calc.py` (24), the real-PDF offline RIVAL run (4) and the multi-site study-event run (7).
- `python scripts/kit_synthetic_fixtures.py --check`: current. Scoped `tsc` over the touched frontend files: exit 0.

## Limits and what is not verified

- **Generation by a live model is untested.** No provider credits were available. Every test uses controlled provider responses or the offline answers. Everything the app does with a generated program is tested; how reliably a live model writes a valid program and correct tests is not.
- **What a program can read:** visit occurrences (dates, given, status, window), protocol dates, answers, logged event dates and today. Findings, measured values, check answers, time of day and the visits an event adds are not exposed yet. Each would be engine work: add it to `buildFacts` and to the language description.
- **What a program can do:** produce the occurrences of one non-repeating visit, or re-date the occurrences of existing visits. Stopping visits, changing assessments or adding events still goes through the existing rule blocks.
- **Approval rests on the reviewer.** A calculation that passes its own tests can still encode a wrong reading of the protocol, and the tests come from the same analysis as the program. That is why approval requires the study team's confirmation.
