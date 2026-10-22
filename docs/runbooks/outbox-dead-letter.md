# Runbook: dead-lettered outbox messages

**Alert:** `UctsmOutboxDeadLetters` · **Owner:** platform on-call

1. List dead messages with `DeadLetterService.list_dead` (needs the grant-only `outbox.replay` capability; org admins do not hold it by default).
2. Find the cause from `last_error_code` (`PERMANENT_DELIVERY_FAILURE`, `RETRYABLE_DELIVERY_FAILURE`, `LEASE_RETRY_LIMIT`). Fix the consumer or provider first.
3. Replay one message with a written reason via `DeadLetterService.replay`. The action is audited (`OUTBOX_DEAD_LETTER_REPLAYED`). The consumer inbox suppresses a second business effect if the consumer had already processed it.
4. Never edit outbox rows directly.
