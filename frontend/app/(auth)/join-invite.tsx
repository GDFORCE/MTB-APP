import React, { useState } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, KeyboardAvoidingView, Platform } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { Ticket, Building2, BadgeCheck, ArrowRight, Mail } from "lucide-react-native";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Eyebrow, Body, Small } from "@/src/components/ui";
import { AuthHeader } from "@/src/components/AuthHeader";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";

// Resolved on the backend after validating the code. Mocked here (as in the design):
// the admin who sent the invite already chose the site and the role.
const RESOLVED_INVITE = {
  org: "Apollo Site 04",
  orgKind: "Site / Hospital",
  role: "Research Coordinator (CRC)",
  invitedBy: "Dr. Meera Nair",
  email: "you@apollosite04.org",
};
const SAMPLE_CODE = "MTB-2026";

export default function JoinInvite() {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [resolved, setResolved] = useState(false);

  const canVerify = code.trim().length >= 4;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <AuthHeader
          eyebrow="Join your team"
          title="Enter your invite"
          subtitle="Paste the code from the invitation your site admin sent you. No new site is created — you join theirs."
          onBack={() => router.back()}
        />

        <ScrollView contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          {/* Code entry */}
          <Rise delay={160}>
            <View style={s.card}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <View style={s.iconBadge}><Ticket size={14} color={colors.primary} /></View>
                <Eyebrow color={colors.mutedFg}>Invitation code</Eyebrow>
              </View>
              <TextInput
                value={code}
                onChangeText={(t) => { setCode(t.toUpperCase()); setResolved(false); }}
                autoCapitalize="characters"
                placeholder={SAMPLE_CODE}
                placeholderTextColor={colors.mutedFg + "66"}
                style={s.codeInput}
              />
              {!resolved && (
                <Pressable onPress={() => canVerify && setResolved(true)} disabled={!canVerify} style={[s.next, canVerify ? { backgroundColor: colors.secondary } : { backgroundColor: colors.surface }]}>
                  <Text style={{ fontFamily: fonts.bold, fontSize: 14, color: canVerify ? colors.primary : colors.mutedFg }}>Next</Text>
                </Pressable>
              )}
            </View>
          </Rise>

          {/* Resolved invitation */}
          {resolved && (
            <Rise delay={40}>
              <LinearGradient colors={[colors.secondary + "66", colors.card]} start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }} style={s.inviteCard}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 16 }}>
                  <BadgeCheck size={16} color={colors.accent} />
                  <Eyebrow color={colors.accent}>You've been invited</Eyebrow>
                </View>
                <View style={{ flexDirection: "row", alignItems: "flex-start", gap: 12 }}>
                  <View style={s.orgIcon}><Building2 size={20} color={colors.primary} /></View>
                  <View style={{ flex: 1 }}>
                    <Text style={{ fontFamily: fonts.heading, fontSize: 19, color: colors.primary }}>{RESOLVED_INVITE.org}</Text>
                    <Small>{RESOLVED_INVITE.orgKind}</Small>
                  </View>
                </View>
                <View style={s.dl}>
                  <Row label="Your role" value={RESOLVED_INVITE.role} />
                  <Row label="Invited by" value={RESOLVED_INVITE.invitedBy} />
                  <Row label="Account email" value={RESOLVED_INVITE.email} icon={<Mail size={14} color={colors.mutedFg} />} />
                </View>
                <View style={s.footNote}>
                  <Small style={{ fontSize: 12 }}>Your role is set by your admin and can't be changed here.</Small>
                </View>
              </LinearGradient>
            </Rise>
          )}
        </ScrollView>

        <View style={s.footer}>
          <Springy onPress={() => resolved && router.push("/(auth)/sign-in")} disabled={!resolved} style={[s.cta, resolved ? { backgroundColor: colors.primary } : { backgroundColor: colors.surface }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: resolved ? colors.primaryFg : colors.mutedFg }}>Accept & continue</Text>
            {resolved && <ArrowRight size={16} color={colors.primaryFg} />}
          </Springy>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Row({ label, value, icon }: { label: string; value: string; icon?: React.ReactNode }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
        {icon}
        <Small>{label}</Small>
      </View>
      <Body weight="700" style={{ fontSize: 13, flexShrink: 1, textAlign: "right" }}>{value}</Body>
    </View>
  );
}

const s = StyleSheet.create({
  card: { borderRadius: radii.xl, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md + 4 },
  iconBadge: { width: 28, height: 28, borderRadius: 999, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  codeInput: { borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, paddingHorizontal: 16, paddingVertical: 14, textAlign: "center", fontFamily: fonts.mono, fontSize: 18, letterSpacing: 6, color: colors.foreground },
  next: { marginTop: 12, paddingVertical: 12, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  inviteCard: { marginTop: spacing.lg, borderRadius: radii.xl, borderWidth: 1, borderColor: colors.primary + "40", padding: spacing.md + 4, overflow: "hidden" },
  orgIcon: { width: 44, height: 44, borderRadius: radii.md, backgroundColor: colors.primary + "1A", alignItems: "center", justifyContent: "center" },
  dl: { marginTop: 16, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 16, gap: 10 },
  footNote: { marginTop: 16, marginHorizontal: -(spacing.md + 4), marginBottom: -(spacing.md + 4), paddingHorizontal: spacing.md + 4, paddingVertical: 10, backgroundColor: colors.surface + "99", borderTopWidth: 1, borderTopColor: colors.border },
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { flexDirection: "row", gap: 8, paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
});
