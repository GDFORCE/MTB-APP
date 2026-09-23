# Remote Audit Module — RTO/RPO, Backup/Restore & Disaster Recovery

Status: **draft, pilot stage.** No production hosting environment is chosen yet (per current-stage decision: stay on `.env`/local dev now, design for AWS later). Targets below are pilot-stage placeholders, revisited once a production host is chosen — this is explicitly a Phase 10 DoD gate, not a Phase 0 blocker.

## Targets (pilot placeholder — set real numbers before production)

- **RPO (recoverable data loss window):** bounded by the existing Mongo backup cadence already in place for this application's other data (reused, not reinvented, for the new `remote_audit_*` collections) and by object-storage versioning for evidence/documents.
- **RTO (recovery time):** no formal target yet at pilot stage; a real target is set once a production host/on-call structure exists.

## Evidence-recovery mechanism

Object-store versioning (S3 versioning, once `STORAGE_BACKEND=s3` is used in production) is the primary recovery mechanism for evidence/document objects — a corrupted or accidentally-modified object is recoverable to its last-known-good version. `STORAGE_BACKEND=local` (today's dev default) has no equivalent and is explicitly called out as non-production-ready for this reason (see `object-storage.md`).

## DB/object-store recovery drill (Phase 10)

A documented drill run in Phase 10, before any production go-live:
1. Restore the Mongo database (or a representative subset including `remote_audit_*` collections) from backup.
2. Restore/verify object-store versions for a sample of evidence/document objects.
3. Confirm referential consistency between restored Mongo records and restored objects (no evidence record pointing at a missing/incorrect object version).
4. Record actual time-to-restore against the RTO target set at that time.

## Third-party provider outage handling

See `provider-dependencies.md` for the per-provider failure-mode table (v1.3 correction #15) — each optional integration (LiveKit, ClamAV, OCR, Play Integrity/App Attest, Google Places, S3) has a specific, tested degraded behavior; none of them are allowed to silently pass a control on outage.

## Secret rotation drill

See `secrets-management.md` — a rotation drill for whichever secrets exist at the time (LiveKit, S3/KMS, attestation credentials) is part of the Phase 10 DR drill once those integrations are live.
