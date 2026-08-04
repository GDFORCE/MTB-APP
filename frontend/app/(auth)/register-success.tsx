import React, { useEffect, useMemo, useRef, useState } from "react";
import { View, Text, StyleSheet, Animated, Easing, ActivityIndicator, Modal, Pressable } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import Svg, { Path, Circle, Defs, LinearGradient as SvgGrad, Stop, Line } from "react-native-svg";
import { Building2, Check } from "lucide-react-native";
import { colors, spacing, radii, fonts, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, Body, Small } from "@/src/components/ui";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";
import { useAuth } from "@/src/auth/AuthContext";

const AnimatedPath = Animated.createAnimatedComponent(Path);
const ARC_LEN = 300; // >= actual path length; sweeps the stroke in on mount

export default function RegisterSuccess() {
  const router = useRouter();
  const { applySession } = useAuth();
  const { session } = useLocalSearchParams<{ session?: string }>();
  const [opening, setOpening] = useState(false);
  const [sessionError, setSessionError] = useState("");
  const [showOrganizationCreated, setShowOrganizationCreated] = useState(true);
  const createdSession = useMemo(() => {
    try {
      const parsed = JSON.parse(session || "");
      return parsed?.access_token && parsed?.refresh_token && parsed?.user ? parsed : null;
    } catch {
      return null;
    }
  }, [session]);

  const continueToApp = async () => {
    if (!createdSession) {
      router.replace("/(auth)/sign-in");
      return;
    }
    setOpening(true);
    setSessionError("");
    try {
      await applySession(createdSession);
    } catch {
      setSessionError("Your account was created, but automatic sign-in failed. Please sign in normally.");
      setOpening(false);
    }
  };

  // Sunrise choreography: the arc sweeps in, then the sun (the check) rises at its crest.
  const arc = useRef(new Animated.Value(0)).current;
  const sun = useRef(new Animated.Value(0)).current;
  const drift = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.timing(arc, { toValue: 1, duration: 1100, delay: 250, easing: Easing.out(Easing.cubic), useNativeDriver: false }).start();
    Animated.timing(sun, { toValue: 1, duration: 800, delay: 700, easing: Easing.out(Easing.back(1.4)), useNativeDriver: true }).start();
    Animated.loop(
      Animated.sequence([
        Animated.timing(drift, { toValue: 1, duration: 3600, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(drift, { toValue: 0, duration: 3600, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
      ]),
    ).start();
  }, [arc, drift, sun]);

  const dashOffset = arc.interpolate({ inputRange: [0, 1], outputRange: [ARC_LEN, 0] });
  const sunStyle = {
    opacity: sun,
    transform: [
      { scale: sun.interpolate({ inputRange: [0, 1], outputRange: [0.6, 1] }) },
      { translateY: sun.interpolate({ inputRange: [0, 1], outputRange: [10, 0] }) },
    ],
  };
  const petal = (o: number) => ({ transform: [{ translateY: drift.interpolate({ inputRange: [0, 1], outputRange: [0, o] }) }] });

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: spacing.xl }}>
        {/* ── Sunrise milestone ── */}
        <View style={s.motif}>
          <Animated.View style={[s.petalA, petal(-10)]} />
          <Animated.View style={[s.petalB, petal(-7)]} />
          <Animated.View style={[s.petalC, petal(-5)]} />

          <Svg viewBox="0 0 260 96" width="100%" height={110}>
            <Defs>
              <SvgGrad id="success-dawn" x1="0" y1="86" x2="260" y2="20">
                <Stop offset="0" stopColor={colors.dawnFrom} />
                <Stop offset="0.55" stopColor={colors.dawnMid} />
                <Stop offset="1" stopColor={colors.dawnTo} />
              </SvgGrad>
            </Defs>
            <Line x1="0" y1="86" x2="260" y2="86" stroke={colors.border} strokeWidth="1" />
            <AnimatedPath
              d="M14 86 C 60 26, 200 26, 246 86"
              stroke="url(#success-dawn)"
              strokeWidth="2.5"
              strokeLinecap="round"
              fill="none"
              strokeDasharray={ARC_LEN}
              strokeDashoffset={dashOffset as any}
            />
            <Circle cx="46" cy="60" r="4.5" fill={colors.dawnFrom} />
            <Circle cx="96" cy="38" r="4.5" fill={colors.dawnMid} />
            <Circle cx="164" cy="38" r="4.5" fill={colors.dawnMid} />
            <Circle cx="214" cy="60" r="4.5" fill={colors.dawnTo} />
          </Svg>

          {/* the sun — the check — rises into the crest */}
          <Animated.View style={[s.sunWrap, sunStyle]}>
            <View style={s.sunHalo} />
            <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.sun}>
              <Check size={32} color={colors.primaryFg} strokeWidth={2.75} />
            </LinearGradient>
          </Animated.View>
        </View>

        <Rise delay={260}><Eyebrow color={colors.accent} style={{ marginTop: spacing.xl, textAlign: "center" }}>Registration complete</Eyebrow></Rise>
        <Rise delay={340}>
          <Text style={s.title}>Welcome aboard<Text style={{ color: colors.dawnMid }}>.</Text></Text>
        </Rise>
        <Rise delay={420}>
          <Body color={colors.mutedFg} style={{ marginTop: 12, textAlign: "center", lineHeight: 22, maxWidth: 260 }}>
            Your account has been created successfully. Continue to open your trial board.
          </Body>
        </Rise>
        {!!sessionError && <Small color={colors.destructive} style={{ marginTop: 12, textAlign: "center" }}>{sessionError}</Small>}
      </View>

      <Rise delay={520} style={{ paddingHorizontal: spacing.lg, paddingBottom: spacing.lg }}>
        <Springy onPress={continueToApp} disabled={opening} style={[s.cta, { backgroundColor: colors.primary }]}>
          {opening
            ? <ActivityIndicator color={colors.primaryFg} />
            : <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: colors.primaryFg }}>
                {createdSession ? "Open My Trial Board" : "Go to Sign In"}
              </Text>}
        </Springy>
      </Rise>

      <Modal
        visible={showOrganizationCreated && Boolean(createdSession?.user?.org_admin && createdSession?.user?.organization)}
        transparent
        animationType="fade"
        onRequestClose={() => setShowOrganizationCreated(false)}
      >
        <View style={s.modalBackdrop}>
          <View style={s.modalCard}>
            <View style={s.modalIcon}>
              <Building2 size={26} color={colors.primary} />
            </View>
            <Eyebrow color={colors.accent} style={s.modalEyebrow}>Registration complete</Eyebrow>
            <Text style={s.modalTitle}>Organization Created</Text>
            <Body color={colors.mutedFg} style={s.modalBody}>
              {createdSession?.user?.organization} has been created successfully on the platform.
            </Body>
            <View style={s.modalNotice}>
              <Small color={colors.foreground} style={s.modalNoticeText}>
                You are now the Organization Admin and can invite authorized users and manage their access.
              </Small>
            </View>
            <Pressable
              testID="organization-created-continue"
              onPress={() => setShowOrganizationCreated(false)}
              style={s.modalButton}
            >
              <Text style={s.modalButtonText}>Continue</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  motif: { position: "relative", width: "100%", maxWidth: 280, justifyContent: "center" },
  title: { fontFamily: fonts.display, fontSize: 32, letterSpacing: -0.8, color: colors.foreground, textAlign: "center", marginTop: 8 },
  sunWrap: { position: "absolute", top: 0, alignSelf: "center", alignItems: "center", justifyContent: "center" },
  sun: { width: 68, height: 68, borderRadius: 34, alignItems: "center", justifyContent: "center" },
  sunHalo: { position: "absolute", width: 96, height: 96, borderRadius: 48, backgroundColor: colors.dawnFrom, opacity: 0.25 },
  petalA: { position: "absolute", top: -4, right: 40, width: 10, height: 10, borderRadius: 5, backgroundColor: colors.accent + "4D", zIndex: 2 },
  petalB: { position: "absolute", top: 40, left: 30, width: 8, height: 8, borderRadius: 4, backgroundColor: colors.primary + "33", zIndex: 2 },
  petalC: { position: "absolute", top: 14, left: 70, width: 6, height: 6, borderRadius: 3, backgroundColor: colors.dawnTo + "40", zIndex: 2 },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
  modalBackdrop: { flex: 1, backgroundColor: colors.primaryDeep + "80", alignItems: "center", justifyContent: "center", padding: spacing.md },
  modalCard: { width: "100%", maxWidth: 420, borderRadius: 28, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, padding: spacing.lg, alignItems: "center", shadowColor: "#2E1B33", shadowOpacity: 0.2, shadowRadius: 24, shadowOffset: { width: 0, height: 12 }, elevation: 12 },
  modalIcon: { width: 50, height: 50, borderRadius: 17, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center", marginBottom: 12 },
  modalEyebrow: { textAlign: "center", marginBottom: 5 },
  modalTitle: { color: colors.foreground, fontFamily: fonts.heading, fontSize: 22, textAlign: "center" },
  modalBody: { marginTop: 8, textAlign: "center", lineHeight: 21 },
  modalNotice: { width: "100%", marginTop: spacing.md, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md },
  modalNoticeText: { textAlign: "center", lineHeight: 20 },
  modalButton: { width: "100%", marginTop: spacing.lg, paddingVertical: 15, borderRadius: radii.pill, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
  modalButtonText: { color: colors.primaryFg, fontFamily: fonts.bold, fontSize: 14 },
});
