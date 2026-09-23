# Remote Audit Module — Threat Model & Abuse Cases

Status: **draft, pilot stage.** Deployment stage: early dev/pilot only (no live regulator-facing deployment yet). Revisit before any production rollout.

Source: spec §3.2 ("Threat model") and §16 ("Failure Modes and Edge Cases"), adapted to this codebase's actual attack surface (MongoDB via `server.py`, not the spec's assumed PostgreSQL/PostGIS stack).

## Actors (spec §3.1, mapped to this app's roles — see `capabilities.py`)

| Spec actor | This app's role | Notes |
|---|---|---|
| Hospital/Site Admin | `site` + `org_admin=true` | manages site profile, nominates users |
| Site Evidence Capturer | `site` / `pi` / `crc` | live capture only; cannot approve own evidence |
| Reviewer/Auditor | `sponsor` / `cro` | org membership gets a minimal audit list; details/actions require an explicit `remote_audit_participants` grant on that audit |
| Approver/Regulatory Admin | `sponsor` / `cro` + `org_admin=true` | explicit `approver`/`geofence_approver` grant, production-MFA-backed step-up required for sensitive actions |
| Platform Admin | `admin` | technical administration only — never implicitly gets reviewer/approver/template-admin capability |
| System/Worker | service identity (no human user) | every automated result carries `rule_set_version` |

## Threats and controls

| Threat source | Example attack | Control | Where implemented |
|---|---|---|---|
| Dishonest site user | Uploads an old gallery photo as if live; reuses another site's evidence; alters EXIF | Live-capture challenge (server nonce), fresh device location, metadata comparison, exact/duplicate-hash detection, `LIVE_IN_APP` vs `GALLERY_HISTORICAL` confidence-ceiling split, no offline high-trust capture in v1 | Phase 2 |
| Compromised/rooted/jailbroken device | Hooks location APIs, bypasses mock-location flag, modifies the app | Play Integrity / App Attest as one weighted signal (never sole authority), server-side content binding, tiered trust, anomaly review — never rely on root detection alone | Phase 2 (server verification), Phase 8 (native bridges) |
| Malicious file | PDF exploit, malware, decompression bomb, spoofed MIME | Mandatory quarantine before any reviewer visibility, magic-byte sniffing after upload (never before bytes exist — see correction #6), malware scan in an isolated worker, sandboxed parsing, timeouts/resource caps | Phase 1 (worker), Phase 2/4 (pipelines) |
| Unauthorized reviewer / cross-org access | Views another hospital's evidence, or a sponsor's own reviewer views a different sponsor's audits, or acts on an audit they weren't assigned to | `remote_audit_participants` resource-level ABAC, tenant-scoped queries, object-key authorization, short-lived signed URLs, cross-organization tenant tests | Phase 1 (model), Phase 2+ (endpoints), Phase 10 (test sweep) |
| Insider/admin misuse | Deletes evidence or changes policy after a decision | Append-only audit events (atomic with the state change — Mongo transaction or outbox), immutable object versions, Object Lock where retention policy requires it, separation of duties for sensitive overrides | Phase 1 (audit/storage), Phase 3/7 (policy actions) |
| Replay/automation | Reuses a successful attestation or capture request | Nonce/expiry, idempotency keys (universal, not conditional — v1.3 correction #1), request/content binding, server-side replay checks, rate limiting | Phase 1 (shared mutation dependency), Phase 2 |
| Web session compromise (new to this deployment — spec assumed a separate Next.js reviewer web app; this app serves reviewers from the same Expo-web build) | Stolen bearer token from browser storage used to perform a sensitive action | Sensitive remote-audit actions on the web build require an HttpOnly/Secure/SameSite cookie session, not a client-readable bearer token (v1.3 correction #6); CSRF/SameSite protection on state-changing requests | Phase 0 decision, Phase 2 implementation, Phase 10 test |
| Stored XSS via reviewer-entered text | A query/CAPA comment or observation note contains a script payload | Output sanitization/encoding on all free-text fields, CSP on the web build | Phase 0 baseline, Phase 10 gate |

## Trust hierarchy (spec §3.3, preserved exactly)

1. Server-observed values (receipt time, server-generated nonce, independently computed hashes, storage checksums, server geospatial calculations) are authoritative.
2. OS/platform signals (Play Integrity, App Attest, Android `isMock`, iOS `sourceInformation`, location accuracy, app version) are persisted as evidence, not proof.
3. EXIF is supporting metadata only — never tamper-proof (ExifTool itself can write geotags).
4. IP geolocation is low-weight context only.
5. Reviewer judgment and a documented override remain necessary for ambiguous evidence.

This precedence order is enforced as a concrete unit test on the verification engine (`app/services/remote_audit/verification.py`, Phase 2), not just documented here.

## MFA policy (v1.3 correction #2)

- **Development/pilot (current stage):** a password-based step-up token is an acceptable fallback for sensitive actions.
- **Production:** any deployment marked production requires `stepup_requires_mfa=true` by default — step-up tokens for final decision, geofence approval, template activation, or critical override must come from an MFA-authenticated session (org IdP or a platform MFA implementation). No MFA exists anywhere in this codebase today; this module does not build general-purpose MFA — it only requires that when platform-wide MFA exists, this module's step-up issuance is gated on it.

## Revisit triggers

- Before any deployment marked "production" (see Phase 10 DoD).
- Before onboarding a sponsor/hospital pair with a real regulator-facing audit.
- After any incident logged per `incident-response.md`.
