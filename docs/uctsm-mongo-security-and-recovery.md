# UCTSM MongoDB security and recovery posture (P14-04)

Scope: what the code and repository enforce today, and what only a hosting decision can provide.

## Enforced in code

- `MongoSettings.posture_issues()` returns stable codes (`TLS_REQUIRED`, `AUTHENTICATION_REQUIRED`, `CERTIFICATE_VALIDATION_DISABLED`, `RETRYABLE_WRITES_DISABLED`, `WRITE_CONCERN_NOT_MAJORITY`) for a non-local URL. With `UCTSM_ENV=staging|production` the application refuses to start on any of them (`MongoPostureError`); the protocol-identity fallback URL is checked too and a configured-but-unsafe URL never falls back to another one.
- A settings object never prints its URL (`repr` and error messages show the host list only).
- Tests refuse any non-local MongoDB.
- UCTSM uses its own database, never the legacy `DB_NAME`.
- Telemetry redacts PHI and secrets (see P13-01); the recovery CLI reports error types only.

## Roles and network (apply per environment)

`deploy/mongo/roles.json` defines `uctsm_runtime` (find/insert/update/remove), `uctsm_migrator` (adds schema actions, deploy time only) and `uctsm_backup_reader` (read only). A test checks that none holds drop, grant or `anyAction` privileges and that the runtime role cannot alter schema. `deploy/mongo/create_roles.mongosh.js` applies them. Network rules: private bind, TLS required, authentication required, one credential set per environment and role.

## Recovery

`python -m app.persistence.mongodb.recovery_cli snapshot|restore` writes an AES-256-GCM sealed logical snapshot and restores it into an ISOLATED database, reconciling counts, content hashes, indexes, validators and UUID references. The restore report now includes `elapsed_seconds` (RTO evidence) and `snapshot_age_seconds` (RPO evidence) and compares them with `UCTSM_RTO_SECONDS` / `UCTSM_RPO_SECONDS` when configured (`MET` / `NOT_MET`; `NOT_CONFIGURED` otherwise, never an invented target).

## NOT established here

TLS/network isolation of a real cluster, managed secret rotation, hosted encrypted backups and point-in-time recovery, an approved RTO/RPO, and a restore drill of production-sized data are hosting/operational work. A local drill proves the tooling, not those.
