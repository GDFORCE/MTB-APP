import React, { useEffect, useState } from "react";
import { View, ScrollView, TextInput, Pressable, StyleSheet, KeyboardAvoidingView, Platform, ActivityIndicator } from "react-native";
import { useRouter, useLocalSearchParams } from "expo-router";
import { Plus, Trash2, Check, Sparkles } from "lucide-react-native";
import * as DocumentPicker from "expo-document-picker";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

type Row = { name: string; day_offset: string; window_days: string; activities: string };
type ExtractedVisit = { name: string; day_offset: number; window_days: number; activities: string[] };

export default function VisitScheduleEditor() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [rows, setRows] = useState<Row[]>([
    { name: "Screening", day_offset: "0", window_days: "3", activities: "Informed consent, Vitals" },
    { name: "Baseline", day_offset: "7", window_days: "3", activities: "Physical exam, Blood draw" },
    { name: "Week 4", day_offset: "28", window_days: "3", activities: "Vitals, Blood draw" },
  ]);
  const [saving, setSaving] = useState(false), [err, setErr] = useState("");
  const [extracting, setExtracting] = useState(false), [extractErr, setExtractErr] = useState("");

  const upd = (i: number, k: keyof Row, v: string) => setRows(prev => prev.map((r, j) => j === i ? { ...r, [k]: v } : r));
  const add = () => setRows(prev => [...prev, { name: `Visit ${prev.length + 1}`, day_offset: "0", window_days: "3", activities: "" }]);
  const rm = (i: number) => setRows(prev => prev.filter((_, j) => j !== i));

  // AI-assisted: upload the protocol PDF, let Claude extract its Schedule of
  // Assessments, and pre-fill the rows below for the sponsor to review + edit.
  // Nothing is saved until they hit "Save & preview" via the normal flow.
  const autofill = async () => {
    setExtractErr("");
    let asset: DocumentPicker.DocumentPickerAsset;
    try {
      const res = await DocumentPicker.getDocumentAsync({ type: "application/pdf", copyToCacheDirectory: true, multiple: false });
      if (res.canceled || !res.assets?.length) return;
      asset = res.assets[0];
    } catch {
      setExtractErr("Couldn't open the file picker."); return;
    }
    const form = new FormData();
    if (Platform.OS === "web") {
      const file = (asset as any).file as File | undefined;
      if (!file) { setExtractErr("Couldn't read the selected file."); return; }
      form.append("file", file, asset.name || "protocol.pdf");
    } else {
      form.append("file", { uri: asset.uri, name: asset.name || "protocol.pdf", type: asset.mimeType || "application/pdf" } as any);
    }
    setExtracting(true);
    try {
      const r = await api.post(`/trials/${id}/extract-schedule`, form, {
        headers: { "Content-Type": "multipart/form-data" }, timeout: 120000,
      });
      const visits: ExtractedVisit[] = r.data?.visits ?? [];
      if (!visits.length) { setExtractErr("No visit schedule was found in that PDF — you can still build it manually below."); return; }
      setRows(visits.map(v => ({
        name: v.name ?? "",
        day_offset: String(v.day_offset ?? 0),
        window_days: String(v.window_days ?? 3),
        activities: (v.activities ?? []).join(", "),
      })));
    } catch (e: any) {
      const status = e?.response?.status;
      setExtractErr(e?.response?.data?.detail
        || (status === 503 ? "AI extraction isn't configured on the server yet."
          : "Couldn't extract the schedule. Try again, or build it manually below."));
    } finally {
      setExtracting(false);
    }
  };

  const save = async () => {
    setSaving(true); setErr("");
    try {
      for (let i = 0; i < rows.length; i++) {
        const r = rows[i];
        await api.post("/visits", {
          trial_id: id, visit_number: i + 1, name: r.name,
          day_offset: parseInt(r.day_offset || "0", 10), window_days: parseInt(r.window_days || "3", 10),
          activities: r.activities.split(",").map(s => s.trim()).filter(Boolean),
        });
      }
      router.replace({ pathname: "/(app)/clinical/trial-summary", params: { id } });
    } catch (e: any) { setErr(e?.response?.data?.detail || "Save failed"); }
    finally { setSaving(false); }
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Build schedule" title="Visit Schedule" />
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }} keyboardShouldPersistTaps="handled">
          <Card>
            <Eyebrow style={{ marginBottom: 6 }}>How it works</Eyebrow>
            <Small>Each visit has a day offset from enrollment and a window (± days). Activities are comma-separated.</Small>
            <Pressable testID="extract-protocol" onPress={autofill} disabled={extracting} style={[s.aiBtn, extracting && { opacity: 0.7 }]}>
              {extracting
                ? <ActivityIndicator size="small" color={colors.primary} />
                : <Sparkles size={18} color={colors.primary} />}
              <Small color={colors.primary} weight="700">{extracting ? "Reading protocol…" : "Auto-fill from protocol PDF"}</Small>
            </Pressable>
            <Small color={colors.mutedFg} style={{ marginTop: 6 }}>Upload the protocol and we'll draft the visit schedule from its Schedule of Assessments. Review and edit before saving.</Small>
            {extractErr ? <Small color={colors.destructive} style={{ marginTop: 6 }}>{extractErr}</Small> : null}
          </Card>
          {rows.map((r, i) => (
            <Card key={i} style={{ marginTop: spacing.md }}>
              <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.sm }}>
                <Body weight="700">Visit {i + 1}</Body>
                <Pressable testID={`remove-visit-${i}`} onPress={() => rm(i)} hitSlop={8}><Trash2 size={18} color={colors.destructive} /></Pressable>
              </View>
              <Small color={colors.foreground} style={{ fontWeight: "600" as any, marginBottom: 4 }}>Name</Small>
              <TextInput testID={`vname-${i}`} value={r.name} onChangeText={v => upd(i, "name", v)} style={s.input} />
              <View style={{ flexDirection: "row", gap: 8, marginTop: 8 }}>
                <View style={{ flex: 1 }}>
                  <Small color={colors.foreground} style={{ fontWeight: "600" as any, marginBottom: 4 }}>Day offset</Small>
                  <TextInput testID={`vday-${i}`} value={r.day_offset} onChangeText={v => upd(i, "day_offset", v)} keyboardType="number-pad" style={s.input} />
                </View>
                <View style={{ flex: 1 }}>
                  <Small color={colors.foreground} style={{ fontWeight: "600" as any, marginBottom: 4 }}>Window ±d</Small>
                  <TextInput testID={`vwin-${i}`} value={r.window_days} onChangeText={v => upd(i, "window_days", v)} keyboardType="number-pad" style={s.input} />
                </View>
              </View>
              <Small color={colors.foreground} style={{ fontWeight: "600" as any, marginBottom: 4, marginTop: 8 }}>Activities (comma-separated)</Small>
              <TextInput testID={`vact-${i}`} value={r.activities} onChangeText={v => upd(i, "activities", v)} style={s.input} />
            </Card>
          ))}
          <Pressable testID="add-visit-row" onPress={add} style={s.addBtn}><Plus size={18} color={colors.primary} /><Small color={colors.primary} weight="700">Add another visit</Small></Pressable>
          {err ? <Small color={colors.destructive} style={{ marginTop: spacing.sm }}>{err}</Small> : null}
        </ScrollView>
        <View style={{ padding: spacing.md }}><Button testID="save-schedule" onPress={save} loading={saving}><View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><Check size={14} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">Save & preview</Small></View></Button></View>
      </KeyboardAvoidingView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  input: { backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: radii.sm, paddingHorizontal: 12, paddingVertical: 10, fontSize: 14, color: colors.foreground },
  addBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, padding: 14, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.primary + "55", borderStyle: "dashed", marginTop: spacing.md, backgroundColor: colors.secondary + "44" },
  aiBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 12, paddingHorizontal: 14, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.primary + "55", marginTop: spacing.md, backgroundColor: colors.primary + "12" },
});
