# Runbook: legacy write attempted after cutover

**Alert:** `UctsmLegacyWriteAfterCutover` · **Owner:** migration lead

1. The write was refused (HTTP 410); no data changed. Find the client by `correlation_id` and route label (`uctsm_legacy_write_blocked_total{route}`).
2. Identify the consumer in the legacy inventory and move it to the UCTSM route. Do not disable the guard.
3. If the guard fired for a trial that should NOT be cut over, stop and escalate: `UCTSM_AUTHORITATIVE` must only be on after per-trial parity is confirmed.
