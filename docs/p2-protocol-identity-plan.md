# P2 — protocol identity gate: design and verification record

Scope: P2-01–P2-05 (validate protocol identity, duplicates and versions **before** full schedule extraction). Not in scope: P3–P14, scheduling migration, read-mode changes. P1 (occurrence recalculation) is untouched and remains partial (see `p1-occurrence-recalculation-plan.md`).

## 1. Requirement matrix

| Task | Status | Evidence |
|---|---|---|
| P2-01 identity model, evidence, Mongo persistence | Implemented | `app/domain/protocol_identity/models.py`; `app/persistence/mongodb/identity_repository.py`; migration `0007_protocol_identity.py` (validators + indexes); memory twin `persistence/memory/identity_store.py`; `tests/test_protocol_identity_store.py` (runs on memory and real Mongo) |
| P2-02 gate before extraction/attachment | Implemented | `server.py` `_gate_protocol_upload` runs after file/MIME/size validation: binary hash → page text → normalized fingerprint → cited identifiers → classification. Quarantine returns 409 with no model call. `tests/test_protocol_identity_workflow.py` |
| P2-03 classification and safe reuse | Implemented | pure `classify_identity` (`classify.py`); durable claim/reuse in `app/application/protocol_identity.py` (`begin_extraction`); `test_protocol_identity_domain.py`, `_store.py` |
| P2-04 review API and reachable UI | Implemented | `GET /protocol-identities`, `GET /protocol-identities/{id}`, `POST …/resolve`, `POST …/resume`, `GET …/extraction`; screen `sponsor/protocol-identity-review`, reachable from the sponsor dashboard "Protocol Review" action and from the blocked notice on both upload screens |
| P2-05 PERIGON regressions | Implemented with **synthetic** fixtures | No PERIGON / NCT02088541 / NCT02088554 file exists in the repository (filename search). Tests reproduce the identity conflict from synthetic text. **Verification against the real documents is pending**; they must be supplied by the owner. |

## 2. Entry points and gate coverage

| Route / worker | Gate |
|---|---|
| `POST /protocols/extract` (Add Trial and existing trial via `trial_id`) | `_run_protocol_extraction` → gate, then extraction or reuse |
| `POST /protocols/extract-details` | gate before `extract_trial_details`; a metadata request cannot validate a wrong document |
| `POST /trials/{id}/extract-schedule` | gate; expected identity from persisted trial; supplied id must agree (`EXPECTED_IDENTITY_CONFLICT`) |
| `POST /trials/{id}/protocol-extractions/{id}/consume` | rechecks identity: drafts that never passed (forged/legacy), belong to another trial, or are quarantined/rejected are refused |
| `POST /protocol-identities/{id}/resume` | single storage-enforced claim; only after an APPROVED review |
| UCTSM SQL extraction runs | unchanged; they do not accept an upload, so there is no document to gate |

## 3. Workflow

upload → validate file/MIME/size → binary SHA-256 → page text (unreadable pages recorded) → normalized-text fingerprint → cited identifiers → deterministic classification → **blocked** (409, identity stored privately, review item) or **permitted** (reuse a durable result, or claim one extraction job) → user-scoped prepared draft carrying `identity_id` → consume into trial → canonical draft. Nothing is approved and no patient assignment changes.

## 4. Ownership

Mongo owns identities (`protocol_document_identities`), append-only reviews (`identity_reviews`) and job/result state (`identity_jobs`, durable extraction results). Scheduling stays where it was. Identifiers are UUIDs derived per organization; BSON ids never reach the API. A review decision and its resume intent are written in one transaction; the provider call happens outside it.

## 5. Policies

**Classification precedence** (first applicable rule wins): no usable text → UNCERTAIN; competing authoritative identifiers → UNCERTAIN; confirmed registry conflict → MISMATCH; expected vs document identity (same kind only) → MISMATCH, or UNCERTAIN when the document simply does not state it; no expected identity → needs a self-consistent authoritative identifier; then deduplication: same verified text → DUPLICATE, same protocol + evidenced new version/date + changed text → NEW_VERSION, changed text with no version evidence → UNCERTAIN, else MATCH. Conflict always beats hash equality. Identifiers appearing only in references/appendices are not authoritative.

**Registry**: only a confirmed CONFLICT blocks. NOT_CHECKED / UNAVAILABLE / NOT_FOUND are recorded as informational `REGISTRY_UNVERIFIED`. The Mongo `protocol_registry` is not treated as external verification.

**Normalization** `protocol-text.v1`: NFC, ligatures, space variants, soft hyphen, dash variants, whitespace/page-boundary collapse. Case, digits, punctuation, headers and hyphenation are preserved. Only documents where every page had text are `verified_full_text`; scanned/partial text never proves a duplicate.

**Add Trial expected identity**: only an identifier the sponsor typed (or a persisted trial record). A file name is never promoted to an expected protocol. Without one, a self-consistent authoritative identifier in the document is required.

**Reuse**: scoped to organization, keyed by text fingerprint + engine signature (provider/prompt/schema/compiler). Completed → reused without a model call; in-progress → `EXTRACTION_IN_PROGRESS`; failed → one retry claim; expired lease → taken over; quarantined uploads never seed a duplicate; reuse never crosses organizations.

**Review**: permission `protocol_identity.review` or organization admin (remote-audit permissions are not reused). Needs an allowed action, a non-empty reason and the expected revision; a correction the document does not support is refused; approval resumes extraction exactly once; reject/keep never resumes.

## 6. Verification (14 Oct 2026)

Backend, real local Mongo replica set `mtb_p2_test`, `UCTSM_REQUIRE_MONGO=1` (no skips reported by `-rs`):

| Module | Result |
|---|---|
| test_protocol_identity_store.py | 20 passed |
| test_protocol_identity_domain.py | 21 passed |
| test_protocol_identity_workflow.py | 18 passed |
| test_extraction_endpoints.py | 9 passed |
| test_protocol_creation.py | 4 passed |
| test_uctsm_identity.py | 2 passed |
| test_mongo_extraction_runner.py | 16 passed |
| contract/test_mongo_platform.py | 9 passed |
| test_protocol_document_index.py | 12 passed |
| test_extraction_ingestion.py | 12 passed |

Concurrency on real Mongo: concurrent uploads of one document run one extraction; concurrent resumes claim the intent once; conflicting reviewer decisions let one win; failure injected inside the decision transaction leaves nothing behind; the claim index is enforced.

Frontend: `npm run test:protocol-identity` 5/5 pass; scoped `tsc` over the touched files (upload screens, notice, review screen, dashboard) is clean. `npm run lint` cannot run here (no `yarnpkg`).

`git diff --check`: no whitespace errors; only LF→CRLF notices.

Test modules were run one at a time, not as a single pytest session, and not as the full curated 34-file gate.

## 7. Limitations

- Real PERIGON documents not available; regressions are synthetic.
- No external registry provider exists; the registry port is a stub and tests use scripted providers.
- No browser/device run of the new UI; verified by unit tests and typecheck only.
- The curated handoff gate and other backend suites were not re-run in full.
