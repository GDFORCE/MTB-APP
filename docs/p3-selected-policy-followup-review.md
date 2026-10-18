# Review of the partial P3 selected-policy implementation

Reviewed 15 October 2026 against the current source, the implementation report and `docs/p1-p5-project-decisions.md`. No application code, test assertions, environment configuration or real grants were changed by this review.

## Intended task scope

P3 does not need to be rebuilt. The intended continuation is the **complete P1-P5 selected-decision closure**, using `docs/claude-code-p1-p5-selected-decisions-prompt.md`. Updating P3 is one part of that task. The Mongo-native synthetic pilot, authority/controller repairs, wrapper mapping, evidence and integration verification remain required.

## What is present

Source contains the reported changes: PI extraction default, removal of automatic org-admin identity review, Sponsor/CRO clinical-write denial, broader self-grant protection, and default-on LAST_EDITOR maker-checker via the environment policy factory.

Independently ran, in separate processes:

- `python -X utf8 -m pytest tests/test_uctsm_authz_policy.py -q`: **151 passed**.
- `python -X utf8 -m pytest tests/test_uctsm_maker_checker.py -q`: **19 passed**.

These runs are not a rerun of the curated gate, real-Mongo suite, browser workflow or PostgreSQL locking.

## 1. Selected grant eligibility is incomplete

The selected policy says high-impact patient commands need an explicitly granted **assigned PI**. It also restricts ordinary patient clinical data-entry grants to assigned PI/CRC, retaining Site/SMO enrolment for administration.

`authorization/policy.py::authorize` currently blocks Sponsor/CRO clinical writes but has no corresponding per-capability recipient eligibility rule for the other supported roles.

Pure-policy reproductions used a found patient resource, matching organization/trial, `Relationship.ASSIGNED` and a valid trial-scoped explicit grant:

| Principal / grant | Current decision | Selected policy |
|---|---|---|
| CRC / patient.command.high_impact | ALLOWED | DENY; assigned PI only |
| Site / patient.occurrence.record | ALLOWED | DENY; PI/CRC data entry only |
| SMO / patient.state.record | ALLOWED | DENY; PI/CRC data entry only |
| Sponsor / patient.occurrence.record | DENIED | Correctly denied |

Add centralized capability recipient eligibility, evaluated for explicit grants as well as role defaults, preserving privacy/non-disclosure ordering and trusted resource relationships. Keep Site/SMO enrolment valid. Test grant issuance where applicable and request-time authorization, so old or manually persisted grants cannot bypass the policy. Cover direct policy, HTTP, preview, confirm and replay, including granted CRC denial, assigned granted PI success, unassigned PI denial and revoked grants. Do not weaken existing legitimate reviewer grants or conflate protocol reviewer eligibility with patient clinical mutation eligibility.

## 2. Runtime policy provenance is stale

The runtime catalog still exposes:

- `POLICY_VERSION = "uctsm-authz.v1"`.
- `MATRIX_STATUS = "PROPOSED_PENDING_OWNER_APPROVAL"`.

An existing policy test still pins the old proposed status. The project-owner-delegated choice has already been recorded. Update policy version/status, API metadata, tests and the original documentation headings/tables consistently. Distinguish owner-delegated target selection, implemented behavior, active configuration and external clinical sign-off. Do not continue requesting the already-resolved owner decision or claim external approval that never happened.

## 3. General-gate maker-checker proof is still missing

`tests/conftest.py` sets `UCTSM_MAKER_CHECKER=off` at import time using `setdefault`. This can be overridden by an explicitly enabled process, and dedicated tests verify the real default; it does not mean the application default is off.

However, an ordinary test run with no setting exercises the older one-account approval workflow rather than the selected default. A unit check of `from_environment({})` and dedicated approval tests do not establish the complete selected upload/review/approve/enrol journey.

Remove the suite-wide opt-out as the ordinary baseline. Update the selected workflow's fixtures to use distinct material editor and approver, with proper grants. If legacy compatibility cases deliberately test OFF, scope that override to named tests/fixtures and document it. Run the selected journey with default-on settings, assert the refusal for self-approval, successful distinct approval, provenance and audit persistence. Preserve existing dedicated ON/OFF coverage; do not globally turn the safeguard off to retain old fixtures.

## 4. Continue the full selected-decision task

Legacy upload/consume mapping is no longer waiting on a product choice: PI extraction/draft consume has been selected. Implement the central checks and production-provider tests. Continue the known authority bypass and controller race/disposal repairs, generic Mongo-native pilot workflow, scoped pagination, genuine protocol/provider tests and component/browser checks.

The previously retrieved official PERIGON PDF and manifest are available locally. The synthetic pilot code/name is already selected. No real patient migration, real-user grant rollout or deployment is authorized by this review.

Update the P1-P5 acceptance matrix with actual outcomes and exact remaining evidence. Do not substitute a P3-only status report or ask whether to rebuild P3 from scratch.
