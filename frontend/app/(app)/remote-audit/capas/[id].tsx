// CAPA detail — spec §11. One screen, both sides, actions decided by role.
//
// The distinction this screen must not blur: "the owner says the work is
// done" (COMPLETED) and "someone else confirmed it worked" (CLOSED) are
// different claims by different people. The screen shows them as two separate
// steps, and never offers the effectiveness review to whoever did the work —
// mirroring the server's SELF_REVIEW_FORBIDDEN rule rather than relying on it.
//
// "Overdue" is computed, never a stored badge: a flag written at some past
// sweep would still say "on time" the day after a CAPA lapsed.
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, TextInput, View,
} from "react-native";
import { useLocalSearchParams } from "expo-router";
import { AlertTriangle, CheckCircle2, Clock, RotateCcw } from "lucide-react-native";

import { Body, Button, Card, Small } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { colors, radii, spacing } from "@/src/theme/tokens";
import { useAuth } from "@/src/auth/AuthContext";
import { canReviewEffectiveness, capaNextAction, computeOverdue } from "@/src/features/remote-audit/queries";
import type { Capa } from "@/src/features/remote-audit/queries";
import { fetchCapa, progressCapa, reviewCapaEffectiveness } from "@/src/features/remote-audit/queriesApi";

const STATUS_TONE: Record<string, string> = {
  OPEN: colors.warning,
  IN_PROGRESS: colors.info,
  COMPLETED: colors.accent,
  CLOSED: colors.success,
  REOPENED: colors.destructive,
  CANCELLED: colors.mutedFg,
};

const STATUS_MEANING: Record<string, string> = {
  OPEN: "Assigned, not yet started.",
  IN_PROGRESS: "The owner is working on it.",
  COMPLETED: "The owner says the work is done. It still needs an effectiveness review before it can close.",
  CLOSED: "Reviewed and confirmed effective.",
  REOPENED: "The effectiveness review found it hadn't worked. It's back with the owner.",
  CANCELLED: "Withdrawn.",
};

function errorMessage(err: unknown): string {
  const detail = (err as any)?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong.";
}

export default function CapaDetailScreen() {
  const { id } = useLocalSearchParams<{ id?: string }>();
  const capaId = String(id ?? "");
  const { user } = useAuth();

  const [capa, setCapa] = useState<Capa | null>(null);
  const [note, setNote] = useState("");
  const [reviewNote, setReviewNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!capaId) return;
    setError(null);
    try {
      setCapa(await fetchCapa(capaId));
    } catch (err: any) {
      setError(err?.response?.status === 404
        ? "This CAPA isn't available to you."
        : "Couldn't load this CAPA. Pull to retry.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [capaId]);

  useEffect(() => { void load(); }, [load]);

  const act = useCallback(async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      setNote("");
      setReviewNote("");
    } catch (err) {
      const code = (err as any)?.response?.data?.detail?.code;
      if (code === "SELF_REVIEW_FORBIDDEN") {
        setError("You completed this CAPA, so you can't be the one to certify that it worked. Someone else has to review it.");
      } else if (code === "STALE_VERSION" || code === "INVALID_TRANSITION") {
        setError("Someone else moved this CAPA while you had it open. Pull to refresh.");
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }, [load]);

  if (loading) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="CAPA" />
        <View style={s.centered}><ActivityIndicator color={colors.primary} /></View>
      </ScreenContainer>
    );
  }

  if (!capa) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="CAPA" />
        <View style={s.centered}><Card><Body>{error ?? "Not available."}</Body></Card></View>
      </ScreenContainer>
    );
  }

  const viewerId = user?.id ?? "";
  const nextAction = capaNextAction(capa, viewerId);
  const mayReview = canReviewEffectiveness(capa, viewerId);
  // Recomputed locally rather than trusting a possibly-stale cached list.
  const overdue = capa.isOverdue || computeOverdue(capa.status, capa.dueDate);
  const tone = STATUS_TONE[capa.status] ?? colors.mutedFg;

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Remote audit" title="CAPA" />
      <ScrollView
        contentContainerStyle={s.body}
        refreshControl={
          <RefreshControl refreshing={refreshing} tintColor={colors.primary}
            onRefresh={() => { setRefreshing(true); void load(); }} />
        }
      >
        <Card style={{ gap: spacing.sm }}>
          <View style={s.row}>
            <View style={[s.pill, { backgroundColor: tone + "22", borderColor: tone }]}>
              <Small color={tone}>{capa.status.replace(/_/g, " ")}</Small>
            </View>
            {capa.criticality === "critical" ? (
              <View style={[s.pill, { backgroundColor: colors.destructive + "22", borderColor: colors.destructive }]}>
                <Small color={colors.destructive}>critical</Small>
              </View>
            ) : null}
          </View>
          <Body weight="700">{capa.title}</Body>
          {capa.description ? <Small>{capa.description}</Small> : null}
          <Small color={colors.mutedFg}>{STATUS_MEANING[capa.status]}</Small>
          <View style={s.spread}>
            <View style={s.row}>
              <Clock size={14} color={overdue ? colors.destructive : colors.mutedFg} />
              <Small color={overdue ? colors.destructive : colors.mutedFg}>Due {capa.dueDate}</Small>
            </View>
            {overdue ? <Small color={colors.destructive}>Overdue</Small> : null}
          </View>
          {capa.reopenCount > 0 ? (
            <View style={s.row}>
              <RotateCcw size={14} color={colors.warning} />
              <Small color={colors.warning}>
                Reopened {capa.reopenCount} time{capa.reopenCount === 1 ? "" : "s"} after an
                ineffective review
              </Small>
            </View>
          ) : null}
          {capa.criticality === "critical" && capa.status !== "CLOSED" && capa.status !== "CANCELLED" ? (
            <Small color={colors.destructive}>
              A critical CAPA in this state blocks the audit&apos;s final decision.
            </Small>
          ) : null}
        </Card>

        {capa.completionNote ? (
          <Card style={{ gap: spacing.xs }}>
            <Body weight="700">What the owner did</Body>
            <Small>{capa.completionNote}</Small>
            <Small color={colors.mutedFg}>
              This is the owner&apos;s own account. It is not, on its own, confirmation that the
              problem is fixed.
            </Small>
          </Card>
        ) : null}

        {capa.effectivenessReview ? (
          <Card style={{ gap: spacing.xs }}>
            <View style={s.row}>
              <CheckCircle2
                size={18}
                color={capa.effectivenessReview.outcome === "EFFECTIVE" ? colors.success : colors.destructive}
              />
              <Body weight="700">
                Effectiveness review — {capa.effectivenessReview.outcome.toLowerCase()}
              </Body>
            </View>
            <Small>{capa.effectivenessReview.note}</Small>
            {capa.effectivenessReview.reviewedAt ? (
              <Small color={colors.mutedFg}>
                {new Date(capa.effectivenessReview.reviewedAt).toLocaleString()}
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

        {nextAction === "START" ? (
          <Button disabled={busy} loading={busy}
            onPress={() => act(() => progressCapa(capa.id, { action: "START", expectedVersion: capa.version }))}>
            Start work
          </Button>
        ) : null}

        {nextAction === "COMPLETE" ? (
          <Card style={{ gap: spacing.sm }}>
            <Body weight="700">Mark the work done</Body>
            <Small color={colors.mutedFg}>
              Describe what you actually did. Someone else reviews whether it worked — this does
              not close the CAPA.
            </Small>
            <TextInput
              value={note}
              onChangeText={setNote}
              placeholder="What was changed, and when?"
              placeholderTextColor={colors.mutedFg}
              multiline
              editable={!busy}
              style={[s.input, { minHeight: 88, textAlignVertical: "top" }]}
            />
            <Button
              disabled={busy || !note.trim()}
              onPress={() => act(() => progressCapa(capa.id, {
                action: "COMPLETE", note: note.trim(), expectedVersion: capa.version,
              }))}
            >
              Submit as complete
            </Button>
          </Card>
        ) : null}

        {capa.status === "COMPLETED" && !mayReview ? (
          <Card>
            <Small color={colors.mutedFg}>
              Waiting on an effectiveness review. Whoever completed this CAPA can&apos;t be the one
              to sign it off.
            </Small>
          </Card>
        ) : null}

        {mayReview ? (
          <Card style={{ gap: spacing.sm }}>
            <Body weight="700">Effectiveness review</Body>
            <Small color={colors.mutedFg}>
              Did the corrective action actually work? An ineffective review sends it back to the
              owner rather than closing the finding.
            </Small>
            <TextInput
              value={reviewNote}
              onChangeText={setReviewNote}
              placeholder="What did you check, and what did you find?"
              placeholderTextColor={colors.mutedFg}
              multiline
              editable={!busy}
              style={[s.input, { minHeight: 88, textAlignVertical: "top" }]}
            />
            <Button
              disabled={busy || !reviewNote.trim()}
              onPress={() => act(() => reviewCapaEffectiveness(capa.id, {
                outcome: "EFFECTIVE", note: reviewNote.trim(), expectedVersion: capa.version,
              }))}
            >
              Effective — close this CAPA
            </Button>
            <Button
              variant="secondary"
              disabled={busy || !reviewNote.trim()}
              onPress={() => act(() => reviewCapaEffectiveness(capa.id, {
                outcome: "INEFFECTIVE", note: reviewNote.trim(), expectedVersion: capa.version,
              }))}
            >
              Not effective — send it back
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
  pill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: radii.pill, borderWidth: 1 },
  input: {
    borderWidth: 1, borderColor: colors.input, borderRadius: radii.md,
    paddingHorizontal: 12, paddingVertical: 10, color: colors.foreground,
    backgroundColor: colors.card, fontSize: 15,
  },
});
