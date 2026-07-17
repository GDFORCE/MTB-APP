import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { Bell, FlaskConical, LayoutGrid, MapPin, UserRound } from "lucide-react-native";
import { colors, fonts, shadows } from "@/src/theme/tokens";

export type SponsorTab = "dashboard" | "trials" | "sites" | "notifs" | "me";

const tabs = [
  { key: "dashboard", label: "Dashboard", icon: LayoutGrid, route: "/(app)/sponsor/dashboard" },
  { key: "trials", label: "Trials", icon: FlaskConical, route: "/(app)/sponsor/trials" },
  { key: "sites", label: "Sites", icon: MapPin, route: "/(app)/sponsor/sites" },
  { key: "notifs", label: "Notifs", icon: Bell, route: "/(app)/sponsor/notifications" },
  { key: "me", label: "Me", icon: UserRound, route: "/(app)/sponsor/profile" },
] as const;

export function SponsorBottomNav({
  active,
  unread = 0,
}: {
  active: SponsorTab;
  unread?: number;
}) {
  const router = useRouter();
  return (
    <View style={styles.shell}>
      {tabs.map((tab) => {
        const selected = active === tab.key;
        const Icon = tab.icon;
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
              {tab.key === "notifs" && unread > 0 ? (
                <View style={styles.badge}>
                  <Text style={styles.badgeText}>{unread > 9 ? "9+" : unread}</Text>
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
    height: 66,
    paddingHorizontal: 6,
    paddingTop: 7,
    paddingBottom: 5,
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
