# Protocol-generated scheduling code: backlog and architecture assessment

Assessed 14 November 2026 against commit `766d933` on `kit/protocol-rules`.
This is an assessment and proposed experiment, not an adopted architecture or an implementation prompt.

## Finding

The fixed kit vocabulary is a demonstrated limitation. The LLM sometimes describes the correct rule
and explicitly explains why the model cannot execute it, while the patient schedule uses an
approximation. Letting the LLM write protocol-specific scheduling functions is a reasonable hypothesis
to test. It addresses expressiveness; it does not establish that the protocol was read correctly or
that the generated program implements it correctly.

The user-facing concern is broader than missing keywords. A requirement must survive reading,
representation, validation, patient data collection, execution and display. An error at any of these
boundaries can produce the same wrong-looking schedule.

## Evidence checked

- Repository state and implementation at `766d933`.
- `backend/app/kit/analyze.py`, `normalize.py`, `prompts.py`, `ledger.py` and `backend/kit_routes.py`.
- `frontend/src/features/kit/schedule.ts`, model types, review UI and benchmark comparator.
- The running log and schema inventory, interpreted as historical documents rather than current truth.
- Recorded analysis artifacts under `D:/mtb_tmp/kit-bench/`, including the previously unlogged
  `run-02-replay` and `run-03-prompts` runs.
- Re-scored all seven `run-03-prompts` saved models against the current engine and expected files.
  This performed no new LLM requests. Reports are local under `.tmp/protocol-code-assessment/`.

### Current re-score of saved analysis models

| Case | Passed / scored checks | Open points on saved model |
|---|---:|---:|
| BP11-301, real-protocol benchmark | 424 / 436 | 30 |
| SYN-01, parallel ruled table | 172 / 173 | 3 |
| SYN-02, oncology cycles | 141 / 141 | 14 |
| SYN-03, vaccine prose | 80 / 82 | 3 |
| SYN-04, crossover borderless table | 99 / 101 | 5 |
| SYN-05, long multipart protocol | 226 / 234 | 14 |
| SYN-06, rules beyond the vocabulary | 20 / 29 | 14 |
| **Total** | **1,162 / 1,196** | **83** |

These are benchmark checks, not a clinical accuracy percentage. There are 34 unsuccessful scored
checks and one additional input-answer matching finding. Visit-name matching, answer mapping and
incomplete comparison of displayed instructions can produce findings that require manual adjudication.
Some expectations are themselves unresolved source questions. The count of checks changes when a
missing/wrong visit prevents downstream assessment checks, so the old baseline and this run are not
strictly comparable totals.

The historical log reports 1,129 / 1,176 for the baseline. It omitted the two later runs. The 15 findings
reported during the GitHub update came from a different experiment: six hand-written models, without
LLM extraction, scoring 754 / 769. Those 15 should not be treated as the current full-pipeline backlog.
For example, two hand-model failures arise because that fixture does not declare `window_stated: false`,
although the current engine supports the distinction and the latest extracted SYN-01 model has it.

## Current backlog, ordered for this decision

| Priority | Problem | Evidence and consequence |
|---|---|---|
| 1 | Rules that the representation cannot compute exactly | SYN-06 explicitly reports calendar-month arithmetic, business-day deadlines, a first-Monday rule and next-visit-only laboratory carryover as unsupported/approximated. Wrong dates and missing visits remain. |
| 1 | Warnings can coexist with plausible wrong planned dates | The same model has 14 open points but still shows 30-day monthly repeats and a Day-210 final visit. Raising a warning does not make those dates correct. |
| 1 | Existing actions are fragile when targets are text | Procedure stopping in `schedule.ts` compares normalized names for equality. A generated stop rule and procedure with different names do not necessarily refer to the same executable target. Stable identities are needed regardless of representation. |
| 1 | Conditional paths and recorded-date dependencies remain wrong in extracted models | Current findings include follow-up after a late vaccination, follow-up after the last dose, continued injections after treatment stop and extension visits that remain after discontinuation. Several of these have mechanisms in the current schema; a new programming language alone does not repair the extracted logic. |
| 2 | Missing inputs and patient-record contracts | New computations can require holiday jurisdiction, actual administration timestamps, explicit missed occurrences and separately recorded assessments. The LLM must declare required inputs, and forms/storage must collect them. Missing inputs must remain unknown. |
| 2 | Source ambiguities and omissions | BP11 findings include unresolved source questions and a missing early-termination path. CRD/09 also contains conflicting pregnancy follow-up wording. Code generation cannot supply a justified answer to an unstated or contradictory rule. |
| 2 | Verification has blind spots | `_proc_names` checks procedures/timepoints, not conditional instructions. The crossover model contains high-fat-breakfast instructions, while two findings call them missing assessments. The `says` check also omits `instructions`. Input answers are mapped by words. Repair/adjudicate these before using the score as an architecture acceptance gate. |
| 2 | Review burden and stale status | The seven saved models carry 83 open points, including repeated descriptions of the same limitation. Current code has quote/page fields and executable procedure changes that the October inventory still describes as missing. Update status from measured evidence. |

The key distinction is between a requirement that cannot be expressed, one that was expressed wrongly,
one that was altered during processing, and one that is executed or displayed wrongly. Keep these
classes separate in subsequent measurements.

## Proposed design to test

At protocol analysis time, let the LLM author a versioned scheduling program, its helper functions and
an input declaration. The program evaluates the protocol for a supplied participant record. It may
define new calculations and compose logic without adding one clinical keyword to the engine for each
new protocol. It must not need to convert those calculations back into kit-only fields, which would
restore the original restriction.

The application supplies a stable execution boundary and display contract:

```text
Protocol + source-linked requirements
                 |
        LLM writes protocol program
        + input declaration + tests
                 |
       Isolated execution and independent checks
                 |
        Review and freeze a version
                 |
Participant record + explicit clock + calendars
                 |
      Frozen program returns schedule + trace
                 |
          Existing application UI
```

### What the program can define

- Calendar arithmetic, recurrence generation and exceptional dates.
- Stateful-looking rules computed from supplied history, such as two consecutive missed infusions.
- Which visits, procedures and follow-up obligations remain after a stop.
- Protocol-specific questions/measurements through a typed input declaration the app can render.
- Reasons and requirement identifiers for each output or unresolved dependency.

The input declaration needs a supported generic form/storage contract. Letting the LLM invent a field
that the application never asks or saves simply moves the present limitation to the input boundary.

### What the application owns

- A deterministic evaluator: same frozen program, patient snapshot, explicit current time and calendar
  versions produce the same result. Evaluation performs no fresh LLM calls.
- Visits, procedures, date/time constraints, required inputs, unknown states and evidence references
  in a validated output contract. This contract describes what the app displays; it must not reimpose
  the kit's finite list of clinical scheduling operations.
- Execution isolation, CPU/time/memory/output limits, and no direct access to credentials, network,
  database, host files or the system clock. The program returns proposed results; the host controls writes.
- Stable identities and explicit semantics for planned dates, actual dates, deadlines and allowable
  ranges. A range does not automatically imply one correct appointment date.
- Immutable program/version/runtime references tied to the protocol hash, review decisions and patients.
  Repairs create new versions; existing patient schedules do not silently change.
- A trace connecting each requirement to implementation, independent expected behavior and displayed
  output, or an explicit unresolved item. Finding a quotation in the PDF does not prove the
  interpretation is correct.

The current scheduling engine runs on the Expo client. A backend sandbox prototype must therefore
remain a parallel experiment until server execution versus portable execution, offline behavior,
patient-record compatibility and consistent staff/patient rendering are deliberately resolved.

## Acceptance experiment

1. Adjudicate the benchmark's input-mapping and instruction-display findings first. Preserve original
   reports; do not redefine expected behavior to fit a new implementation.
2. Use SYN-06 to test whether an AI-written program can implement its calendar, business-day, weekday,
   missed-visit and next-visit carryover rules. Supply an explicit holiday calendar for tests; an
   unspecified jurisdiction must produce a missing-input result.
3. Score boundary cases independently: January 31, leap years, a holiday inside a deadline, a late
   actual infusion, one missed infusion, two consecutive misses, and two nonconsecutive misses.
4. Use CRD/09 and the existing real-protocol benchmark for different structures and contradictions.
   Record expected unresolved answers explicitly. Reserve additional protocols/scenarios as holdouts
   if available; synthetic coverage is not proof of general protocol coverage.
5. Have expected schedules specified independently of generated implementation and its tests. The
   code-writing model may suggest tests, but it cannot be the only judge of its own correctness.
6. Exercise malformed inputs, loops, resource exhaustion and forbidden access in a real sandbox.
   No generated code should be executed inside the main FastAPI process as a substitute for isolation.
7. Compare adjudicated errors, unsupported requirements, unresolved inputs, repeatability, review
   effort and evaluation cost with the existing kit path. Require unchanged existing goldens.

Recommendation: run this bounded experiment before committing to a replacement engine. A successful
result would justify introducing protocol programs behind the existing versioning and review workflow.
A failed result should identify whether the failure is reading, code generation, data availability or
execution, rather than prompting another undifferentiated schema extension.

## Runtime references checked

- [Wasmtime security model](https://docs.wasmtime.dev/security.html): external capabilities are supplied
  through imports; this supports evaluating a tightly limited execution host as one candidate.
- [Wasmtime execution interruption](https://docs.wasmtime.dev/examples-interrupting-wasm.html): fuel
  and epoch mechanisms can bound computation. They do not establish correctness of a clinical rule.
- [RestrictedPython documentation](https://restrictedpython.readthedocs.io/en/stable/index.html): the
  project explicitly states it is not a sandbox or secured environment. Restricted syntax alone is
  insufficient isolation for generated Python.

No runtime has been selected, installed or deployed by this assessment. No model calls, patient
updates, schedule-mode changes or migrations were performed.
