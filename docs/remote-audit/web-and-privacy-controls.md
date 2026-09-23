# Remote Audit Module — Remaining Web/Privacy/Security Edge Controls

Status: **draft, pilot stage.** (v1.3 correction #8 — these were previously implicit; made explicit here and wired to a Phase 10 DoD gate.)

## Output sanitization / stored XSS

Every reviewer-entered free-text field this module adds — query text, CAPA comments, rejection reasons, observation notes — is rendered through the same output-encoding discipline as the rest of this app's UI (React/React Native never injects raw HTML by default, but any web-rendered rich text or markdown-like preview added for this module must explicitly sanitize before rendering). Phase 10 gate: a stored-XSS payload submitted through each of these fields must not execute when viewed by another user.

## Content-Security-Policy (web build)

The Expo-web build serves the reviewer/approver experience for this module (this app has no separate Next.js reviewer site, unlike the spec's reference architecture). A CSP is added to the web build's served HTML restricting script/style/connect sources to this app's own origin and the CDN allowlist it actually needs — reducing the blast radius of any XSS that does slip through.

## Filename sanitization

Object keys are always server-generated and opaque (see `object-storage.md`); the **display** filename shown to reviewers is the client-supplied name, sanitized before storage/display (strip path separators, control characters, and script-like extensions) so it can never be used for path traversal or log injection, and never for a stored-XSS vector via a crafted filename rendered unescaped somewhere.

## WAF / API-gateway rate limiting

The application-level rate limiting built into the Phase-1 shared mutation dependency (per-endpoint, e.g. capture-session creation) is the inner layer. A production deployment additionally puts a WAF/API-gateway rate-limit layer in front of the API as the outer layer — this is an infrastructure decision made when a production host is chosen (see `dr-plan.md`), tracked as a Phase 10 DoD gate rather than built into application code now.

## Production network-security hardening

Production mobile builds disable: debug menus, mock server endpoints, verbose/sensitive logging, and any permissive network-security config (cleartext traffic allowances, trust-all-certificate debug overrides) that might exist in a debug build. Checked in Phase 10 as part of the mobile-security test matrix.

## Incidental patient/PHI capture

See `privacy-controls.md` for the pre-capture-warning + redacted-derived-copy workflow — the original evidence object is never modified by redaction; only a derived copy is redacted for broader reviewer access.

## Web session security (v1.3 correction #6, cross-referenced from `threat-model.md`)

Sensitive remote-audit actions performed from the web build use an HttpOnly/Secure/SameSite session cookie, not a client-readable bearer token — see `threat-model.md`'s web-session-compromise row. This requires the existing CORS configuration (`allow_origins=["*"]` with `allow_credentials=True` in `server.py`) to be corrected to an explicit origin allowlist when this lands (a wildcard origin with credentials is rejected by browsers for cookie-based requests, and is poor practice regardless) — tracked as an implementation detail for the phase that builds the cookie-session path, not fixed prematurely here.
