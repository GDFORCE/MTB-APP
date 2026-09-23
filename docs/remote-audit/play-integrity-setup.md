# Play Integrity (Android) — setup

Written for whoever configures the Google Cloud and Play Console side. The code
is complete; this is the part that needs your accounts.

## What this buys you

Everything else in the capture chain is the app vouching for itself. The server
issues a nonce, the app reports a photo, a location and a hash — and a modified
build on a rooted phone reports exactly the same shape of thing,
indistinguishably.

Play Integrity is Google vouching for the app instead: a signed statement that
this is the genuine published build on a real device. The backend verifies that
signature against Google directly. It is the one signal an attacker running a
patched APK cannot fake.

**Until this is configured, every capture is routed to human review** rather
than auto-accepted. Nothing is broken and nothing silently passes — you just
don't yet have the signal that separates "genuine app at the hospital" from
"patched app on an emulator claiming to be at the hospital".

## What is already built

| Piece | Where |
|---|---|
| Native Android module producing the token | `frontend/modules/mtb-play-integrity/` |
| JS bridge, never throws | `frontend/modules/mtb-play-integrity/index.ts` |
| Requested at capture time, bound to the session nonce | `frontend/app/(app)/site/remote-audit/capture.tsx` |
| Server-side verification and binding checks | `backend/app/services/remote_audit/attestation.py` |
| 23 tests | `backend/tests/test_remote_audit_attestation.py` |

iOS is **not** implemented. App Attest needs CBOR/COSE parsing and
certificate-chain validation against Apple's root CA, and cannot be
meaningfully tested without a real device-produced attestation object. It
reports `UNAVAILABLE`, which routes to review — the same safe outcome as an
Android device with no Play Services.

---

## Steps

### 1. Build a custom dev client

Expo Go cannot load native modules, so Play Integrity cannot produce a token
inside it, at all. `eas.json` already has a `development` profile:

```bash
npx eas build --profile development --platform android
```

Install the resulting APK on a real device. **An emulator will not produce a
passing verdict** — that is the point of the feature.

### 2. Link the app to a Google Cloud project

In **Play Console → your app → Release → App integrity → Play Integrity API**,
link a Google Cloud project.

Apps distributed through Play are linked via the Console and need nothing in
the request itself. If you ever distribute outside Play, the native module
needs `setCloudProjectNumber()` added — it is not there today, deliberately,
because it isn't needed for a Play-distributed app and an unused code path is
an untested code path.

### 3. Enable the API and create a service account

In the linked Google Cloud project:

1. Enable **Play Integrity API**
2. Create a service account with the **Play Integrity API User** role
3. Download its JSON key

The backend uses this to call `decodeIntegrityToken`. It never ships in the
mobile build — the app only produces tokens, it never decodes them. A client
that read its own verdict and reported "I'm fine" would be exactly the
self-attestation this replaces.

### 4. Configure the backend

```bash
GOOGLE_PLAY_INTEGRITY_CREDENTIALS_PATH=/secure/path/play-integrity-sa.json
GOOGLE_PLAY_INTEGRITY_PACKAGE_NAME=com.gdforce.mytrialboard
# Optional. How stale a verdict may be, in seconds. Default 600.
REMOTE_AUDIT_ATTESTATION_MAX_AGE_SECONDS=600
```

In production the JSON key belongs in the secrets manager, not on disk — read
through `get_secret()` like every other credential.

### 5. Verify

Capture a photo on the dev-client build and open the evidence in the sponsor
view. The signals panel should show `attestation_tier: VERIFIED_HIGH` instead
of `UNAVAILABLE`.

---

## What the server actually checks

A token that decodes is not a token that means something. Verification
([attestation.py](../../backend/app/services/remote_audit/attestation.py))
checks four things, and the bindings run **before** the verdict:

1. **Nonce** — the verdict echoes the nonce it was requested with, and that
   must equal the capture session's own nonce, read from the server's stored
   session and never from the client's manifest. Without this, a valid token
   lifted from one capture verifies against every other one and the control is
   decorative.
2. **Package name** — both `requestDetails.requestPackageName` and
   `appIntegrity.packageName` must be this app. A perfectly valid token from
   someone else's app proves nothing about ours.
3. **Freshness** — a verdict from yesterday says nothing about now.
4. **The verdict itself** — and only then.

A token that fails any binding is reported `TAMPERED`, not `VERIFIED`, **even
when the device verdict is perfect**. "Google signed something" is not "Google
signed this".

## The four tiers

| Tier | Means | Outcome |
|---|---|---|
| `VERIFIED_HIGH` | `MEETS_DEVICE_INTEGRITY` — genuine build, real device | Contributes to a pass |
| `VERIFIED_MEDIUM` | Recognized but below the strongest tier, e.g. an old institutional tablet | Review-tier |
| `TAMPERED` | Modified/unrecognized build, **or** a mis-bound verdict | Configurable: review or hard block |
| `UNAVAILABLE` | Not configured, no Play Services, provider outage, or no token | Review-tier |

`VERIFIED_MEDIUM` and `TAMPERED` are kept apart deliberately (plan correction
#8). A hospital's five-year-old Android tablet is not an attacker, and grouping
it with a patched APK would either punish the hospital or excuse the attacker.

## Policy flags

Both live in `verification.py`:

- `treat_attestation_unavailable_as_review` — defaults `True`. Right while
  attestation is unavailable for *every* capture. Once it's live and
  "unavailable" means a rare Google outage rather than the permanent state,
  consider relaxing it.
- `block_on_tampered_app` — defaults `False`, so a tampered verdict routes to
  review rather than blocking outright. Turn it on once you have enough real
  telemetry to know your false-positive rate.

## Known limits

- **The nonce is passed as hex, not re-encoded.** The server's
  `secrets.token_hex(16)` is 32 URL-safe characters, inside Google's 16–500
  range. Re-encoding it to base64url would add an encoding both sides must
  agree on, and a mismatch there fails in a way that looks like an attack.
  There's a test pinning the nonce format so a future change fails loudly.
- **No Standard (warm-up) API.** The Classic request is the right shape for a
  one-off high-value action and takes our server nonce directly. If capture
  volume ever makes the latency hurt, Standard is the migration — and it uses
  a request *hash* rather than a nonce, so the binding check changes with it.
- **Play Services is required.** A device without it (some institutional
  hardware, most emulators) gets `ERR_INTEGRITY_REQUEST_FAILED` → `UNAVAILABLE`
  → review. That is correct, not a bug.
- **Rate limits apply.** Classic requests are quota-limited per app per day.
  Evidence capture is low-volume, but worth watching if a site does a
  hundred-photo sweep.
