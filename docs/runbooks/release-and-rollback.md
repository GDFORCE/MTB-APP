# Runbook: controlled release, canary and rollback

**Owner:** platform on-call + release approver

1. Build the manifest: `python scripts/release_gate.py --gate-report <report.json> --output release-manifest.json`. It records the commit, versions (policy/evaluator/schema), applied-migration checksums, the alert/criteria hashes and flag state, and fails on a dirty tree, a missing or failed gate report, or an unapproved risky flag.
2. Deploy with the new code **disabled behind its flags**, run the smoke checks (`/health/live`, `/health/ready`), then move through the canary stages in `deploy/release/criteria.yml`.
3. At each stage observe the configured minutes. `UctsmWorkflowErrors`, `UctsmHttp5xxRate`, `UctsmEvaluateLatencyHigh` or `UctsmLegacyWriteAfterCutover` firing means **roll back**; a hold alert means pause; any failed synthetic check means pause.
4. Roll back by redeploying the previous manifest's commit. Migrations are additive and are not reversed; the previous code must run against the newer schema (tests for each migration check idempotency).
5. Never flip `UCTSM_AUTHORITATIVE`, a trial read mode or `DEV_OTP_MODE` as part of a release without the documented per-trial approval.
