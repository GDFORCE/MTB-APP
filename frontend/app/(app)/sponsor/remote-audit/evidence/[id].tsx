// Sponsor-side evidence viewer — spec §15.1.
//
// The rule this screen exists to honour: a reviewer must NEVER be shown only a
// verdict or a score. Every state is rendered together with the reasons behind
// it and the raw signals those reasons were derived from, so a reviewer can
// disagree with the engine on the evidence rather than on faith. A bare
// "PASS" badge with nothing under it would be a regression, not a cleaner UI.
//
// It also shows the full verification history. Verification is append-only
// server-side (`verification_run_id` / `supersedes_verification_id`), so a
// re-run after a rule-set change never erases what a reviewer saw earlier —
// and this screen has to make that visible or the guarantee is pointless.
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, View } from "react-native";
import { useLocalSearchParams } from "expo-router";

import { Body, Card, Small } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { colors, radii, spacing } from "@/src/theme/tokens";
import { fetchEvidence, fetchVerification } from "@/src/features/remote-audit/api";
import type {
  EvidenceItem,
  EvidenceState,
  VerificationReason,
  VerificationResult,
} from "@/src/features/remote-audit/types";

/** Colour carries emphasis only — it never replaces the written reason. */
const STATE_TONE: Record<string, string> = {
  PASS: colors.success,
  PASS_WITH_LIMITED_CONFIDENCE: colors.warning,
  REVIEW: colors.warning,
  SUSPICIOUS_REVIEW: colors.destructive,
  FAIL_TECHNICAL: colors.destructive,
  HOLD: colors.info,
  PROCESSING: colors.mutedFg,
  PROCESSING_BLOCKED: colors.destructive,
  PROCESSING_FAILED: colors.destructive,
  QUARANTINED: colors.mutedFg,
};

const SEVERITY_TONE: Record<string, string> = {
  critical: colors.destructive,
  high: colors.destructive,
  medium: colors.warning,
  low: colors.info,
  info: colors.mutedFg,
};

/** Plain-language gloss for each state. The engine's own `reasons` carry the
 *  specifics; this only explains what the state itself means for the reviewer. */
const STATE_MEANING: Record<string, string> = {
  PASS: "All checks succeeded. Review the reasons below before relying on it.",
  PASS_WITH_LIMITED_CONFIDENCE: "Accepted, but one or more signals were weaker than the standard bar.",
  REVIEW: "A human decision is required. The engine did not accept or reject this on its own.",
  SUSPICIOUS_REVIEW: "Signals consistent with tampering or spoofing were found. Treat with caution.",
  FAIL_TECHNICAL: "A technical check failed outright — for example the file's hash did not match what the device claimed.",
  HOLD: "Held pending an unresolved condition.",
  QUARANTINED: "Uploaded and isolated. Not yet examined.",
  PROCESSING: "Checks are running.",
  PROCESSING_BLOCKED: "Checks could not complete — most often because the malware scanner was unreachable. This file stays isolated rather than being shown to you unscanned.",
  PROCESSING_FAILED: "Processing failed. This is a system fault, not a verdict on the evidence.",
};

const SIGNAL_LABELS: Record<string, string> = {
  hash_match: "File hash matches device",
  mime_match: "File type matches declared type",
  malware_status: "Malware scan",
  capture_session_replayed: "Capture session replayed",
  attestation_tier: "Device integrity",
  geo_status: "Location",
  android_is_mock: "Android mock location",
  ios_is_simulated: "iOS simulated location",
  exif_device_distance_m: "EXIF vs. device location",
  duplicate_sha256: "Identical file seen before",
  duplicate_visual_candidate: "Visually similar file seen before",
  capture_mode: "Capture mode",
};

function formatSignal(key: string, value: unknown): string {
  if (value === null || value === undefined) return "not available";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (key === "exif_device_distance_m" && typeof value === "number") return `${Math.round(value)} m apart`;
  if (typeof value === "number") return String(Math.round(value * 100) / 100);
  return String(value);
}

function StatePill({ state }: { state: EvidenceState | string }) {
  const tone = STATE_TONE[state] ?? colors.mutedFg;
  return (
    <View style={[s.pill, { backgroundColor: tone + "22", borderColor: tone }]}>
      <Small color={tone}>{String(state).replace(/_/g, " ")}</Small>
    </View>
  );
}

function ReasonRow({ reason }: { reason: VerificationReason }) {
  const tone = SEVERITY_TONE[reason.severity] ?? colors.mutedFg;
  return (
    <View style={[s.reason, { borderLeftColor: tone }]}>
      <View style={s.reasonHead}>
        <Small color={tone}>{reason.severity.toUpperCase()}</Small>
        <Small color={colors.mutedFg}>{reason.code}</Small>
      </View>
      <Body>{reason.explanation}</Body>
      {reason.measuredValue !== undefined && reason.measuredValue !== null ? (
        <Small>
          Measured: {String(reason.measuredValue)}
          {reason.threshold !== undefined && reason.threshold !== null ? ` · threshold: ${String(reason.threshold)}` : ""}
        </Small>
      ) : null}
      {reason.source ? <Small color={colors.mutedFg}>Source: {reason.source}</Small> : null}
    </View>
  );
}

export default function RemoteAuditEvidenceScreen() {
  const { id } = useLocalSearchParams<{ id?: string }>();
  const evidenceId = String(id ?? "");

  const [evidence, setEvidence] = useState<EvidenceItem | null>(null);
  const [latest, setLatest] = useState<VerificationResult | null>(null);
  const [history, setHistory] = useState<VerificationResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!evidenceId) return;
    setError(null);
    try {
      const [item, verification] = await Promise.all([
        fetchEvidence(evidenceId),
        fetchVerification(evidenceId),
      ]);
      setEvidence(item);
      setLatest(verification.latest);
      setHistory(verification.history);
    } catch (err: any) {
      // The API answers 404 for both "gone" and "not yours" on purpose, so
      // this message must not distinguish them either.
      setError(err?.response?.status === 404
        ? "This evidence isn't available to you."
        : "Couldn't load this evidence. Pull to retry.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [evidenceId]);

  useEffect(() => { void load(); }, [load]);

  if (loading) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Evidence" />
        <View style={s.centered}><ActivityIndicator color={colors.primary} /></View>
      </ScreenContainer>
    );
  }

  const state = latest?.overallState ?? evidence?.status ?? "PROCESSING";
  // Everything before a completed verification is genuinely undecided — the
  // screen says so rather than rendering an empty reasons list as if it were
  // a clean bill of health.
  const undecided = !latest;
  const superseded = history.filter((h) => h.verificationRunId !== latest?.verificationRunId);

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Remote audit" title="Evidence" />
      <ScrollView
        contentContainerStyle={s.body}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} tintColor={colors.primary} />}
      >
        {error ? (
          <Card><Body>{error}</Body></Card>
        ) : (
          <>
            <Card style={{ gap: spacing.sm }}>
              <View style={s.row}>
                <Body weight="700">Verification</Body>
                <View style={{ flex: 1 }} />
                <StatePill state={state} />
              </View>
              <Small>{STATE_MEANING[state] ?? "State reported by the verification engine."}</Small>
              {latest ? (
                <Small color={colors.mutedFg}>
                  Rule set {latest.ruleSetVersion} · run #{latest.verificationRunId}
                  {latest.completedAt ? ` · ${new Date(latest.completedAt).toLocaleString()}` : ""}
                </Small>
              ) : (
                <Small color={colors.mutedFg}>No verification run has completed for this item yet.</Small>
              )}
            </Card>

            <Card style={{ gap: spacing.sm }}>
              <Body weight="700">Why</Body>
              {undecided ? (
                <Small>
                  Nothing has been decided yet, so there is nothing to justify. Don&apos;t read the
                  absence of findings as a clean result.
                </Small>
              ) : latest!.reasons.length === 0 ? (
                <Small>
                  The engine recorded no findings for this run. That is the reason for the state
                  above — not an omission.
                </Small>
              ) : (
                latest!.reasons.map((reason, i) => <ReasonRow key={`${reason.code}-${i}`} reason={reason} />)
              )}
            </Card>

            {latest && Object.keys(latest.signals).length > 0 ? (
              <Card style={{ gap: spacing.sm }}>
                <Body weight="700">Signals</Body>
                <Small color={colors.mutedFg}>
                  The raw inputs the reasons above were derived from.
                </Small>
                {Object.entries(latest.signals).map(([key, value]) => (
                  <View key={key} style={s.kvRow}>
                    <Small style={{ flex: 1 }}>{SIGNAL_LABELS[key] ?? key.replace(/_/g, " ")}</Small>
                    <Small color={colors.foreground}>{formatSignal(key, value)}</Small>
                  </View>
                ))}
              </Card>
            ) : null}

            {evidence ? (
              <Card style={{ gap: spacing.xs }}>
                <Body weight="700">Capture</Body>
                <View style={s.kvRow}><Small>Mode</Small><Small color={colors.foreground}>{evidence.captureMode === "LIVE_IN_APP" ? "Live, in-app" : "Historical / gallery"}</Small></View>
                <View style={s.kvRow}><Small>Media type</Small><Small color={colors.foreground}>{evidence.mediaType}</Small></View>
                {evidence.displayName ? (
                  <View style={s.kvRow}><Small>Name</Small><Small color={colors.foreground}>{evidence.displayName}</Small></View>
                ) : null}
                {evidence.createdAt ? (
                  <View style={s.kvRow}><Small>Received</Small><Small color={colors.foreground}>{new Date(evidence.createdAt).toLocaleString()}</Small></View>
                ) : null}
                {evidence.captureMode === "GALLERY_HISTORICAL" ? (
                  <Small color={colors.warning}>
                    Historical evidence can&apos;t demonstrate present-day presence, so its confidence
                    is capped regardless of how the other checks came out.
                  </Small>
                ) : null}
              </Card>
            ) : null}

            {superseded.length > 0 ? (
              <Card style={{ gap: spacing.sm }}>
                <Body weight="700">Earlier verification runs</Body>
                <Small color={colors.mutedFg}>
                  Kept in full. A re-run adds a result; it never rewrites one.
                </Small>
                {superseded
                  .slice()
                  .sort((a, b) => b.verificationRunId - a.verificationRunId)
                  .map((run) => (
                    <View key={run.verificationRunId} style={s.historyRow}>
                      <View style={s.row}>
                        <Small color={colors.mutedFg}>Run #{run.verificationRunId}</Small>
                        <View style={{ flex: 1 }} />
                        <StatePill state={run.overallState} />
                      </View>
                      <Small color={colors.mutedFg}>
                        {run.ruleSetVersion}
                        {run.completedAt ? ` · ${new Date(run.completedAt).toLocaleString()}` : ""}
                        {` · ${run.reasons.length} finding${run.reasons.length === 1 ? "" : "s"}`}
                      </Small>
                    </View>
                  ))}
              </Card>
            ) : null}
          </>
        )}
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.md },
  body: { padding: spacing.md, gap: spacing.md, paddingBottom: spacing.xxl },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  kvRow: { flexDirection: "row", justifyContent: "space-between", gap: spacing.sm },
  pill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: radii.pill, borderWidth: 1 },
  reason: { borderLeftWidth: 3, paddingLeft: spacing.sm, gap: 2 },
  reasonHead: { flexDirection: "row", justifyContent: "space-between", gap: spacing.sm },
  historyRow: { gap: 2, paddingVertical: spacing.xs, borderTopWidth: 1, borderTopColor: colors.border },
});
