# Remote Audit Module — Object-Storage Architecture

Status: **draft, pilot stage.**

## Reuse vs. new

Reuses `backend/storage.py`'s existing `Storage` protocol (`LocalDiskStorage` / `S3Storage`, selected via `STORAGE_BACKEND`), its presigned-URL pattern (currently 15-minute TTL for S3 GET), and its lazy-boto3-import approach — no new storage abstraction is invented.

## Evidence/document object layout (spec §6.1, adapted)

```
{storage-root}/{tenant_id}/{site_id}/{audit_id}/
  originals/{evidence_id}/{opaque_object_key}
  derived/{evidence_id}/thumbnail.jpg
  derived/{evidence_id}/preview.mp4
  reports/{evidence_id}/metadata.json
  documents/{document_id}/v{n}/{opaque_object_key}
  virtual-visits/{session_id}/recording.{ext}   # only if recording policy allows
  exports/{audit_id}/audit-report-v{n}.pdf
```

- **Originals and derived artifacts live under different prefixes** (already reflected above) so derivative generation (thumbnails, redacted copies, previews) can never mutate or replace an original.
- **Object keys are unique and opaque** — never reused, never derived from a user-controlled filename (the display filename is sanitized and stored as metadata, not used as the key).
- **The database stores only the opaque object key**, never a permanent public URL. Every download goes through an authorization-checked, short-lived signed URL — reusing `storage.py`'s existing pattern.

## Corrected Object Lock requirement (v1.3 correction #5)

Not "Object Lock enabled for a prefix" as a casual toggle. For evidence/documents whose retention class requires WORM (see `retention-policy.md`):
- A **dedicated evidence storage configuration** (its own S3 bucket, or an equivalent isolated configuration if the provider only supports Object Lock at the bucket level) — kept separate from this app's existing general-purpose `files` collection/bucket, which has no WORM requirement.
- Versioning + Object Lock enabled on that configuration.
- Retention/legal-hold applied at the object level, reconciled with `retention-policy.md` before being turned on for any class.
- SSE-KMS encryption when `STORAGE_BACKEND=s3` (via `get_secret()`-sourced KMS configuration, once that exists).
- SHA-256 checksum computed server-side (in the Phase-1 worker, after the object is uploaded — see the corrected upload-intent sequencing below) and stored alongside the object, re-verified on read where practical.

## Provider-level object-access logging (required in addition to, not instead of, the app's own audit event — v1.3 correction #5)

The application's `OBJECT_ACCESS` audit event (via `write_audit()`) is necessary but not sufficient: an attacker who compromises the application can control what it logs. When S3 is the backend, **CloudTrail S3 Data Events** (or the equivalent for another provider) must be enabled so object reads/writes are captured independently of the application's own code path. This is a Phase 10 DoD gate.

## Corrected upload-intent/finalize sequencing (v1.3 correction #6)

- **Upload-intent** (bytes don't exist server-side yet): validate the *claimed* filename, claimed MIME, allowed category, claimed size against limits, and the caller's authorization — then create the unique opaque key and a short-lived, size/content-type-constrained presigned upload URL. No magic-byte or hash check happens here; it can't, because the file doesn't exist yet.
- **After upload / at finalize**: verify the object exists and that storage-reported size/checksum (where the backend exposes it) is consistent with the claim; mark `QUARANTINED`; enqueue a worker job.
- **In the worker**: real magic-byte/MIME sniff, real server-side SHA-256, malware scan, metadata extraction — only after all of this does the record leave `QUARANTINED`/`PROCESSING`.

## `STORAGE_BACKEND=local` limitation

Today's dev default (`STORAGE_BACKEND=local`) cannot provide WORM/Object Lock or provider-level access logging. The application logs a startup warning if evidence retention policy requires WORM but local storage is active. This is a hard Phase 10 DoD blocker for any production deployment that requires WORM retention — not a silent gap.
