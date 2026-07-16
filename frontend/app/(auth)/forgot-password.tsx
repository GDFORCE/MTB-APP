import React, { useState } from "react";
import { View, TextInput, StyleSheet, Pressable } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { ArrowLeft, HelpCircle } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, H1, Small, Button } from "@/src/components/ui";
import { api } from "@/src/api/client";

export default function ForgotPassword() {
  const router = useRouter();
  const [step, setStep] = useState<"email" | "otp">("email");
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [newPw, setNewPw] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const sendOtp = async () => {
    setLoading(true); setErr("");
    try {
      const r = await api.post("/auth/forgot", { email: email.trim().toLowerCase() });
      setMsg(r.data.otp ? `Demo OTP: ${r.data.otp}` : "If that email exists, an OTP has been sent");
      setStep("otp");
    } catch (e: any) { setErr(e?.response?.data?.detail || "Error"); }
    finally { setLoading(false); }
  };
  const reset = async () => {
    setLoading(true); setErr("");
    try {
      await api.post("/auth/reset", { email: email.trim().toLowerCase(), otp, new_password: newPw });
      router.replace("/(auth)/sign-in");
    } catch (e: any) { setErr(e?.response?.data?.detail || "Reset failed"); }
    finally { setLoading(false); }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <View style={{ padding: spacing.lg }}>
        <Pressable onPress={() => router.back()} hitSlop={12}><ArrowLeft size={22} color={colors.foreground} /></Pressable>
        <Eyebrow color={colors.accent} style={{ marginTop: spacing.md }}>Recover access</Eyebrow>
        <H1 style={{ marginTop: 6 }}>{step === "email" ? "Forgot password?" : "Enter the code"}</H1>
        <Small style={{ marginTop: 6 }}>{step === "email" ? "We'll send you a one-time code." : msg}</Small>
        <View style={{ marginTop: spacing.xl, gap: spacing.md }}>
          {step === "email" ? (
            <>
              <TextInput testID="forgot-email" placeholder="Email" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" style={s.input} />
              {err ? <Small color={colors.destructive}>{err}</Small> : null}
              <Button testID="forgot-send-otp" onPress={sendOtp} loading={loading}>Send OTP</Button>
              <Pressable
                testID="forgot-contact-support"
                onPress={() => router.push("/(auth)/help-support")}
                accessibilityRole="link"
                style={s.supportLink}
              >
                <HelpCircle size={15} color={colors.mutedFg} />
                <Small>Need help? Contact Support</Small>
              </Pressable>
            </>
          ) : (
            <>
              <TextInput testID="forgot-otp" placeholder="6-digit OTP" value={otp} onChangeText={setOtp} keyboardType="number-pad" style={s.input} />
              <TextInput testID="forgot-newpw" placeholder="New password" value={newPw} onChangeText={setNewPw} secureTextEntry style={s.input} />
              {err ? <Small color={colors.destructive}>{err}</Small> : null}
              <Button testID="forgot-reset" onPress={reset} loading={loading}>Reset password</Button>
            </>
          )}
        </View>
      </View>
    </SafeAreaView>
  );
}
const s = StyleSheet.create({
  input: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground },
  supportLink: { marginTop: spacing.sm, paddingVertical: spacing.sm, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 },
});
