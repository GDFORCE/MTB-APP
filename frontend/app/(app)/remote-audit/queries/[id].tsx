// Query thread — spec §11. Deliberately ONE screen serving both sides.
//
// A query is a conversation between the sponsor and the site, and building two
// screens for it would mean two renderings of the same thread that could drift
// apart. Instead the thread is identical for everyone and only the ACTIONS
// differ, decided by `canRespond` / `canDecide` — which mirror the server's
// rules rather than inventing UI-only ones.
//
// The thread is append-only, and the screen shows that: rejected answers stay
// visible above the newer ones, a closure that was later reopened still shows
// as having happened. An audit record that quietly tidies itself up is not an
// audit record.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, TextInput, View,
} from "react-native";
import { useLocalSearchParams } from "expo-router";
import { AlertTriangle, MessageSquare, RotateCcw } from "lucide-react-native";

import { Body, Button, Card, Small } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { colors, radii, spacing } from "@/src/theme/tokens";
import { useAuth } from "@/src/auth/AuthContext";
import {
  canCancelQuery, canDecide, canReopen, canRespond,
} from "@/src/features/remote-audit/queries";
import type { AuditQuery } from "@/src/features/remote-audit/queries";
import { decideQuery, fetchQuery, respondToQuery } from "@/src/features/remote-audit/queriesApi";

const STATUS_TONE: Record<string, string> = {
  OPEN: colors.warning,
  RESPONDED: colors.info,
  REOPENED: colors.warning,
  CLOSED: colors.success,
  CANCELLED: colors.mutedFg,
};

const CRITICALITY_TONE: Record<string, string> = {
  critical: colors.destructive,
  major: colors.warning,
  minor: colors.mutedFg,
};

const REJECT_REASONS = [
  { value: "INSUFFICIENT_DETAIL", label: "Not enough detail" },
  { value: "EVIDENCE_MISSING", label: "Evidence missing" },
  { value: "DOES_NOT_ADDRESS", label: "Doesn't address the finding" },
  { value: "CONTRADICTS_RECORD", label: "Contradicts the record" },
];

function errorMessage(err: unknown): string {
  const detail = (err as any)?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong.";
}

function Pill({ label, tone }: { label: string; tone: string }) {
  return (
    <View style={[s.pill, { backgroundColor: tone + "22", borderColor: tone }]}>
      <Small color={tone}>{label}</Small>
    </View>
  );
}

export default function QueryThreadScreen() {
  const { id } = useLocalSearchParams<{ id?: string }>();
  const queryId = String(id ?? "");
  const { user } = useAuth();

  const [query, setQuery] = useState<AuditQuery | null>(null);
  const [draft, setDraft] = useState("");
  const [reasonCode, setReasonCode] = useState<string | null>(null);
  const [reopenReason, setReopenReason] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!queryId) return;
    setError(null);
    try {
      setQuery(await fetchQuery(queryId));
    } catch (err: any) {
      setError(err?.response?.status === 404
        ? "This query isn't available to you."
        : "Couldn't load this query. Pull to retry.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [queryId]);

  useEffect(() => { void load(); }, [load]);

  const act = useCallback(async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      setDraft("");
      setReasonCode(null);
      setReopenReason("");
    } catch (err) {
      // A stale-version conflict is the common case here and deserves a real
      // explanation rather than "try again": someone else moved the query.
      const code = (err as any)?.response?.data?.detail?.code;
      setError(code === "STALE_VERSION" || code === "INVALID_TRANSITION"
        ? "Someone else acted on this query while you had it open. Pull to refresh and take another look."
        : errorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [load]);

  const responses = useMemo(
    () => (query?.responses ?? []).slice().sort((a, b) => a.sequence - b.sequence),
    [query],
  );

  if (loading) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Query" />
        <View style={s.centered}><ActivityIndicator color={colors.primary} /></View>
      </ScreenContainer>
    );
  }

  if (!query) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="Query" />
        <View style={s.centered}><Card><Body>{error ?? "Not available."}</Body></Card></View>
      </ScreenContainer>
    );
  }

  const viewerId = user?.id ?? "";
  const mayRespond = canRespond(query.status);
  const mayDecide = canDecide(query.status);
  const mayReopen = canReopen(query.status);

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Remote audit" title="Query" />
      <ScrollView
        contentContainerStyle={s.body}
        refreshControl={
          <RefreshControl refreshing={refreshing} tintColor={colors.primary}
            onRefresh={() => { setRefreshing(true); void load(); }} />
        }
      >
        <Card style={{ gap: spacing.sm }}>
          <View style={s.row}>
            <Pill label={query.status.replace(/_/g, " ")} tone={STATUS_TONE[query.status] ?? colors.mutedFg} />
            <Pill label={query.criticality} tone={CRITICALITY_TONE[query.criticality]} />
          </View>
          <Body weight="700">{query.title}</Body>
          {query.detail ? <Small>{query.detail}</Small> : null}
          {query.dueDate ? <Small color={colors.mutedFg}>Response due {query.dueDate}</Small> : null}
          {query.lastRejectionReasonCode ? (
            <Small color={colors.warning}>
              A previous response was sent back: {query.lastRejectionReasonCode.replace(/_/g, " ").toLowerCase()}
            </Small>
          ) : null}
          {query.reopenCount > 0 ? (
            <View style={s.row}>
              <RotateCcw size={14} color={colors.warning} />
              <Small color={colors.warning}>
                Reopened {query.reopenCount} time{query.reopenCount === 1 ? "" : "s"}
                {query.reopenReason ? ` — ${query.reopenReason}` : ""}
              </Small>
            </View>
          ) : null}
        </Card>

        <Card style={{ gap: spacing.md }}>
          <View style={s.row}>
            <MessageSquare size={18} color={colors.primary} />
            <Body weight="700">Thread</Body>
          </View>
          {responses.length === 0 ? (
            <Small>No response yet.</Small>
          ) : (
            responses.map((response) => (
              <View key={response.id} style={s.message}>
                <View style={s.spread}>
                  <Small color={colors.mutedFg}>
                    {response.authorId === viewerId ? "You" : "Site"} · #{response.sequence}
                  </Small>
                  {response.createdAt ? (
                    <Small color={colors.mutedFg}>{new Date(response.createdAt).toLocaleString()}</Small>
                  ) : null}
                </View>
                <Body>{response.body}</Body>
              </View>
            ))
          )}
          {query.status === "CLOSED" ? (
            <Small color={colors.success}>
              Closed{query.closeReasonCode ? ` — ${query.closeReasonCode.replace(/_/g, " ").toLowerCase()}` : ""}.
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

        {mayRespond ? (
          <Card style={{ gap: spacing.sm }}>
            <Body weight="700">Respond</Body>
            <Small color={colors.mutedFg}>
              Responses are part of the audit record and can&apos;t be edited afterwards. Add a new
              one if you need to correct something.
            </Small>
            <TextInput
              value={draft}
              onChangeText={setDraft}
              placeholder="Describe what you found and what you did."
              placeholderTextColor={colors.mutedFg}
              multiline
              editable={!busy}
              style={[s.input, { minHeight: 96, textAlignVertical: "top" }]}
            />
            <Button
              disabled={busy || !draft.trim()}
              loading={busy}
              onPress={() => act(() => respondToQuery(query.id, {
                body: draft.trim(), expectedVersion: query.version,
              }))}
            >
              Send response
            </Button>
          </Card>
        ) : null}

        {mayDecide ? (
          <Card style={{ gap: spacing.sm }}>
            <Body weight="700">Decision</Body>
            <Button
              disabled={busy}
              onPress={() => act(() => decideQuery(query.id, {
                action: "CLOSE", reasonCode: "SATISFACTORY", expectedVersion: query.version,
              }))}
            >
              Close — the response is satisfactory
            </Button>
            <Small color={colors.mutedFg}>
              Or send it back. A reason is required so the site knows what to fix.
            </Small>
            <View style={s.chips}>
              {REJECT_REASONS.map((reason) => {
                const active = reason.value === reasonCode;
                return (
                  <Pressable
                    key={reason.value}
                    onPress={() => setReasonCode(active ? null : reason.value)}
                    style={[s.chip, active && { backgroundColor: colors.warning, borderColor: colors.warning }]}
                  >
                    <Small color={active ? colors.warningFg : colors.foreground}>{reason.label}</Small>
                  </Pressable>
                );
              })}
            </View>
            <Button
              variant="secondary"
              disabled={busy || !reasonCode}
              onPress={() => act(() => decideQuery(query.id, {
                action: "REJECT", reasonCode: reasonCode ?? undefined, expectedVersion: query.version,
              }))}
            >
              Send back to the site
            </Button>
          </Card>
        ) : null}

        {mayReopen ? (
          <Card style={{ gap: spacing.sm }}>
            <Body weight="700">Reopen</Body>
            <Small color={colors.mutedFg}>
              Reopening keeps everything above intact — the closure stays on the record, and the
              thread continues from here.
            </Small>
            <TextInput
              value={reopenReason}
              onChangeText={setReopenReason}
              placeholder="Why does this need reopening?"
              placeholderTextColor={colors.mutedFg}
              editable={!busy}
              style={s.input}
            />
            <Button
              variant="secondary"
              disabled={busy || !reopenReason.trim()}
              onPress={() => act(() => decideQuery(query.id, {
                action: "REOPEN", reason: reopenReason.trim(), expectedVersion: query.version,
              }))}
            >
              Reopen this query
            </Button>
          </Card>
        ) : null}

        {canCancelQuery(query.status) && !mayRespond ? (
          <Button
            variant="ghost"
            disabled={busy}
            onPress={() => act(() => decideQuery(query.id, {
              action: "CANCEL", reason: "Raised in error.", expectedVersion: query.version,
            }))}
          >
            Withdraw — this was raised in error
          </Button>
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
  message: {
    gap: 4, paddingLeft: spacing.sm, borderLeftWidth: 3, borderLeftColor: colors.border,
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
});
