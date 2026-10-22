# Runbook: schedule workflow or API errors

**Alerts:** `UctsmWorkflowErrors`, `UctsmHttp5xxRate` · **Owner:** platform on-call

1. Open `/health/ready`. A `plane_mongo: false` check means the Mongo patient plane is unreachable: restore connectivity first (do not restart workers until it is back).
2. Take a `correlation_id` from a failing response header (`X-Correlation-ID`) and search the structured logs and the audit trail (`metadata.correlation_id`) for it. Logs carry the error type only; never ask for or paste patient data into the incident channel.
3. Compare `versions` in `/health/ready` with the last release manifest. A mismatch after a deploy means rollback (see the release runbook once P13-05 lands).
4. Do not flip `UCTSM_AUTHORITATIVE`, the read mode or any authority flag as a mitigation.
5. Record the cause and the correlation ids in the incident ticket.
