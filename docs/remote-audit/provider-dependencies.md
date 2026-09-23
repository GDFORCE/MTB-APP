# Remote Audit Module — Third-Party Provider Responsibility & Failure Model

Status: **draft, pilot stage.**

## Principle

No third-party provider outage may ever cause a control to be silently passed. Each provider below has a **specific**, tested failure behavior — not one generic "degrade to REVIEW" (v1.3 correction #15 — that wording was imprecise in an earlier draft of this plan and has been corrected everywhere it appears).

## Per-provider behavior

| Provider | Used for | Credential/account owner | Behavior on outage/unavailability |
|---|---|---|---|
| Google Play Integrity | Android device/app attestation | you (Google Cloud project, enabled when Phase 8 starts) | `REVIEW`/fallback tier — attestation is one weighted signal, never a hard blocker, but never silently treated as passed either |
| Apple App Attest | iOS device/app attestation | you (Apple Developer account, enabled when Phase 8 starts) | Same as Play Integrity — `REVIEW`/fallback tier |
| ClamAV (or equivalent malware scanner) | Evidence/document malware scanning | not yet provisioned (pilot stage) | File **remains `QUARANTINED`/`PROCESSING_BLOCKED`** — never becomes reviewer-visible. Stricter than REVIEW: REVIEW would still let a reviewer see it; this does not. |
| Tesseract / OCR provider | Document field pre-fill | not yet provisioned (pilot stage; local `pytesseract` if the binary is present) | Document processing continues without OCR pre-fill; reviewer enters/confirms fields manually — not a processing failure |
| LiveKit | Virtual-visit video/audio | you (LiveKit Cloud project or self-hosted server, provisioned when Phase 5/6 starts) | Virtual-visit scheduling/join is unavailable; user is told to reschedule. The rest of the audit (documents, queries, evidence, scoring) remains fully usable |
| Google Places | Address geocoding at site enrollment | already provisioned (`GOOGLE_PLACES_API_KEY`, existing integration) | Address entry falls back to manual entry + manual map pin; nothing silently guesses a location |
| Google Maps Static API | Read-only pin/boundary preview image shown to the sponsor before Approve (Phase 3) | you (same Google Cloud project as Places — needs this API enabled separately) | Approval screen shows the geocoded address as text with no image rather than failing to load the screen; approval by address/coordinates alone is still possible |
| OpenStreetMap Overpass API | Attempting to find a REAL hospital/campus boundary before falling back to a circle (Phase 3) | none — public instance, no account; `OSM_OVERPASS_URL` swappable to a mirror/self-hosted instance later | No match, invalid geometry, or a timeout/outage all fall back to the circle-with-default-radius flow — this is the expected common case (OSM coverage is inconsistent), never surfaced to the sponsor as an error |
| Object storage (S3, once used) | Evidence/document storage | you (AWS account, when production hosting is chosen) | Upload-intent and finalize are safely blocked with a clear error — never a silent partial acceptance |
| Secrets manager (AWS Secrets Manager, once used) | Production credential storage | you (AWS account, when production hosting is chosen) | Application fails to start / a specific secret-dependent feature is disabled with a clear error — never falls back to a hardcoded/default secret |

## Circuit-breaker behavior

Each integration above is called through a thin client with a timeout and a "provider unavailable" exception type distinct from "provider said no" — callers branch on that distinction to apply the correct behavior from the table, rather than treating every exception the same way. Provider status is checked/logged so repeated failures are visible in Phase 9 monitoring (`attestation_unavailable_rate`, etc.) rather than only surfacing as individual request failures.

## Ownership

At pilot stage, all of the above credential/account ownership items are yours. This is revisited once there's a team with distinct provider-owner responsibilities.
