import React, { useEffect, useMemo, useState } from "react";
import { View, ScrollView, StyleSheet, Pressable, ActivityIndicator } from "react-native";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { Check, Calendar as CalIcon, Building2, Phone, Home, Pill, Sparkles } from "lucide-react-native";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

type Slot = { time: string; label?: string };
type Med = { id: string; name: string; dosage: string; route?: string; schedule?: Slot[]; start_date?: string; end_date?: string | null; active?: boolean };
type Dose = { id?: string; medication_id: string; date: string; time: string; status: string; logged_at?: string };
type UiStatus = "taken" | "pending" | "notTaken" | "skipped";

// Adherence days are UTC-based (product decision) — anchor "today" and dose logs to UTC.
const todayStr = new Date().toISOString().slice(0, 10);

const uiStatus = (s?: string): UiStatus =>
  s === "taken" ? "taken" : s === "skipped" ? "skipped" : s === "not_taken" ? "notTaken" : "pending";

function fmtTime(t?: string): string {
  if (!t) return "";
  const [h, m] = t.split(":").map(Number);
  if (isNaN(h)) return t;
  const ap = h < 12 ? "AM" : "PM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${String(m || 0).padStart(2, "0")} ${ap}`;
}

function fmtLoggedAt(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
}

function fmtDate(ymd?: string | null): string {
  if (!ymd) return "";
  const d = new Date(ymd + "T00:00:00Z");
  if (isNaN(d.getTime())) return ymd;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

const HIST = {
  taken: { t: "Taken ✓", c: colors.success },
  skipped: { t: "Skipped", c: colors.warning },
  not_taken: { t: "Not taken", c: colors.destructive },
  remind_later: { t: "Remind later", c: colors.info },
} as const;

export default function MyTrial() {
  const router = useRouter();
  const [visits, setVisits] = useState<any[]>([]);
  const [meds, setMeds] = useState<Med[]>([]);
  const [doses, setDoses] = useState<Dose[]>([]);
  const [adherence, setAdherence] = useState<any>(null);
  const [trial, setTrial] = useState<any>(null);
  const [tab, setTab] = useState<"visits" | "medications" | "progress">("visits");
  const [medTab, setMedTab] = useState<"today" | "schedule" | "history">("today");
  const [loading, setLoading] = useState(true);
  const [doseError, setDoseError] = useState<string | null>(null);

  useEffect(() => { (async () => {
    try {
      const from = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
      const [v, m, a, t] = await Promise.all([
        api.get("/visits/mine").then(r => r.data).catch(() => []),
        api.get("/medications").then(r => r.data).catch(() => []),
        api.get("/adherence").then(r => r.data).catch(() => null),
        api.get("/trials").then(r => r.data).catch(() => []),
      ]);
      setVisits(v || []); setMeds(m || []); setAdherence(a);
      setTrial(Array.isArray(t) ? t[0] ?? null : null);
      const doseLists = await Promise.all((m || []).map((med: Med) =>
        api.get(`/medications/${med.id}/doses`, { params: { from, to: todayStr } }).then(r => r.data).catch(() => [])));
      setDoses(doseLists.flat());
    } finally {
      setLoading(false);
    }
  })(); }, []);

  const refreshAdherence = () => api.get("/adherence").then(r => setAdherence(r.data)).catch(() => {});

  const completed = visits.filter(v => v.status === "completed").length;
  const total = visits.length || 10;
  const next = visits.find(v => v.status === "upcoming");
  const pct = Math.round((completed / total) * 100);
  const trialLine = trial ? [trial.protocol_id, trial.condition].filter(Boolean).join(" · ") : "";

  const activeMeds = useMemo(
    () => meds.filter(m => m.active !== false && (m.schedule?.length ?? 0) > 0),
    [meds],
  );

  // Today's expected doses = one row per (med, schedule slot), status from today's logs.
  const todayEntries = useMemo(() => {
    const list = activeMeds.flatMap(m => (m.schedule || []).map(s => {
      const log = doses.find(d => d.medication_id === m.id && d.date === todayStr && d.time === s.time);
      return {
        key: `${m.id}|${s.time}`, medId: m.id, name: m.name, dosage: m.dosage,
        time: s.time, status: uiStatus(log?.status), loggedAt: log?.logged_at,
      };
    }));
    return list.sort((a, b) => a.time.localeCompare(b.time));
  }, [activeMeds, doses]);

  const takenCount = todayEntries.filter(e => e.status === "taken").length;
  const allDone = todayEntries.length > 0 && takenCount === todayEntries.length;

  const medById = useMemo(() => Object.fromEntries(meds.map(m => [m.id, m])), [meds]);
  const history = useMemo(() => {
    const byDate: Record<string, Dose[]> = {};
    doses.forEach(d => { (byDate[d.date] ||= []).push(d); });
    return Object.keys(byDate).sort().reverse().map(date => ({
      date,
      items: byDate[date].slice().sort((a, b) => a.time.localeCompare(b.time)).map(d => ({
        name: `${medById[d.medication_id]?.name ?? "Medication"} ${medById[d.medication_id]?.dosage ?? ""}`.trim(),
        time: fmtTime(d.time),
        status: d.status,
      })),
    }));
  }, [doses, medById]);

  // Optimistic dose log with scoped revert on error (touches only this slot).
  const logDose = async (medId: string, time: string, backend: "taken" | "not_taken" | "skipped") => {
    const isSlot = (d: Dose) => d.medication_id === medId && d.date === todayStr && d.time === time;
    const prevSlot = doses.find(isSlot); // restore only this slot's prior state on failure
    const nowIso = new Date().toISOString();
    setDoses(cur => [
      ...cur.filter(d => !isSlot(d)),
      { medication_id: medId, date: todayStr, time, status: backend, logged_at: nowIso },
    ]);
    try {
      await api.post(`/medications/${medId}/doses`, { date: todayStr, time, status: backend });
      setDoseError(null); // clear on next successful action
      refreshAdherence();
    } catch {
      // Revert ONLY the failed slot so other slots' concurrent optimistic entries survive.
      setDoses(cur => [
        ...cur.filter(d => !isSlot(d)),
        ...(prevSlot ? [prevSlot] : []),
      ]);
      setDoseError("Couldn't save — try again");
    }
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow={trialLine} title="My Trial" />
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
            {loading && (
              <Card style={s.loadingCard}><ActivityIndicator color={colors.primary} /></Card>
            )}
            {!loading && visits.length === 0 && (
              <Card><Small color={colors.mutedFg}>No visits scheduled yet</Small></Card>
            )}
            {!loading && visits.map((v, i) => {
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
              {loading ? (
                <ActivityIndicator color={colors.primary} style={{ alignSelf: "flex-start" }} />
              ) : todayEntries.length === 0 ? (
                <Small color={colors.mutedFg}>No medications prescribed</Small>
              ) : (
                <View style={{ flexDirection: "row", gap: 6 }}>
                  {todayEntries.map(e => <View key={e.key} style={[s.dot, { backgroundColor: e.status === "taken" ? colors.success : e.status === "pending" ? colors.border : e.status === "skipped" ? colors.warning : colors.destructive }]} />)}
                  <Small style={{ marginLeft: "auto", fontWeight: "700" as any }}>{allDone ? "All done ✓" : `${takenCount}/${todayEntries.length}`}</Small>
                </View>
              )}
            </Card>
            <View style={s.tabs}>
              {(["today", "schedule", "history"] as const).map(t => (
                <Pressable key={t} onPress={() => setMedTab(t)} style={[s.tab, medTab === t && s.tabActive]}>
                  <Small weight="700" color={medTab === t ? colors.foreground : colors.mutedFg} style={{ textTransform: "capitalize", fontWeight: "700" as any }}>{t}</Small>
                </Pressable>
              ))}
            </View>
            {doseError && (
              <View style={s.errorBanner}>
                <Small weight="700" color={colors.destructive}>{doseError}</Small>
              </View>
            )}
            {medTab === "today" && (loading ? (
              <Card style={[s.loadingCard, { marginTop: spacing.md }]}><ActivityIndicator color={colors.primary} /></Card>
            ) : todayEntries.length === 0 ? (
              <Card style={{ marginTop: spacing.md, alignItems: "center", paddingVertical: 24 }}>
                <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" }}><Pill size={22} color={colors.primary} /></View>
                <Body weight="700" style={{ marginTop: 10 }}>No medications yet</Body>
                <Small>Your care team hasn't prescribed any</Small>
              </Card>
            ) : allDone ? (
              <Card style={{ marginTop: spacing.md, alignItems: "center", paddingVertical: 24 }}>
                <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: colors.success + "26", alignItems: "center", justifyContent: "center" }}><Sparkles size={22} color={colors.success} /></View>
                <Body weight="700" style={{ marginTop: 10 }}>All medications done for today!</Body>
                <Small>Great job keeping up 💪</Small>
              </Card>
            ) : todayEntries.map(e => (
              <Card key={e.key} style={{ marginTop: spacing.sm, borderColor: e.status === "taken" ? colors.success + "55" : e.status === "skipped" ? colors.warning + "55" : e.status === "notTaken" ? colors.destructive + "55" : colors.border }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                  <View style={s.medIcon}><Pill size={20} color={colors.primary} /></View>
                  <View style={{ flex: 1 }}>
                    <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
                      <Body weight="700">{e.name} {e.dosage}</Body>
                      <View style={[s.pill, { backgroundColor: e.status === "taken" ? colors.success + "22" : e.status === "skipped" ? colors.warning + "22" : e.status === "notTaken" ? colors.destructive + "22" : colors.surface }]}>
                        <Small weight="700" color={e.status === "taken" ? colors.success : e.status === "skipped" ? colors.warning : e.status === "notTaken" ? colors.destructive : colors.mutedFg}>{e.status === "taken" ? "Taken ✓" : e.status === "notTaken" ? "Not taken" : e.status === "skipped" ? "Skipped" : "Pending"}</Small>
                      </View>
                    </View>
                    <Small style={{ marginTop: 2 }}>{fmtTime(e.time)}{e.status === "taken" && e.loggedAt ? ` · logged ${fmtLoggedAt(e.loggedAt)}` : ""}</Small>
                  </View>
                </View>
                {e.status === "pending" && (
                  <View style={{ flexDirection: "row", gap: 8, marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderColor: colors.border }}>
                    <Pressable testID={`med-${e.key}-taken`} onPress={() => logDose(e.medId, e.time, "taken")} style={[s.medBtn, { backgroundColor: colors.success }]}><Small weight="700" color={colors.successFg}>✓ Taken</Small></Pressable>
                    <Pressable testID={`med-${e.key}-not`} onPress={() => logDose(e.medId, e.time, "not_taken")} style={[s.medBtn, { borderWidth: 1, borderColor: colors.destructive + "66" }]}><Small weight="700" color={colors.destructive}>✗ Not taken</Small></Pressable>
                    <Pressable testID={`med-${e.key}-skip`} onPress={() => logDose(e.medId, e.time, "skipped")} style={[s.medBtn, { borderWidth: 1, borderColor: colors.warning + "66" }]}><Small weight="700" color={colors.warning}>Skip</Small></Pressable>
                  </View>
                )}
              </Card>
            )))}
            {medTab === "schedule" && (
              <View style={{ marginTop: spacing.md }}>
                {loading && (
                  <Card style={s.loadingCard}><ActivityIndicator color={colors.primary} /></Card>
                )}
                {!loading && activeMeds.length === 0 && (
                  <Card><Small color={colors.mutedFg}>No medications prescribed</Small></Card>
                )}
                {!loading && activeMeds.map(m => (
                  <Card key={m.id} style={{ marginBottom: spacing.sm }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                      <View style={s.medIcon}><Pill size={18} color={colors.primary} /></View>
                      <Body weight="700" style={{ flex: 1 }}>{m.name} {m.dosage}</Body>
                    </View>
                    <View style={{ marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderColor: colors.border, gap: 4 }}>
                      <Small>{(m.schedule || []).map(sl => fmtTime(sl.time)).join(" · ") || "No schedule"}</Small>
                      {!!m.route && <Small>Route: {m.route}</Small>}
                      <Small color={colors.mutedFg}>Period: {fmtDate(m.start_date)}{m.end_date ? ` – ${fmtDate(m.end_date)}` : " – ongoing"}</Small>
                    </View>
                  </Card>
                ))}
              </View>
            )}
            {medTab === "history" && (
              <View style={{ marginTop: spacing.md }}>
                {loading && (
                  <Card style={s.loadingCard}><ActivityIndicator color={colors.primary} /></Card>
                )}
                {!loading && history.length === 0 && (
                  <Card><Small color={colors.mutedFg}>No dose history yet</Small></Card>
                )}
                {!loading && history.map((d, i) => (
                  <View key={i} style={{ marginBottom: spacing.md }}>
                    <Eyebrow style={{ marginBottom: 8 }}>{fmtDate(d.date)}</Eyebrow>
                    <Card padded={false}>
                      {d.items.map((it, j) => {
                        const meta = HIST[it.status as keyof typeof HIST] ?? { t: it.status, c: colors.mutedFg };
                        return (
                          <View key={j} style={[{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: spacing.md }, j > 0 && { borderTopWidth: 1, borderColor: colors.border }]}>
                            <View><Body weight="700">{it.name}</Body><Small>{it.time}</Small></View>
                            <View style={[s.pill, { backgroundColor: meta.c + "22" }]}><Small weight="700" color={meta.c}>{meta.t}</Small></View>
                          </View>
                        );
                      })}
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
              {[{v:completed,l:"Completed",c:colors.accent},{v:visits.filter(v=>v.status==="upcoming").length,l:"Upcoming",c:colors.warning},{v:Math.max(0,total-completed-visits.filter(v=>v.status==="upcoming").length),l:"Remaining",c:colors.foreground},{v:adherence?.rate != null ? `${adherence.rate}%` : "—",l:"Med. rate",c:colors.info}].map((s2,i) => (
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
              <View style={{ flexDirection: "row", justifyContent: "space-between" }}><Body weight="700">Medication adherence</Body>{adherence?.rate != null && adherence.rate >= 90 && <Small color={colors.success} weight="700">Excellent!</Small>}</View>
              <View style={[s.barTrackLight, { marginTop: 8 }]}><View style={[s.barFillInfo, { width: `${adherence?.rate ?? 0}%` }]} /></View>
              <Small style={{ marginTop: 4 }}>{adherence?.total ? `${adherence.taken} of ${adherence.total} doses (${adherence.rate}%)` : "No doses expected yet"}</Small>
              {!!adherence?.streak_days && <Small color={colors.mutedFg} style={{ marginTop: 2 }}>🔥 {adherence.streak_days}-day streak</Small>}
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
  loadingCard: { alignItems: "center", justifyContent: "center", paddingVertical: 28 },
  errorBanner: { marginTop: spacing.sm, padding: spacing.sm, borderRadius: radii.lg, backgroundColor: colors.destructive + "14", borderWidth: 1, borderColor: colors.destructive + "40" },
});
