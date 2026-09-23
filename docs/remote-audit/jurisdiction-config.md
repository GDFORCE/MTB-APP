# Remote Audit Module — Jurisdiction / Audit-Authority Configuration

Status: **draft, pilot stage.**

## Principle (spec §2, "Important regulatory boundary")

The software supports remote audits/inspections/qualification visits/sponsor oversight/accreditation workflows, but never claims that every authority accepts remote evidence as a substitute for an on-site inspection. Every audit template names its governing organization and decision authority. There is no single global hard-coded jurisdiction rule.

## Configuration (per `remote_audits` record, set from the template at audit-creation time)

- `jurisdiction` — free-text/coded jurisdiction the audit operates under (e.g. `IN`, `US`, or a named regulator).
- `governing_authority` — the organization whose rules govern this audit's acceptance criteria.
- `template_version` — the audit-template version in force, so a later template edit never silently changes an in-flight audit's rules (spec §16.9).
- `regulated_system_of_record` — `true | false | jurisdiction_dependent` (v1.3 correction #12). **Current default: `false`** for all audits, matching this deployment's current stage (early dev/pilot, no live regulator-facing deployment). This flag is revisited per deployment before any real jurisdiction is onboarded — see "Regulated-system-of-record decision" below.

## Regulated-system-of-record decision

If `regulated_system_of_record` is ever set to `true` (or `jurisdiction_dependent` resolves to `true`) for a given deployment, that deployment additionally requires a formal computerized-system validation (CSV) package before go-live: intended-use statement, user/functional/system requirements, a risk assessment, a validation plan, a requirements traceability matrix (distinct from the engineering traceability matrix in the implementation plan), risk-based validation evidence (IQ/OQ/PQ or an equivalent proportionate approach), test evidence, deviation handling, release approval, change control, periodic review, and validated electronic-signature controls where Part-11-equivalent rules apply.

**This module's engineering test suite (Phases 1–10) is necessary but explicitly not sufficient for that package.** It feeds the CSV package's test-evidence section; it doesn't replace the QA/regulatory-owned activities around it.

**Current status: not applicable.** No jurisdiction has been marked `regulated_system_of_record=true`. This is a Phase 10 production-DoD gate, scoped per deployment — it does not block a deployment where the flag is `false`.

## FDA / ICH / DPDP references this config maps to (spec §2, §R1–R3, §R21)

- FDA remote regulatory assessment guidance (2025) — model as a complete workflow with evidence lineage, review states, final sign-off (Phases 1, 5, 7).
- ICH E6(R3) — audit trails/logs as first-class records, UTC timestamps, workflow-action logging (Phase 1 audit-event schema).
- FDA Part 11 — applies only if/when this module becomes the regulated system of record for a given deployment (see above).
- India DPDP Rules 2025 — location/staff-data minimization, purpose/retention definition, access logging, applicable data-principal obligations (see `privacy-controls.md`).
