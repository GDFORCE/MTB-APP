import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { AlertTriangle, ChevronRight, ClipboardList } from "lucide-react-native";

import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Card, Small } from "@/src/components/ui";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import { getPendingScheduleReviews } from "./api";
import type { PendingScheduleReview } from "./api";

const errorText = (error: any) =>
  error?.response?.status === 403
    ? "You don't have access to schedule reviews."
    : error?.response?.data?.detail || error?.message
    || "The review inbox could not be loaded.";

const submittedLabel = (value: string | null) => {
  if (!value) return "Submitted for review";
  try {
    return `Submitted ${new Date(value).toLocaleDateString(undefined, {
      month: "short", day: "numeric", year: "numeric",
    })}`;
  } catch { return "Submitted for review"; }
};

/**
 * Doc 9 / Phase 4 - the cross-trial "what needs my decision" list, replacing
 * the legacy `schedule_reviews` Mongo inbox. Every version here is IN_REVIEW;
 * tapping one opens the same approve/reject screen a sponsor uses to submit
 * it (`ProtocolScheduleScreen`), so there is one review surface, not two.
 */
export default function ScheduleReviewInboxScreen() {
  const router = useRouter();
  const [items, setItems] = useState<PendingScheduleReview[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setBusy(true); setError("");
    try {
      setItems(await getPendingScheduleReviews());
    } catch (nextError) {
      setError(errorText(nextError));
    } finally { setBusy(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const open = (item: PendingScheduleReview) => router.push({
    pathname: "/(app)/sponsor/protocol-schedule",
    params: {
      scheduleVersionId: item.schedule_version_id, id: item.trial_id,
      trialName: item.trial_name,
      protocolVersion: item.protocol_version_label
        ? `Protocol ${item.protocol_version_label} · ${item.schedule_name}`
        : item.schedule_name,
    },
  });

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Clinical" title="Schedule Reviews" />
      {busy ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
          <Small style={{ marginTop: 10 }}>Loading pending schedules…</Small>
        </View>
      ) : (
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          {!!error && (
            <Card style={styles.notice}>
              <AlertTriangle size={20} color={colors.warning} />
              <Small style={styles.noticeText}>{error}</Small>
            </Card>
          )}

          {!error && !items.length ? (
            <Card style={styles.empty}>
              <ClipboardList size={28} color={colors.mutedFg} />
              <Body weight="700" style={{ marginTop: 10 }}>Nothing needs your review</Body>
              <Small style={styles.emptyText}>
                A schedule appears here once a sponsor submits it for review.
              </Small>
            </Card>
          ) : items.map((item) => (
            <Pressable
              key={item.schedule_version_id}
              testID={`pending-review-${item.schedule_version_id}`}
              accessibilityRole="button"
              accessibilityLabel={`Review ${item.trial_name}`}
              onPress={() => open(item)}
              style={styles.row}
            >
              <View style={{ flex: 1 }}>
                <Text style={styles.trialName}>{item.trial_name}</Text>
                <Text style={styles.meta}>
                  {item.schedule_name} · v{item.version_number}
                  {item.protocol_version_label ? ` · Protocol ${item.protocol_version_label}` : ""}
                </Text>
                <Text style={styles.submitted}>{submittedLabel(item.submitted_at)}</Text>
              </View>
              <ChevronRight size={18} color={colors.mutedFg} />
            </Pressable>
          ))}
        </ScrollView>
      )}
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  content: { padding: spacing.md, paddingBottom: 50, gap: 10 },
  notice: { flexDirection: "row", alignItems: "center", gap: 10 },
  noticeText: { flex: 1 },
  empty: { alignItems: "center", paddingVertical: 30 },
  emptyText: { marginTop: 4, textAlign: "center", maxWidth: 300 },
  row: {
    flexDirection: "row", alignItems: "center", gap: 10,
    backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border,
    borderRadius: radii.lg, padding: 14,
  },
  trialName: { fontFamily: fonts.semibold, fontSize: 15, color: colors.foreground },
  meta: { fontFamily: fonts.regular, fontSize: 12, color: colors.mutedFg, marginTop: 3 },
  submitted: { fontFamily: fonts.regular, fontSize: 11, color: colors.mutedFg, marginTop: 4 },
});
