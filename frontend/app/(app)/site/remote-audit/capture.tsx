// Site-side evidence capture — spec §8 (capture/geo-validation protocol).
//
// Three deliberate constraints that look like missing features but aren't:
//
// 1. THERE IS NO GALLERY PICKER HERE. `LIVE_IN_APP` evidence means the bytes
//    came from this app's camera during an open, server-issued session. A
//    "choose from photos" button would let any saved image enter under a
//    trust level it hasn't earned. Historical documents go through the
//    document upload flow (a separate, lower-ceiling `capture_mode`).
//
// 2. THE SESSION IS ARMED BEFORE THE SHUTTER, NOT AFTER. The server's nonce
//    has to exist before the photo does, otherwise it binds nothing (v1.3
//    correction #5 — no offline path can produce LIVE_IN_APP evidence). That
//    is why this screen can't be used offline and says so plainly.
//
// 3. THIS SCREEN NEVER SAYS "VERIFIED". It reports what it captured and what
//    the server accepted for processing. The verdict comes from the backend
//    verification engine, asynchronously, and is shown in the reviewer's
//    evidence view.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, View } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { useLocalSearchParams, useRouter } from "expo-router";
import {
  AlertTriangle, Camera as CameraIcon, CheckCircle2, MapPin, RefreshCw, ShieldCheck,
} from "lucide-react-native";

import { Body, Button, Card, Small } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { colors, radii, spacing } from "@/src/theme/tokens";
import {
  createCaptureSession,
  createUploadIntent,
  finalizeCapture,
  sendBytes,
} from "@/src/features/remote-audit/api";
import {
  acquireFreshLocation,
  attestCapture,
  buildCaptureManifest,
  fileSize,
  isPlayIntegrityAvailable,
  LocationUnavailable,
  sha256OfFile,
} from "@/src/features/remote-audit/capture";
import type { CaptureLocation, CaptureSession, EvidenceItem } from "@/src/features/remote-audit/types";

const MEDIA_TYPE = "image/jpeg";

type Step =
  | { kind: "arming" }
  | { kind: "ready" }
  | { kind: "working"; label: string }
  | { kind: "done"; evidence: EvidenceItem }
  | { kind: "error"; message: string; retryable: boolean };

function secondsUntil(iso: string): number {
  const ms = new Date(iso).getTime() - Date.now();
  return Number.isFinite(ms) ? Math.max(0, Math.floor(ms / 1000)) : 0;
}

function errorMessage(err: unknown): string {
  const res = (err as any)?.response;
  const detail = res?.data?.detail ?? res?.data?.message;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong. Please try again.";
}

export default function RemoteAuditCaptureScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{
    auditId?: string;
    linkedEntityType?: string;
    linkedEntityId?: string;
    label?: string;
  }>();
  const auditId = String(params.auditId ?? "");
  const linkedEntityType = String(params.linkedEntityType ?? "facility");
  const linkedEntityId = String(params.linkedEntityId ?? "");
  const label = String(params.label ?? "Evidence photo");

  const cameraRef = useRef<CameraView>(null);
  const [permission, requestPermission] = useCameraPermissions();
  const [session, setSession] = useState<CaptureSession | null>(null);
  const [step, setStep] = useState<Step>({ kind: "arming" });
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [location, setLocation] = useState<CaptureLocation | null>(null);
  const [locationError, setLocationError] = useState<string | null>(null);

  // ── Arm a server-issued session (this is what makes the capture live) ────
  const arm = useCallback(async () => {
    if (!auditId || !linkedEntityId) {
      setStep({ kind: "error", message: "This screen was opened without an audit and entity to attach evidence to.", retryable: false });
      return;
    }
    setStep({ kind: "arming" });
    try {
      const created = await createCaptureSession(auditId, {
        linkedEntityType,
        linkedEntityId,
        requestedMediaType: MEDIA_TYPE,
        captureMode: "LIVE_IN_APP",
      });
      setSession(created);
      setStep({ kind: "ready" });
    } catch (err) {
      setStep({ kind: "error", message: errorMessage(err), retryable: true });
    }
  }, [auditId, linkedEntityId, linkedEntityType]);

  useEffect(() => { void arm(); }, [arm]);

  // ── A live location preview, so the user isn't guessing pre-shot ─────────
  const refreshLocation = useCallback(async () => {
    setLocationError(null);
    try {
      setLocation(await acquireFreshLocation());
    } catch (err) {
      setLocation(null);
      setLocationError(err instanceof LocationUnavailable ? err.message : errorMessage(err));
    }
  }, []);

  useEffect(() => { void refreshLocation(); }, [refreshLocation]);

  // ── Session expiry countdown (TTL is 5 minutes server-side) ──────────────
  useEffect(() => {
    if (!session) return;
    const tick = () => setSecondsLeft(secondsUntil(session.expiresAt));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [session]);

  const expired = Boolean(session) && secondsLeft <= 0;

  const capture = useCallback(async () => {
    if (!session || !cameraRef.current) return;
    try {
      setStep({ kind: "working", label: "Capturing…" });
      const photo = await cameraRef.current.takePictureAsync({ quality: 0.9, exif: true, skipProcessing: false });
      if (!photo?.uri) throw new Error("The camera returned no image.");
      const capturedAtMs = Date.now();

      // Fresh fix taken AT capture time — not the preview's cached reading.
      setStep({ kind: "working", label: "Reading location…" });
      let fix: CaptureLocation | null = null;
      try {
        fix = await acquireFreshLocation();
        setLocation(fix);
      } catch {
        // A missing fix is not a client-side abort: the server records it as
        // LOCATION_MISSING and routes the item to review. Silently dropping
        // the capture here would lose real evidence from a basement or a
        // shielded ward, which is exactly where audits happen.
        fix = null;
      }

      // Attestation is requested with the SERVER's session nonce, so the
      // resulting token is bound to this one capture and can't be replayed
      // against another. It never aborts the capture: an unavailable provider
      // means the evidence goes to human review, not that it's lost.
      setStep({ kind: "working", label: "Checking device…" });
      const attestation = await attestCapture(session.nonce);

      setStep({ kind: "working", label: "Hashing image…" });
      const [sha256Client, size] = await Promise.all([sha256OfFile(photo.uri), fileSize(photo.uri)]);

      setStep({ kind: "working", label: "Reserving upload…" });
      const intent = await createUploadIntent(session.id, {
        fileName: `${label.replace(/[^a-zA-Z0-9-_ ]/g, "").trim() || "evidence"}.jpg`,
        mimeClaimed: MEDIA_TYPE,
        sizeClaimed: size,
      });

      setStep({ kind: "working", label: "Uploading…" });
      await sendBytes(session.id, intent, { uri: photo.uri, name: "evidence.jpg", type: MEDIA_TYPE });

      setStep({ kind: "working", label: "Submitting…" });
      const evidence = await finalizeCapture(session.id, {
        objectKey: intent.objectKey,
        sha256Client,
        captureManifest: buildCaptureManifest({
          sessionNonce: session.nonce,
          location: fix,
          capturedAtMs,
          playIntegrityToken: attestation.token,
          attestationUnavailableReason: attestation.reason,
        }),
      });
      setStep({ kind: "done", evidence });
    } catch (err) {
      // The session is single-use server-side; once finalize has been
      // attempted the only safe recovery is a brand-new session, so the
      // retry path re-arms rather than reusing this one.
      setStep({ kind: "error", message: errorMessage(err), retryable: true });
    }
  }, [session, label]);

  // ── Permission gate ─────────────────────────────────────────────────────
  if (!permission) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Capture evidence" />
        <View style={s.centered}><ActivityIndicator color={colors.primary} /></View>
      </ScreenContainer>
    );
  }

  if (!permission.granted) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Capture evidence" />
        <View style={s.centered}>
          <Card style={{ gap: spacing.sm }}>
            <Body weight="600">Camera access is required</Body>
            <Small>
              Audit evidence has to be taken with this app&apos;s camera during an open capture
              session. Photos chosen from your gallery can&apos;t be accepted as live evidence.
            </Small>
            <Button onPress={() => { void requestPermission(); }}>Allow camera</Button>
          </Card>
        </View>
      </ScreenContainer>
    );
  }

  // ── Result screen ───────────────────────────────────────────────────────
  if (step.kind === "done") {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Evidence submitted" />
        <ScrollView contentContainerStyle={s.body}>
          <Card style={{ gap: spacing.sm }}>
            <View style={s.row}>
              <CheckCircle2 size={20} color={colors.success} />
              <Body weight="600">Received for verification</Body>
            </View>
            <Small>
              Your photo was uploaded and is being checked. Verification runs on the server and
              takes a moment — the reviewer sees the result, including the reasons behind it, once
              it completes. Nothing is marked verified from this device.
            </Small>
            <View style={s.kvRow}><Small>Evidence ID</Small><Small color={colors.foreground}>{step.evidence.id}</Small></View>
            <View style={s.kvRow}><Small>Status</Small><Small color={colors.foreground}>{step.evidence.status}</Small></View>
          </Card>
          <Button onPress={() => { setSession(null); void arm(); }}>Capture another</Button>
          <Button variant="secondary" onPress={() => router.back()}>Done</Button>
        </ScrollView>
      </ScreenContainer>
    );
  }

  // ── Error screen ────────────────────────────────────────────────────────
  if (step.kind === "error") {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Capture evidence" />
        <ScrollView contentContainerStyle={s.body}>
          <Card style={{ gap: spacing.sm }}>
            <View style={s.row}>
              <AlertTriangle size={20} color={colors.destructive} />
              <Body weight="600">Couldn&apos;t submit this capture</Body>
            </View>
            <Small>{step.message}</Small>
          </Card>
          {step.retryable ? (
            <Button onPress={() => { setSession(null); void arm(); }}>Start a new capture</Button>
          ) : null}
          <Button variant="secondary" onPress={() => router.back()}>Go back</Button>
        </ScrollView>
      </ScreenContainer>
    );
  }

  const busy = step.kind === "working" || step.kind === "arming";
  const shutterEnabled = step.kind === "ready" && !!session && !expired;

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Remote audit" title={label} />
      <View style={s.cameraWrap}>
        <CameraView ref={cameraRef} style={StyleSheet.absoluteFill} facing="back" />
        {busy ? (
          <View style={s.scrim}>
            <ActivityIndicator color={colors.white} />
            <Small color={colors.white} style={{ marginTop: spacing.sm }}>
              {step.kind === "working" ? step.label : "Opening a capture session…"}
            </Small>
          </View>
        ) : null}
      </View>

      <ScrollView contentContainerStyle={s.body}>
        <Card style={{ gap: spacing.sm }}>
          <View style={s.row}>
            <MapPin size={18} color={location ? colors.primary : colors.mutedFg} />
            <Body weight="600">Location</Body>
            <View style={{ flex: 1 }} />
            <Pressable onPress={() => { void refreshLocation(); }} hitSlop={10}>
              <RefreshCw size={16} color={colors.primary} />
            </Pressable>
          </View>
          {location ? (
            <>
              <Small>
                Accuracy {location.accuracyM == null ? "unknown" : `±${Math.round(location.accuracyM)} m`}
                {location.locationAgeMs != null ? ` · ${Math.round(location.locationAgeMs / 1000)}s old` : ""}
              </Small>
              {location.mocked ? (
                <Small color={colors.destructive}>
                  This device is reporting a mock location. The capture will still be submitted, but
                  it will be flagged for review.
                </Small>
              ) : null}
              {location.accuracyM != null && location.accuracyM > 100 ? (
                <Small color={colors.warning}>
                  Accuracy is low here. Moving nearer a window or outside usually helps; a low-accuracy
                  fix is accepted but carries less weight.
                </Small>
              ) : null}
            </>
          ) : (
            <Small color={locationError ? colors.warning : colors.mutedFg}>
              {locationError ?? "Getting a location fix…"}
              {locationError ? " You can still capture — the reviewer will see that no location was available." : ""}
            </Small>
          )}
        </Card>

        <Card style={{ gap: spacing.xs }}>
          <View style={s.row}>
            <ShieldCheck
              size={18}
              color={isPlayIntegrityAvailable() ? colors.primary : colors.mutedFg} />
            <Body weight="600">Device check</Body>
          </View>
          {isPlayIntegrityAvailable() ? (
            <Small>
              This build can confirm to the reviewer that it&apos;s the genuine app on a real
              device. That check runs when you capture.
            </Small>
          ) : (
            // Said plainly rather than hidden: the capture still works, it just
            // carries less weight, and the site should know that up front
            // rather than discovering it when a reviewer queries the photo.
            <Small color={colors.mutedFg}>
              This build can&apos;t verify itself to the reviewer, so your capture will be checked
              by a person rather than accepted automatically. Nothing is lost — it just takes an
              extra step.
            </Small>
          )}
        </Card>

        <Card style={{ gap: spacing.xs }}>
          <Small>
            {expired
              ? "This capture session expired. Start a new one to continue."
              : `Capture session open · ${secondsLeft}s remaining`}
          </Small>
          <Small color={colors.mutedFg}>
            Live evidence must be taken in-app while a session is open. Gallery photos are handled
            separately and can&apos;t be submitted here.
          </Small>
        </Card>

        {expired ? (
          <Button onPress={() => { setSession(null); void arm(); }}>Start a new session</Button>
        ) : (
          <Button testID="remote-audit-shutter" disabled={!shutterEnabled} onPress={() => { void capture(); }}>
            <View style={s.row}>
              <CameraIcon size={18} color={colors.primaryFg} />
              <Body color={colors.primaryFg} weight="700">Capture</Body>
            </View>
          </Button>
        )}
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.md },
  body: { padding: spacing.md, gap: spacing.md },
  cameraWrap: { height: 320, backgroundColor: colors.black, overflow: "hidden", borderBottomLeftRadius: radii.xl, borderBottomRightRadius: radii.xl },
  scrim: { ...StyleSheet.absoluteFillObject, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(0,0,0,0.45)" },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  kvRow: { flexDirection: "row", justifyContent: "space-between", gap: spacing.sm },
});
