# Remote Audit Module — Incident Response

Status: **draft, pilot stage.**

## Paging contact

**You** — sole contact at this solo/pilot stage. Revisit once there's a real ops team; this document should be updated the moment that changes, since a stale contact is worse than no documented contact.

## What pages (once Phase 9 monitoring/alerting is live)

| Event | Why it matters | Immediate response |
|---|---|---|
| `MALWARE_DETECTED` | A malicious file reached the pipeline | Confirm the file stayed blocked (never reviewer-visible); check for other uploads from the same session/user around the same time |
| `REPLAY_REJECTED` (repeated, same user/session pattern) | Possible active attack against the capture-session nonce mechanism | Check the account's recent activity; consider forcing re-authentication |
| Repeated `ATTESTATION_FAILED` from one device/app-version | Possible compromised device or a bad app release, not necessarily fraud | Distinguish tampered-app vs. old-OS pattern (v1.3 correction #8) before deciding whether to widen the block policy |
| Cross-tenant authorization-denial spike | Possible probing for an IDOR/authorization gap, or a client bug leaking wrong IDs | Check whether it's one account (probing) or many (client bug); do not assume malice without checking |
| Secrets-manager access-log anomaly (once a secrets manager is in use) | Possible credential compromise | Rotate the affected secret per `secrets-management.md`'s break-glass procedure |

## Scope note

This module reuses the existing application's audit-log (`db.audit_logs`, via `write_audit()`) and does not stand up a separate incident-tracking system. At pilot stage, "response" means you personally reviewing the relevant audit events — this document exists so that expectation is written down, not assumed.

## Revisit trigger

Before Phase 9 (Observability & Security Monitoring) is considered complete, this document is updated with concrete alert thresholds matching whatever metrics backend Phase 9 actually implements.
