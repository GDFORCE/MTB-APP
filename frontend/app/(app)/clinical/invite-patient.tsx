import React, { useState } from "react";
import { View, ScrollView, TextInput, StyleSheet, KeyboardAvoidingView, Platform } from "react-native";
import { useRouter } from "expo-router";
import { CheckCircle2 } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";

export default function InvitePatient() {
  const router = useRouter();
  const [email, setEmail] = useState(""), [phone, setPhone] = useState(""), [name, setName] = useState(""), [done, setDone] = useState(false);

  const send = () => {
    if (!email && !phone) return;
    // Demo: just acknowledge — real impl would call POST /api/invitations.
    setDone(true);
    setTimeout(() => router.back(), 1500);
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Send invitation" title="Invite Patient" />
      {done ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.lg }}>
          <View style={s.successBox}><CheckCircle2 size={36} color={colors.success} /></View>
          <Body weight="700" style={{ marginTop: spacing.md, fontSize: 18 }}>Invitation sent!</Body>
          <Small style={{ marginTop: 4 }}>They'll receive a registration link by email/SMS.</Small>
        </View>
      ) : (
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
          <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }} keyboardShouldPersistTaps="handled">
            <Card>
              <Eyebrow style={{ marginBottom: spacing.sm }}>How it works</Eyebrow>
              <Small>The patient receives a secure link to register and download the app. Their record is auto-linked to your trial.</Small>
            </Card>
            <Eyebrow style={{ marginTop: spacing.lg, marginBottom: spacing.sm }}>Patient details</Eyebrow>
            <Field label="Full name" value={name} onChange={setName} testID="invite-name" />
            <Field label="Email" value={email} onChange={setEmail} testID="invite-email" keyboardType="email-address" />
            <Field label="Phone (optional)" value={phone} onChange={setPhone} testID="invite-phone" keyboardType="phone-pad" />
          </ScrollView>
          <View style={{ padding: spacing.md }}><Button testID="invite-send" onPress={send}>Send invitation</Button></View>
        </KeyboardAvoidingView>
      )}
    </ScreenContainer>
  );
}

function Field({ label, value, onChange, testID, keyboardType }: any) {
  return (
    <View style={{ marginBottom: spacing.md }}>
      <Small color={colors.foreground} style={{ marginBottom: 6, fontWeight: "600" as any }}>{label}</Small>
      <TextInput testID={testID} value={value} onChangeText={onChange} keyboardType={keyboardType} autoCapitalize={keyboardType === "email-address" ? "none" : "sentences"} style={s.input} />
    </View>
  );
}

const s = StyleSheet.create({
  input: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground },
  successBox: { width: 80, height: 80, borderRadius: 40, backgroundColor: colors.success + "1A", alignItems: "center", justifyContent: "center" },
});
