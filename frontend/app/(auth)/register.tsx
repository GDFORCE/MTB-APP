import React, { useState } from "react";
import { View, TextInput, StyleSheet, ScrollView, Pressable, KeyboardAvoidingView, Platform } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { ArrowLeft } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, H1, Small, Button } from "@/src/components/ui";
import { useAuth } from "@/src/auth/AuthContext";

export default function Register() {
  const router = useRouter();
  const { role } = useLocalSearchParams<{ role: string }>();
  const { signUp } = useAuth();
  const [name, setName] = useState(""), [email, setEmail] = useState(""), [phone, setPhone] = useState(""), [org, setOrg] = useState(""), [pw, setPw] = useState(""), [err, setErr] = useState(""), [loading, setLoading] = useState(false);

  const submit = async () => {
    if (!name || !email || !pw) { setErr("Please fill all required fields"); return; }
    setLoading(true); setErr("");
    try {
      await signUp({ full_name: name, email: email.trim().toLowerCase(), phone, organization: org, password: pw, role: role || "patient", security_question: "What is your favorite color?", security_answer: "blue" });
    } catch (e: any) { setErr(e?.response?.data?.detail || "Registration failed"); }
    finally { setLoading(false); }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxl }} keyboardShouldPersistTaps="handled">
          <Pressable onPress={() => router.back()} hitSlop={12} style={{ marginBottom: spacing.md }}><ArrowLeft size={22} color={colors.foreground} /></Pressable>
          <Eyebrow color={colors.accent}>Step 2 of 5</Eyebrow>
          <H1 style={{ marginTop: 6 }}>Your details</H1>
          <Small style={{ marginTop: 6 }}>Joining as: {String(role).toUpperCase()}</Small>

          <View style={{ marginTop: spacing.xl, gap: spacing.md }}>
            <Field label="Full name" value={name} onChange={setName} testID="reg-name" />
            <Field label="Email" value={email} onChange={setEmail} testID="reg-email" keyboardType="email-address" />
            <Field label="Phone" value={phone} onChange={setPhone} testID="reg-phone" keyboardType="phone-pad" />
            {role !== "patient" && <Field label="Organization" value={org} onChange={setOrg} testID="reg-org" />}
            <Field label="Password" value={pw} onChange={setPw} testID="reg-password" secure />
            {err ? <Small color={colors.destructive}>{err}</Small> : null}
          </View>
        </ScrollView>
        <View style={{ padding: spacing.lg }}>
          <Button testID="register-submit-button" onPress={submit} loading={loading}>Create account</Button>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Field({ label, value, onChange, testID, keyboardType, secure }: any) {
  return (
    <View>
      <Small color={colors.foreground} style={{ marginBottom: 6, fontWeight: "600" as any }}>{label}</Small>
      <TextInput testID={testID} value={value} onChangeText={onChange} keyboardType={keyboardType} secureTextEntry={secure} autoCapitalize={keyboardType === "email-address" ? "none" : "sentences"} style={st.input} />
    </View>
  );
}
const st = StyleSheet.create({ input: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground } });
