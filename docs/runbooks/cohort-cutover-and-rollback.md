# Runbook: pilot cohort cutover and rollback

**Owner:** migration lead + a second approver. **Scope:** an explicit set of at most 200 named patients of one trial. Nothing here changes `UCTSM_AUTHORITATIVE`, a trial read mode or any legacy collection, and no cohort has been cut over by this repository.

## Before anything

1. Legacy inventory approved by a named owner (`docs/legacy-consumer-inventory.md`; every row is still `PROPOSED / UNASSIGNED`).
2. Migrate the cohort's patients into the plane (`backend/scripts/migrate_legacy_patients.py`, dry run first, then `--apply`). Mirrors are created UNCLASSIFIED: the plane cannot change them yet.
3. Every member must be `RECONCILED` (parity MATCH over the same horizon as enrolment). Parity pairs repeated visit names only by a whole `visit_number` present and distinct on both sides; otherwise it reports `AMBIGUOUS_MATCH` and the patient cannot be reconciled (never override it). Check on real data that the legacy `visit_number`/`seq` lines up with the schedule before relying on this.

## Shadow -> pilot

1. `CohortCutoverService.create_shadow(principal, org, trial, name, [legacy patient ids])`. The principal needs the grant-only `cohort.cutover` capability. Shadow changes nothing.
2. `readiness(org, cohort_id)` lists per-patient reasons (`MAPPING_NOT_RECONCILED`, `PARITY_NOT_MATCH`, `ALREADY_AUTHORITATIVE_OR_CLASSIFIED`, ...).
3. Take an encrypted snapshot (`python -m app.persistence.mongodb.recovery_cli snapshot ...`) and keep its report. A **different** person with `cohort.cutover` calls `promote(..., reason, snapshot=<that report>)`; promotion is refused without a completed snapshot taken in the last 24 hours, and the snapshot hashes are written to the audit event. It re-checks readiness inside the transaction and either promotes every member or none: members become `authority_plane="MONGO"`, their previous value is recorded, and legacy schedule writes for them are refused with HTTP 409 `MIGRATED_TO_UCTSM` (route `uctsm_legacy_write_blocked_total`).
4. Only after the pilot behaves, and per trial, use the separate gated `ParityService.set_read_mode` for reads. Do not combine the steps.

## Monitoring during the pilot

`UctsmLegacyWriteAfterCutover`, `UctsmWorkflowErrors`, `UctsmHttp5xxRate` and the release criteria in `deploy/release/criteria.yml` apply. A user hitting the 409 is expected, not an incident; find their screen in the inventory and move it to the UCTSM route.

## Rollback

1. `plane_activity_since_promotion(org, cohort_id)` shows the commands, occurrence facts and evaluations written in the plane since promotion.
2. `rollback(principal, org, cohort_id, reason)` restores each member's previous authority and lifts the legacy guard. If any command or occurrence fact was recorded in the plane it REFUSES (`PLANE_CHANGES_WOULD_BE_LOST`); the legacy store never received those changes. Either reconcile them into the legacy record first, or call again with `accept_plane_changes_loss=True` after a named owner accepts the loss (recorded in the audit event).
3. Verify with `readiness` and by confirming a legacy write for a member succeeds again.

## Export and removal

The snapshot is now required for promotion (see above); also take one before any removal (`docs/uctsm-mongo-security-and-recovery.md`). Remove consumers first, then code, then data, only after the inventory shows no remaining caller and retention/hold obligations are met. No legacy code or data has been removed.

## Not built

No screen for these steps (HTTP routes exist under /api/uctsm/mongo-plane); no real rollback drill on real data; no approved window or triggers.
