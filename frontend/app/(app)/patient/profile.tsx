import React from "react";
import { View, ScrollView, Pressable, StyleSheet } from "react-native";
import { useRouter } from "expo-router";
import { User, Lock, Bell, FileText, HelpCircle, ChevronRight, LogOut, Pill, Info } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { useAuth } from "@/src/auth/AuthContext";

export default function Profile() {
  const { user, signOut } = useAuth();
  const router = useRouter();
  if (!user) return null;

  const items: { icon: any; label: string; onPress?: () => void; color?: string }[] = [
    { icon: User, label: "Edit profile" },
    { icon: Lock, label: "Change password" },
    { icon: Bell, label: "Notification preferences" },
    { icon: Pill, label: "Medication reminder", onPress: () => router.push("/(app)/patient/medication-reminder") },
    { icon: Info, label: "About this trial", onPress: () => router.push("/(app)/patient/about-trial") },
    { icon: FileText, label: "Terms & conditions" },
    { icon: HelpCircle, label: "Help & support" },
  ];

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Account" title="Profile & Settings" />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md }}>
        <Card>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
            <View style={s.avatar}><Body weight="700" color={colors.primary} style={{ fontSize: 20 }}>{user.avatar_initials}</Body></View>
            <View style={{ flex: 1 }}>
              <Body weight="700" style={{ fontSize: 17 }}>{user.full_name}</Body>
              <Small>{user.email}</Small>
              <Small color={colors.accent} style={{ marginTop: 2, textTransform: "capitalize" as any, fontWeight: "700" as any }}>{user.role}</Small>
            </View>
          </View>
        </Card>

        <Card padded={false}>
          {items.map((it, i) => (
            <Pressable key={i} testID={`profile-${it.label.toLowerCase().replace(/\s+/g, "-")}`} onPress={it.onPress} style={[s.row, i > 0 && { borderTopWidth: 1, borderTopColor: colors.border }]}>
              <View style={s.iconCircle}><it.icon size={18} color={colors.primary} /></View>
              <Body weight="500" style={{ flex: 1 }}>{it.label}</Body>
              <ChevronRight size={18} color={colors.mutedFg} />
            </Pressable>
          ))}
        </Card>

        <Pressable testID="profile-logout" onPress={async () => { await signOut(); router.replace("/(auth)/welcome"); }}>
          <Card style={{ borderColor: colors.destructive + "33" }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
              <View style={[s.iconCircle, { backgroundColor: colors.destructive + "1A" }]}><LogOut size={18} color={colors.destructive} /></View>
              <Body weight="700" color={colors.destructive}>Log out</Body>
            </View>
          </Card>
        </Pressable>
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  avatar: { width: 56, height: 56, borderRadius: 28, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: spacing.md, paddingVertical: 14 },
  iconCircle: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
});
