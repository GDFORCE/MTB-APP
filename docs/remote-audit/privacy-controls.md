# Remote Audit Module — Privacy & Data-Minimization Controls

Status: **draft, pilot stage.**

## Location data

- Collected only at the point of a capture action (spec §7.2) — no continuous/background tracking. A live-visit session may request periodic location signals only when proportionate and disclosed (spec §12.3), never by default.
- Purpose text is shown before requesting precise location/camera/microphone permission (v1.3 correction #8).
- Multiple location samples during a single capture, if taken, are reduced to summary statistics + the chosen fix rather than all stored, to minimize retained location data (spec §7.2).
- Exact GPS coordinates never appear in general application logs — only in protected domain tables (`evidence_locations`-equivalent collection), enforced by the Phase 9 log-redaction test.

## Staff vs. patient data

- Staff identity/qualification data is kept separate from patient data; patient information is not required for site infrastructure/equipment verification.
- **Incidental PHI workflow (v1.3 correction #8):** an explicit pre-capture warning is shown in patient-care-area contexts. If a capture incidentally includes patient-identifiable content, the **original** stays under the same restricted access/retention controls as any other evidence (never modified), and a **redacted derived copy** is offered for broader reviewer access — the redaction step never mutates or replaces the original object.

## Data minimization for this deployment (pilot stage)

- No jurisdiction is currently marked `regulated_system_of_record=true` (see `jurisdiction-config.md`) — pilot data collection is scoped to what's needed to validate the workflow, not broad collection "in case it's useful later."
- India DPDP Rules 2025 mapping is deferred until a real India-jurisdiction deployment is scheduled; this document is updated at that point with the specific notice/consent/retention/access-log obligations that apply.

## Redaction and PHI-adjacent tooling

- Redacted-derived-copy generation is a Phase 4/Phase 2 worker capability (same worker infrastructure as thumbnail/derivative generation), never a synchronous main-API-process operation, consistent with the Phase-1 worker-isolation requirement.
- Access to unredacted originals containing incidental PHI follows the same resource-level ABAC as any other evidence (`remote_audit_participants`), with no broader default visibility.

## Revisit triggers

Same as `jurisdiction-config.md` — before any deployment to a jurisdiction with binding data-protection obligations (e.g. India DPDP, EU/UK equivalents if ever in scope).
