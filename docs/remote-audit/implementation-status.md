# Virtual Visit & Remote Audit — implementation status

Tracks the Production-Grade Implementation Plan v1.3 (`noble-splashing-crab.md`)
against what is actually built and tested. Update it when a phase moves.

**Navigation is still untouched.** `SponsorBottomNav.tsx` and every other
navigation file are unmodified, per the standing instruction: the `notifs` tab
is replaced only in Phase 11, after Phase 10's Production DoD is met. Every
screen below exists at its route and is reachable by URL, but nothing links to
them yet.

---

## Phase status

| Phase | State | Notes |
|---|---|---|
| 0 — Architecture & security baseline | **Done** | 12 documents in this directory; RBAC/ABAC and audit-event schema implemented with tests |
| 1 — Platform infrastructure | **Done** | Job queue, worker, geofence classifier, participant resolution, retention job |
| 2 — Core evidence & trust chain | **Done (code)** | Backend + both screens. **Mini-pilot outstanding** — needs a real site visit |
| 3 — Sites, facilities, equipment, geofence | **Done** | OSM-first boundary lookup, circle fallback, Static Maps approval |
| 4 — Documents | **Done** | Full lifecycle, pyHanko signature validation, AI/text extraction, both screens |
| 5 — Query / CAPA | **Done** | Both state machines, effectiveness review, overdue sweep, both screens |
| 6 — Virtual visit | **Done (code), env-gated** | Needs LiveKit credentials to run. In-call video screen needs the EAS build |
| 7 — Scoring & decision | **Done** | Two-axis engine, step-up-gated sign-off, PDF report |
| 8 — Device/app integrity (native) | **Android code done; iOS not started** | Play Integrity native module, bridge, nonce/package/freshness binding and 23 tests are in. Needs an EAS build + Google Cloud setup to run — see [play-integrity-setup.md](play-integrity-setup.md). App Attest deliberately not implemented |
| 9 — Observability & security monitoring | **Done** | Metrics, alert rules, log-redaction CI gate wired into real call sites |
| 10 — Production assurance & DoD | **Partly blocked** | Engineering tests done; infrastructure/pen-test/device-matrix gates need you |
| 11 — Navigation wiring & release | **Not started** | Deliberately gated on Phase 10 |
| 12 — Advanced assurance | **Not started** | Post-release by design |

---

## What is built

### Backend services — `backend/app/services/remote_audit/`

| Module | Responsibility |
|---|---|
| `capabilities.py` | RBAC/ABAC capability model. `admin` grants platform admin only — never template/reviewer/approver by implication |
| `participants.py` | `resolve_audit_access(user, audit)` — org membership earns a minimal list; details and actions need a participant record on that audit |
| `geometry.py`, `geofence.py` | Pure geodesic geometry and the §7.3 classifier |
| `boundary_lookup.py`, `geofence_onboarding.py` | OSM Overpass boundary lookup, circle fallback, Static Maps preview |
| `job_queue.py` | Mongo durable queue: atomic claim, fencing tokens, backoff, DLQ, stuck-job reconciliation |
| `mutation_guard.py` | Idempotency store, rate limiting, correlation ids, 404-not-403 helpers |
| `evidence.py`, `verify_pipeline.py`, `verification.py` | Capture sessions, upload intent, finalize, and the §15.2 decision engine |
| `attestation.py` | Play Integrity verification; App Attest deferred to Phase 8 and reported as UNAVAILABLE rather than assumed |
| `file_checks.py` | Magic-byte sniffing and the malware scanner. UNAVAILABLE is never CLEAN |
| `pdf_inspect.py` | PyMuPDF structure/metadata extraction with hostile-input bounds |
| `signatures.py` | pyHanko validation at full correction-#11 scope |
| `documents.py`, `document_pipeline.py` | Document lifecycle and worker processing |
| `queries.py` | Query and CAPA state machines, effectiveness review, overdue derivation |
| `virtual_visits.py` | Sessions, privacy modes, consent gating, participant tokens, egress reconciliation |
| `scoring.py`, `decisions.py` | Two-axis engine (pure) and append-only score/decision persistence |
| `report.py` | The regulator-facing PDF |
| `observability.py` | Metrics, alert rules, and the redaction used by the CI gate |
| `retention.py`, `templates.py`, `sites.py`, `facilities.py`, `stepup.py`, `capability_grants.py`, `audits.py`, `audit_events.py`, `vision_extraction.py`, `db.py` | Supporting services |

**API:** 57 endpoints in `backend/remote_audit_routes.py`, mounted on the
existing FastAPI app.
**Worker:** `backend/remote_audit_worker.py`, five job types —
`retention.sweep`, `evidence.verify`, `document.process`,
`document.expiry_sweep`, `capa.overdue_sweep`.

### Frontend — `frontend/`

| Route | Purpose |
|---|---|
| `app/(app)/site/remote-audit/capture.tsx` | In-app camera capture. No gallery picker, session armed before the shutter |
| `app/(app)/sponsor/remote-audit/evidence/[id].tsx` | Evidence viewer — reasons and signals, never a bare verdict |
| `app/(app)/site/remote-audit/documents/upload.tsx` | Document upload into quarantine |
| `app/(app)/sponsor/remote-audit/documents/[id].tsx` | Document review — suggestions visually separate from confirmed values |
| `app/(app)/remote-audit/queries/[id].tsx` | Query thread, shared by both sides |
| `app/(app)/remote-audit/capas/[id].tsx` | CAPA detail and effectiveness review |
| `app/(app)/remote-audit/virtual-sessions/[id].tsx` | Privacy notice, consent, join gate |
| `app/(app)/sponsor/remote-audit/[auditId]/decision.tsx` | Scoring dashboard and step-up-gated sign-off |

Pure logic lives in `src/features/remote-audit/` (`manifest`, `normalize`,
`documentNormalize`, `queries`, `scoring`, `virtualVisits`) so it is testable
with `node --test`, matching this repo's existing frontend test convention.

---

## Decisions worth knowing before changing anything

These are the places where the obvious simplification is wrong. Each has a
test that fails if it is "cleaned up".

1. **An unavailable malware scanner is not a clean scan.** Evidence and
   documents stay `PROCESSING_BLOCKED` and invisible. This is stricter than
   routing to review, on purpose — a reviewer is never the scanner of last
   resort.
2. **The two scoring axes are never combined.** Compliance and evidence
   confidence answer different questions. There is no helper anywhere that
   averages them, and the PDF prints two numbers.
3. **Blockers render before the score,** on screen and in the report.
4. **"Signature present" is never "signature trusted."**
   `SignatureTrust.PRESENT_UNVERIFIED` and `TRUSTED` are distinct values so no
   caller can collapse them.
5. **A certificate valid at signing time but expired today is accepted.**
   Validation runs at the signing moment, not at `now`.
6. **N/A is excluded from the denominator; MISSING is not.** Folding
   unanswered into not-applicable is how a gap disappears.
7. **Extracted ≠ confirmed.** AI and OCR output lands in `extracted`; only
   `confirmed` is fact, and only a confirmed due date drives expiry.
8. **Overdue is derived, never stored.** A persisted flag is wrong the moment
   the clock passes it.
9. **E2EE and recording cannot coexist.** One `privacy_mode` value, fixed
   before the room exists.
10. **Consent gates joining, not scheduling.** A session exists as a record
    with `consent_status=PENDING`; nobody joins and no media flows until
    consent is recorded.
11. **A human decision is never re-run by the worker.** Re-processing refuses
    to touch an ACCEPTED or REJECTED document.
12. **An override stores the machine's verdict beside the human's,** never
    instead of it.
13. **404, never 403,** on every authorization mismatch.
14. **Document expiry is evaluated in UTC** and a document is valid through
    the whole of its due date. Sites east of UTC keep a certificate a few
    extra hours — expiring early would flag a compliant site as non-compliant,
    which is the more damaging error. Per-site timezone evaluation is a
    documented follow-up.

---

## Tests

```bash
# Backend (needs the .env Mongo connection)
cd backend && python -m pytest tests/ -k remote_audit -q

# Frontend pure logic
cd frontend && npm run test:remote-audit
```

The CI gate in `tests/test_remote_audit_observability.py` runs the real
document pipeline with logging captured and scans the output for raw
coordinates, tokens, signed URLs and file content. A future
`log.info(f"...{manifest}")` anywhere in that path fails the suite.

Known unrelated failure: `tests/test_uctsm_qualifier_resolution.py` has two
pre-existing failures in the UCTSM schedule-approval validator. That subsystem
is untouched by this work.

---

## What is blocked on you

| Needed | Blocks | Why it can't be done here |
|---|---|---|
| A real (or close-proxy) hospital site visit | Phase 2 mini-pilot, Phase 10 field validation | Thresholds have to come from real basements, entrances and building edges, not from assumptions |
| LiveKit project URL + API key/secret | Phase 6 runtime | Scheduling, consent and token minting are built and tested; they report `LIVEKIT_UNAVAILABLE` until configured |
| EAS / custom dev client build | Play Integrity actually running, the in-call video screen | Expo Go cannot load native modules. The module is written and wired; it just cannot execute in Expo Go |
| Google Cloud: Play Integrity API + service account | Play Integrity verification | Your project and credentials — full steps in [play-integrity-setup.md](play-integrity-setup.md) |
| Apple Developer: App Attest capability | iOS attestation | Your account. Not started by choice — Android first |
| Maps Static API enabled on the existing Google Cloud project | Phase 3 geofence preview image | Same project as `GOOGLE_PLACES_API_KEY`, one more API to turn on |
| ClamAV (or equivalent) reachable | Any evidence or document reaching a reviewer | Nothing is released unscanned, so with no scanner everything correctly stops at `PROCESSING_BLOCKED` |
| Production secrets manager choice | Phase 10 DoD | AWS Secrets Manager / SSM / Vault. **`get_secret()` is specified in `secrets-management.md` but NOT yet written** — every module currently reads `os.environ` directly, so adopting a manager means writing that function and routing the call sites through it |
| Production hosting decision | Phase 10 DoD | Worker container isolation and S3 Object Lock configuration |
| Penetration test | Phase 10 DoD | External engagement |
| Incident-response paging target | Phase 9 drill | Currently you, per the Phase 0 decision |

## Optional environment variables

| Variable | Effect when unset |
|---|---|
| `CLAMAV_HOST` / `CLAMAV_PORT` | Evidence and documents stay `PROCESSING_BLOCKED` |
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | Virtual visits schedule but cannot be joined |
| `REMOTE_AUDIT_TRUST_ANCHORS_DIR` | Signed PDFs report `PRESENT_UNVERIFIED` and route to review — never `TRUSTED` |
| `REMOTE_AUDIT_SIGNATURE_REVOCATION_MODE` | Defaults to `soft-fail` |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | Extraction falls back to manual entry, which is never a processing failure |
| `OSM_OVERPASS_URL` | Geofences use the circle fallback |
| `GOOGLE_PLAY_INTEGRITY_*` | Every capture reports `ATTESTATION_UNAVAILABLE` and routes to human review |
| `REMOTE_AUDIT_STEPUP_REQUIRES_MFA` | Defaults to **true**; the `.env` here sets it `false` for dev only |
| `REMOTE_AUDIT_METRICS_ENABLED` | Defaults to enabled |
