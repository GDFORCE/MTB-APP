// Remote Audit landing — the sponsor-side entry point that replaces the
// Notifs tab (Production-Grade Implementation Plan v1.3, Phase 11).
//
// Notifications are NOT deleted. They move to the bell in this screen's header,
// exactly as the plan specifies: the tab slot changes owner, the feature stays.
// Every other sponsor screen already has that same bell, so nothing became
// unreachable.
//
// The list shows what a reviewer can act on. Note that org membership alone
// earns only this minimal view — id, state, site. Opening an audit requires an
// explicit participant record on that specific audit, which the server enforces
// and this screen does not try to second-guess: it shows what the API returned.
import React, { useCallback, useState } from "react";
import {
  ActivityIndicator, Pressable, RefreshControl, ScrollView, StatusBar, StyleSheet, Text, View,
} from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Bell, FolderOpen, ShieldCheck } from "lucide-react-native";

import { useAuth } from "@/src/auth/AuthContext";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import { SponsorBottomNav } from "@/src/features/sponsor/components/SponsorBottomNav";
import { fetchAudits } from "@/src/features/remote-audit/api";
import type { AuditSummary } from "@/src/features/remote-audit/types";
import { colors, fonts, shadows } from "@/src/theme/tokens";

const STATE_TONE: Record<string, string> = {
  draft: colors.mutedFg,
  in_progress: colors.info,
  under_review: colors.warning,
  decided: colors.success,
  closed: colors.mutedFg,
};

export default function SponsorRemoteAudits() {
  const router = useRouter();
  const { user } = useAuth();
  const unread = useUnreadCount();

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

  const initials = (user?.avatar_initials
    || (user?.full_name || "").split(" ").map((p) => p[0]).join("").slice(0, 2)
    || "?").toUpperCase();
  const roleLabel = (user?.role || "sponsor").toUpperCase();

  return (
    <View style={s.page}>
      <StatusBar barStyle="light-content" backgroundColor={colors.primaryDeep} />
      <SafeAreaView edges={["top"]} style={s.header}>
        <View style={s.headerIdentity}>
          <Text style={s.headerEyebrow} numberOfLines={1}>
            {roleLabel}{user?.organization ? ` · ${user.organization}` : ""}
          </Text>
          <Text style={s.headerTitle}>Remote audits</Text>
        </View>
        {/* Notifications live here now — same bell as every other sponsor
            screen, so nothing was lost when the tab changed hands. */}
        <Pressable
          onPress={() => router.push("/(app)/sponsor/notifications" as never)}
          style={s.iconButton}
          accessibilityLabel="Notifications"
        >
          <Bell size={18} color={colors.primaryFg} />
          {!!unread && unread > 0 ? (
            <View style={s.notifBadge}>
              <Text style={s.notifBadgeText}>{Math.min(9, unread)}</Text>
            </View>
          ) : null}
        </Pressable>
        <Pressable onPress={() => router.push("/(app)/sponsor/profile" as never)} style={s.iconButton}>
          <Text style={s.avatarText}>{initials}</Text>
        </Pressable>
      </SafeAreaView>

      {loading ? (
        <View style={s.centered}><ActivityIndicator color={colors.primary} /></View>
      ) : (
        <ScrollView
          contentContainerStyle={s.body}
          refreshControl={
            <RefreshControl refreshing={refreshing} tintColor={colors.primary}
              onRefresh={() => { setRefreshing(true); void load(); }} />
          }
        >
          {error ? <View style={s.card}><Text style={s.cardBody}>{error}</Text></View> : null}

          {!error && audits.length === 0 ? (
            <View style={s.card}>
              <Text style={s.cardTitle}>No audits yet</Text>
              <Text style={s.cardBody}>
                Audits you&apos;re assigned to appear here. Being in the organisation isn&apos;t
                enough on its own — someone has to add you to the specific audit.
              </Text>
            </View>
          ) : null}

          {audits.map((audit) => {
            const tone = STATE_TONE[audit.state] ?? colors.mutedFg;
            return (
              <View key={audit.id} style={s.card}>
                <View style={s.rowBetween}>
                  <Text style={s.cardTitle} numberOfLines={1}>Site {audit.siteId}</Text>
                  <View style={[s.pill, { backgroundColor: tone + "22", borderColor: tone }]}>
                    <Text style={[s.pillText, { color: tone }]}>
                      {audit.state.replace(/_/g, " ")}
                    </Text>
                  </View>
                </View>
                <Text style={s.cardMeta} numberOfLines={1}>{audit.id}</Text>

                <View style={s.actions}>
                  <Pressable
                    style={s.action}
                    onPress={() => router.push(`/(app)/remote-audit/${audit.id}` as never)}
                  >
                    <FolderOpen size={15} color={colors.primary} />
                    <Text style={s.actionText}>Open audit</Text>
                  </Pressable>
                  <Pressable
                    style={s.action}
                    onPress={() => router.push(`/(app)/sponsor/remote-audit/${audit.id}/decision` as never)}
                  >
                    <ShieldCheck size={15} color={colors.primary} />
                    <Text style={s.actionText}>Score &amp; decide</Text>
                  </Pressable>
                </View>
              </View>
            );
          })}
        </ScrollView>
      )}

      <SponsorBottomNav active="audits" unread={unread ?? 0} />
    </View>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.background },
  centered: { flex: 1, alignItems: "center", justifyContent: "center" },
  header: {
    minHeight: 74, paddingHorizontal: 18, paddingTop: 8, paddingBottom: 13,
    flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.primaryDeep,
  },
  headerIdentity: { flex: 1, minWidth: 0 },
  headerEyebrow: {
    fontFamily: fonts.semibold, fontSize: 9, letterSpacing: 1.1,
    color: "rgba(255,255,255,0.64)", textTransform: "uppercase",
  },
  headerTitle: { marginTop: 2, fontFamily: fonts.heading, fontSize: 20, color: colors.white },
  iconButton: {
    width: 38, height: 38, borderRadius: 19, alignItems: "center", justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.15)", borderWidth: 1, borderColor: "rgba(255,255,255,0.2)",
  },
  avatarText: { fontFamily: fonts.bold, fontSize: 12, color: colors.primaryFg },
  notifBadge: {
    position: "absolute", top: -2, right: -2, minWidth: 16, height: 16, borderRadius: 8,
    paddingHorizontal: 3, alignItems: "center", justifyContent: "center",
    backgroundColor: colors.destructive, borderWidth: 2, borderColor: colors.primaryDeep,
  },
  notifBadgeText: { fontFamily: fonts.bold, fontSize: 8, color: colors.white },
  body: { padding: 14, gap: 12, paddingBottom: 32 },
  card: {
    backgroundColor: colors.card, borderRadius: 18, borderWidth: 1, borderColor: colors.border,
    padding: 14, gap: 6, ...shadows.sm,
  },
  rowBetween: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  cardTitle: { flex: 1, fontFamily: fonts.semibold, fontSize: 15, color: colors.foreground },
  cardBody: { fontFamily: fonts.regular, fontSize: 13, color: colors.mutedFg, lineHeight: 18 },
  cardMeta: { fontFamily: fonts.regular, fontSize: 11, color: colors.mutedFg },
  pill: { paddingHorizontal: 9, paddingVertical: 3, borderRadius: 999, borderWidth: 1 },
  pillText: { fontFamily: fonts.semibold, fontSize: 10, textTransform: "capitalize" },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 6 },
  action: {
    flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 11, paddingVertical: 7,
    borderRadius: 999, borderWidth: 1, borderColor: colors.primary + "44",
    backgroundColor: colors.secondary,
  },
  actionText: { fontFamily: fonts.semibold, fontSize: 11, color: colors.primary },
});
