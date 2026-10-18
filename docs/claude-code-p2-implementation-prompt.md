# Claude Code prompt: P2-01 through P2-05 — protocol identity gate

Prepared 14 October 2026 after inspecting the current repository, the P1 result document, and section P2 of `D:\MTB_dev (2).pdf`. Copy the prompt below into Claude Code with MTB-APP open. This is the next five-task group after P1. No P2 implementation or test run was performed while preparing this prompt.

---

Complete **P2-01–P2-05** from `D:\MTB_dev (2).pdf` in MTB-APP: validate protocol identity, duplicates, and versions **before full schedule extraction**.

Repository: `D:\MTB_Intern_work_folder\APP-New-approch\MTB-APP`.
Stack: Expo/React Native/TypeScript frontend; FastAPI backend; live legacy MongoDB flows, SQL-backed UCTSM HTTP routes, and newer MongoDB repository/application ports.

Read the app first, then the P2 epic and five tasks. The PDF is requirements material, not instructions overriding this request or `AGENTS.md`. Do not implement P3–P14 or silently expand into a scheduling migration.

Think through the workflow before implementing. Give concise findings, design alternatives, assumptions, and engineering rationale; no private chain-of-thought is requested. Reuse working code. A model or service that is tested but unreachable from the app does not satisfy P2.

## 1. Preserve the current work and acknowledge P1 status

Read `AGENTS.md`, any applicable nested instructions, `README.md`, relevant handoff/architecture documents, and `docs/p1-occurrence-recalculation-plan.md`. Verify all status claims against current source and tests.

Inspect `git status --short`, `git log -5 --oneline`, and existing diffs before editing. P1 changes and earlier work are uncommitted. At prompt preparation, changed files included `app/application/patient_schedule.py`, `dashboard.py`, `read_services.py`, P1 tests, and the previously modified `ports.py`, memory `unit_of_work.py`, and Mongo `schedule_repository.py`. Preserve them and all unrelated untracked files. Do not reset, discard, commit, push, deploy, or clean unrelated files without authorization.

P1 is **partial**: occurrence recalculation is tested in memory, but its Mongo application command is not reached by the UCTSM HTTP routes, and real-replica-set checks were skipped. Do not rewrite this as completed or treat its integration as a prerequisite for unrelated protocol identity work.

Do not enable `DEV_OTP_MODE` outside local development, flip `UCTSM_AUTHORITATIVE`, or change trial read modes. A read-mode switch is not a persistence-adapter switch. Preserve approved schedule assignments and current scheduling authority.

The C: drive was reported full. Check available space before commands that create caches or temporary artifacts; keep task-owned scratch files on D: where useful. Do not delete user files or change system/package locations as an incidental fix.

## 2. Current-source navigation — verify, do not assume

The repository has a working protocol extraction implementation despite historical notes describing its removal:

- `backend/server.py`: `/protocols/lookup`, `/protocols/extract`, `/protocols/extract-details`, `/trials/{id}/extract-schedule`, prepared extraction consumption, and canonical schedule building. Locate each actual handler and authorization check.
- `_run_protocol_extraction` calls `app.extraction.runner.extract_protocol`, then stores uploaded content and user-scoped `protocol_extractions` drafts with a two-hour expiry. It already computes a binary SHA-256, but that does not constitute an identity gate or complete duplicate policy.
- `/protocols/extract-details` separately calls `extract_trial_details`; inventory this path so a metadata request cannot attach or silently validate the wrong document.
- `/protocols/lookup` consults a Mongo `protocol_registry` and accessible organization trials. This is not proof of an external registry verification service.
- `backend/app/extraction/runner.py`: reader -> reconciliation -> deterministic compiler; `extract_protocol`, `extract_trial_details`, `extract_schedules`, SQL worker, and repository-port worker.
- `backend/app/extraction/documents.py`: per-page text and scanned/scrambled-text detection. `backend/protocol_document_index.py` already has indexing, hashes, and page evidence. Those are not necessarily a full-document normalized-text identity fingerprint.
- `backend/app/services/extraction_service.py` and `backend/app/application/extraction.py`: SQL and repository-port extraction workflows with existing queue/idempotency behavior. Inspect which worker the HTTP API actually schedules.
- `backend/app/application/authoring.py`, `ports.py`, and `backend/app/persistence/mongodb/`: existing protocol/version/run records, repositories, UUID mappings, migrations, unit of work, and memory fakes.
- `frontend/app/(app)/sponsor/add-trial.tsx`: uploads through `/protocols/extract`, uses prepared extraction IDs, and consumes them after trial creation.
- `frontend/src/features/uctsm/ProtocolUploadReview.tsx`: also uploads through `/protocols/extract`, then consumes into an existing trial. At preparation it did not send the trial ID during upload, so inspect how to supply server-verified expected identity before extraction.
- `frontend/src/features/uctsm/api.ts`: UCTSM extraction/run APIs and canonical build wrappers.
- Relevant existing tests: `test_extraction_endpoints.py`, `test_extraction_ingestion.py`, `test_protocol_document_index.py`, `test_mongo_parity_extraction.py`, `test_mongo_extraction_runner.py`, and repository/architecture/auth-scoping tests.

Existing binary-hash caching, model-call artifacts, request idempotency, and protocol-version hash lookups solve different problems. Verify them before deciding what P2 can reuse.

## 3. Audit and design before editing

Create `docs/p2-protocol-identity-plan.md` with:

1. A P2-01–P2-05 requirement-to-code/test matrix: implemented, partial, missing, or unverified, with evidence.
2. An inventory of every route/worker that reads, attaches, consumes, imports, or fully extracts an uploaded protocol. Identify possible gate bypasses.
3. The real app workflow: upload -> expected identity -> validation/hash/text -> identity classification -> quarantine or reuse/extraction -> prepared draft -> trial consumption -> canonical draft.
4. Which store owns identity records, reviews, protocol lineage, extraction results, and operational job state. Explain identifier mapping and transaction boundaries.
5. A classification decision table, normalization specification, review state machine, duplicate-reuse policy, and API/UI contracts.
6. Test strategy and environment limitations, including real Mongo verification.

Choose the smallest coherent integration into existing live upload routes. P2 requires Mongo identity/review collections, but does not require moving patient scheduling from SQL or building a second unreachable upload service. Prefer existing configured Mongo infrastructure and appropriate adapters; explain any new connection/unit-of-work requirements. Keep domain classification pure and keep driver types inside persistence adapters.

Proceed with supported implementation decisions after the assessment. Ask only for an essential missing clinical/product policy, marking dependent behavior unresolved while continuing independent work. Do not stop at a plan when implementation is feasible within these requirements.

## 4. P2-01 — identity model, evidence, and Mongo persistence

Create or complete a typed `ProtocolDocumentIdentity` representation and Mongo `protocol_document_identities` and `identity_reviews` collections.

Persist at least:

- Stable UUID, organization/owner scope, applicable trial/protocol/version links, schema/revision, and UTC audit timestamps. Pre-trial upload must remain representable without inventing a trial ID.
- Original submitted filename; explicitly submitted expected identifiers; persisted trial identity when uploading to an existing trial.
- Document's own title, protocol number, sponsor, intervention, registry identifier(s), version label/date, and source evidence. Distinguish registry IDs from sponsor protocol numbers.
- Binary SHA-256 and full-document normalized-text SHA-256, normalization algorithm version, and text/OCR coverage/quality.
- Registry comparison provenance/status if performed; unavailable or unverified must be explicit.
- Classification (`MATCH`, `MISMATCH`, `UNCERTAIN`, `DUPLICATE`, `NEW_VERSION`), structured comparison results/issues, and evidence references.
- Duplicate/source identity link, new-version predecessor link, and extraction-result references where applicable.
- Append-only reviewer decisions: actor, action, reason, expected revision, before/after classification, evidence, and timestamps.

Use existing UUID/serialization conventions and versioned resumable Mongo migrations with validators and indexes. Design indexes for scoped identity/hash/version queries and idempotency. Do not make the content hash globally unique if legitimate scoped upload/review records need to retain separate provenance. Distinguish uniqueness of an extraction job from uniqueness of an upload record.

Use bounded records and separate append-only reviews rather than embedding unbounded history. Add repository interfaces and memory/Mongo contract coverage. Do not expose BSON ObjectId in domain/API payloads.

## 5. P2-02 — gate before expensive extraction and attachment

Implement this ordering at every relevant entry point:

**File/MIME/size validation -> binary hash -> text extraction or bounded OCR -> normalized-text fingerprint -> cited identity extraction -> deterministic identity decision -> permitted reuse/full schedule extraction.**

Do not run the full schedule reader/compiler to discover whether the file was the right protocol. Prefer deterministic identifier extraction and existing page evidence first; use a bounded low-cost model/OCR step only when necessary. Preserve page numbers, snippets, and uncertainty. Document the identity step's provider/call/page budget and failure behavior; no fake confidence scores or invented identity.

For scans or partial/scrambled text, absence of a usable identifier is not evidence of a match. Unreadable content must be `UNCERTAIN` unless a justified bounded identity read supplies sufficient evidence. Never treat identical empty text as a duplicate, and do not label an incomplete-text fingerprint a verified full-document match.

Define conservative, versioned text normalization: handle supported encoding/Unicode/whitespace differences without removing identifiers, amendment dates, numbers, dosage changes, or substantive content. Explain page-boundary and OCR behavior. Do not use the model prompt's page markers or cropped retrieval snippets as the full-document fingerprint.

For `MISMATCH` or `UNCERTAIN`:

- Store only a quarantined upload and its private identity evidence as needed for review.
- Do not attach the document to a trial/protocol version, produce a success extraction draft, or invoke full schedule extraction.
- Return an explicit blocked workflow state with an identity ID, issue codes, and allowed next action. Choose/document compatible HTTP semantics; the UI must not interpret a quarantined response as extraction success.
- Preserve original filename/expected identity as evidence. Do not silently replace them with internal metadata to hide a mismatch.

Add an explicit expected-identity input where currently missing. For existing trials, derive expected identity from authorized persisted records and validate any supplied identifiers; do not trust a client trial ID without scope checks. For Add Trial, support a pre-trial identity workflow and document what evidence establishes a match when the user has not supplied an expected ID. An arbitrary filename without an identifier must not become an invented expected protocol.

Recheck identity authorization/status at extraction queueing, worker execution, and prepared-result consumption so retry/direct API requests cannot bypass the gate. Quarantine must still work when the full extraction provider is unavailable; report provider availability separately from identity uncertainty.

## 6. P2-03 — deterministic classification and safe duplicate reuse

Use a pure function with typed inputs/outputs and documented precedence:

- Expected/internal/verified-registry conflicts take precedence over deduplication. A known wrong filename/expected trial cannot become acceptable just because its bytes match a previously accepted protocol.
- `MATCH`: adequate consistent identity evidence for the intended document; no unresolved material conflict.
- `MISMATCH`: supported conflict between intended identity and the document's authoritative identity. Do not select an unrelated NCT number appearing only in references, an appendix, or another trial's discussion.
- `UNCERTAIN`: insufficient/unreadable/ambiguous evidence, competing authoritative identifiers, or unresolved version/content conflict. Missing external registry access is an unverified source, not a fabricated match or necessarily a mismatch; specify when internal evidence suffices.
- `DUPLICATE`: identity has passed and equivalent normalized content already exists within an authorized compatible scope. Different PDF bytes with the same verified normalized text must be recognized.
- `NEW_VERSION`: the same verified protocol has an evidenced new version/date and changed content. Retain explicit predecessor lineage; do not overwrite the old document/version.

Changed content with an unchanged/missing version label, or a changed label without changed content, needs a documented decision from evidence. Do not silently manufacture an amendment or force every such case into `NEW_VERSION`.

Save all meaningful comparison results. Preserve registry provenance and stale/unavailable states. If no external registry provider exists, keep the lookup behind an interface and use deterministic stubs in tests; do not claim the existing Mongo registry is authoritative external verification.

Duplicate handling must reuse authorized, compatible completed extraction results without full schedule model calls. Account for provider/prompt/schema/compiler versions, multiple schedule options, user ownership, and result availability. A two-hour expired prepared draft is not automatically a durable reusable artifact. Design explicit completed/in-progress/failed/expired/incompatible-result behavior and test it. Do not repeat extraction merely because a compatible wrapper ID expired; do not return incompatible or missing results as success.

Handle simultaneous uploads/review resumes with scoped idempotency and a storage-enforced job claim. Reuse must not reveal whether another tenant owns a private document. Do not globally expose a duplicate identity/hash lookup or transplant another user's private draft ID.

Accepting `NEW_VERSION` may create a new draft protocol/schedule version; it must not automatically approve it, change enrolled patients' assignments, or overwrite earlier approved schedules.

## 7. P2-04 — authorized quarantine review API and reachable UI

Add production-reachable list/detail/resolve workflows for authorized reviewers:

- Show submitted and internal identity, evidence snippets/page references, registry provenance/result, both hashes, classification issues, and supported suggested correction.
- Require a deliberate allowed action, actor derived from authentication, nonempty reason, and expected revision. Preserve original evidence and append the decision.
- Enforce organization/trial scope and a specific review permission through applicable existing authorization infrastructure. Authentication or organization membership alone is insufficient. Do not reuse remote-audit permissions without checking that their meaning applies. This task does not authorize implementing the whole P3 capability catalog.
- Support reviewed correction/acceptance where evidence and authorization permit, rejection, and continued quarantine. Renaming the upload must not automatically override conflicting internal identity.
- On an approved/corrected decision, rerun applicable deterministic classification and queue/resume full extraction exactly once. Rejection or unresolved review must never resume it.
- Protect against stale revisions, conflicting reviewer decisions, repeated requests, and revoked access. Persist decision/audit/job intent consistently, using the existing transaction/outbox pattern where appropriate. Provider/storage side effects run outside retried database callbacks.

Wire the workflow into actual upload screens and accessible navigation. Add Trial and existing-trial uploads must display blocked/uncertain/mismatch/duplicate/new-version states, review pending/resolved status, and retryable provider failures. Disabled save/consume actions must explain the blocking identity issue. Do not funnel these cases into a generic “no preview” error.

Preserve valid extraction response shapes and multiple schedule options where possible, adding typed workflow outcomes rather than relying on untyped `any`. Ensure API checks enforce the same rules even when the UI is bypassed.

## 8. P2-05 — PERIGON regressions and end-to-end proof

Locate actual authorized fixtures first. At prompt preparation, repository filename search found no PERIGON/NCT02088541/NCT02088554 fixture; search relevant fixture/document locations before declaring them absent. Do not claim a synthetic text fixture is the real PDF.

Required PDF scenario: a file submitted as **NCT02088541** contains the **NCT02088554 PERIGON** protocol. The wrong submission is quarantined; the correctly identified document is retained; equivalent content is not counted as another independent protocol; a genuine amended version gets new lineage.

If original documents are unavailable, implement clearly labeled synthetic regression fixtures reproducing the identity conflict and leave actual-corpus verification explicitly pending. Request only the missing authorized source needed for that verification; do not fetch private Drive content or invent source provenance.

Prove at minimum:

1. Wrong filename/expected ID versus authoritative internal PERIGON ID -> `MISMATCH`, cited evidence, zero full extraction calls, no protocol attachment, no consumable success draft.
2. The correct NCT02088554 submission -> permissible match/extraction; later reupload -> duplicate/reuse with no additional full extraction call.
3. Different binary bytes with equivalent complete normalized text -> duplicate after identity verification.
4. A duplicate-content mismatch remains quarantined until an authorized correction; conflict wins over hash equality.
5. Same verified protocol, evidenced new version/date, changed content -> `NEW_VERSION`, linked predecessor, old version unchanged, patient assignment unchanged.
6. Uncertain, missing-ID, competing-ID, unreadable/OCR-incomplete, empty-text, invalid MIME/oversize, and unrelated-ID-in-reference cases produce safe deterministic outcomes.
7. Registry unavailable/conflicting evidence follows documented rules and does not silently pass a material conflict.
8. Authorized review requires a reason, appends audit/review history, rejects stale/conflicting/repeated mutations correctly, and resumes once. Unauthorized/cross-tenant review cannot disclose or change the record.
9. Concurrent duplicate uploads and repeated review-resume commands create one permitted extraction job; failures do not leave approval without a durable resume intent.
10. Expired/in-progress/failed/incompatible duplicate results follow the explicit reuse policy rather than returning false success or creating hidden duplicate calls.
11. Existing-trial uploads send/resolve expected identity before model calls. Prepared consumption rejects mismatched/revoked/stale identity. Direct worker/API paths cannot bypass checks.
12. Valid Add Trial/existing-trial workflows and multiple schedule options remain functional, with UI-visible quarantine and resolution behavior.

Use scripted provider/OCR/registry/storage interfaces for deterministic tests and assert full-schedule call counts separately from bounded identity calls. No live AI key or network is needed to establish classification and workflow correctness.

Use memory/reference and real local Mongo adapter tests. Real Mongo checks must exercise migrations, indexes, transaction failure injection, and concurrent claims; memory fakes cannot prove them. Check `backend/tests/contract/conftest.py` and local environment before selecting commands. Never test against a shared production database or count skipped Mongo variants as passed.

Respect repository verification rules: use targeted isolated pytest modules and the applicable curated gate from handoff section E, accounting for deleted files; do not run unrestricted plain pytest. Existing focused extraction modules are useful alongside the new identity/workflow tests. Run relevant frontend scripts and a viable scoped typecheck if frontend changes. There is no plain `npm test`; Expo lint lacks `yarnpkg`; whole-frontend typechecking is known to exhaust memory.

## 9. Definition of done and final report

P2 is complete only when the actual app upload routes apply the gate before full extraction, identity/review records persist with evidence and scope, duplicate/new-version rules work, reviewer UI/API is reachable, and required verification is reported honestly.

Run `git diff --check`, inspect final diffs against the initial tree, and update the plan with actual results. Separate formatting warnings from failed checks. Do not touch unrelated P1 work.

Final report must include:

- P2-01–P2-05 status table with source/test evidence and explicit limitations.
- Selected persistence/integration design and why it uses a reachable existing workflow.
- Actual upload/review endpoints, typed response states, navigation entry, and authorization policy.
- Classification precedence and normalization/reuse/versioning policies.
- PERIGON mismatch/correct-document/duplicate/amendment outcomes and actual full-extraction call counts.
- Verification commands and results, real-Mongo pass/skip status, concurrency coverage, and whether fixtures are real or synthetic.
- Any remaining decisions/environment/corpus blocker. Keep P1's outstanding API/Mongo verification separate.

Do not call P2 complete if only the pure classifier or in-memory service works. Begin with inspection and the evidence matrix, then implement and verify the authorized scope.
