import React, { useEffect, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, ActivityIndicator } from "react-native";
import { useRouter, useLocalSearchParams } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { Calendar as CalIcon, Users, FlaskConical, Share2, FileText, Pencil, Upload, Download } from "lucide-react-native";
import * as DocumentPicker from "expo-document-picker";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";
import { uploadFile, downloadFile, UploadedFile } from "@/src/lib/upload";

export default function TrialSummary() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [trial, setTrial] = useState<any | null>(null);
  const [visits, setVisits] = useState<any[]>([]);
  const [patientCount, setPatientCount] = useState(0);

  // Trial documents. 5.1 exposes no trial-file LIST endpoint, so this is an
  // in-screen session list of docs uploaded during this visit to the screen.
  const [docs, setDocs] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [docErr, setDocErr] = useState("");
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const uploadDoc = async () => {
    if (uploading || !trial?.id) return;
    setDocErr("");
    let asset: DocumentPicker.DocumentPickerAsset;
    try {
      const res = await DocumentPicker.getDocumentAsync({
        type: ["application/pdf", "image/png", "image/jpeg", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
        copyToCacheDirectory: true, multiple: false,
      });
      if (res.canceled || !res.assets?.length) return;
      asset = res.assets[0];
    } catch { setDocErr("Couldn't open the file picker."); return; }
    setUploading(true);
    try {
      const uploaded = await uploadFile(
        { uri: asset.uri, name: asset.name || "document", mimeType: asset.mimeType, file: (asset as any).file },
        { scopeType: "trial", scopeId: trial.id },
      );
      setDocs((d) => [uploaded, ...d]);
    } catch (e: any) {
      setDocErr(e?.response?.data?.detail || "Upload failed. Allowed: pdf, png, jpg, docx (max 10 MB).");
    } finally { setUploading(false); }
  };

  const openDoc = async (f: UploadedFile) => {
    setDocErr(""); setDownloadingId(f.id);
    try { await downloadFile(f); }
    catch (e: any) { setDocErr(e?.message || "Couldn't open that file."); }
    finally { setDownloadingId(null); }
  };

  useEffect(() => { (async () => {
    const r = await api.get(`/trials/${id}`); setTrial(r.data); setVisits(r.data.visits || []);
    const p = await api.get("/patients").catch(() => ({ data: [] }));
    setPatientCount(p.data.filter((x: any) => x.trial_id === id).length);
  })(); }, [id]);

  if (!trial) return <ScreenContainer><ScreenHeader eyebrow="Trial" title="Loading…" /></ScreenContainer>;

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow={trial.protocol_id} title="Trial Summary" />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md }}>
        <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.hero}>
          <Eyebrow color={colors.overlay25}>Study title</Eyebrow>
          <H1 color={colors.primaryFg} style={{ marginTop: 4, fontSize: 18 }}>{trial.title}</H1>
          <View style={{ flexDirection: "row", gap: 8, marginTop: spacing.md, flexWrap: "wrap" }}>
            <View style={s.chip}><Small color={colors.primaryFg} weight="700">{trial.phase}</Small></View>
            <View style={s.chip}><Small color={colors.primaryFg} weight="700">{trial.condition}</Small></View>
            <View style={s.chip}><Small color={colors.primaryFg} weight="700" style={{ textTransform: "capitalize" }}>{trial.status}</Small></View>
          </View>
        </LinearGradient>

        <View style={{ flexDirection: "row", gap: spacing.sm }}>
          <Stat icon={<Users size={18} color={colors.primary} />} label="Patients" value={patientCount} />
          <Stat icon={<CalIcon size={18} color={colors.primary} />} label="Visits" value={visits.length} />
          <Stat icon={<FlaskConical size={18} color={colors.primary} />} label="Phase" value={trial.phase} />
        </View>

        <Card>
          <Eyebrow style={{ marginBottom: spacing.sm }}>Overview</Eyebrow>
          <Body>{trial.description}</Body>
        </Card>

        <Card>
          <Eyebrow style={{ marginBottom: spacing.sm }}>Visit schedule template</Eyebrow>
          {visits.slice(0, 6).map((v, i) => (
            <View key={v.id} style={[{ flexDirection: "row", justifyContent: "space-between", paddingVertical: 10 }, i > 0 && { borderTopWidth: 1, borderTopColor: colors.border }]}>
              <View style={{ flex: 1 }}>
                <Body weight="700">Visit {v.visit_number} · {v.name}</Body>
                <Small style={{ marginTop: 2 }}>Day {v.day_offset} · window ±{v.window_days}d</Small>
              </View>
            </View>
          ))}
          {visits.length > 6 && <Small color={colors.mutedFg} style={{ marginTop: 8 }}>+ {visits.length - 6} more visits</Small>}
        </Card>

        <Card>
          <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.sm }}>
            <Eyebrow>Trial documents</Eyebrow>
            <Pressable testID="upload-doc" onPress={uploadDoc} disabled={uploading} style={s.uploadBtn}>
              {uploading ? <ActivityIndicator size="small" color={colors.primary} /> : <Upload size={14} color={colors.primary} />}
              <Small color={colors.primary} weight="700">{uploading ? "Uploading…" : "Upload"}</Small>
            </Pressable>
          </View>
          {docs.length === 0 ? (
            <View style={{ alignItems: "center", paddingVertical: spacing.md, gap: 6 }}>
              <FileText size={26} color={colors.mutedFg + "66"} />
              <Small color={colors.mutedFg}>No documents uploaded in this session yet.</Small>
            </View>
          ) : (
            docs.map((f, i) => (
              <View key={f.id} style={[{ flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 10 }, i > 0 && { borderTopWidth: 1, borderTopColor: colors.border }]}>
                <View style={s.docIcon}><FileText size={16} color={colors.primary} /></View>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Body weight="700" numberOfLines={1} style={{ fontSize: 14 }}>{f.name}</Body>
                  <Small color={colors.mutedFg}>{(f.size / 1024).toFixed(0)} KB</Small>
                </View>
                <Pressable testID={`download-doc-${i}`} onPress={() => openDoc(f)} disabled={downloadingId === f.id} hitSlop={8} style={s.downloadBtn}>
                  {downloadingId === f.id ? <ActivityIndicator size="small" color={colors.primary} /> : <Download size={16} color={colors.primary} />}
                </Pressable>
              </View>
            ))
          )}
          {docErr ? <Small color={colors.destructive} style={{ marginTop: 8 }}>{docErr}</Small> : null}
        </Card>

        <View style={{ flexDirection: "row", gap: spacing.sm }}>
          <Button testID="edit-schedule" variant="secondary" style={{ flex: 1 }} onPress={() => router.push({ pathname: "/(app)/sponsor/visit-schedule", params: { id: trial.id } })}><View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><Pencil size={14} color={colors.primary} /><Small color={colors.primary} weight="700">Edit schedule</Small></View></Button>
          <Button testID="share-schedule" variant="primary" style={{ flex: 1 }} onPress={() => router.push("/(app)/sponsor/share-schedule")}><View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><Share2 size={14} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">Share</Small></View></Button>
        </View>
      </ScrollView>
    </ScreenContainer>
  );
}

function Stat({ icon, label, value }: any) {
  return (
    <Card style={{ flex: 1, alignItems: "center", marginBottom: 0 }}>
      <View style={s.statIcon}>{icon}</View>
      <Body weight="700" style={{ marginTop: 6, fontSize: 18 }}>{value}</Body>
      <Small>{label}</Small>
    </Card>
  );
}

const s = StyleSheet.create({
  hero: { borderRadius: radii.xl, padding: spacing.md },
  chip: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.overlay20 },
  statIcon: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  uploadBtn: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 7, borderRadius: radii.pill, borderWidth: 1, borderColor: colors.primary + "40", backgroundColor: colors.primary + "0F" },
  docIcon: { width: 34, height: 34, borderRadius: 10, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  downloadBtn: { width: 34, height: 34, borderRadius: 17, alignItems: "center", justifyContent: "center", backgroundColor: colors.secondary },
});
