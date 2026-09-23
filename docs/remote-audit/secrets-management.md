# Remote Audit Module — Secrets / KMS / Key-Management Strategy

Status: **draft, pilot stage.** Decision: stay on `.env` for local development now; design for AWS Secrets Manager as the production target (chosen because this repo already uses `boto3`/S3 elsewhere, so staying within the AWS ecosystem avoids introducing a second cloud vendor for one module). Provisioning AWS Secrets Manager itself is a Phase 10 gate, not a Phase 0 blocker.

## Current state (pilot/dev)

`.env` (via `python-dotenv`, already the pattern for every existing secret in this app — `JWT_SECRET`, `ANTHROPIC_API_KEY`, `AWS_ACCESS_KEY_ID`, etc.) remains fine for local development and controlled non-production environments. New secrets this module introduces follow the same `.env` convention at pilot stage: `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`, `CLAMAV_HOST`/`CLAMAV_PORT`, `PLAY_INTEGRITY_*`, `APPLE_APP_ATTEST_*`.

## Production target

All of the following move to **AWS Secrets Manager** (or AWS SSM Parameter Store for lower-sensitivity config) for any deployment marked production:
- LiveKit API secret
- S3/KMS credentials (beyond what's already required for `STORAGE_BACKEND=s3`)
- Play Integrity / App Attest service credentials
- Malware-scanner credentials (if the chosen scanner needs any)
- Database secrets

## Access abstraction

All application code reads secrets through one function, `get_secret(name: str) -> str`, implemented once (`backend/app/services/remote_audit/secrets.py`, added when the first secret-dependent integration lands — Phase 5/6 for LiveKit is the first real candidate). In dev, it reads `os.environ`. In production, the same function reads from AWS Secrets Manager. Swapping the backing store touches this one module, not every call site.

## Requirements for production (Phase 10 DoD gate)

- **Rotation:** a documented procedure per secret, exercised at least once in the Phase 10 DR drill.
- **Least privilege:** each secret's IAM policy grants access only to the specific service/role that needs it — never a blanket "read all secrets" credential.
- **Access logging:** every secret read is logged (CloudTrail, for AWS Secrets Manager) independently of the application's own logs.
- **Break-glass procedure:** a documented emergency-access path when the normal rotation/access flow is unavailable, reviewed and understood before production go-live.
- **No secrets in mobile builds:** already true today (confirmed by research — no AWS/API keys found in `frontend/`) and preserved by this module; nothing added here changes that.
- **No secrets committed to source control:** enforced by the Phase 10 secret-scanning gate (SAST/secret-scanning pipeline step).

## Open decision

Exact AWS Secrets Manager configuration (regions, KMS key for secret encryption, IAM role boundaries) is decided when a production AWS account/environment exists — not before, per the current-stage decision to defer infra provisioning until it's actually needed.
