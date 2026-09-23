# Remote Audit Module — Queue/Worker Architecture

Status: **draft, pilot stage.**

## Why a new queue, and why not a message broker

No message broker (Redis/RabbitMQ/SQS) exists anywhere in this codebase today. Introducing one as new required infrastructure for a single module is a heavier ask than this codebase's current operational model (a FastAPI + MongoDB monolith). Instead: a **Mongo-backed durable job queue**, consumed by a separate worker **process** (never in-request, never in the API process).

## Design

- `remote_audit_jobs` collection: `{id, job_type, payload, status (queued|claimed|processing|done|failed), attempts, max_attempts, claimed_by, claimed_at, lease_expires_at, created_at, updated_at, correlation_id}`.
- `remote_audit_jobs_dlq` collection: jobs that exhausted `max_attempts`, moved here with full failure history — never silently dropped.
- Atomic claim via `findOneAndUpdate` on `{status: 'queued', ...}` → `{status: 'claimed', claimed_by, lease_expires_at}`, so two workers can never claim the same job.
- A reconciliation sweep periodically re-queues jobs whose `lease_expires_at` has passed without completion (the worker that claimed it died or hung) — this is what makes "isolated worker" safe rather than a single point of silent failure.
- Retry policy: exponential backoff between attempts, capped at `max_attempts`, then dead-letter.
- Behind a small `JobQueue` interface (`enqueue`, `claim`, `complete`, `fail`, `dead_letter`) so swapping to SQS/RabbitMQ later — once volume justifies the operational cost of running a broker — is a driver change, not a rewrite.

## Isolation: development vs. production (v1.3 correction #4)

**Development (current stage):** the worker runs as a separate OS process (`backend/remote_audit_worker.py`), with a subprocess + wall-clock-timeout wrapper around each parse job, a filesystem write-scope limited to a quarantine temp directory, and no special network restriction beyond what the dev machine already has. This is good enough for local development and is explicitly **not** the production bar.

**Production (Phase 10 DoD gate):** the worker runs in an isolated container with:
- a non-root user,
- a read-only root filesystem,
- a dedicated writable quarantine volume (nothing else writable),
- a CPU limit, a memory limit, a process/PID limit,
- a hard execution timeout per job,
- no default outbound network access (an explicit allowlist only for what a given job type genuinely needs — e.g. the ClamAV daemon host),
- minimum IAM/cloud permissions (read/write only the evidence/document object-storage prefixes it needs — never broad database or account-wide credentials),
- patched parser libraries, tracked by the Phase 10 SCA gate.

**The API process never parses untrusted evidence/media/documents directly, in any environment** — every parse happens in the worker, reachable only through the queue. This is true from Phase 1 onward, not just in production.

## Job types (added incrementally as phases land)

- `evidence.verify` (Phase 2)
- `document.process` (Phase 4)
- `retention.sweep` (Phase 1, periodic)
- `virtual_visit.egress_reconcile` (Phase 6)

## Tests (Phase 1)

- enqueue → claim → complete happy path.
- enqueue → claim → worker "dies" (simulated by not completing before lease expiry) → reconciliation sweep re-queues it → a second claim succeeds.
- max-attempts exceeded → job lands in the DLQ with its full failure history, not silently dropped.
