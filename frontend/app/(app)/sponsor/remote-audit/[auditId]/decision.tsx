// Scoring dashboard and final decision — spec §13.
//
// Layout is the control here, not decoration:
//
//   1. BLOCKERS FIRST, above the numbers. A reviewer who scrolls no further
//      has still seen what is wrong. A dashboard that leads with a green 94%
//      and buries a critical failure below the fold is the specific failure
//      this ordering prevents.
//   2. TWO NUMBERS, NEVER ONE. Compliance and evidence confidence answer
//      different questions and are shown side by side with those questions
//      written under them. There is no combined figure anywhere on screen.
//   3. THE OVERRIDE PATH IS VISIBLE, NOT HIDDEN. A reviewer may disagree with
//      the engine — what they cannot do is disagree silently. Choosing a
//      decision the engine did not propose reveals a mandatory reason field.
//
// Signing requires a step-up token, which in production can only be minted
// from an MFA-authenticated session.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, TextInput, View,
} from "react-native";
import { useLocalSearchParams } from "expo-router";
import { AlertTriangle, ShieldCheck } from "lucide-react-native";

import { Body, Button, Card, Small } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { colors, radii, spacing } from "@/src/theme/tokens";
import {
  canDecide, conditionalNeedsCapa, formatPercent, hasCriticalBlocker,
  requiresOverrideReason, sortedBlockers,
} from "@/src/features/remote-audit/scoring";
import type { AuditDecision, FinalDecision, ScoreRun } from "@/src/features/remote-audit/scoring";
import {
  fetchAuditCapas, fetchDecision, fetchScore, recordDecision, requestStepUpToken,
} from "@/src/features/remote-audit/scoringApi";
import type { Capa } from "@/src/features/remote-audit/queries";

const SEVERITY_TONE: Record<string, string> = {
  critical: colors.destructive,
  high: colors.destructive,
  medium: colors.warning,
  low: colors.info,
  info: colors.mutedFg,
};

const DECISION_TONE: Record<string, string> = {
  APPROVED: colors.success,
  APPROVED_WITH_CONDITIONS: colors.warning,
  NOT_APPROVED: colors.destructive,
  REVIEW_REQUIRED: colors.info,
};

const DECISIONS: { value: FinalDecision; label: string }[] = [
  { value: "APPROVED", label: "Approve" },
  { value: "APPROVED_WITH_CONDITIONS", label: "Approve with conditions" },
  { value: "NOT_APPROVED", label: "Do not approve" },
];

function errorMessage(err: unknown): string {
  const detail = (err as any)?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong.";
}

export default function AuditDecisionScreen() {
  const { auditId: rawAuditId } = useLocalSearchParams<{ auditId?: string }>();
  const auditId = String(rawAuditId ?? "");

  const [run, setRun] = useState<ScoreRun | null>(null);
  const [decision, setDecision] = useState<AuditDecision | null>(null);
  const [decisionHistory, setDecisionHistory] = useState<AuditDecision[]>([]);
  const [capas, setCapas] = useState<Capa[]>([]);
  const [choice, setChoice] = useState<FinalDecision | null>(null);
  const [reason, setReason] = useState("");
  const [selectedCapaIds, setSelectedCapaIds] = useState<string[]>([]);
  const [password, setPassword] = useState("");
  const [signature, setSignature] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!auditId) return;
    setError(null);
    try {
      const [score, decisions, auditCapas] = await Promise.all([
        fetchScore(auditId), fetchDecision(auditId), fetchAuditCapas(auditId),
      ]);
      setRun(score.latest);
      setDecision(decisions.latest);
      setDecisionHistory(decisions.history);
      setCapas(auditCapas);
    } catch (err: any) {
      setError(err?.response?.status === 404
        ? "This audit isn't available to you."
        : "Couldn't load this audit. Pull to retry.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [auditId]);

  useEffect(() => { void load(); }, [load]);

  const blockers = useMemo(() => sortedBlockers(run?.blockers ?? []), [run]);
  const needsReason = choice !== null && requiresOverrideReason(run, choice);
  const needsCapa = choice !== null && conditionalNeedsCapa(choice, selectedCapaIds);

  const sign = useCallback(async () => {
    if (!choice) return;
    setBusy(true);
    setError(null);
    try {
      // The token is minted for this one action and consumed by it.
      const token = await requestStepUpToken("audit.decision", password);
      const recorded = await recordDecision(auditId, {
        decision: choice,
        stepUpToken: token,
        overrideReason: reason.trim() || undefined,
        linkedCapaIds: selectedCapaIds,
        signatureStatement: signature.trim(),
      });
      setDecision(recorded);
      setPassword("");
      setChoice(null);
      setReason("");
      await load();
    } catch (err) {
      const code = (err as any)?.response?.data?.detail?.code;
      if (code === "STEP_UP_REQUIRED") {
        setError("That password wasn't accepted. Signing off an audit needs a fresh confirmation.");
      } else if (code === "OVERRIDE_REASON_REQUIRED") {
        setError("This decision differs from the system's. A written reason is required.");
      } else if (code === "CONDITIONS_REQUIRED") {
        setError("A conditional approval needs at least one CAPA attached.");
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }, [choice, password, reason, selectedCapaIds, signature, auditId, load]);

  if (loading) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Decision" />
        <View style={s.centered}><ActivityIndicator color={colors.primary} /></View>
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Remote audit" title="Scoring & decision" />
      <ScrollView
        contentContainerStyle={s.body}
        refreshControl={
          <RefreshControl refreshing={refreshing} tintColor={colors.primary}
            onRefresh={() => { setRefreshing(true); void load(); }} />
        }
      >
        {/* 1. Blockers, before any number. */}
        <Card style={{ gap: spacing.sm }}>
          <View style={s.row}>
            <AlertTriangle
              size={18}
              color={hasCriticalBlocker(blockers) ? colors.destructive : colors.mutedFg} />
            <Body weight="700">Findings that affect this result</Body>
          </View>
          {!run ? (
            <Small>
              This audit hasn&apos;t been scored yet. There is nothing to report and nothing to
              decide.
            </Small>
          ) : blockers.length === 0 ? (
            <Small>No blocking findings were recorded.</Small>
          ) : (
            blockers.map((blocker, i) => {
              const tone = SEVERITY_TONE[blocker.severity] ?? colors.mutedFg;
              return (
                <View key={`${blocker.code}-${i}`} style={[s.blocker, { borderLeftColor: tone }]}>
                  <View style={s.spread}>
                    <Small color={tone}>{blocker.severity.toUpperCase()}</Small>
                    <Small color={colors.mutedFg}>{blocker.code}</Small>
                  </View>
                  <Body>{blocker.explanation}</Body>
                  {blocker.requirementIds.length > 0 ? (
                    <Small color={colors.mutedFg}>{blocker.requirementIds.join(", ")}</Small>
                  ) : null}
                </View>
              );
            })
          )}
        </Card>

        {/* 2. Two axes, never combined. */}
        {run ? (
          <Card style={{ gap: spacing.sm }}>
            <Body weight="700">Result</Body>
            <View style={s.axes}>
              <View style={s.axis}>
                <Small color={colors.mutedFg}>Compliance</Small>
                <Body weight="700" style={{ fontSize: 28 }}>
                  {formatPercent(run.compliancePercent)}
                </Body>
                <Small color={colors.mutedFg}>Did the site meet the requirements?</Small>
              </View>
              <View style={s.axisDivider} />
              <View style={s.axis}>
                <Small color={colors.mutedFg}>Evidence confidence</Small>
                <Body weight="700" style={{ fontSize: 28 }}>
                  {formatPercent(run.evidenceConfidencePercent)}
                </Body>
                <Small color={colors.mutedFg}>How far can the evidence be relied on?</Small>
              </View>
            </View>
            <Small color={colors.mutedFg}>
              These answer different questions and are deliberately not combined. Full compliance
              recorded on evidence that couldn&apos;t be verified is not the same as a
              well-evidenced pass.
            </Small>
            {run.notes.map((note, i) => (
              <Small key={i} color={colors.mutedFg}>• {note}</Small>
            ))}
            <View style={s.spread}>
              <Small color={colors.mutedFg}>System proposal</Small>
              <View style={[s.pill, {
                backgroundColor: (DECISION_TONE[run.proposedDecision] ?? colors.mutedFg) + "22",
                borderColor: DECISION_TONE[run.proposedDecision] ?? colors.mutedFg,
              }]}>
                <Small color={DECISION_TONE[run.proposedDecision] ?? colors.mutedFg}>
                  {run.proposedDecision.replace(/_/g, " ")}
                </Small>
              </View>
            </View>
            <Small color={colors.mutedFg}>
              {run.algorithmVersion} · run #{run.scoreRunId}
            </Small>
          </Card>
        ) : null}

        {/* Sections */}
        {run && run.sections.length > 0 ? (
          <Card style={{ gap: spacing.xs }}>
            <Body weight="700">By section</Body>
            {run.sections.map((section) => (
              <View key={section.sectionId} style={s.sectionRow}>
                <View style={s.spread}>
                  <Small color={colors.foreground}>{section.sectionId}</Small>
                  <Small color={section.belowMinimum ? colors.destructive : colors.foreground}>
                    {section.displayPercent === null ? "not applicable" : `${section.displayPercent}%`}
                  </Small>
                </View>
                <Small color={colors.mutedFg}>
                  weight {section.weightPercent}%
                  {section.notApplicableCount > 0 ? ` · ${section.notApplicableCount} N/A` : ""}
                  {section.missingCount > 0 ? ` · ${section.missingCount} unanswered` : ""}
                  {section.waivedCount > 0 ? ` · ${section.waivedCount} waived` : ""}
                </Small>
              </View>
            ))}
            <Small color={colors.mutedFg}>
              Not-applicable requirements are excluded from the score rather than counted as zero.
              Unanswered ones are counted and score zero.
            </Small>
          </Card>
        ) : null}

        {/* Existing decision */}
        {decision ? (
          <Card style={{ gap: spacing.xs }}>
            <View style={s.row}>
              <ShieldCheck size={18} color={DECISION_TONE[decision.decision] ?? colors.mutedFg} />
              <Body weight="700">{decision.decision.replace(/_/g, " ")}</Body>
            </View>
            <Small color={colors.mutedFg}>
              Recorded by {decision.decidedBy}
              {decision.decidedAt ? ` on ${new Date(decision.decidedAt).toLocaleString()}` : ""}
            </Small>
            {decision.overridden ? (
              <>
                <Small color={colors.warning}>
                  This overrode the system, which proposed {decision.machineProposed?.replace(/_/g, " ")}.
                </Small>
                <Small>{decision.overrideReason}</Small>
              </>
            ) : null}
            {decision.signatureStatement ? <Small>{decision.signatureStatement}</Small> : null}
            {decisionHistory.length > 1 ? (
              <Small color={colors.mutedFg}>
                {decisionHistory.length} decisions on record. Earlier ones are superseded, not
                erased.
              </Small>
            ) : null}
          </Card>
        ) : null}

        {error ? (
          <Card style={{ gap: spacing.xs }}>
            <View style={s.row}>
              <AlertTriangle size={18} color={colors.destructive} />
              <Body weight="600">{error}</Body>
            </View>
          </Card>
        ) : null}

        {/* 3. Decision, with a visible override path. */}
        {canDecide(run) ? (
          <Card style={{ gap: spacing.sm }}>
            <Body weight="700">{decision ? "Record a new decision" : "Record the decision"}</Body>
            <View style={s.chips}>
              {DECISIONS.map((option) => {
                const active = option.value === choice;
                const tone = DECISION_TONE[option.value];
                return (
                  <Pressable
                    key={option.value}
                    onPress={() => setChoice(active ? null : option.value)}
                    style={[s.chip, active && { backgroundColor: tone, borderColor: tone }]}
                  >
                    <Small color={active ? colors.white : colors.foreground}>{option.label}</Small>
                  </Pressable>
                );
              })}
            </View>

            {needsReason ? (
              <>
                <Small color={colors.warning}>
                  This differs from the system&apos;s proposal
                  {run ? ` (${run.proposedDecision.replace(/_/g, " ")})` : ""}. A written reason is
                  required, and it is kept alongside the system&apos;s verdict rather than replacing it.
                </Small>
                <TextInput
                  value={reason}
                  onChangeText={setReason}
                  placeholder="Why does this decision differ?"
                  placeholderTextColor={colors.mutedFg}
                  multiline
                  editable={!busy}
                  style={[s.input, { minHeight: 80, textAlignVertical: "top" }]}
                />
              </>
            ) : null}

            {choice === "APPROVED_WITH_CONDITIONS" ? (
              <>
                <Small color={colors.mutedFg}>
                  Attach the corrective actions this approval is conditional on. Each needs an owner
                  and a due date — an approval with no real conditions is an unconditional one.
                </Small>
                {capas.length === 0 ? (
                  <Small color={colors.warning}>
                    There are no CAPAs on this audit yet. Create one before approving conditionally.
                  </Small>
                ) : (
                  <View style={s.chips}>
                    {capas.map((capa) => {
                      const active = selectedCapaIds.includes(capa.id);
                      return (
                        <Pressable
                          key={capa.id}
                          onPress={() => setSelectedCapaIds((prev) =>
                            active ? prev.filter((id) => id !== capa.id) : [...prev, capa.id])}
                          style={[s.chip, active && {
                            backgroundColor: colors.primary, borderColor: colors.primary,
                          }]}
                        >
                          <Small color={active ? colors.primaryFg : colors.foreground}>
                            {capa.title} · due {capa.dueDate}
                          </Small>
                        </Pressable>
                      );
                    })}
                  </View>
                )}
              </>
            ) : null}

            <TextInput
              value={signature}
              onChangeText={setSignature}
              placeholder="Signature statement (optional)"
              placeholderTextColor={colors.mutedFg}
              editable={!busy}
              style={s.input}
            />

            <Small color={colors.mutedFg}>
              Signing off requires confirming your password. In production this must come from an
              MFA-authenticated session.
            </Small>
            <TextInput
              value={password}
              onChangeText={setPassword}
              placeholder="Confirm your password"
              placeholderTextColor={colors.mutedFg}
              secureTextEntry
              autoCapitalize="none"
              editable={!busy}
              style={s.input}
            />

            <Button
              loading={busy}
              disabled={busy || !choice || !password || (needsReason && !reason.trim()) || needsCapa}
              onPress={() => { void sign(); }}
            >
              Sign and record
            </Button>
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
  blocker: { borderLeftWidth: 3, paddingLeft: spacing.sm, gap: 2 },
  axes: { flexDirection: "row", alignItems: "stretch", gap: spacing.sm },
  axis: { flex: 1, gap: 2 },
  axisDivider: { width: 1, backgroundColor: colors.border },
  sectionRow: { gap: 2, paddingVertical: spacing.xs, borderTopWidth: 1, borderTopColor: colors.border },
  pill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: radii.pill, borderWidth: 1 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  chip: {
    paddingHorizontal: 12, paddingVertical: 8, borderRadius: radii.pill,
    borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card,
  },
  input: {
    borderWidth: 1, borderColor: colors.input, borderRadius: radii.md,
    paddingHorizontal: 12, paddingVertical: 10, color: colors.foreground,
    backgroundColor: colors.card, fontSize: 15,
  },
});
