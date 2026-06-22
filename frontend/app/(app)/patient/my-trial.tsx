import React, { useEffect, useState } from "react";
import { View, ScrollView, StyleSheet, Pressable } from "react-native";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { Check, Clock, Calendar as CalIcon, Building2, Phone, Home, Pill, Sparkles } from "lucide-react-native";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

type Med = { id: string; name: string; dosage: string; time: string; status: "taken" | "pending" | "notTaken" | "skipped"; takenAt?: string };

export default function MyTrial() {
  const router = useRouter();
  const [visits, setVisits] = useState<any[]>([]);
  const [tab, setTab] = useState<"visits" | "medications" | "progress">("visits");
  const [medTab, setMedTab] = useState<"today" | "schedule" | "history">("today");
  const [meds, setMeds] = useState<Med[]>([
    { id: "m1", name: "Metformin", dosage: "500mg", time: "8:00 AM", status: "taken", takenAt: "8:03 AM" },
    { id: "m2", name: "Aspirin", dosage: "75mg", time: "2:00 PM", status: "pending" },
    { id: "m3", name: "Metformin", dosage: "500mg", time: "8:00 PM", status: "pending" },
  ]);

  useEffect(() => { (async () => { try { const r = await api.get("/visits/mine"); setVisits(r.data); } catch {} })(); }, []);

  const completed = visits.filter(v => v.status === "completed").length;
  const total = visits.length || 10;
  const next = visits.find(v => v.status === "upcoming");
  const pct = Math.round((completed / total) * 100);
  const takenCount = meds.filter(m => m.status === "taken").length;
  const allDone = takenCount === meds.length;

  const setMedStatus = (id: string, st: Med["status"]) => {
    const t = new Date().toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
    setMeds(prev => prev.map(m => m.id === id ? { ...m, status: st, takenAt: st === "taken" ? t : undefined } : m));
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Protocol-001 · Dr. Sharma" title="My Trial" />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }} showsVerticalScrollIndicator={false}>
        {/* Journey progress */}
        <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.hero}>
          <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
            <Eyebrow color={colors.overlay25}>Your journey</Eyebrow>
            <Body weight="700" color={colors.primaryFg}>{pct}%</Body>
          </View>
          <View style={s.barTrack}><View style={[s.barFill, { width: `${pct}%` }]} /></View>
          <View style={{ flexDirection: "row", justifyContent: "space-between", marginTop: 8 }}>
            <Small color={colors.overlay25}>{completed} of {total} visits done</Small>
            {next && <Small color={colors.overlay25}>Next · {next.name}</Small>}
          </View>
        </LinearGradient>

        {/* Inner tabs */}
        <View style={s.tabs}>
          {(["visits", "medications", "progress"] as const).map(t => (
            <Pressable key={t} testID={`tab-${t}`} onPress={() => setTab(t)} style={[s.tab, tab === t && s.tabActive]}>
              <Small weight="700" color={tab === t ? colors.foreground : colors.mutedFg} style={{ textTransform: "capitalize", fontWeight: "700" as any }}>{t}</Small>
            </Pressable>
          ))}
        </View>

        {/* VISITS */}
        {tab === "visits" && (
          <View>
            <Eyebrow style={{ marginTop: spacing.md, marginBottom: spacing.sm }}>The road ahead</Eyebrow>
            {visits.map((v, i) => {
              const done = v.status === "completed";
              const isNext = v.status === "upcoming";
              const Icon = v.name?.includes("Telephonic") ? Phone : v.name?.includes("Home") ? Home : Building2;
              return (
                <Pressable key={v.id} testID={`visit-${v.visit_number}`} onPress={() => router.push({ pathname: "/(app)/patient/visit-detail", params: { id: v.id } })} style={{ flexDirection: "row", gap: 10, marginBottom: 10 }}>
                  <View style={s.spineWrap}>
                    {i < visits.length - 1 && <View style={[s.spine, done && { backgroundColor: colors.accent }]} />}
                    <View style={[s.node, done && { backgroundColor: colors.accent }, isNext && { backgroundColor: colors.warning }]}>
                      {done ? <Check size={14} color={colors.primaryFg} /> : <Small weight="700" color={isNext ? colors.warningFg : colors.mutedFg}>{v.visit_number}</Small>}
                    </View>
                  </View>
                  <Card style={[{ flex: 1, marginBottom: 0 }, isNext && { borderColor: colors.warning + "66", backgroundColor: colors.warning + "0D" }]}>
                    <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
                      <Body weight="700" style={{ flex: 1 }}>Visit {v.visit_number} · {v.name}</Body>
                      <View style={[s.pill, done && { backgroundColor: colors.accent + "22" }, isNext && { backgroundColor: colors.warning + "22" }]}>
                        <Small color={done ? colors.accent : isNext ? colors.warning : colors.info} weight="700">{done ? "Done" : isNext ? "Next →" : "Scheduled"}</Small>
                      </View>
                    </View>
                    <View style={{ flexDirection: "row", gap: 12, marginTop: 4 }}>
                      <Small><CalIcon size={11} color={colors.mutedFg} /> {new Date(v.scheduled_date).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}</Small>
                      <Small><Icon size={11} color={colors.mutedFg} /> Hospital</Small>
                    </View>
                  </Card>
                </Pressable>
              );
            })}
          </View>
        )}

        {/* MEDICATIONS */}
        {tab === "medications" && (
          <View>
            <Card style={{ marginTop: spacing.md }}>
              <Eyebrow style={{ marginBottom: 10 }}>Today's medications</Eyebrow>
              <View style={{ flexDirection: "row", gap: 6 }}>
                {meds.map(m => <View key={m.id} style={[s.dot, { backgroundColor: m.status === "taken" ? colors.success : m.status === "pending" ? colors.border : m.status === "skipped" ? colors.warning : colors.destructive }]} />)}
                <Small style={{ marginLeft: "auto", fontWeight: "700" as any }}>{allDone ? "All done ✓" : `${takenCount}/${meds.length}`}</Small>
              </View>
            </Card>
            <View style={s.tabs}>
              {(["today", "schedule", "history"] as const).map(t => (
                <Pressable key={t} onPress={() => setMedTab(t)} style={[s.tab, medTab === t && s.tabActive]}>
                  <Small weight="700" color={medTab === t ? colors.foreground : colors.mutedFg} style={{ textTransform: "capitalize", fontWeight: "700" as any }}>{t}</Small>
                </Pressable>
              ))}
            </View>
            {medTab === "today" && (allDone ? (
              <Card style={{ marginTop: spacing.md, alignItems: "center", paddingVertical: 24 }}>
                <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.success + "26", alignItems: "center", justifyContent: "center" }}><Sparkles size={22} color={colors.success} /></View>
                <Body weight="700" style={{ marginTop: 10 }}>All medications done for today!</Body>
                <Small>Great job keeping up 💪</Small>
              </Card>
            ) : meds.map(med => (
              <Card key={med.id} style={{ marginTop: spacing.sm, borderColor: med.status === "taken" ? colors.success + "55" : med.status === "skipped" ? colors.warning + "55" : med.status === "notTaken" ? colors.destructive + "55" : colors.border }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                  <View style={s.medIcon}><Pill size={20} color={colors.primary} /></View>
                  <View style={{ flex: 1 }}>
                    <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
                      <Body weight="700">{med.name} {med.dosage}</Body>
                      <View style={[s.pill, { backgroundColor: med.status === "taken" ? colors.success + "22" : med.status === "skipped" ? colors.warning + "22" : med.status === "notTaken" ? colors.destructive + "22" : colors.surface }]}>
                        <Small weight="700" color={med.status === "taken" ? colors.success : med.status === "skipped" ? colors.warning : med.status === "notTaken" ? colors.destructive : colors.mutedFg}>{med.status === "taken" ? "Taken ✓" : med.status === "notTaken" ? "Not taken" : med.status === "skipped" ? "Skipped" : "Pending"}</Small>
                      </View>
                    </View>
                    <Small style={{ marginTop: 2 }}>{med.time}{med.takenAt ? ` · logged ${med.takenAt}` : ""}</Small>
                  </View>
                </View>
                {med.status === "pending" && (
                  <View style={{ flexDirection: "row", gap: 8, marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderColor: colors.border }}>
                    <Pressable testID={`med-${med.id}-taken`} onPress={() => setMedStatus(med.id, "taken")} style={[s.medBtn, { backgroundColor: colors.success }]}><Small weight="700" color={colors.successFg}>✓ Taken</Small></Pressable>
                    <Pressable testID={`med-${med.id}-not`} onPress={() => setMedStatus(med.id, "notTaken")} style={[s.medBtn, { borderWidth: 1, borderColor: colors.destructive + "66" }]}><Small weight="700" color={colors.destructive}>✗ Not taken</Small></Pressable>
                    <Pressable testID={`med-${med.id}-skip`} onPress={() => setMedStatus(med.id, "skipped")} style={[s.medBtn, { borderWidth: 1, borderColor: colors.warning + "66" }]}><Small weight="700" color={colors.warning}>Skip</Small></Pressable>
                  </View>
                )}
              </Card>
            )))}
            {medTab === "schedule" && (
              <View style={{ marginTop: spacing.md }}>
                {[{name:"Metformin",dose:"500mg",freq:"Twice daily: 8:00 AM & 8:00 PM",ins:"Take with food",per:"1 Mar 2025 – 18 Aug 2025"},{name:"Aspirin",dose:"75mg",freq:"Once daily: 2:00 PM",ins:"After meals",per:"1 Mar 2025 – 18 Aug 2025"}].map((m,i) => (
                  <Card key={i} style={{ marginBottom: spacing.sm }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                      <View style={s.medIcon}><Pill size={18} color={colors.primary} /></View>
                      <Body weight="700" style={{ flex: 1 }}>{m.name} {m.dose}</Body>
                    </View>
                    <View style={{ marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderColor: colors.border, gap: 4 }}>
                      <Small>{m.freq}</Small>
                      <Small>Instructions: {m.ins}</Small>
                      <Small color={colors.mutedFg}>Period: {m.per}</Small>
                    </View>
                  </Card>
                ))}
              </View>
            )}
            {medTab === "history" && (
              <View style={{ marginTop: spacing.md }}>
                {[{date:"25 May 2025", items:[["Metformin 500mg","8:00 AM","taken"],["Aspirin 75mg","2:00 PM","taken"],["Metformin 500mg","8:00 PM","taken"]]},{date:"24 May 2025", items:[["Metformin 500mg","8:00 AM","taken"],["Aspirin 75mg","2:00 PM","skipped"],["Metformin 500mg","8:00 PM","taken"]]}].map((d,i) => (
                  <View key={i} style={{ marginBottom: spacing.md }}>
                    <Eyebrow style={{ marginBottom: 8 }}>{d.date}</Eyebrow>
                    <Card padded={false}>
                      {d.items.map(([n,t,st], j) => (
                        <View key={j} style={[{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: spacing.md }, j > 0 && { borderTopWidth: 1, borderColor: colors.border }]}>
                          <View><Body weight="700">{n}</Body><Small>{t}</Small></View>
                          <View style={[s.pill, { backgroundColor: st === "taken" ? colors.success + "22" : colors.warning + "22" }]}><Small weight="700" color={st === "taken" ? colors.success : colors.warning}>{st === "taken" ? "Taken ✓" : "Skipped"}</Small></View>
                        </View>
                      ))}
                    </Card>
                  </View>
                ))}
              </View>
            )}
          </View>
        )}

        {/* PROGRESS */}
        {tab === "progress" && (
          <View style={{ marginTop: spacing.md }}>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: spacing.sm }}>
              {[{v:completed,l:"Completed",c:colors.accent},{v:visits.filter(v=>v.status==="upcoming").length,l:"Upcoming",c:colors.warning},{v:Math.max(0,total-completed-visits.filter(v=>v.status==="upcoming").length),l:"Remaining",c:colors.foreground},{v:"93%",l:"Med. rate",c:colors.info}].map((s2,i) => (
                <View key={i} style={[s.statBox, { borderColor: s2.c + "33" }]}>
                  <Body weight="700" color={s2.c} style={{ fontSize: 28 }}>{s2.v}</Body>
                  <Small>{s2.l}</Small>
                </View>
              ))}
            </View>
            <Card style={{ marginTop: spacing.md }}>
              <Body weight="700">Visit completion</Body>
              <View style={[s.barTrackLight, { marginTop: 8 }]}><View style={[s.barFillAccent, { width: `${pct}%` }]} /></View>
              <Small style={{ marginTop: 4 }}>{completed} of {total} visits complete ({pct}%)</Small>
            </Card>
            <Card style={{ marginTop: spacing.md }}>
              <View style={{ flexDirection: "row", justifyContent: "space-between" }}><Body weight="700">Medication adherence · this week</Body><Small color={colors.success} weight="700">Excellent!</Small></View>
              <View style={[s.barTrackLight, { marginTop: 8 }]}><View style={[s.barFillInfo, { width: "93%" }]} /></View>
              <Small style={{ marginTop: 4 }}>13 of 14 doses (93%)</Small>
            </Card>
          </View>
        )}
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  hero: { borderRadius: radii.xl, padding: spacing.md, marginBottom: spacing.md },
  barTrack: { height: 8, borderRadius: 4, backgroundColor: colors.overlay25, marginTop: 8, overflow: "hidden" },
  barFill: { height: "100%", backgroundColor: colors.white, borderRadius: 4 },
  barTrackLight: { height: 10, borderRadius: 5, backgroundColor: colors.surface, overflow: "hidden" },
  barFillAccent: { height: "100%", backgroundColor: colors.accent, borderRadius: 5 },
  barFillInfo: { height: "100%", backgroundColor: colors.info, borderRadius: 5 },
  tabs: { flexDirection: "row", backgroundColor: colors.surface, borderRadius: 999, padding: 4 },
  tab: { flex: 1, paddingVertical: 10, alignItems: "center", borderRadius: 999 },
  tabActive: { backgroundColor: colors.card },
  spineWrap: { width: 28, alignItems: "center", paddingTop: 14 },
  spine: { position: "absolute", top: 32, bottom: -10, width: 2, backgroundColor: colors.border, borderRadius: 1 },
  node: { width: 28, height: 28, borderRadius: 14, backgroundColor: colors.card, borderWidth: 2, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  pill: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, backgroundColor: colors.info + "1A" },
  medIcon: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  medBtn: { flex: 1, paddingVertical: 8, borderRadius: 12, alignItems: "center" },
  dot: { flex: 1, height: 10, borderRadius: 5 },
  statBox: { flex: 1, minWidth: "47%", padding: 14, borderRadius: radii.lg, backgroundColor: colors.card, borderWidth: 1, alignItems: "center" },
});
