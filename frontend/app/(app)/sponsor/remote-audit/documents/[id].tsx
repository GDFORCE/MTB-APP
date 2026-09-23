// Sponsor-side document review — spec §10, extract → review → commit.
//
// The design rule this screen exists to honour: a machine-suggested value and
// a human-confirmed value must never look the same. Suggestions render in a
// distinct "Suggested" panel with their source and confidence attached, and a
// reviewer has to type or accept each one before it becomes the document's
// confirmed value. Pre-filling the confirmed fields directly from the model
// would technically be a nicer flow and would quietly destroy the distinction
// the whole pipeline is built around.
//
// The second rule: findings are shown in full, with severity and measured
// values, above the decision buttons. A reviewer approving a certificate must
// see what the checks actually said — never just a status word.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, TextInput, View,
} from "react-native";
import { useLocalSearchParams } from "expo-router";
import { AlertTriangle, Check, Sparkles, X } from "lucide-react-native";

import { Body, Button, Card, Small } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { colors, radii, spacing } from "@/src/theme/tokens";
import { isDecidable } from "@/src/features/remote-audit/documentNormalize";
import {
  confirmDocumentFields,
  decideDocument,
  fetchDocument,
  fetchDocumentChecks,
} from "@/src/features/remote-audit/documentsApi";
import type {
  DocumentCheckRun, DocumentFinding, DocumentItem,
} from "@/src/features/remote-audit/types";

const SEVERITY_TONE: Record<string, string> = {
  critical: colors.destructive,
  high: colors.destructive,
  medium: colors.warning,
  low: colors.info,
  info: colors.mutedFg,
};

const STATUS_TONE: Record<string, string> = {
  ACCEPTED: colors.success,
  TECHNICALLY_VALID: colors.success,
  REVIEW_REQUIRED: colors.warning,
  REJECTED: colors.destructive,
  PROCESSING_BLOCKED: colors.destructive,
  EXPIRED: colors.warning,
  SUPERSEDED: colors.mutedFg,
};

const STATUS_MEANING: Record<string, string> = {
  TECHNICALLY_VALID: "Every automated check passed. Your decision is still required.",
  REVIEW_REQUIRED: "One or more checks need a person to look. The findings are below.",
  ACCEPTED: "Accepted by a reviewer.",
  REJECTED: "Rejected by a reviewer.",
  SUPERSEDED: "Replaced by a newer document. Kept for the record.",
  EXPIRED: "Past its confirmed due date.",
  PROCESSING_BLOCKED: "Checks could not complete, most often because the virus scanner was unreachable. The file stays isolated rather than being shown to you unscanned.",
};

/** Fields a reviewer confirms. Order matters — these read top to bottom the
 *  way a certificate does. */
const FIELDS: { key: string; label: string; placeholder: string }[] = [
  { key: "certificate_number", label: "Certificate number", placeholder: "e.g. CAL-2026-771" },
  { key: "calibration_date", label: "Calibration date", placeholder: "YYYY-MM-DD" },
  { key: "due_date", label: "Due date", placeholder: "YYYY-MM-DD" },
  { key: "calibrated_by", label: "Calibrated by", placeholder: "Issuing lab" },
  { key: "result", label: "Result", placeholder: "e.g. Pass" },
];

const REJECT_REASONS = [
  { value: "ILLEGIBLE", label: "Illegible" },
  { value: "WRONG_DOCUMENT", label: "Wrong document" },
  { value: "EXPIRED", label: "Expired" },
  { value: "INCOMPLETE", label: "Incomplete" },
  { value: "NOT_AUTHENTIC", label: "Authenticity in doubt" },
];

function errorMessage(err: unknown): string {
  const detail = (err as any)?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong.";
}

function StatusPill({ status }: { status: string }) {
  const tone = STATUS_TONE[status] ?? colors.mutedFg;
  return (
    <View style={[s.pill, { backgroundColor: tone + "22", borderColor: tone }]}>
      <Small color={tone}>{status.replace(/_/g, " ")}</Small>
    </View>
  );
}

function FindingRow({ finding }: { finding: DocumentFinding }) {
  const tone = SEVERITY_TONE[finding.severity] ?? colors.mutedFg;
  return (
    <View style={[s.finding, { borderLeftColor: tone }]}>
      <View style={s.spread}>
        <Small color={tone}>{finding.severity.toUpperCase()}</Small>
        <Small color={colors.mutedFg}>{finding.code}</Small>
      </View>
      <Body>{finding.explanation}</Body>
      {finding.measuredValue != null ? (
        <Small>
          Found: {String(finding.measuredValue)}
          {finding.threshold != null ? ` · expected: ${String(finding.threshold)}` : ""}
        </Small>
      ) : null}
    </View>
  );
}

export default function DocumentReviewScreen() {
  const { id } = useLocalSearchParams<{ id?: string }>();
  const documentId = String(id ?? "");

  const [doc, setDoc] = useState<DocumentItem | null>(null);
  const [latest, setLatest] = useState<DocumentCheckRun | null>(null);
  const [history, setHistory] = useState<DocumentCheckRun[]>([]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!documentId) return;
    setError(null);
    try {
      const [item, checks] = await Promise.all([
        fetchDocument(documentId),
        fetchDocumentChecks(documentId),
      ]);
      setDoc(item);
      setLatest(checks.latest);
      setHistory(checks.history);
      // Seed the editable draft from CONFIRMED values only. A suggestion
      // deliberately does not pre-fill the field — the reviewer taps "Use" to
      // adopt it, which is what makes the confirmation a real act.
      const confirmed = (item.confirmed ?? {}) as Record<string, unknown>;
      setDraft(Object.fromEntries(
        FIELDS.map(({ key }) => [key, confirmed[key] == null ? "" : String(confirmed[key])]),
      ));
    } catch (err: any) {
      setError(err?.response?.status === 404
        ? "This document isn't available to you."
        : "Couldn't load this document. Pull to retry.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [documentId]);

  useEffect(() => { void load(); }, [load]);

  const suggestions = useMemo(
    () => (doc?.extracted?.fields ?? {}) as Record<string, unknown>,
    [doc],
  );

  const confirm = useCallback(async () => {
    if (!doc) return;
    setBusy(true);
    setError(null);
    try {
      const fields = Object.fromEntries(
        Object.entries(draft).filter(([, v]) => v.trim() !== "").map(([k, v]) => [k, v.trim()]),
      );
      const updated = await confirmDocumentFields(doc.id, fields);
      setDoc(updated);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [doc, draft]);

  const decide = useCallback(async (decision: "ACCEPTED" | "REJECTED") => {
    if (!doc) return;
    if (decision === "REJECTED" && !rejectReason) {
      setError("Choose a reason before rejecting.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await decideDocument(doc.id, {
        decision,
        reasonCode: decision === "REJECTED" ? rejectReason ?? undefined : undefined,
      });
      setDoc(updated);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [doc, rejectReason, load]);

  if (loading) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Document" />
        <View style={s.centered}><ActivityIndicator color={colors.primary} /></View>
      </ScreenContainer>
    );
  }

  if (!doc) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Document" />
        <View style={s.centered}><Card><Body>{error ?? "Not available."}</Body></Card></View>
      </ScreenContainer>
    );
  }

  const decidable = isDecidable(doc.status);
  const superseded = history.filter((h) => h.checkRunId !== latest?.checkRunId);

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Remote audit" title={doc.displayName} />
      <ScrollView
        contentContainerStyle={s.body}
        refreshControl={
          <RefreshControl refreshing={refreshing} tintColor={colors.primary}
            onRefresh={() => { setRefreshing(true); void load(); }} />
        }
      >
        <Card style={{ gap: spacing.sm }}>
          <View style={s.row}>
            <Body weight="700">Status</Body>
            <View style={{ flex: 1 }} />
            <StatusPill status={doc.status} />
          </View>
          <Small>{STATUS_MEANING[doc.status] ?? "Reported by the document pipeline."}</Small>
          {latest ? (
            <Small color={colors.mutedFg}>
              Rule set {latest.ruleSetVersion} · run #{latest.checkRunId}
              {latest.completedAt ? ` · ${new Date(latest.completedAt).toLocaleString()}` : ""}
            </Small>
          ) : (
            <Small color={colors.mutedFg}>No check run has completed yet.</Small>
          )}
          {doc.decisionReasonCode ? (
            <Small color={colors.foreground}>Reason: {doc.decisionReasonCode}</Small>
          ) : null}
          {doc.contentBlocked ? (
            <Small color={colors.destructive}>
              This file&apos;s contents are permanently unavailable — malware was detected in it.
            </Small>
          ) : null}
        </Card>

        <Card style={{ gap: spacing.sm }}>
          <Body weight="700">Findings</Body>
          {!latest ? (
            <Small>
              Nothing has been checked yet, so there is nothing to report. Don&apos;t read the absence
              of findings as a clean result.
            </Small>
          ) : latest.findings.length === 0 ? (
            <Small>No findings. Every automated check passed.</Small>
          ) : (
            latest.findings.map((f, i) => <FindingRow key={`${f.code}-${i}`} finding={f} />)
          )}
          {latest ? (
            <View style={s.spread}>
              <Small color={colors.mutedFg}>Malware scan</Small>
              <Small color={String(latest.checks?.malware_status) === "CLEAN" ? colors.success : colors.destructive}>
                {String(latest.checks?.malware_status ?? "unknown")}
              </Small>
            </View>
          ) : null}
          {latest?.checks?.signature ? (
            <View style={s.spread}>
              <Small color={colors.mutedFg}>Signature</Small>
              <Small color={colors.foreground}>
                {String((latest.checks.signature as any)?.overall ?? "unknown").replace(/_/g, " ")}
              </Small>
            </View>
          ) : null}
        </Card>

        {doc.extracted ? (
          <Card style={[s.suggestionCard, { gap: spacing.sm }]}>
            <View style={s.row}>
              <Sparkles size={18} color={colors.violet} />
              <Body weight="700">Suggested by automatic extraction</Body>
            </View>
            <Small color={colors.mutedFg}>
              Read from {doc.extracted.source}
              {doc.extracted.confidence ? ` · ${doc.extracted.confidence} confidence` : " · confidence unknown"}.
              These are suggestions, not values. Nothing here counts until you confirm it below.
            </Small>
            {doc.extracted.confidenceNotes ? (
              <Small color={colors.warning}>{doc.extracted.confidenceNotes}</Small>
            ) : null}
            {FIELDS.map(({ key, label }) => {
              const value = suggestions[key];
              const raw = suggestions[`${key}_raw`];
              if (value == null && raw == null) return null;
              return (
                <View key={key} style={s.suggestionRow}>
                  <View style={{ flex: 1 }}>
                    <Small color={colors.mutedFg}>{label}</Small>
                    <Small color={colors.foreground}>
                      {value != null ? String(value) : `unreadable (printed as "${String(raw)}")`}
                    </Small>
                  </View>
                  {value != null ? (
                    <Pressable
                      onPress={() => setDraft((prev) => ({ ...prev, [key]: String(value) }))}
                      style={s.useBtn}
                    >
                      <Small color={colors.primary}>Use</Small>
                    </Pressable>
                  ) : null}
                </View>
              );
            })}
          </Card>
        ) : null}

        <Card style={{ gap: spacing.sm }}>
          <Body weight="700">Confirmed values</Body>
          <Small color={colors.mutedFg}>
            Only what you enter here is recorded as fact, and only the confirmed due date drives
            expiry. Dates must be unambiguous — use YYYY-MM-DD.
          </Small>
          {FIELDS.map(({ key, label, placeholder }) => (
            <View key={key} style={{ gap: 4 }}>
              <Small>{label}</Small>
              <TextInput
                value={draft[key] ?? ""}
                onChangeText={(v) => setDraft((prev) => ({ ...prev, [key]: v }))}
                placeholder={placeholder}
                placeholderTextColor={colors.mutedFg}
                editable={!busy}
                autoCapitalize="none"
                style={s.input}
              />
            </View>
          ))}
          <Button variant="secondary" onPress={() => { void confirm(); }} loading={busy}>
            Save confirmed values
          </Button>
          {doc.confirmedBy ? (
            <Small color={colors.success}>
              Confirmed{doc.expiresOn ? ` · expires ${doc.expiresOn}` : ""}
            </Small>
          ) : null}
        </Card>

        {error ? (
          <Card style={{ gap: spacing.xs }}>
            <View style={s.row}>
              <AlertTriangle size={18} color={colors.destructive} />
              <Body weight="600">{error}</Body>
            </View>
          </Card>
        ) : null}

        {decidable ? (
          <Card style={{ gap: spacing.sm }}>
            <Body weight="700">Decision</Body>
            <Small color={colors.mutedFg}>
              Accepting a certificate requires a confirmed due date above — without one, nothing
              can ever expire it.
            </Small>
            <View style={s.chips}>
              {REJECT_REASONS.map((reason) => {
                const active = reason.value === rejectReason;
                return (
                  <Pressable
                    key={reason.value}
                    onPress={() => setRejectReason(active ? null : reason.value)}
                    style={[s.chip, active && { backgroundColor: colors.destructive, borderColor: colors.destructive }]}
                  >
                    <Small color={active ? colors.destructiveFg : colors.foreground}>{reason.label}</Small>
                  </Pressable>
                );
              })}
            </View>
            <Button onPress={() => { void decide("ACCEPTED"); }} disabled={busy}>
              <View style={s.row}>
                <Check size={18} color={colors.primaryFg} />
                <Body color={colors.primaryFg} weight="700">Accept</Body>
              </View>
            </Button>
            <Button variant="secondary" onPress={() => { void decide("REJECTED"); }} disabled={busy || !rejectReason}>
              <View style={s.row}>
                <X size={18} color={colors.primary} />
                <Body color={colors.primary} weight="700">Reject</Body>
              </View>
            </Button>
          </Card>
        ) : null}

        {superseded.length > 0 ? (
          <Card style={{ gap: spacing.sm }}>
            <Body weight="700">Earlier check runs</Body>
            <Small color={colors.mutedFg}>Kept in full. A re-check adds a run; it never rewrites one.</Small>
            {superseded.slice().sort((a, b) => b.checkRunId - a.checkRunId).map((run) => (
              <View key={run.checkRunId} style={s.historyRow}>
                <View style={s.spread}>
                  <Small color={colors.mutedFg}>Run #{run.checkRunId}</Small>
                  <StatusPill status={run.outcome} />
                </View>
                <Small color={colors.mutedFg}>
                  {run.ruleSetVersion}
                  {run.completedAt ? ` · ${new Date(run.completedAt).toLocaleString()}` : ""}
                  {` · ${run.findings.length} finding${run.findings.length === 1 ? "" : "s"}`}
                </Small>
              </View>
            ))}
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
  pill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: radii.pill, borderWidth: 1 },
  finding: { borderLeftWidth: 3, paddingLeft: spacing.sm, gap: 2 },
  // Visually distinct from the confirmed panel: a suggestion must never be
  // mistakable for a recorded value at a glance.
  suggestionCard: { backgroundColor: colors.secondary, borderColor: colors.violet + "55" },
  suggestionRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  useBtn: {
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: radii.pill,
    borderWidth: 1, borderColor: colors.primary + "55", backgroundColor: colors.card,
  },
  input: {
    borderWidth: 1, borderColor: colors.input, borderRadius: radii.md,
    paddingHorizontal: 12, paddingVertical: 10, color: colors.foreground,
    backgroundColor: colors.card, fontSize: 15,
  },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  chip: {
    paddingHorizontal: 12, paddingVertical: 8, borderRadius: radii.pill,
    borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card,
  },
  historyRow: { gap: 2, paddingVertical: spacing.xs, borderTopWidth: 1, borderTopColor: colors.border },
});
