# Runbook: restore drill

**Alerts:** `UctsmRestoreDrillFailed`, `UctsmRestoreDrillOverdue` · **Owner:** platform on-call

A drill is `app.persistence.mongodb.drill.run_drill(client, source_db, key=..., workdir=..., archive_root=..., rto_seconds=..., rpo_seconds=...)`. It
takes an encrypted logical snapshot, restores it into an isolated `uctsm_restore_*` database, reconciles counts/hashes/indexes/references, verifies
every archived evaluation payload in the archive store (exists, authenticates, matches its hash and counts), records the configuration manifest
(migration checksums plus hashes of the alert, routing, release-criteria and role files; never environment values) and compares the elapsed time and
snapshot age with the approved RTO/RPO. It sets `uctsm_restore_drill_status` and `uctsm_restore_drill_last_success_timestamp_seconds`.

1. **Failed:** read `problems` in the report. `RESTORE_NOT_VERIFIED` means the snapshot did not reconcile (do not trust the backup path until fixed);
   `ARCHIVE_OBJECTS_NOT_VERIFIED` means an archived payload is missing, damaged or encrypted with another key (restore the object store from its own
   backup, never edit metadata); `RTO_RPO_NOT_MET` means the target is not achievable as configured (raise it with the approver, do not edit the target).
2. **Overdue:** the scheduler stopped. Run a drill by hand and fix the schedule; a drill older than 8 days is not evidence.
3. The drill drops its restored database unless `keep_restored=True`; it never touches the source or the live application.

Not covered by this drill: hosting-provider point-in-time recovery, the real object store's own backups, and production-sized data. Those need the hosting environment.
