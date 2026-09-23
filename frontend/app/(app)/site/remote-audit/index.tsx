// Site-side audit list — the hospital's view of audits it is part of.
//
// Deliberately thinner than the sponsor's landing screen. A site doesn't score
// or decide; it captures evidence, uploads documents, answers queries and does
// corrective actions. So this lists the audits and hands off to the shared
// audit-detail screen, which shows the site-side actions.
import React, { useCallback, useState } from "react";
import {
  ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, View,
} from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { ChevronRight } from "lucide-react-native";

import { Body, Card, Small } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { colors, radii, spacing } from "@/src/theme/tokens";
import { fetchAudits } from "@/src/features/remote-audit/api";
import type { AuditSummary } from "@/src/features/remote-audit/types";

const STATE_TONE: Record<string, string> = {
  draft: colors.mutedFg,
  in_progress: colors.info,
  under_review: colors.warning,
  decided: colors.success,
  closed: colors.mutedFg,
};

export default function SiteRemoteAudits() {
  const router = useRouter();
  const [audits, setAudits] = useState<AuditSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setAudits(await fetchAudits());
    } catch {
      setError("Couldn't load audits. Pull to retry.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void load(); }, [load]));

  if (loading) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Remote audit" title="My audits" />
        <View style={s.centered}><ActivityIndicator color={colors.primary} /></View>
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Remote audit" title="My audits" />
      <ScrollView
        contentContainerStyle={s.body}
        refreshControl={
          <RefreshControl refreshing={refreshing} tintColor={colors.primary}
            onRefresh={() => { setRefreshing(true); void load(); }} />
        }
      >
        {error ? <Card><Body>{error}</Body></Card> : null}

        {!error && audits.length === 0 ? (
          <Card style={{ gap: spacing.xs }}>
            <Body weight="700">No audits yet</Body>
            <Small>
              Audits your site is part of appear here once a sponsor has assigned you to one.
            </Small>
          </Card>
        ) : null}

        {audits.map((audit) => {
          const tone = STATE_TONE[audit.state] ?? colors.mutedFg;
          return (
            <Pressable
              key={audit.id}
              onPress={() => router.push(`/(app)/remote-audit/${audit.id}` as never)}
            >
              <Card style={{ gap: spacing.xs }}>
                <View style={s.row}>
                  <View style={{ flex: 1, gap: 2 }}>
                    <Body weight="600" numberOfLines={1}>Site {audit.siteId}</Body>
                    <Small color={colors.mutedFg} numberOfLines={1}>{audit.id}</Small>
                  </View>
                  <View style={[s.pill, { backgroundColor: tone + "22", borderColor: tone }]}>
                    <Small color={tone}>{audit.state.replace(/_/g, " ")}</Small>
                  </View>
                  <ChevronRight size={18} color={colors.mutedFg} />
                </View>
              </Card>
            </Pressable>
          );
        })}
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.md },
  body: { padding: spacing.md, gap: spacing.md, paddingBottom: spacing.xxl },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  pill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: radii.pill, borderWidth: 1 },
});
