import React from "react";
import { Linking, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { ChevronLeft, ChevronRight, CircleHelp, Clock3, Mail, MessageCircle, Phone, Ticket } from "lucide-react-native";
import { Body, Eyebrow, Small } from "@/src/components/ui";
import { colors, fonts, radii, shadows, spacing } from "@/src/theme/tokens";

const items = [
  { icon: CircleHelp, tint: colors.info, label: "Frequently Asked Questions", sub: "Browse common questions" },
  { icon: MessageCircle, tint: colors.success, label: "Contact Support", sub: "Get help from our team" },
  { icon: Ticket, tint: colors.violet, label: "My Tickets", sub: "Track your raised tickets" },
];

export default function HelpSupport() {
  const router = useRouter();

  return (
    <SafeAreaView style={s.container} edges={["top", "bottom"]}>
      <View style={s.header}>
        <Pressable onPress={() => router.back()} hitSlop={12} accessibilityRole="button" accessibilityLabel="Back to forgot password" style={s.back}>
          <ChevronLeft size={22} color={colors.primaryFg} />
        </Pressable>
        <View>
          <Eyebrow color={colors.overlay25}>Profile & settings</Eyebrow>
          <Text style={s.title}>Help & Support</Text>
        </View>
      </View>

      <ScrollView contentContainerStyle={s.body}>
        {items.map((item) => (
          <Pressable key={item.label} style={({ pressed }) => [s.card, pressed && s.pressed]}>
            <View style={s.row}>
              <View style={[s.icon, { backgroundColor: item.tint + "1A" }]}>
                <item.icon size={20} color={item.tint} />
              </View>
              <View>
                <Body weight="600">{item.label}</Body>
                <Small>{item.sub}</Small>
              </View>
            </View>
            <ChevronRight size={18} color={colors.mutedFg} />
          </Pressable>
        ))}

        <View style={[s.card, s.contactCard]}>
          <Eyebrow color={colors.primary}>Contact us</Eyebrow>
          <Pressable onPress={() => Linking.openURL("mailto:support@patientvisitschedule.com")} style={s.contactRow}>
            <View style={[s.smallIcon, { backgroundColor: colors.info + "1A" }]}><Mail size={15} color={colors.info} /></View>
            <Small>support@patientvisitschedule.com</Small>
          </Pressable>
          <Pressable onPress={() => Linking.openURL("tel:1800XXXXXXX")} style={s.contactRow}>
            <View style={[s.smallIcon, { backgroundColor: colors.success + "1A" }]}><Phone size={15} color={colors.success} /></View>
            <Small>1800-XXX-XXXX (Toll Free)</Small>
          </Pressable>
          <View style={s.contactRow}>
            <View style={[s.smallIcon, { backgroundColor: colors.warning + "1A" }]}><Clock3 size={15} color={colors.warning} /></View>
            <Small>Mon – Fri, 9:00 AM – 6:00 PM</Small>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  header: { minHeight: 76, paddingHorizontal: spacing.md, paddingVertical: 14, backgroundColor: colors.primaryDeep, flexDirection: "row", alignItems: "center", gap: 12 },
  back: { width: 36, height: 36, alignItems: "center", justifyContent: "center", borderRadius: radii.pill },
  title: { color: colors.primaryFg, fontFamily: fonts.heading, fontSize: 18, marginTop: 1 },
  body: { padding: spacing.md, gap: 10 },
  card: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, padding: spacing.md, flexDirection: "row", alignItems: "center", justifyContent: "space-between", ...shadows.sm },
  pressed: { opacity: 0.78, transform: [{ scale: 0.99 }] },
  row: { flexDirection: "row", alignItems: "center", gap: 12 },
  icon: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center" },
  contactCard: { marginTop: 2, flexDirection: "column", alignItems: "stretch", gap: 12 },
  contactRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  smallIcon: { width: 30, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center" },
});
