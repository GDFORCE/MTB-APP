import React from "react";
import { View, StyleSheet, ScrollView, Pressable } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import Svg, { Path, Circle, Defs, LinearGradient as SvgGrad, Stop, Line } from "react-native-svg";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, Display, Body, Small, Button } from "@/src/components/ui";

export default function Welcome() {
  const router = useRouter();
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <ScrollView contentContainerStyle={s.container} showsVerticalScrollIndicator={false}>
        {/* Masthead */}
        <View style={s.masthead}>
          <LinearGradient colors={dawnGradient as any} style={s.logo} />
          <View style={{ flex: 1 }}>
            <Eyebrow>My Trial Board</Eyebrow>
            <Small>Patient Visit Schedule</Small>
          </View>
          <View style={s.pill}><Eyebrow color={colors.mutedFg}>Est. 2026</Eyebrow></View>
        </View>
        <View style={s.hairline} />

        {/* Headline */}
        <View style={{ marginTop: spacing.xl, paddingHorizontal: spacing.lg }}>
          <Display>Your trial,</Display>
          <Display>one <Display style={{ color: colors.dawnMid }}>sunrise</Display></Display>
          <Display>at a time<Display style={{ color: colors.primary }}>.</Display></Display>
          <Body color={colors.mutedFg} style={{ marginTop: spacing.md, maxWidth: 280 }}>
            One warm place for sponsors, sites and patients to follow a clinical trial — visit by visit, morning by morning.
          </Body>
        </View>

        {/* Sunrise arc */}
        <View style={{ paddingHorizontal: spacing.lg, marginTop: spacing.xl, alignItems: "center" }}>
          <Svg viewBox="0 0 340 130" width="100%" height={130}>
            <Defs>
              <SvgGrad id="arc" x1="0" y1="120" x2="340" y2="20">
                <Stop offset="0" stopColor={colors.dawnFrom} />
                <Stop offset="0.55" stopColor={colors.dawnMid} />
                <Stop offset="1" stopColor={colors.dawnTo} />
              </SvgGrad>
            </Defs>
            <Line x1="0" y1="112" x2="340" y2="112" stroke={colors.border} strokeWidth="1" />
            <Path d="M10 112 C 70 30, 270 30, 330 112" stroke="url(#arc)" strokeWidth="2.5" strokeLinecap="round" fill="none" />
            <Circle cx="58" cy="74" r="4.5" fill={colors.dawnFrom} />
            <Circle cx="118" cy="44" r="4.5" fill={colors.dawnMid} />
            <Circle cx="240" cy="49" r="4" fill="none" stroke={colors.dawnTo} strokeOpacity="0.5" strokeWidth="1.5" />
            <Circle cx="295" cy="84" r="4" fill="none" stroke={colors.dawnTo} strokeOpacity="0.35" strokeWidth="1.5" />
            <Circle cx="170" cy="32" r="14" fill={colors.dawnMid} />
          </Svg>
          <Eyebrow color={colors.mutedFg} style={{ textAlign: "center", marginTop: spacing.md }}>Screening · Baseline · Follow-up · Completion</Eyebrow>
        </View>

        {/* Actions */}
        <View style={{ paddingHorizontal: spacing.lg, marginTop: spacing.xl, gap: spacing.sm }}>
          <Button testID="welcome-signup-button" onPress={() => router.push("/(auth)/entity-type")}>Create an account</Button>
          <Button testID="welcome-signin-button" variant="secondary" onPress={() => router.push("/(auth)/sign-in")}>Sign in</Button>
          <Pressable testID="welcome-forgot-button" onPress={() => router.push("/(auth)/forgot-password")} style={{ paddingVertical: 6 }}>
            <Small color={colors.mutedFg} style={{ textAlign: "center" }}>Forgot password?</Small>
          </Pressable>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { paddingBottom: spacing.xl },
  masthead: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: spacing.lg, paddingTop: spacing.md },
  logo: { width: 40, height: 40, borderRadius: 12 },
  pill: { borderWidth: 1, borderColor: colors.border, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 4, backgroundColor: colors.card },
  hairline: { height: 1, backgroundColor: colors.border, marginHorizontal: spacing.lg, marginTop: spacing.md },
});
