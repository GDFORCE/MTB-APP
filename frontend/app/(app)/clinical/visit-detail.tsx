import React, { useEffect, useState } from "react";
import { View, ScrollView, StyleSheet, Pressable } from "react-native";
import { useRouter, useLocalSearchParams } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { Phone, MessageCircle, Calendar as CalIcon, Check, X, FileText } from "lucide-react-native";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

// Clinical (PI/CRC) view of a patient — shows demographics + visit timeline.
export default function ClinicalVisitDetail() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [patient, setPatient] = useState<any | null>(null);

  useEffect(() => { (async () => {
    const r = await api.get("/patients");
    setPatient(r.data.find((p: any) => p.id === id) || r.data[0]);
  })(); }, [id]);

  if (!patient) return <ScreenContainer><ScreenHeader eyebrow="Patient record" title="Loading…" /></ScreenContainer>;

  const visits = [
    { num: 1, name: "Screening", date: "3 Mar 2025", status: "completed" },
    { num: 2, name: "Baseline", date: "10 Mar 2025", status: "completed" },
    { num: 3, name: "Week 2", date: "24 Mar 2025", status: "completed" },
    { num: 4, name: "Week 4", date: "7 Apr 2025", status: "completed" },
    { num: 5, name: "Week 8", date: "5 May 2025", status: "completed" },
    { num: 6, name: "Week 12", date: "19 May 2025", status: "completed" },
    { num: 7, name: "Week 14 Follow-Up", date: "23 May 2025", status: "upcoming" },
  ];

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Patient record" title={patient.full_name} />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md }}>
        <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.hero}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
            <View style={s.avatar}><Body weight="700" color={colors.primary} style={{ fontSize: 22 }}>{patient.avatar_initials}</Body></View>
            <View style={{ flex: 1 }}>
              <H1 color={colors.primaryFg} style={{ fontSize: 18 }}>{patient.full_name}</H1>
              <Small color={colors.overlay25}>Protocol-001 · Enrolled {patient.enrolled_date}</Small>
            </View>
          </View>
          <View style={{ flexDirection: "row", gap: 8, marginTop: spacing.md }}>
            <Pressable testID="contact-call" style={s.heroBtn} onPress={() => router.push("/(app)/chat")}><Phone size={14} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">Call</Small></Pressable>
            <Pressable testID="contact-chat" style={s.heroBtn} onPress={() => router.push("/(app)/chat")}><MessageCircle size={14} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">Chat</Small></Pressable>
          </View>
        </LinearGradient>

        <Card>
          <Eyebrow style={{ marginBottom: spacing.sm }}>Contact info</Eyebrow>
          <Row label="Email" value={patient.email} />
          <Row label="Phone" value={patient.phone || "—"} />
          <Row label="Enrolled" value={patient.enrolled_date} last />
        </Card>

        <View>
          <Eyebrow style={{ marginBottom: spacing.sm }}>Visit timeline</Eyebrow>
          {visits.map((v, i) => {
            const done = v.status === "completed";
            return (
              <Card key={v.num} style={{ marginBottom: spacing.sm }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                  <View style={[s.node, done && { backgroundColor: colors.accent, borderColor: colors.accent }]}>
                    {done ? <Check size={14} color={colors.primaryFg} /> : <Small weight="700">{v.num}</Small>}
                  </View>
                  <View style={{ flex: 1 }}>
                    <Body weight="700">Visit {v.num} · {v.name}</Body>
                    <View style={{ flexDirection: "row", gap: 4, alignItems: "center", marginTop: 2 }}>
                      <CalIcon size={11} color={colors.mutedFg} /><Small>{v.date}</Small>
                    </View>
                  </View>
                  <View style={[s.pill, { backgroundColor: done ? colors.accent + "22" : colors.warning + "22" }]}>
                    <Small weight="700" color={done ? colors.accent : colors.warning}>{done ? "Done" : "Next"}</Small>
                  </View>
                </View>
                {!done && (
                  <View style={{ flexDirection: "row", gap: 8, marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderColor: colors.border }}>
                    <Button testID={`mark-${v.num}-done`} variant="primary" style={{ flex: 1, paddingVertical: 10 }}><Small weight="700" color={colors.primaryFg}>Mark complete</Small></Button>
                    <Button testID={`reschedule-${v.num}`} variant="secondary" style={{ flex: 1, paddingVertical: 10 }}><Small weight="700" color={colors.primary}>Reschedule</Small></Button>
                  </View>
                )}
              </Card>
            );
          })}
        </View>

        <Button testID="view-records" variant="secondary"><View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><FileText size={14} color={colors.primary} /><Small weight="700" color={colors.primary}>View clinical records</Small></View></Button>
      </ScrollView>
    </ScreenContainer>
  );
}

function Row({ label, value, last }: { label: string; value: string; last?: boolean }) {
  return (
    <View style={[{ flexDirection: "row", justifyContent: "space-between", paddingVertical: 8 }, !last && { borderBottomWidth: 1, borderBottomColor: colors.border }]}>
      <Small>{label}</Small>
      <Small weight="700" color={colors.foreground}>{value}</Small>
    </View>
  );
}

const s = StyleSheet.create({
  hero: { borderRadius: radii.xl, padding: spacing.md },
  avatar: { width: 56, height: 56, borderRadius: 28, backgroundColor: colors.card, alignItems: "center", justifyContent: "center" },
  heroBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 10, borderRadius: 999, backgroundColor: colors.overlay20 },
  node: { width: 32, height: 32, borderRadius: 16, borderWidth: 2, borderColor: colors.border, backgroundColor: colors.card, alignItems: "center", justifyContent: "center" },
  pill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 },
});
