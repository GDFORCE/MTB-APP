# Remote Audit Module — Retention & Legal-Hold Policy

Status: **draft, pilot stage.** Enforced at runtime by the Phase-1 retention-enforcement job (`app/services/remote_audit/retention.py`, built in Phase 1) — this document defines the policy; the job (and its tests) prove the policy is actually applied, per v1.3 correction #14.

## Principle

Retention is defined **per evidence/document class**, never as one blanket rule, and is reconciled with Object Lock/WORM configuration *before* WORM is ever turned on for a class (spec §16.12, §17.4). Object Lock, once active on an object, can make deletion technically impossible even when policy calls for it — the system must always report the true state, never claim a deletion succeeded when it didn't.

## Retention classes (initial, pilot defaults — tune per jurisdiction/template before production)

| Evidence/document class | Default retention | Legal-hold override | WORM required? |
|---|---|---|---|
| Live-captured evidence (photo/video, `LIVE_IN_APP`) bound to a finalized audit | Life of the audit + configurable regulatory tail (template-defined; no universal number) | Yes — a legal hold blocks deletion regardless of elapsed retention | Only if the audit's jurisdiction/template requires WORM |
| Gallery/historical evidence (`GALLERY_HISTORICAL`) | Same as above | Yes | Same as above |
| Documents (certificates, SOPs, calibration records) | Per document type + expiry rules (spec §10) | Yes | Same as above |
| Derived artifacts (thumbnails, previews, OCR text) | Same lifecycle as their original; deleted when the original's retention expires and no hold applies | No independent hold — derived from the original | Never — derived artifacts are never under Object Lock themselves |
| Virtual-visit recordings (when recording is enabled) | Per jurisdiction/template; defaults to **no recording**, so nothing to retain unless explicitly enabled | Yes | Only if enabled and required |
| Audit events (`db.audit_logs`) | Indefinite — never auto-deleted by this module | N/A (never deleted programmatically) | N/A — protected by access control (no update/delete endpoint), not Object Lock |

## Retention-enforcement job behavior (built in Phase 1, per v1.3 correction #14)

For each retained item, on a scheduled sweep:
1. Compute retention expiry from its class + the audit's `template_version`/policy-version at time of capture (a later policy change never retroactively changes a past item's computed expiry — the policy version is snapshotted).
2. Check legal-hold status. If held, the item is **never** touched regardless of age, and this is logged (not silently skipped).
3. Check Object Lock/WORM state before attempting deletion. If Object Lock prevents deletion, the system reports the item as **retention-expired-but-immutable**, never as "deleted."
4. If expired, unheld, and not Object-Locked: mark archival/delete-eligible; deletion (where legally and technically permitted) is a distinct, audited action — never automatic without this check sequence.
5. Emit an audit event for every retention action (including "deletion attempted but blocked"), referencing the policy version in force.
6. An item still referenced by an unresolved audit or open CAPA is excluded from archival/deletion regardless of age.

## Required tests (Phase 1)

- expired + no hold → eligible for retention action.
- expired + legal hold → deletion blocked, reported as blocked.
- active Object Lock → deletion remains impossible; system status matches reality.
- policy-version change → itself versioned and audited.
- evidence referenced by an unresolved audit/CAPA → excluded from archival/deletion regardless of age.

## Open decision

Exact retention durations per class are **template/jurisdiction-owned configuration**, not hard-coded here — this document defines the mechanism and defaults for pilot use, not a permanent number. Revisit before any real regulator-facing deployment (see `jurisdiction-config.md`).
