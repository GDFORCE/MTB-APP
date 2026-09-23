import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ClipboardCheck, FlaskConical, LayoutGrid, MessageCircle, UserRound } from "lucide-react-native";
import { colors, fonts, shadows } from "@/src/theme/tokens";

// "sites" and "notifs" are kept in the union but NOT in the tab bar:
//   - "sites" so sites.tsx's existing `active="sites"` prop still type-checks
//     (Sites was replaced by Messages but stays reachable from Dashboard).
//   - "notifs" for the same reason after the Remote Audit module took the
//     fifth slot. Notifications were NOT removed — they moved to the bell in
//     each screen's header, which every sponsor screen already had. Passing
//     `active="notifs"` now simply highlights nothing, which is correct:
//     Notifications is a pushed screen, not a tab.
export type SponsorTab =
  | "dashboard" | "trials" | "chat" | "audits" | "me" | "sites" | "patients" | "notifs";

const tabs = [
  { key: "dashboard", label: "Dashboard", icon: LayoutGrid, route: "/(app)/sponsor/dashboard" },
  { key: "trials", label: "Trials", icon: FlaskConical, route: "/(app)/sponsor/trials" },
  { key: "chat", label: "Messages", icon: MessageCircle, route: "/(app)/chat" },
  { key: "audits", label: "Audits", icon: ClipboardCheck, route: "/(app)/sponsor/remote-audit" },
  { key: "me", label: "Me", icon: UserRound, route: "/(app)/sponsor/profile" },
] as const;

export function SponsorBottomNav({
  active,
  unread = 0,
  unreadMessages = 0,
}: {
  active: SponsorTab;
  unread?: number;
  unreadMessages?: number;
}) {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const bottomInset = Math.max(insets.bottom, 10);
  return (
    <View style={[styles.shell, { height: 58 + bottomInset, paddingBottom: bottomInset }]}>
      {tabs.map((tab) => {
        const selected = active === tab.key;
        const Icon = tab.icon;
        // `unread` is still accepted and still drives the header bell on every
        // screen — it just no longer has a tab to badge, since Notifications
        // is not a tab any more.
        const badgeCount = tab.key === "chat" ? unreadMessages : 0;
        return (
          <Pressable
            key={tab.key}
            accessibilityRole="button"
            accessibilityState={{ selected }}
            onPress={() => router.replace(tab.route as never)}
            style={({ pressed }) => [styles.item, pressed && { opacity: 0.65 }]}
          >
            <View style={[styles.iconWrap, selected && styles.iconWrapSelected]}>
              <Icon
                size={19}
                strokeWidth={selected ? 2.4 : 1.8}
                color={selected ? colors.primary : colors.mutedFg}
              />
              {badgeCount > 0 ? (
                <View style={styles.badge}>
                  <Text style={styles.badgeText}>{badgeCount > 9 ? "9+" : badgeCount}</Text>
                </View>
              ) : null}
            </View>
            <Text style={[styles.label, selected && styles.labelSelected]}>{tab.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  shell: {
    paddingHorizontal: 6,
    paddingTop: 7,
    flexDirection: "row",
    backgroundColor: colors.card,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    ...shadows.sm,
  },
  item: { flex: 1, alignItems: "center", justifyContent: "center", gap: 2 },
  iconWrap: {
    width: 34,
    height: 27,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
  },
  iconWrapSelected: { backgroundColor: colors.secondary },
  label: { fontFamily: fonts.medium, fontSize: 10, color: colors.mutedFg },
  labelSelected: { color: colors.primary, fontFamily: fonts.semibold },
  badge: {
    position: "absolute",
    right: -3,
    top: -4,
    minWidth: 16,
    height: 16,
    paddingHorizontal: 4,
    borderRadius: 8,
    backgroundColor: colors.destructive,
    borderWidth: 2,
    borderColor: colors.card,
    alignItems: "center",
    justifyContent: "center",
  },
  badgeText: { color: colors.white, fontFamily: fonts.bold, fontSize: 8 },
});
