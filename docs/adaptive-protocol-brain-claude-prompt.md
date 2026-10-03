# Claude Code implementation prompt

Copy this prompt into Claude Code with the MTB-APP repository open. The companion
`adaptive-protocol-brain-claude-full-prompt.txt` embeds this prompt and both
specifications for use as one self-contained handoff. The Markdown source files
are the maintained documents; the combined text file is a generated snapshot.

---

You are implementing the Adaptive Protocol Brain in the existing MTB-APP
repository. Work as a senior engineer responsible for the complete product
journey, including extraction, domain semantics, persistence, API, UI and tests.

This is an implementation task. Inspect the current system, make a concrete plan,
then implement and verify the in-scope requirements. Continue beyond an architecture
proposal, schema-only patch, mock UI or a successful isolated unit test.

## Product intent

When I upload a clinical-trial protocol, the application should read it, understand
its scheduling requirements and show an editable Visit Schedule:

- Header: Visit Schedule, extraction status and review summary.
- Filters: All / Pending / Ready.
- Table: status, visit name, Timing, Window.
- Expandable rows: assessments, applicable groups, anchors, repetition, conditions,
  all additional constraints, exact source evidence and questions to resolve.
- Actions: edit, Add Visit, export and approve when backend validation permits it.
- After approval and enrollment: compute patient dates from actual/planned facts
  as explicitly allowed by the rules; recalculate appropriately as facts change.

Use existing MTB visual components and styling. The original screenshot is a
layout reference, not source evidence for dates. Its contents must not be hardcoded.

I want a reusable reasoning system that constructs an executable model of each
protocol from composable semantics. Do not implement a growing collection of
trial-specific conditions or a prompt that merely extracts a fixed visit table.

The intended architecture is:

```text
Protocol and attachments
  -> document inventory and source coverage
  -> cited atomic requirements
  -> global entity and meaning reconciliation
  -> typed composable protocol program
  -> evidence, capability and consistency validation
  -> scenario simulation and human review
  -> approved immutable model
  -> deterministic patient evaluator
  -> readable schedule, operational projection and eligible reminders
```

AI proposes meanings and can retrieve/revisit source context during bounded repair.
Deterministic code validates and executes the approved model. Runtime patient
scheduling must not require an LLM call. Preserve missing and contradictory
meaning; do not silently resolve it by choosing a clinically plausible default.

## Required documents and precedence

Read these files, or their complete embedded versions below:

1. `docs/adaptive-protocol-brain-requirements.md`: product scope, requirement IDs,
   acceptance probes and definition of done.
2. `docs/adaptive-protocol-brain-design.md`: architecture, proposed rule language,
   semantics, persistence/API/UI contracts and work packages.

Read repository `AGENTS.md` and applicable nested instructions before editing.
Inspect `MTB_IMPLEMENTATION_HANDOFF.txt`, especially architecture boundaries,
protected route dependencies and verification guidance. Use the current code and
git state to verify implementation status; some older documents say extraction is
removed although a reader/compiler exists in the inspected working tree.

Product requirements define intended behavior. Proposed names and structures in
the design can be adapted to existing abstractions when equivalent behavior is
preserved and the decision is documented. Do not treat pseudocode as a claim that
an endpoint or schema already exists. If instructions genuinely conflict, identify
the exact conflict and continue independent authorized work while resolving it.

## Start with current-state evidence

1. Inspect git status, recent history and relevant diffs. Preserve all existing
   modified/untracked work; this checkout already contains substantial changes.
   Do not reset, clean, overwrite or discard work to obtain a simpler baseline.
2. Inspect current extraction, compiler, schedule model/evaluator/validator, review
   services, enrollment, projection and active UI paths.
3. Map existing capabilities to requirement IDs. Verify suspected gaps; some may
   already have been fixed since the design was written.
4. Run appropriate existing narrow checks and record pre-existing failures before
   changing related behavior.
5. Create `docs/adaptive-protocol-brain-implementation-status.md` with a concise
   implementation plan and traceability table. Update it with real evidence.

Useful starting locations:

```text
backend/app/extraction/{reader,facts,compiler,contracts,runner}.py
backend/app/domain/schedule/{models,timing,condition,conditional,evaluator,validator}.py
backend/app/services/{native_import,schedule_service,impact_service,parity_service}.py
backend/app/services/{schedule_projection,operational_projection}.py
backend/app/db/
backend/app/api/uctsm.py
backend/server.py
frontend/src/features/uctsm/{presentation,types,api}.ts
frontend/src/features/uctsm/SimpleScheduleTable.tsx
frontend/src/features/uctsm/ProtocolReviewScreen.tsx
frontend/src/features/uctsm/ProtocolScheduleScreen.tsx
frontend/app/(app)/sponsor/add-trial.tsx
frontend/app/(app)/clinical/add-patient.tsx
```

Search for existing abstractions before introducing parallel replacements.

## Core engineering requirements

- Evolve the existing engine through typed operators and adapters. Keep clinical
  rules as data, not executable code generated by a model.
- Compose simultaneous timing restrictions across anchors. Distinguish AND, OR,
  duplication, exceptions, supersession and actual contradiction.
- Preserve source-to-statement-to-rule-to-result links. Every discovered requirement
  must have an inspectable disposition. Uncompiled additional restrictions cannot
  become ignorable warnings while the main rule is marked ready.
- Preserve nested UNKNOWN applicability and missing anchors. Do not fabricate
  recurrence maxima, holiday behavior, resume policy, window, visit mode, anchor
  equivalence, clinical day numbering or phase-boundary ownership.
- Distinguish calendar months from fixed day counts, planned from actual facts,
  protocol targets from appointment bookings, and repeated templates from occurrences.
- Use occurrence identities independent of computed dates. Keep history protected
  while updating affected future obligations.
- Represent independent intervention/process states, confirmed decisions and dated
  assignment changes. Full automatic dosing/statistical allocation is outside this
  release; retain relevant statements and surface scheduling dependencies honestly.
- Preserve material detail through persistence, API, table expansion and export.
  A simple display is a projection of the rule model, not a lossy storage format.
- Reject unknown semantic fields/operators at the normalized execution boundary,
  retain invalid raw items for review, and bound model repair/retrieval loops.
- Keep document text untrusted. It cannot override system instructions, invoke tools,
  disable validation or supply arbitrary executable expressions.
- Use existing authentication, tenant access, auditing and job infrastructure.
  Do not expose source documents, patient data or credentials in ordinary logs.

## Implement through integrated work packages

Follow the design's WP0-WP5 sequence, adjusting order for actual dependencies:

1. Preserve statement/evidence/coverage gaps and prevent false readiness.
2. Add composable constraint semantics and global reconciliation with explanations.
3. Add calendar/recurrence combinations and explicit planned/actual and process states.
4. Complete durable upload, review, edit, approval, export and patient integration.
5. Expand full-document coverage and demonstrate unfamiliar rule combinations.

For each package, implement relevant schema, compiler, validator, evaluator,
serialization/migration, API, UI and projection changes together. Keep the product
runnable. Do not declare a capability complete if it exists only in the backend
model or only in a frontend fixture.

Make ordinary reversible implementation decisions without repeatedly asking for
permission. When a genuine clinical interpretation is missing, build the review
path and use explicitly synthetic fixture choices in tests; do not decide the
clinical meaning yourself. Record technical design decisions and their rationale.

## Repository boundaries

- Never enable `DEV_OTP_MODE` outside local development.
- Do not flip `UCTSM_AUTHORITATIVE` or switch a real trial's read mode as part of
  implementation. Preserve the existing per-trial parity/cutover mechanism.
- Do not migrate `sponsor/visit-schedule.tsx`, `clinical/schedule-review.tsx` or
  `clinical/trial-summary.tsx` without first checking handoff C3/D-4 and preserving
  the required legacy features. Prefer existing canonical feature components.
- Preserve current authoritative write paths and Mongo operational compatibility;
  do not invent a second clinical-truth store or assume SQL/Mongo atomic commits.
- A canonical draft-build failure must surface visibly; do not silently redirect
  the sponsor to a legacy editor and imply extraction succeeded.
- Preserve approved-version immutability, patient version pinning, stale-impact
  protection and protected performed history.
- No production deployment, destructive data migration or clinical cutover is
  implied by this local implementation task.

## Verification expectations

Use independent synthetic examples, metamorphic/property checks where useful and
existing regression tests. Cover AT-01 through AT-20 and maintain a requirement-to-
implementation-to-check map. Include composition tests with renamed entities and
unfamiliar combinations so hardcoded sample-specific behavior cannot pass.

Run relevant backend tests in the repository environment. Do not run plain pytest
across the entire backend: documented TestClient teardown issues require isolating
some modules. Use the surviving curated gate in handoff section E plus affected
new tests; omit deleted files identified in AGENTS rather than recreating obsolete
tests solely to satisfy the old list.

Use `npm run test:uctsm` or relevant narrower scripts from `frontend/`. Generic
`npm test` is unavailable. Full-project TypeScript checking has a documented memory
issue, and Expo lint requires unavailable `yarnpkg`; perform a suitably configured
scoped typecheck and report any limitation. Verify the rendered review workflow
when a usable runtime is available; source-text tests alone cannot prove UI behavior.

Use offline scripted model clients for deterministic development tests. Do not
ship fake successful extraction as a provider fallback. The original sample PDF
was not supplied with this handoff, so do not claim real-document extraction quality
from the pasted assessment or screenshot. Continue implementation without it and
record the remaining real-document evaluation dependency.

No tests were run by the author merely to prepare these documents; establish and
report your own actual results. Distinguish failures introduced by your work from
pre-existing failures with evidence. Do not weaken tests or mark warnings as resolved
just to obtain a green result.

## Completion and handoff

Keep working until the in-scope implementation and relevant verification are done,
or a concrete external dependency prevents further progress. If the work spans
sessions, preserve exact state and next steps in the implementation-status document.

Your completion report must include:

1. The working upload-to-review-to-patient behavior and how to reproduce it locally.
2. Changed areas and migration/configuration requirements.
3. Requirement/operator coverage, including incomplete or unsupported behavior.
4. Exact verification commands and observed outcomes.
5. Whether any real source protocol was evaluated, with no inflated accuracy claim.
6. Remaining risks or external blockers and the next concrete action.

Do not describe the system as able to handle every possible trial. Demonstrate
generalization through composition, source accountability and deterministic
execution, and expose unsupported meaning precisely.

Begin by inspecting the current working tree and documents, then implement.
