// Virtual visit — pre-call consent and join gate (spec §12).
//
// This screen's job is to tell the truth about privacy BEFORE anyone joins,
// and to make consent a real act rather than a checkbox someone clicks past.
//
//   * The recording status is stated in plain words at the top, in a colour
//     that matches what it means. "Recorded" is never implied or omitted.
//   * The consent button sends back the privacy mode THIS SCREEN DISPLAYED.
//     The server compares it to the session's real mode and refuses a
//     mismatch, so consent given for a no-recording call can never be applied
//     to a recorded one — even if this screen were stale or wrong.
//   * Join is disabled until consent is recorded. The server enforces that
//     too; the button state just avoids offering something that will fail.
//
// THE IN-CALL SURFACE IS NOT HERE. Video rendering needs
// `@livekit/react-native`, a native module Expo Go cannot load — it lands
// with the EAS/custom-dev-client build alongside Phase 8's attestation
// bridges. Everything up to and including the join token works today, so the
// gate that matters (consent before media) is real now rather than deferred.
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, View } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { AlertTriangle, Circle, Lock, Video } from "lucide-react-native";

import { Body, Button, Card, Small } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { colors, radii, spacing } from "@/src/theme/tokens";
import {
  consentPermitsJoin, fetchSessions, joinSession, recordConsent,
} from "@/src/features/remote-audit/scoringApi";
import type { PrivacyMode, VirtualSession } from "@/src/features/remote-audit/scoringApi";

/** What each mode actually means for the person about to join, in their
 *  words rather than ours. The wording is the consent. */
const PRIVACY_COPY: Record<PrivacyMode, { title: string; detail: string; tone: string }> = {
  SERVER_RECORDED: {
    title: "This call will be recorded",
    detail:
      "Video and audio are recorded and stored with the audit's evidence, under the same retention "
      + "rules. Anyone assigned to this audit can review the recording afterwards.",
    tone: colors.destructive,
  },
  SERVER_NO_RECORDING: {
    title: "This call is not being recorded",
    detail:
      "Video and audio pass through the server so everyone can see and hear each other, but nothing "
      + "is saved. Notes a reviewer writes during the call are saved.",
    tone: colors.success,
  },
  E2EE_NO_RECORDING: {
    title: "This call is end-to-end encrypted and cannot be recorded",
    detail:
      "Media is encrypted between participants. The server relays it without being able to read it, "
      + "so recording is not just switched off — it isn't possible.",
    tone: colors.success,
  },
};

function errorMessage(err: unknown): string {
  const detail = (err as any)?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong.";
}

export default function VirtualSessionScreen() {
  const { id, auditId: rawAuditId } = useLocalSearchParams<{ id?: string; auditId?: string }>();
  const sessionId = String(id ?? "");
  const auditId = String(rawAuditId ?? "");

  const [session, setSession] = useState<VirtualSession | null>(null);
  const [livekitConfigured, setLivekitConfigured] = useState(true);
  const [joined, setJoined] = useState<{ room: string; role: string; canPublish: boolean } | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!auditId) return;
    setError(null);
    try {
      const { sessions, livekitConfigured: configured } = await fetchSessions(auditId);
      setLivekitConfigured(configured);
      setSession(sessions.find((s) => s.id === sessionId) ?? null);
    } catch (err: any) {
      setError(err?.response?.status === 404
        ? "This session isn't available to you."
        : "Couldn't load this session. Pull to retry.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [auditId, sessionId]);

  useEffect(() => { void load(); }, [load]);

  const consent = useCallback(async (granted: boolean) => {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      // Sends back the mode THIS SCREEN SHOWED. The server refuses a mismatch,
      // so a stale screen cannot launder consent onto a different call.
      setSession(await recordConsent(session.id, {
        granted, privacyModeShown: session.privacyMode,
      }));
    } catch (err) {
      const code = (err as any)?.response?.data?.detail?.code;
      setError(code === "CONSENT_MODE_MISMATCH"
        ? "This session's privacy settings changed since this screen loaded. Pull to refresh and read them again before consenting."
        : errorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [session]);

  const join = useCallback(async (role: "host" | "presenter" | "observer") => {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      const token = await joinSession(session.id, role);
      setJoined({ room: token.room, role: token.role, canPublish: token.canPublish });
    } catch (err) {
      const code = (err as any)?.response?.data?.detail?.code;
      if (code === "LIVEKIT_UNAVAILABLE") {
        setError("Video isn't available on this deployment yet. The rest of the audit — documents, queries and evidence — is unaffected.");
      } else if (code === "CONSENT_REQUIRED") {
        setError("Consent has to be recorded before anyone can join.");
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }, [session]);

  if (loading) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Virtual visit" />
        <View style={s.centered}><ActivityIndicator color={colors.primary} /></View>
      </ScreenContainer>
    );
  }

  if (!session) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Virtual visit" />
        <View style={s.centered}><Card><Body>{error ?? "Not available."}</Body></Card></View>
      </ScreenContainer>
    );
  }

  const privacy = PRIVACY_COPY[session.privacyMode];
  const canJoin = consentPermitsJoin(session) && session.state !== "ENDED"
    && session.state !== "CANCELLED";
  const declined = session.consentStatus === "DECLINED";

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Remote audit" title="Virtual visit" />
      <ScrollView
        contentContainerStyle={s.body}
        refreshControl={
          <RefreshControl refreshing={refreshing} tintColor={colors.primary}
            onRefresh={() => { setRefreshing(true); void load(); }} />
        }
      >
        {/* Recording status, stated first and plainly. */}
        <Card style={[s.privacyCard, { borderColor: privacy.tone }]}>
          <View style={s.row}>
            {session.recordingEnabled
              ? <Circle size={18} color={colors.destructive} fill={colors.destructive} />
              : <Lock size={18} color={privacy.tone} />}
            <Body weight="700" color={privacy.tone}>{privacy.title}</Body>
          </View>
          <Small>{privacy.detail}</Small>
        </Card>

        <Card style={{ gap: spacing.xs }}>
          <View style={s.spread}>
            <Small color={colors.mutedFg}>Status</Small>
            <Small color={colors.foreground}>{session.state}</Small>
          </View>
          {session.scheduledStartUtc ? (
            <View style={s.spread}>
              <Small color={colors.mutedFg}>Scheduled</Small>
              <Small color={colors.foreground}>
                {new Date(session.scheduledStartUtc).toLocaleString()}
              </Small>
            </View>
          ) : null}
          <View style={s.spread}>
            <Small color={colors.mutedFg}>Expected length</Small>
            <Small color={colors.foreground}>{session.expectedDurationMinutes} min</Small>
          </View>
          {session.purpose ? <Small>{session.purpose}</Small> : null}
        </Card>

        {error ? (
          <Card style={{ gap: spacing.xs }}>
            <View style={s.row}>
              <AlertTriangle size={18} color={colors.destructive} />
              <Body weight="600">{error}</Body>
            </View>
          </Card>
        ) : null}

        {session.consentStatus === "PENDING" ? (
          <Card style={{ gap: spacing.sm }}>
            <Body weight="700">Before you join</Body>
            <Small>
              Read the privacy notice above. Nobody joins and no video or audio flows until consent
              is recorded — including from the sponsor&apos;s side.
            </Small>
            <Button loading={busy} disabled={busy} onPress={() => { void consent(true); }}>
              I understand and consent
            </Button>
            <Button variant="secondary" disabled={busy} onPress={() => { void consent(false); }}>
              I do not consent
            </Button>
          </Card>
        ) : null}

        {declined ? (
          <Card style={{ gap: spacing.xs }}>
            <Body weight="600" color={colors.destructive}>Consent was declined</Body>
            <Small>
              This call can&apos;t go ahead. A single decline is enough — consent isn&apos;t a
              majority vote, and a later approval from someone else doesn&apos;t clear it. Schedule
              a new session if the privacy terms need to change.
            </Small>
          </Card>
        ) : null}

        {session.consentStatus === "NOT_REQUIRED" ? (
          <Card>
            <Small color={colors.mutedFg}>
              Consent isn&apos;t required for this session under its governing policy.
            </Small>
          </Card>
        ) : null}

        {canJoin ? (
          <Card style={{ gap: spacing.sm }}>
            <View style={s.row}>
              <Video size={18} color={colors.primary} />
              <Body weight="700">Join</Body>
            </View>
            {!livekitConfigured ? (
              <Small color={colors.warning}>
                Video isn&apos;t configured on this deployment. Everything else on this audit works
                normally — documents, queries, evidence and scoring are unaffected.
              </Small>
            ) : null}
            <Button
              loading={busy}
              disabled={busy || !livekitConfigured}
              onPress={() => { void join("presenter"); }}
            >
              Join with camera
            </Button>
            <Button
              variant="secondary"
              disabled={busy || !livekitConfigured}
              onPress={() => { void join("observer"); }}
            >
              Join to watch only
            </Button>
            <Small color={colors.mutedFg}>
              Watching only means your camera and microphone stay off — useful in a patient-care
              area where a second camera isn&apos;t appropriate.
            </Small>
          </Card>
        ) : null}

        {joined ? (
          <Card style={{ gap: spacing.xs }}>
            <Body weight="700">Ready to connect</Body>
            <Small>
              You have a join token for room {joined.room} as {joined.role}
              {joined.canPublish ? " with camera" : " (watching only)"}.
            </Small>
            <Small color={colors.mutedFg}>
              The in-call video screen ships with the custom development build — Expo Go can&apos;t
              load the video module. The token, the consent gate and the privacy policy above are
              all live now.
            </Small>
          </Card>
        ) : null}
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.md },
  body: { padding: spacing.md, gap: spacing.md, paddingBottom: spacing.xxl },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  spread: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: spacing.sm },
  privacyCard: { gap: spacing.sm, borderWidth: 2, borderRadius: radii.xl },
});
