import React, { useEffect, useState } from "react";
import { View, ScrollView, TextInput, StyleSheet, KeyboardAvoidingView, Platform } from "react-native";
import { useRouter } from "expo-router";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

export default function AddPatient() {
  const router = useRouter();
  const [trials, setTrials] = useState<any[]>([]);
  const [trialId, setTrialId] = useState<string>("");
  const [name, setName] = useState(""), [email, setEmail] = useState(""), [phone, setPhone] = useState(""), [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [err, setErr] = useState(""), [loading, setLoading] = useState(false);
  useEffect(() => { (async () => { const r = await api.get("/trials"); setTrials(r.data); if (r.data[0]) setTrialId(r.data[0].id); })(); }, []);

  const submit = async () => {
    if (!name || !email || !trialId) { setErr("Name, email, trial required"); return; }
    setLoading(true); setErr("");
    try {
      await api.post("/patients", { full_name: name, email: email.trim().toLowerCase(), phone, trial_id: trialId, enrolled_date: date });
      router.back();
    } catch (e: any) { setErr(e?.response?.data?.detail || "Failed"); }
    finally { setLoading(false); }
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="New enrollment" title="Add Patient" />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }} keyboardShouldPersistTaps="handled">
          <Eyebrow style={{ marginBottom: spacing.sm }}>Patient details</Eyebrow>
          <Field label="Full name" value={name} onChange={setName} testID="patient-name" />
          <Field label="Email" value={email} onChange={setEmail} testID="patient-email" keyboardType="email-address" />
          <Field label="Phone" value={phone} onChange={setPhone} testID="patient-phone" keyboardType="phone-pad" />
          <Field label="Enrollment date" value={date} onChange={setDate} testID="patient-date" />

          <Eyebrow style={{ marginTop: spacing.lg, marginBottom: spacing.sm }}>Trial</Eyebrow>
          {trials.map(t => (
            <Card key={t.id} style={{ marginBottom: spacing.sm, borderColor: trialId === t.id ? colors.primary : colors.border, borderWidth: trialId === t.id ? 2 : 1 }}>
              <View>
                <Body weight="700">{t.title}</Body>
                <Small style={{ marginTop: 2 }}>{t.protocol_id} · {t.phase}</Small>
                <Button testID={`select-trial-${t.id}`} variant={trialId === t.id ? "primary" : "secondary"} style={{ marginTop: spacing.sm }} onPress={() => setTrialId(t.id)}>{trialId === t.id ? "Selected" : "Select"}</Button>
              </View>
            </Card>
          ))}
          {err ? <Small color={colors.destructive} style={{ marginTop: spacing.sm }}>{err}</Small> : null}
        </ScrollView>
        <View style={{ padding: spacing.md }}><Button testID="add-patient-submit" onPress={submit} loading={loading}>Add Patient</Button></View>
      </KeyboardAvoidingView>
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
const s = StyleSheet.create({ input: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground } });
