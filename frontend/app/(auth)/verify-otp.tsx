import React, { useEffect, useRef, useState } from "react";
import { View, Text, TextInput, StyleSheet, ScrollView, Pressable, KeyboardAvoidingView, Platform, Animated, Easing } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { Mail, Smartphone, Clock, ShieldOff, Check } from "lucide-react-native";
import { colors, spacing, radii, fonts, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, Small } from "@/src/components/ui";
import { AuthHeader } from "@/src/components/AuthHeader";
import { Rise } from "@/src/components/Rise";
import { Springy } from "@/src/components/Springy";
import { api } from "@/src/api/client";

const OTP_LEN = 6;
const OTP_DURATION = 120; // 2 minutes
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
  const complete = value.length === OTP_LEN;
  const Icon = channel === "phone" ? Smartphone : Mail;
  return (
    <View style={s.block}>
      <View style={s.blockHead}>
        <View style={s.channelChip}>
          <Icon size={15} color={colors.primary} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={s.channelText}>{channel === "phone" ? "Phone" : "Email"}</Text>
          <Text style={s.dest} numberOfLines={1}>{destination}</Text>
        </View>
        {complete ? (
          <View style={s.doneChip}>
            <Check size={13} color={colors.success} strokeWidth={3} />
          </View>
        ) : (
          <Text style={s.progressCount}>{value.length}/{OTP_LEN}</Text>
        )}
      </View>
      <Pressable onPress={() => ref.current?.focus()} style={s.cells}>
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

/**
 * Sunrise countdown rule — the code's remaining life drains as a dawn-gradient
 * track (mirrors the header's step progress and the password-strength bar), so
 * the whole registration flow shares one motion language. The fill retimes to
 * the current second on each tick, giving a continuous drain rather than steps.
 */
function CountdownTrack({ timeLeft, expired }: { timeLeft: number; expired: boolean }) {
  const fill = useRef(new Animated.Value(timeLeft / OTP_DURATION)).current;
  useEffect(() => {
    Animated.timing(fill, {
      toValue: Math.max(0, timeLeft / OTP_DURATION),
      duration: 1000,
      easing: Easing.linear,
      useNativeDriver: false,
    }).start();
  }, [fill, timeLeft]);
  return (
    <View style={s.track}>
      <Animated.View style={{ height: "100%", width: fill.interpolate({ inputRange: [0, 1], outputRange: ["0%", "100%"] }) }}>
        <LinearGradient
          colors={expired ? [colors.border, colors.border] : (dawnGradient as any)}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 0 }}
          style={{ flex: 1, borderRadius: 3 }}
        />
      </Animated.View>
    </View>
  );
}

export default function VerifyOtp() {
  const router = useRouter();
  const params = useLocalSearchParams<{ registration_id: string; channels: string; email: string; phone: string; role: string }>();
  const channels: string[] = (() => { try { return JSON.parse(params.channels || "[]"); } catch { return []; } })();
  const needEmail = channels.includes("email");
  const needPhone = channels.includes("phone");
  const invalidChannels = !params.registration_id
    || (!needEmail && !needPhone)
    || channels.some((channel) => channel !== "email" && channel !== "phone");

  const [emailOtp, setEmailOtp] = useState("");
  const [phoneOtp, setPhoneOtp] = useState("");
  const [timeLeft, setTimeLeft] = useState(OTP_DURATION);
  const [resendCounts, setResendCounts] = useState({ email: 0, phone: 0 });
  const [resending, setResending] = useState<"email" | "phone" | null>(null);
  const [resendResults, setResendResults] = useState<{ email?: string; phone?: string }>({});
  const [serverExpired, setServerExpired] = useState(false);
  const [isLocked, setIsLocked] = useState(false);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (timeLeft <= 0 || isLocked) return;
    const t = setTimeout(() => setTimeLeft((x) => x - 1), 1000);
    return () => clearTimeout(t);
  }, [timeLeft, isLocked]);

  const formatTime = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;
  const expired = timeLeft <= 0;
  const urgent = !expired && timeLeft <= 15;
  const allComplete = !invalidChannels
    && (!needEmail || emailOtp.length === OTP_LEN)
    && (!needPhone || phoneOtp.length === OTP_LEN)
    && !loading;

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
      const detail = String(e?.response?.data?.detail || "");
      const normalized = detail.toLowerCase();
      if (normalized.includes("expired") || normalized.includes("restart registration")) {
        setServerExpired(true);
        setTimeLeft(0);
        setErr("These verification codes have expired. Restart registration to request new codes.");
      } else if (e?.response?.status === 429 || normalized.includes("too many incorrect")) {
        setIsLocked(true);
        setErr("Too many incorrect attempts. Restart registration to continue.");
      } else {
        setErr(detail || "Verification failed. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleResend = async (channel: "email" | "phone") => {
    if (
      timeLeft > 0
      || isLocked
      || serverExpired
      || resending
      || resendCounts[channel] >= MAX_RESEND
    ) return;
    setErr("");
    setResending(channel);
    setResendResults((current) => ({ ...current, [channel]: undefined }));
    try {
      const { data } = await api.post("/auth/register/resend", {
        registration_id: params.registration_id,
        channel,
      });
      const nextCount = Number(data?.resend_count || resendCounts[channel] + 1);
      setResendCounts((current) => ({ ...current, [channel]: nextCount }));
      setResendResults((current) => ({
        ...current,
        [channel]: `New ${channel} code sent successfully.`,
      }));
      setTimeLeft(OTP_DURATION);
      if (channel === "phone") setPhoneOtp("");
      if (channel === "email") setEmailOtp("");
    } catch (e: any) {
      const detail = String(e?.response?.data?.detail || "Could not resend the code.");
      const normalized = detail.toLowerCase();
      if (normalized.includes("resend limit") || normalized.includes("restart registration")) {
        setResendCounts((current) => ({ ...current, [channel]: MAX_RESEND }));
      }
      setResendResults((current) => ({ ...current, [channel]: detail }));
    } finally {
      setResending(null);
    }
  };

  const channelSummary = needPhone && needEmail ? "your phone and email" : needPhone ? "your phone" : "your email";
  const blocked = invalidChannels || isLocked || serverExpired;
  const blockedTitle = invalidChannels
    ? "Verification unavailable"
    : serverExpired
      ? "Verification expired"
      : "Account temporarily locked";
  const blockedCopy = invalidChannels
    ? "No valid verification channel was provided. Restart registration so we can securely verify your contact details."
    : serverExpired
      ? "These codes are no longer valid. Restart registration to request a fresh set."
      : "Too many incorrect attempts were made. Restart registration to continue securely.";

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <AuthHeader
          eyebrow="Step 4 of 5"
          title={blocked ? blockedTitle : "Verify your contact details"}
          subtitle={blocked ? undefined : `Enter the codes we sent to ${channelSummary} — this keeps your account secure.`}
          onBack={() => router.back()}
          step={4}
        />

        <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          {blocked ? (
            <Rise delay={120}>
              <View style={s.lockCard}>
                <View style={s.lockIcon}><ShieldOff size={28} color={colors.destructive} /></View>
                <Text style={{ fontFamily: fonts.heading, fontSize: 18, color: colors.destructive, marginBottom: 6 }}>{blockedTitle}</Text>
                <Small style={{ textAlign: "center", lineHeight: 20 }}>{blockedCopy}</Small>
                <Pressable
                  onPress={() => router.replace("/(auth)/register")}
                  style={({ pressed }) => [s.restartBtn, pressed && { opacity: 0.8 }]}
                >
                  <Small color={colors.primaryFg} weight="700">Restart registration</Small>
                </Pressable>
              </View>
            </Rise>
          ) : (
            <>
              <Rise delay={200}>
                <View style={s.otpPanel}>
                  {needPhone && <OtpRow channel="phone" destination={maskPhone(params.phone || "")} value={phoneOtp} onChange={setPhoneOtp} />}
                  {needPhone && needEmail ? <View style={s.divider} /> : null}
                  {needEmail && <OtpRow channel="email" destination={maskEmail(params.email || "")} value={emailOtp} onChange={setEmailOtp} />}
                </View>
              </Rise>

              <Rise delay={400}>
                <View style={s.metaCard}>
                  <View style={s.metaHead}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                      <Clock size={15} color={expired ? colors.destructive : urgent ? colors.accent : colors.mutedFg} />
                      <Eyebrow color={colors.mutedFg}>{expired ? "Code expired" : "Expires in"}</Eyebrow>
                    </View>
                    {!expired && (
                      <Text style={[s.countdown, urgent && { color: colors.destructive }]}>{formatTime(timeLeft)}</Text>
                    )}
                  </View>
                  <CountdownTrack timeLeft={timeLeft} expired={expired} />
                  <View style={s.resendList}>
                    {(["phone", "email"] as const)
                      .filter((channel) => channel === "phone" ? needPhone : needEmail)
                      .map((channel) => {
                        const count = resendCounts[channel];
                        const enabled = expired && count < MAX_RESEND && !resending;
                        const result = resendResults[channel];
                        return (
                          <View key={channel} style={s.resendItem}>
                            <View style={{ flex: 1 }}>
                              <Small weight="700">{channel === "phone" ? "Phone code" : "Email code"}</Small>
                              <Small color={colors.mutedFg}>{count}/{MAX_RESEND} resends used</Small>
                              {!!result && (
                                <Small
                                  color={result.toLowerCase().includes("success") ? colors.success : colors.destructive}
                                  style={{ marginTop: 3 }}
                                >
                                  {result}
                                </Small>
                              )}
                            </View>
                            <Pressable
                              onPress={() => handleResend(channel)}
                              disabled={!enabled}
                              hitSlop={8}
                              style={[s.resendBtn, enabled && s.resendBtnActive]}
                            >
                              <Small style={{
                                fontFamily: fonts.bold,
                                fontSize: 13,
                                color: enabled ? colors.primary : colors.mutedFg + "80",
                              }}>
                                {resending === channel
                                  ? "Sending…"
                                  : count >= MAX_RESEND
                                    ? "Limit reached"
                                    : "Resend"}
                              </Small>
                            </Pressable>
                          </View>
                        );
                      })}
                  </View>
                </View>
              </Rise>

              {err ? <Small color={colors.destructive} style={{ marginTop: 14, textAlign: "center" }}>{err}</Small> : null}
            </>
          )}
        </ScrollView>

        <View style={s.footer}>
          <Springy onPress={verify} disabled={!allComplete || blocked} style={[s.cta, !allComplete || blocked ? { backgroundColor: colors.surface } : { backgroundColor: colors.primary }]}>
            <Text style={{ fontFamily: fonts.bold, fontSize: 15, color: !allComplete || blocked ? colors.mutedFg : colors.primaryFg }}>{loading ? "Verifying…" : "Verify OTP"}</Text>
          </Springy>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  content: { flexGrow: 1, paddingHorizontal: spacing.lg, paddingTop: spacing.md, paddingBottom: spacing.lg },
  otpPanel: { borderRadius: radii.xl, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md, shadowColor: colors.foreground, shadowOpacity: 0.06, shadowRadius: 12, shadowOffset: { width: 0, height: 4 }, elevation: 2 },
  block: { width: "100%" },
  blockHead: { flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 12 },
  channelChip: { width: 34, height: 34, borderRadius: 999, backgroundColor: colors.secondary + "88", alignItems: "center", justifyContent: "center" },
  channelText: { fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 0.8, textTransform: "uppercase", color: colors.mutedFg },
  dest: { marginTop: 1, fontFamily: fonts.mono, fontSize: 13, color: colors.foreground, fontVariant: ["tabular-nums"] },
  doneChip: { width: 24, height: 24, borderRadius: 999, backgroundColor: colors.success + "1F", alignItems: "center", justifyContent: "center" },
  progressCount: { fontFamily: fonts.mono, fontSize: 12, color: colors.mutedFg + "B0" },
  cells: { flexDirection: "row", gap: 7 },
  cell: { flex: 1, height: 52, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, alignItems: "center", justifyContent: "center" },
  cellFilled: { borderColor: colors.primary, backgroundColor: colors.secondary + "55" },
  cellCursor: { borderColor: colors.accent, borderWidth: 1.5, backgroundColor: colors.card },
  cellText: { fontFamily: fonts.mono, fontSize: 20, color: colors.foreground },
  hiddenInput: { position: "absolute", width: 1, height: 1, opacity: 0 },
  divider: { height: 1, backgroundColor: colors.border, marginVertical: spacing.md },
  metaCard: { marginTop: spacing.lg, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, padding: spacing.md },
  metaHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 12 },
  countdown: { fontFamily: fonts.mono, fontSize: 15, color: colors.foreground, fontVariant: ["tabular-nums"] },
  track: { height: 6, borderRadius: 3, backgroundColor: colors.surface, overflow: "hidden" },
  resendList: { marginTop: 14, gap: 10 },
  resendItem: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  resendBtn: { paddingHorizontal: 16, paddingVertical: 8, borderRadius: radii.pill, alignItems: "center" },
  resendBtnActive: { backgroundColor: colors.secondary },
  lockCard: { borderRadius: radii.xl, borderWidth: 1, borderColor: colors.destructive + "40", backgroundColor: colors.destructive + "0D", padding: spacing.lg, alignItems: "center" },
  lockIcon: { width: 56, height: 56, borderRadius: 999, backgroundColor: colors.destructive + "1A", alignItems: "center", justifyContent: "center", marginBottom: 16 },
  restartBtn: { marginTop: 18, minHeight: 42, paddingHorizontal: 20, borderRadius: radii.pill, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
  footer: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.background },
  cta: { paddingVertical: 15, borderRadius: radii.pill, alignItems: "center", justifyContent: "center" },
});
