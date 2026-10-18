# Independent review: native pilot, registry identity and remaining closure

Reviewed 15 October 2026 against the latest implementation report, selected project decisions and current source. No application source, clinical data, real-user grants, live authority or read modes were changed. Only review/prompt documents were edited.

## Verdict

The earlier explicit-SQL-authority bypass, grant eligibility and controller cancellation/disposal regression suites now pass in the focused runs below. However, **missing browser evidence is not the only remaining acceptance problem**. Additional checks found a production-access integration failure, overly permissive registry identity classification and incomplete pilot recovery. Unknown authority still permits writes, contrary to the selected fail-closed direction. The acknowledged old SQL impact authorization gap also remains an engineering requirement.

## F1 - High: the freshly seeded pilot is inaccessible through production relationships

Sources: `app/application/pilot.py::seed/_enrol_native`, `server.py::_OperationalAccess`, `authorization/guard.py::_resolve_mongo_plane`, plane patient-list/calendar handlers.

The seed creates native canonical patients, anchors and assignments in the port UoW. It creates no operational patient records or relationship mappings. Production access still asks the legacy operational `patients` collection whether the caller can access the external patient ID. A canonical assignment alone is not read by that provider.

Reproduction used:

- Actual FastAPI router, capability policy/guard and production `server._OperationalAccess`.
- Test-authenticated PI holding the explicit DEMO_SEED grant and normal relevant read/preview rights.
- In-memory canonical UoW; genuine Motor queries to an empty, isolated local replica-set Mongo operational database.
- `UCTSM_PILOT_MODE=true`; no AllowAllAccess or FakeOperationalAccess.

Results for the creator of a fresh pilot:

| Request | Observed |
|---|---|
| POST `/mongo-plane/pilot/seed` | 200; returns the native patient IDs |
| GET `/mongo-plane/trials/{id}/patients` | 200; `items=[]`, no next cursor |
| GET returned patient's `/calendar` | 404 `Not found` |
| Operational patient documents after seed | 0 |

The pilot tests use a permissive relationship provider; the separate production-provider test manually inserts operational patient documents. Neither currently connects fresh pilot seeding to production-provider reachability.

Repair the supported identity/relationship contract: either resolve trusted native canonical assignments in the real provider or persist the necessary synthetic operational relationship records in the selected isolated environment with proper owner/trial/staff mapping. Do not add a blanket pilot-mode access bypass. Cover fresh seed -> visible patient list -> calendar -> preview/confirm/reload, assigned staff, unassigned user, revocation, foreign organization and forged parent IDs using the production provider.

The service uses a server-generated approver ID and a prebuilt approved fixture. That can bootstrap synthetic test data, but it is not itself a walkthrough of two real local user accounts reviewing/approving through product UI. Demonstrate the selected review/approval/enrol workflow separately; do not describe a fixture bootstrap as the entire upload-to-patient journey.

## F2 - High: registry corroboration accepts an unrelated document

Source: `domain/protocol_identity/classify.py::_registry_corroborates`.

The implementation says the registry acronym and organization appear in front matter, but actually checks substring presence of the acronym and **only the organization's first word** anywhere in the first 8,000 front-matter characters. It does not establish that either descriptor names this document's own study.

Negative-case reproduction used the real extraction/classification/application service with a deliberately injected registry comparison, not a claim about a live study:

- Expected identifier: format-valid constructed `NCT12345678`.
- Injected registry descriptors: acronym `ORBIT`, organization `National Heart Research Institute`.
- Uploaded text explicitly names a different diagnostic study, sponsor `National Cancer Institute`, protocol `OTHER-777`, and mentions ORBIT only as another study in background text.
- No NCT is stated by the uploaded document.

Observed classification: **MATCH, blocked=false**, with `REGISTRY_CORROBORATED`.

Shared words and a background citation are not authoritative identity evidence. The correct PERIGON fixture being uncertain before review is not, by itself, a classifier defect. Do not broaden automatic acceptance merely to make its test green.

Select the safe existing workflow: absence of a sufficiently proven document identity stays UNCERTAIN and goes to authorized, reasoned identity review. Registry descriptors can support the review. If adding automatic identification through an official registry attachment, bind the exact document/version/hash to that specific registry record with trusted retrieval provenance. An exact official document linkage is stronger evidence than word overlap. Do not equate an arbitrary upload to the locally downloaded official PDF without verifying its bytes/version.

Add false-positive regressions for shared organization prefixes, unrelated study mentions, substring collisions, wrong intervention/protocol/version and contradictory authoritative identifiers. Validate the returned registry NCT against the request before recording a confirmed provider result. Preserve quarantine, reviewed resolution, safe duplicate reuse and real/synthetic provenance.

## F3 - Medium: first-evaluation failure leaves a partial pilot reported as successful on retry

Source: `app/application/pilot.py::seed/_enrol_native`.

Pilot drafting, approval, patient enrolment and first evaluation are separate commits. On reseed, finding any pilot trial returns `_describe` immediately rather than reconciling its completeness.

Reproduction, both memory and real local migrated Mongo:

1. Inject a RuntimeError from the first patient evaluation.
2. `seed` fails after the trial, approved version and first patient/anchors/assignment already committed.
3. Retry `seed`.
4. It returns success with **one patient instead of two**, whose **evaluation pointer is None**.

Repair atomic patient enrolment plus first evaluation using the existing shared transactional path where possible. Make overall seed state resumable/reconcilable with explicit completeness checks rather than treating trial existence as completion. Preserve legitimate existing synthetic data and non-pilot collision refusal. Cover failures after draft/approval/each patient, retry, duplicate/concurrent seed and one complete immutable first evaluation per enrolled patient. No cleanup by deleting clinical history or repurposing a non-pilot trial.

## F4 - High: unclassified authority still authorizes changes

Source: `app/application/authority.py::require_command_plane_authority`.

The helper returns `UNCLASSIFIED_COMPAT` and permits writes for `authority_plane=None`. The corresponding authority regression currently asserts that allowance. No evidence-backed native classification is required.

Actual router/guard + in-memory canonical UoW reproduction:

- Patient authority is None.
- Command preview returns 200.
- Confirm returns 200 and persists one ledger row.

This is a selected-policy mismatch, separate from the repaired explicit-SQL case. A compatibility label is not proof that a patient is native. Require explicit trusted Mongo authority or a persisted auditable evidence-backed compatibility classification. Keep retained history readable, do not silently relabel existing patients, and verify unknown/SQL/legacy/native and authority-changed replay cases. Update native test fixtures explicitly rather than preserving a permissive unknown-authority test solely to avoid fixture changes.

## F5 - High, source-verified: older SQL impact operations still bypass the high-impact grant

The report correctly discloses this gap. It is **not merely a browser/locking verification limit**.

`services/impact_service.py` supports persisted process transitions, including hold/resume/stop. `api/uctsm.py` accepts them in schedule impact preview and confirms the stored proposal. The manifest requires generic `patient.impact.preview` / `patient.impact.confirm`, without the selected additional `patient.command.high_impact` check for those operations. PI has generic confirm by default.

Resolve required rights from the trusted stored proposal operation at confirmation/replay and the typed operation at preview. High-impact aliases must not be weaker routes around the selected grant. Cover PI without high-impact grant, explicitly granted assigned PI, ineligible granted CRC, revocation, payload tampering and replay. Preserve ordinary low-impact workflows and the existing SQL cohort's authority. This review verified the source mismatch; it did not perform a full SQL process-transition HTTP reproduction or PostgreSQL locking test.

## Independent verification

Separate test processes; explicit local replica-set settings and isolated contract databases:

| Run | Passed |
|---|---:|
| `test_uctsm_authz_policy.py` | 160 |
| `test_p5_authority.py` | 12 |
| `test_p5_pilot.py` | 12 |
| **Backend total** | **184** |
| `npm run test:uctsm-command-controller` (controller + plane presentation) | **15** |

No skips in these runs. Passing them does not cover the additional failures above. Additional reproductions: F1 production-provider HTTP with real local Mongo operational lookup; F2 injected-registry negative case; F3 memory and real Mongo failure/retry; F4 actual HTTP guard/router with memory records. No browser/device, React render, full curated gate or PostgreSQL locking run by this reviewer.

All review-owned random Mongo databases were removed after their runs; no shared/production database was used. Application source remains unchanged by this review.

## Closure/reporting

Repair F1-F5 before describing the remaining blockers as browser/document evidence only. The project choices and synthetic pilot remain selected; no new architecture or role decision is needed.

Rewrite the closure report's stale sections 3-6 and P2-02 row, not only its latest appendix: they still describe resolved owner questions, missing registry/provider work and unmapped wrappers as current. Classify each PDF row against its own criteria. Real-patient migration is outside the selected scope, not an unmet requirement to create a clinical migration merely to close the synthetic pilot.

Actual UI walkthrough/render checks and genuine amendment/version evidence remain required where the PDF asks for them. Do not invent their results, apply automatic identity guesses or withhold otherwise complete domain criteria solely because an unrelated UI check is missing.
