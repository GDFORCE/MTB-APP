# Local encrypted recovery drill

Status: bounded engineering recovery tooling, 19 October 2026. Owner: the engineer running the isolated local drill. There is no configured production RTO/RPO, hosted PITR, object-store/configuration backup or production restore approval in this record.

## Scope and prerequisites

`app.persistence.mongodb.recovery` exports portable JSON and collection schema/index metadata from one replica-set snapshot transaction. It records document counts/hashes and checks declared UUID, tenant, patient and version references. The catalog must stay unchanged and no migration may be RUNNING. The in-memory logical payload limit is 16 MiB; larger data needs a separately designed streaming/hosted backup implementation. Do not increase this limit to disguise a capacity failure.

The encrypted file uses AES-GCM with a 32-byte key, fresh nonce and authenticated format identifier. Source files are created exclusively; an existing backup is never overwritten. The CLI reads the key and local Mongo connection from `UCTSM_RECOVERY_KEY_B64` and `UCTSM_RECOVERY_MONGO_URL`. Do not put either value in a committed command, report, screenshot or shell transcript. Recoverability requires retaining the managed key separately from the encrypted file. The local synthetic test may use an ephemeral key; a real recovery plan needs documented escrow, access, rotation and recovery responsibility.

Run from `backend/`. Select an explicitly named synthetic source database; the CLI does not create a missing source. Set both environment variables securely in the process, then:

```powershell
python -m app.persistence.mongodb.recovery_cli snapshot --database uctsm_test_named_synthetic_source --file D:/mtb_tmp/recovery/synthetic-snapshot.enc --report D:/mtb_tmp/recovery/snapshot-report.json
python -m app.persistence.mongodb.recovery_cli restore --database uctsm_restore_pdf_drill_20261019 --file D:/mtb_tmp/recovery/synthetic-snapshot.enc --report D:/mtb_tmp/recovery/restore-report.json
```

Replace the source name with the actual isolated fixture database. Use a new backup filename and new restore database for each attempt. Restore names must match `uctsm_restore_[a-z0-9_]{6,80}`. A populated target is refused. The tool neither overwrites nor repoints an active application database. The CLI accepts local Mongo hosts and requires a replica set; it is not a hosted-backup integration.

## Verification and failure handling

Exit 0 means the requested local operation completed; exit 1 means failure. A successful restore recreates validators/indexes, imports documents, resnapshots the isolated target and reconciles counts, hashes, declared references and catalog. Inspect both JSON reports and retain their hashes. Measure snapshot duration, restore duration and the age of the captured snapshot if conducting an operational exercise; do not call an unconfigured target met. The report keeps `production_pitr_verified=false`. It now records elapsed time and snapshot age and can compare them with explicitly configured `UCTSM_RTO_SECONDS` and `UCTSM_RPO_SECONDS`, returning MET, NOT_MET or NOT_CONFIGURED. Test targets and a tiny synthetic drill do not establish approved production objectives.

An invalid key, tampered ciphertext, oversized input, orphan reference, changed catalog or nonempty restore target stops the operation. Failed imports may leave an isolated partial target for investigation. Do not redirect the application, retry into that target, delete the source, or erase the failed target as part of this command. Investigate with count/hash/reference evidence first and use a newly named isolated target for a repaired retry.

Run `python -m pytest -q tests/test_pdf_recovery.py` with a required local replica set for the codec, encryption/tamper, isolation and real-Mongo restore regressions. These tests cover a bounded fixture; they do not establish recovery of every production collection/history relationship or a live workload.

## Before production recovery can be accepted

Complete the hosting-specific backup/PITR design; least-privileged access/TLS/private connectivity; managed key and restore credentials; object/archive/configuration backups; immutable retention and holds; all lineage/facts/commands/evaluations/outbox/reminder reconciliation; a measured restore at representative scale; and owner-selected RTO/RPO and rollback/cutover approval. Keep restoration isolated until the separately authorized operational decision. This runbook authorizes none of those production actions.
