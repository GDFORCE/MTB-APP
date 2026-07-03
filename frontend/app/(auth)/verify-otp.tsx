import React, { useEffect, useRef, useState } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, KeyboardAvoidingView, Platform } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { Mail, Smartphone, Clock, ShieldOff } from "lucide-react-native";
import { colors, spacing, radii, fonts } from "@/src/theme/tokens";
import { Eyebrow, Small } from "@/src/components/ui";
import { AuthHeader } from "@/src/components/AuthHeader";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";
import { api } from "@/src/api/client";

const OTP_LEN = 6;
const OTP_DURATION = 300; // 5 minutes
const MAX_RESEND = 3;

// Keep the country code visible, mask the middle: "+91 ••••••3210".
function maskPhone(full: string): string {
  const digits = full.replace(/\D/g, "");
  if (!digits) return full;
  const local = full.includes("+91") && digits.length > 2 ? digits.slice(2) : digits;
  const last4 = local.slice(-4);
  return `+91 ${"•".repeat(Math.max(0, local.length - 4))}${last4}`;
}
// Keep first + last of the local part, domain visible: "j•••e@example.com".
function maskEmail(email: string): string {
  const [local, domain] = email.split("@");
  if (!domain) return email;
  if (local.length <= 2) return `${local[0]}•@${domain}`;
  return `${local[0]}${"•".repeat(Math.min(local.length - 2, 4))}${local[local.length - 1]}@${domain}`;
}

function OtpRow({ channel, destination, value, onChange }: { channel: "phone" | "email"; destination: string; value: string; onChange: (v: string) => void }) {
  const ref = useRef<TextInput>(null);
  const digits = value.split("");
  const Icon = channel === "phone" ? Smartphone : Mail;
  return (
    <View style={s.block}>
      <View style={s.blockHead}>
        <View style={s.iconBadge}><Icon size={14} color={colors.primary} /></View>
        <Eyebrow color={colors.mutedFg}>{channel === "phone" ? "Phone" : "Email"}</Eyebrow>
        <Text style={s.dest} numberOfLines={1}>{destination}</Text>
      </View>
      <Pressable onPress={() => ref.current?.focus()} style={{ flexDirection: "row", justifyContent: "space-between" }}>
        {Array.from({ length: OTP_LEN }).map((_, i) => {
          const filled = i < digits.length;
          const isCursor = i === digits.length;
          return (
            <View key={i} style={[s.cell, filled ? s.cellFilled : isCursor ? s.cellCursor : null]}>
              <Text style={s.cellText}>{digits[i] || ""}</Text>
            </View>
          );
        })}
        <TextInput
          ref={ref}
          value={value}
          onChangeText={(t) => onChange(t.replace(/\D/g, "").slice(0, OTP_LEN))}
          keyboardType="number-pad"
          maxLength={OTP_LEN}
          autoComplete="one-time-code"
          textContentType="oneTimeCode"
          style={s.hiddenInput}
        />
      </Pressable>
    </View>
  );
}

export default function VerifyOtp() {
  const router = useRouter();
  const params = useLocalSearchParams<{ registration_id: string; channels: string; email: string; phone: string; role: string }>();
  const channels: string[] = (() => { try { return JSON.parse(params.channels || "[]"); } catch { return []; } })();
  const needEmail = channels.includes("email");
  const needPhone = channels.includes("phone");

  const [emailOtp, setEmailOtp] = useState("");
  const [phoneOtp, setPhoneOtp] = useState("");
  const [timeLeft, setTimeLeft] = useState(OTP_DURATION);
  const [resendCount, setResendCount] = useState(0);
  const [isLocked, setIsLocked] = useState(false);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (timeLeft <= 0 || isLocked) return;
    const t = setTimeout(() => setTimeLeft((x) => x - 1), 1000);
    return () => clearTimeout(t);
  }, [timeLeft, isLocked]);

  const formatTime = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;
  const allComplete = (!needEmail || emailOtp.length === OTP_LEN) && (!needPhone || phoneOtp.length === OTP_LEN) && !loading;

  const verify = async () => {
    if (!allComplete || isLocked) return;
    setLoading(true); setErr("");
    try {
      const body: any = { registration_id: params.registration_id };
      if (needEmail) body.email_otp = emailOtp;
      if (needPhone) body.phone_otp = phoneOtp;
      const { data } = await api.post("/auth/register/verify", body);
      if (!data.verified) { setErr("Some codes were not accepted. Please check and try again."); return; }
      router.push({
        pathname: "/(auth)/set-password",
        params: { registration_id: params.registration_id, role: params.role },
      });
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Verification failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleResend = async () => {
    if (timeLeft > 0 || isLocked) return;
    const next = resendCount + 1;
    if (next >= MAX_RESEND) { setIsLocked(true); return; }
    setErr("");
    try {
      await api.post("/auth/register/resend", { registration_id: params.registration_id, channel: needPhone ? "phone" : "email" });
      if (needEmail && needPhone) await api.post("/auth/register/resend", { registration_id: params.registration_id, channel: "email" }).catch(() => {});
      setResendCount(next);
      setTimeLeft(OTP_DURATION);
      setPhoneOtp(""); setEmailOtp("");
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Could not resend the code.");
    }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <AuthHeader
          eyebrow="Step 4 of 5 · Verify"
          title={isLocked ? "Verification paused" : "Check your messages"}
          subtitle={isLocked ? undefined : needEmail && needPhone ? "We've sent a 6-digit code to both your phone and your email." : "We've sent a 6-digit code to your phone."}
          onBack={() => router.back()}
          step={4}
        />

        <ScrollView contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.lg }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          {isLocked ? (
            <Rise delay={120}>
              <View style={s.lockCard}>
                <View style={s.lockIcon}><ShieldOff size={28} color={colors.destructive} /></View>
                <Text style={{ fontFamily: fonts.heading, fontSize: 18, color: colors.destructive, marginBottom: 6 }}>Account temporarily locked</Text>
                <Small style={{ textAlign: "center", lineHeight: 20 }}>Too many resend attempts. Please contact support or try again after 30 minutes.</Small>
              </View>
            </Rise>
          ) : (
            <>
              {needPhone && <Rise delay={200}><OtpRow channel="phone" destination={maskPhone(params.phone || "")} value={phoneOtp} onChange={setPhoneOtp} /></Rise>}
              {needEmail && <Rise delay={300}><OtpRow channel="email" destination={maskEmail(params.email || "")} value={emailOtp} onChange={setEmailOtp} /></Rise>}

              <Rise delay={400}>
                <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: spacing.sm }}>
                  {timeLeft > 0 ? (
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                      <Clock size={16} color={colors.mutedFg} />
                      <Small>Expires in <Small color={colors.primary} style={{ fontFamily: fonts.bold }}>{formatTime(timeLeft)}</Small></Small>
                    </View>
                  ) : (
                    <Small color={colors.destructive} style={{ fontFamily: fonts.semibold }}>OTP expired</Small>
                  )}
                  <View style={{ alignItems: "flex-end" }}>
                    <Pressable onPress={handleResend} disabled={timeLeft > 0}>
                      <Small style={{ fontFamily: fonts.bold, color: timeLeft > 0 ? colors.mutedFg + "80" : colors.accent }}>Resend OTP</Small>
                    </Pressable>
                    <Text style={{ fontSize: 11, color: colors.mutedFg + "99", fontFamily: fonts.regular, marginTop: 2 }}>{resendCount}/{MAX_RESEND} resends used</Text>
                  </View>
                </View>
              </Rise>

              {err ? <Small color={colors.destructive} style={{ marginTop: 12, textAlign: "center" }}>{err}</Small> : null}
            </>
          )}
        </ScrollView>

        <View style={s.footer}>
          <Springy onPress={verify} disabled={!allComplete || isLocked} style={[s.cta, !allComplete || isLocked ? { backgroundColor: colors.surface } : { backgroundColor: colors.primary }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: !allComplete || isLocked ? colors.mutedFg : colors.primaryFg }}>{loading ? "Verifying…" : "Verify OTP"}</Text>
          </Springy>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  block: { marginBottom: spacing.md, padding: spacing.md, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  blockHead: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 12 },
  iconBadge: { width: 28, height: 28, borderRadius: 999, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  dest: { flex: 1, textAlign: "right", fontFamily: fonts.medium, fontSize: 14, color: colors.foreground, fontVariant: ["tabular-nums"] },
  cell: { width: 44, height: 54, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, alignItems: "center", justifyContent: "center" },
  cellFilled: { borderColor: colors.primary, backgroundColor: colors.secondary + "80", transform: [{ scale: 1.03 }] },
  cellCursor: { borderColor: colors.accent, backgroundColor: colors.card },
  cellText: { fontFamily: fonts.mono, fontSize: 20, color: colors.primary },
  hiddenInput: { position: "absolute", width: 1, height: 1, opacity: 0 },
  lockCard: { borderRadius: radii.xl, borderWidth: 1, borderColor: colors.destructive + "40", backgroundColor: colors.destructive + "0D", padding: spacing.lg, alignItems: "center" },
  lockIcon: { width: 56, height: 56, borderRadius: 999, backgroundColor: colors.destructive + "1A", alignItems: "center", justifyContent: "center", marginBottom: 16 },
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
});
