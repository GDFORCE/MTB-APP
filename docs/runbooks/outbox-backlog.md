# Runbook: outbox backlog or job failures

**Alerts:** `UctsmOutboxBacklog`, `UctsmJobFailures` · **Owner:** platform on-call

1. Confirm a worker is running (`start-worker.bat` / process supervisor) and that leases are not stuck: expired leases are reclaimed on the next claim.
2. Check consumer health before scaling workers; a failing consumer only moves messages to the dead-letter state faster.
3. Backlog counts are published by `publish_queue_gauges`; verify it is being called by the worker loop.
