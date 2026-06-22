import React, { useState } from "react";
import { View, ScrollView, TextInput, StyleSheet, KeyboardAvoidingView, Platform } from "react-native";
import { useRouter } from "expo-router";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Small, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

export default function AddTrial() {
  const router = useRouter();
  const [title, setTitle] = useState(""), [proto, setProto] = useState("Protocol-"), [phase, setPhase] = useState("Phase II"), [cond, setCond] = useState(""), [desc, setDesc] = useState(""), [err, setErr] = useState(""), [loading, setLoading] = useState(false);

  const submit = async () => {
    if (!title || !proto || !cond) { setErr("Title, protocol ID, condition required"); return; }
    setLoading(true); setErr("");
    try {
      const r = await api.post("/trials", { title, protocol_id: proto, phase, condition: cond, description: desc, sponsor_name: "" });
      router.replace({ pathname: "/(app)/sponsor/visit-schedule", params: { id: r.data.id } });
    } catch (e: any) { setErr(e?.response?.data?.detail || "Failed"); }
    finally { setLoading(false); }
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="New study" title="Add Trial" />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }} keyboardShouldPersistTaps="handled">
          <Eyebrow style={{ marginBottom: spacing.sm }}>Trial details</Eyebrow>
          <F label="Study title" value={title} onChange={setTitle} testID="trial-title" />
          <F label="Protocol ID" value={proto} onChange={setProto} testID="trial-proto" />
          <F label="Phase" value={phase} onChange={setPhase} testID="trial-phase" />
          <F label="Condition / Indication" value={cond} onChange={setCond} testID="trial-condition" />
          <F label="Description" value={desc} onChange={setDesc} testID="trial-desc" multiline />
          {err ? <Small color={colors.destructive}>{err}</Small> : null}
        </ScrollView>
        <View style={{ padding: spacing.md }}><Button testID="add-trial-submit" onPress={submit} loading={loading}>Next: Build visit schedule</Button></View>
      </KeyboardAvoidingView>
    </ScreenContainer>
  );
}
function F({ label, value, onChange, testID, multiline }: any) {
  return (
    <View style={{ marginBottom: spacing.md }}>
      <Small color={colors.foreground} style={{ marginBottom: 6, fontWeight: "600" as any }}>{label}</Small>
      <TextInput testID={testID} value={value} onChangeText={onChange} multiline={multiline} style={[s.input, multiline && { minHeight: 80, textAlignVertical: "top" }]} />
    </View>
  );
}
const s = StyleSheet.create({ input: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: colors.foreground } });
