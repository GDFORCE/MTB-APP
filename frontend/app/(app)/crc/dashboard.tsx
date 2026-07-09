import React, { useEffect, useMemo, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, StatusBar, Text as RNText, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import Svg, { Path, Circle } from "react-native-svg";
import {
  Bell, Sun, FileText, Building2, Stethoscope, ArrowUpRight,
  FilePlus2, UserPlus, Send, ListTodo, AlertTriangle, ChevronRight,
  Clock, Home, Users, MessageCircle, Calendar as CalIcon, User,
} from "lucide-react-native";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { useUnreadCount } from "@/src/hooks/use-unread-count";

// ── Dawn Rounds palette (matches /app/frontend/src/theme/tokens.ts) ──────────
const C = {
  bg: "#FBF2E8", surface: "#F4E5D3", card: "#FEFAF1", fg: "#2E1B33",
  muted: "#7B5F73", border: "#E6D6C5",
  primary: "#A6213F", primaryDeep: "#6B1437", primaryFg: "#FBF2E8",
  secondary: "#F0D7DC",
  accent: "#E69B5C", accentFg: "#5A3318",
  info: "#7B6BB8", violet: "#8E5BB4",
  warning: "#D89A3C", success: "#5C9A6E", destructive: "#C0392B",
  dawnFrom: "#F5C57A", dawnMid: "#E07A4B", dawnTo: "#A6213F",
  w10: "rgba(255,255,255,0.10)", w15: "rgba(255,255,255,0.15)", w20: "rgba(255,255,255,0.20)", w25: "rgba(255,255,255,0.25)", w55: "rgba(255,255,255,0.55)", w65: "rgba(255,255,255,0.65)", w70: "rgba(255,255,255,0.70)", w80: "rgba(255,255,255,0.80)",
};
const DAWN = [C.dawnFrom, C.dawnMid, C.dawnTo] as const;

// GET /api/tasks item — action queue computed server-side for site staff.
type Task = {
  id: string;
  type: "overdue_visit" | "visit_today" | "schedule_review" | "unread_messages";
  title: string;
  subtitle: string;
  due: string | null;
  patient_id?: string;
  trial_id?: string;
  priority: "high" | "medium" | "low";
  count?: number;
};

const fmtTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) : "";
const fmtDate = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "";
const daysLate = (iso: string | null) =>
  iso ? Math.max(1, Math.round((Date.now() - Date.parse(iso)) / 86400000)) : 1;

export default function CrcDashboard() {
  const router = useRouter();
  const { user } = useAuth();
  const unread = useUnreadCount();
  const [trials, setTrials] = useState<any[]>([]);
  const [patients, setPatients] = useState<any[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => { (async () => {
    const [t, p, k] = await Promise.all([
      api.get("/trials").catch(() => ({ data: [] })),
      api.get("/patients").catch(() => ({ data: [] })),
      api.get("/tasks").catch(() => ({ data: [] })),
    ]);
    setTrials(t.data); setPatients(p.data); setTasks(k.data);
    setLoading(false);
  })(); }, []);

  const trialById = useMemo(() => Object.fromEntries(trials.map((t: any) => [t.id, t])), [trials]);
  const patientById = useMemo(() => Object.fromEntries(patients.map((p: any) => [p.id, p])), [patients]);
  const visitsToday = useMemo(() => tasks.filter(t => t.type === "visit_today"), [tasks]);
  const overdueVisits = useMemo(() => tasks.filter(t => t.type === "overdue_visit"), [tasks]);
  const sponsorCount = useMemo(() => new Set(trials.map((t: any) => t.sponsor_name).filter(Boolean)).size, [trials]);
  const piCount = useMemo(() => new Set(patients.map((p: any) => p.pi_id).filter(Boolean)).size, [patients]);

  const fullName = user?.full_name || "";
  const firstName = fullName.split(" ")[0] || "";
  const initials = user?.avatar_initials || fullName.split(" ").map(w => w[0]).slice(0, 2).join("").toUpperCase() || "?";
  const todayLabel = new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  // The queue only lists pending work, so the ring fills once today's visit queue is clear.
  const dayProgress = loading ? 0 : visitsToday.length === 0 ? 1 : 0;

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: 110 }} showsVerticalScrollIndicator={false}>
        {/* ── Hero with dawn gradient + concentric arcs ── */}
        <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
          {/* Plum overlay top → transparent bottom for legibility */}
          <LinearGradient colors={[C.primaryDeep, "rgba(107,20,55,0.55)", "rgba(107,20,55,0)"] as any} style={StyleSheet.absoluteFill} />
          {/* Concentric sunrise arcs top-right */}
          <View style={{ position: "absolute", right: -48, top: -48, width: 240, height: 240, opacity: 0.85 }} pointerEvents="none">
            <Svg viewBox="0 0 200 200" width={240} height={240}>
              <Path d="M30 110 a70 70 0 0 1 140 0" stroke={C.w25} strokeWidth="1.5" fill="none" />
              <Path d="M52 110 a48 48 0 0 1 96 0" stroke={C.w25} strokeWidth="1" fill="none" />
              <Circle cx="100" cy="110" r="22" stroke={C.w15} strokeWidth="1" fill="none" />
            </Svg>
          </View>
          {/* Motes */}
          <View pointerEvents="none" style={{ position: "absolute", right: 36, top: 96, width: 6, height: 6, borderRadius: 3, backgroundColor: "rgba(255,255,255,0.40)" }} />
          <View pointerEvents="none" style={{ position: "absolute", right: 112, top: 144, width: 5, height: 5, borderRadius: 2.5, backgroundColor: "rgba(255,255,255,0.30)" }} />
          <View pointerEvents="none" style={{ position: "absolute", left: 36, top: 176, width: 4, height: 4, borderRadius: 2, backgroundColor: "rgba(255,255,255,0.30)" }} />

          <SafeAreaView edges={["top"]}>
            {/* Top row */}
            <View style={st.heroTop}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={st.eyebrowLight}>RESEARCH TEAM · CRC</Text>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 4 }}>
                  <Text style={st.heroTitle}>{firstName ? `Hi, ${firstName}` : "Hello"}</Text>
                  <Sun size={20} color={C.w80} />
                </View>
              </View>
              <Pressable testID="crc-bell" onPress={() => router.push("/(app)/notifications")} style={st.iconBtn}>
                <Bell size={20} color={C.primaryFg} />
                {unread != null && unread > 0 && (
                  <View style={st.bellBadge}><Text style={st.bellBadgeText}>{unread > 9 ? "9+" : unread}</Text></View>
                )}
              </Pressable>
              <Pressable testID="crc-avatar" onPress={() => router.push("/(app)/clinical/profile")} style={st.iconBtn}>
                <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 13 }}>{initials}</Text>
              </Pressable>
            </View>

            {/* Day deck */}
            <View style={st.dayDeck}>
              <ProgressRing value={dayProgress} size={84} stroke={7}>
                <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 22, lineHeight: 24, fontVariant: ["tabular-nums"] }}>{loading ? "–" : visitsToday.length}</Text>
                <Text style={{ color: C.w70, fontSize: 8, fontWeight: "700", letterSpacing: 1.4, marginTop: 2 }}>VISITS</Text>
              </ProgressRing>
              <View style={{ flex: 1, minWidth: 0, marginLeft: 16 }}>
                <Text style={st.eyebrowLight}>{todayLabel.toUpperCase()}</Text>
                <Text style={st.heroSubtitle}>Your day at the site</Text>
                <View style={{ flexDirection: "row", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                  <View style={st.heroChip}><ListTodo size={13} color={C.primaryFg} /><Text style={st.heroChipText}>{loading ? "–" : tasks.length} tasks due</Text></View>
                  <View style={[st.heroChip, !loading && overdueVisits.length > 0 && { backgroundColor: "rgba(192,57,43,0.30)" }]}>
                    <AlertTriangle size={13} color={C.primaryFg} /><Text style={st.heroChipText}>{loading ? "–" : overdueVisits.length} overdue</Text>
                  </View>
                </View>
              </View>
            </View>
          </SafeAreaView>
        </LinearGradient>

        {/* ── Body floats up into hero ── */}
        <View style={{ marginTop: -40, paddingHorizontal: 16, paddingBottom: 24 }}>
          {/* Stat tiles */}
          <View style={{ flexDirection: "row", gap: 10 }}>
            <StatTile icon={FileText} iconColor={C.info} iconBg="rgba(123,107,184,0.12)" glow="rgba(123,107,184,0.20)" value={loading ? "–" : trials.length} label="Total Trials" />
            <StatTile icon={Building2} iconColor={C.accent} iconBg="rgba(230,155,92,0.15)" glow="rgba(230,155,92,0.20)" value={loading ? "–" : sponsorCount} label="Sponsors" />
            <StatTile icon={Stethoscope} iconColor={C.violet} iconBg="rgba(142,91,180,0.12)" glow="rgba(142,91,180,0.20)" value={loading ? "–" : piCount} label="PI's" />
          </View>

          {/* Quick Actions */}
          <SectionLabel label="QUICK ACTIONS" />
          <View style={{ flexDirection: "row", gap: 10 }}>
            <QuickAction icon={FilePlus2} bgGradient={false} bgColor={C.info} iconColor={"#FFFFFF"} label="New Trial" onPress={() => router.push("/(app)/sponsor/add-trial")} testID="qa-new-trial" />
            <QuickAction icon={UserPlus} bgGradient bgColor={undefined} iconColor={C.primaryFg} label="Add Patient" onPress={() => router.push("/(app)/clinical/add-patient")} testID="qa-add-patient" />
            <QuickAction icon={Send} bgGradient={false} bgColor={C.accent} iconColor={C.accentFg} label="Invite Patient" onPress={() => router.push("/(app)/clinical/invite-patient")} testID="qa-invite-patient" />
          </View>

          {/* My Trials */}
          <SectionLabel label="MY TRIALS" action={
            <Pressable testID="see-all-trials" onPress={() => router.push("/(app)/clinical/my-trials")} style={{ flexDirection: "row", alignItems: "center" }}>
              <Text style={{ color: C.info, fontSize: 14, fontWeight: "700" }}>See all </Text>
              <ChevronRight size={16} color={C.info} />
            </Pressable>
          } />
          <View style={{ gap: 12 }}>
            {loading && <LoadingCard />}
            {!loading && trials.length === 0 && <EmptyCard text="No trials assigned yet" />}
            {!loading && trials.slice(0, 2).map((tr: any) => (
              <TrialPanel
                key={tr.id}
                tr={tr}
                patientCount={patients.filter((p: any) => p.trial_id === tr.id).length}
                onPress={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: tr.id } })}
              />
            ))}
          </View>

          {/* Today's Visits */}
          <SectionLabel label="TODAY'S VISITS" action={!loading ? <Text style={{ fontSize: 11, fontWeight: "700", color: C.muted, fontVariant: ["tabular-nums"] }}>{visitsToday.length} PENDING</Text> : undefined} />
          {loading && <LoadingCard />}
          {!loading && visitsToday.length === 0 && <EmptyCard text="No visits scheduled for today — you're all clear" />}
          {!loading && (
            <View>
              {visitsToday.map((v, i) => {
                const isNext = i === 0;
                const last = i === visitsToday.length - 1;
                const trial = v.trial_id ? trialById[v.trial_id] : null;
                const patient = v.patient_id ? patientById[v.patient_id] : null;
                const visitName = v.title.replace(/^Today: /, "");
                return (
                  <View key={v.id} style={{ flexDirection: "row", gap: 12 }}>
                    <View style={{ alignItems: "center", paddingTop: 4 }}>
                      <View style={[
                        { width: 28, height: 28, borderRadius: 14, alignItems: "center", justifyContent: "center", borderWidth: 2, zIndex: 1 },
                        { backgroundColor: C.card, borderColor: isNext ? C.info : C.border },
                      ]}>
                        <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: isNext ? C.info : "rgba(123,95,115,0.30)" }} />
                      </View>
                      {!last && <View style={{ width: 2, flex: 1, marginVertical: 4, borderRadius: 1, backgroundColor: C.border }} />}
                    </View>
                    <View style={[st.visitCard, isNext && { borderColor: "rgba(123,107,184,0.40)" }]}>
                      <View style={{ flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
                        <View style={{ flex: 1, minWidth: 0 }}>
                          <View style={{ flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 6 }}>
                            <Text style={{ fontSize: 14, fontWeight: "700", color: C.fg }}>{v.subtitle}</Text>
                            {patient?.avatar_initials ? <Text style={{ fontSize: 12, color: C.muted }}>· {patient.avatar_initials}</Text> : null}
                            {isNext && (
                              <View style={{ flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, backgroundColor: "rgba(123,107,184,0.10)" }}>
                                <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: C.info }} />
                                <Text style={{ fontSize: 10, fontWeight: "700", color: C.info }}>Up next</Text>
                              </View>
                            )}
                          </View>
                          {trial ? <Text style={{ fontSize: 11, color: C.muted, marginTop: 2 }} numberOfLines={1}>{trial.protocol_id} · {trial.title}</Text> : null}
                          <View style={{ flexDirection: "row", alignItems: "center", marginTop: 6, gap: 6, flexWrap: "wrap" }}>
                            <View style={{ flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: C.surface, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999 }}>
                              <Clock size={11} color={C.muted} /><Text style={{ fontSize: 11, fontFamily: "monospace" as any, fontWeight: "600", color: C.fg }}>{fmtTime(v.due)}</Text>
                            </View>
                            <Text style={{ fontSize: 11, color: C.muted }}>{visitName}</Text>
                          </View>
                        </View>
                        <Pressable testID={`update-${v.id}`} onPress={() => router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: v.patient_id || "" } })}>
                          <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.updateBtn}>
                            <Text style={{ color: C.primaryFg, fontSize: 12, fontWeight: "700" }}>Update</Text>
                          </LinearGradient>
                        </Pressable>
                      </View>
                    </View>
                  </View>
                );
              })}
            </View>
          )}

          {/* Overdue */}
          {!loading && overdueVisits.length > 0 && (
            <>
              <SectionLabel label="OVERDUE" tone={C.destructive} action={
                <View style={{ minWidth: 20, height: 20, borderRadius: 10, backgroundColor: C.destructive, alignItems: "center", justifyContent: "center", paddingHorizontal: 6 }}>
                  <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 10 }}>{overdueVisits.length}</Text>
                </View>
              } />
              {overdueVisits.map(v => (
                <View key={v.id} style={st.overdueCard}>
                  <View style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 6, backgroundColor: C.destructive }} />
                  <View style={{ flexDirection: "row", alignItems: "flex-start", gap: 12, padding: 16, paddingLeft: 20 }}>
                    <View style={{ width: 44, height: 44, borderRadius: 14, backgroundColor: "rgba(192,57,43,0.12)", alignItems: "center", justifyContent: "center" }}>
                      <AlertTriangle size={20} color={C.destructive} />
                    </View>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={{ fontSize: 14, fontWeight: "700", color: C.fg }}>{v.subtitle}</Text>
                      <Text style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>{v.title.replace(/^Overdue: /, "")}{v.trial_id && trialById[v.trial_id] ? ` · ${trialById[v.trial_id].protocol_id}` : ""}</Text>
                      <Text style={{ fontSize: 12, color: C.destructive, marginTop: 4, fontWeight: "600" }}>{daysLate(v.due)} {daysLate(v.due) === 1 ? "day" : "days"} overdue · Was due {fmtDate(v.due)}</Text>
                    </View>
                    <Pressable testID={`review-${v.id}`} onPress={() => router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: v.patient_id || "" } })} style={{ backgroundColor: C.destructive, paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999 }}>
                      <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 12 }}>Review</Text>
                    </Pressable>
                  </View>
                </View>
              ))}
            </>
          )}
        </View>
      </ScrollView>

      {/* Bottom nav */}
      <View style={st.tabBar}>
        <TabItem icon={Home} label="Dashboard" active />
        <TabItem icon={Users} label="Patients" onPress={() => router.push("/(app)/clinical/patients")} testID="tab-patients" />
        <TabItem icon={MessageCircle} label="Messages" onPress={() => router.push("/(app)/chat")} testID="tab-messages" />
        <TabItem icon={CalIcon} label="Calendar" onPress={() => router.push({ pathname: "/(app)/clinical/team-calendar", params: { role: "crc" } } as any)} testID="tab-calendar" />
        <TabItem icon={User} label="Me" onPress={() => router.push("/(app)/clinical/profile")} testID="tab-me" />
      </View>
    </View>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────
function Text(props: any) {
  return <RNText {...props} style={[{ color: C.fg }, props.style]} />;
}

function LoadingCard() {
  return (
    <View style={[st.visitCard, { alignItems: "center", justifyContent: "center", paddingVertical: 28, marginBottom: 0 }]}>
      <ActivityIndicator color={C.primary} />
    </View>
  );
}

function EmptyCard({ text }: { text: string }) {
  return (
    <View style={[st.visitCard, { marginBottom: 0 }]}>
      <Text style={{ color: C.muted, fontSize: 13 }}>{text}</Text>
    </View>
  );
}

function SectionLabel({ label, action, tone }: { label: string; action?: React.ReactNode; tone?: string }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 24, marginBottom: 12 }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        {tone === C.destructive ? (
          <View style={{ width: 4, height: 14, borderRadius: 2, backgroundColor: C.destructive }} />
        ) : (
          <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }} style={{ width: 4, height: 14, borderRadius: 2 }} />
        )}
        <Text style={{ color: tone || C.muted, fontSize: 11, fontWeight: "700", letterSpacing: 1.5 }}>{label}</Text>
      </View>
      {action}
    </View>
  );
}

function StatTile({ icon: Icon, iconColor, iconBg, glow, value, label }: any) {
  return (
    <Pressable style={st.statTile}>
      <View style={{ position: "absolute", top: -24, right: -24, width: 64, height: 64, borderRadius: 32, backgroundColor: glow }} />
      <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" }}>
        <View style={{ width: 36, height: 36, borderRadius: 12, backgroundColor: iconBg, alignItems: "center", justifyContent: "center" }}>
          <Icon size={18} color={iconColor} />
        </View>
        <ArrowUpRight size={14} color="rgba(123,95,115,0.45)" />
      </View>
      <Text style={{ fontSize: 30, fontWeight: "700", color: C.fg, marginTop: 8, lineHeight: 32, fontVariant: ["tabular-nums"] }}>{value}</Text>
      <Text style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>{label}</Text>
    </Pressable>
  );
}

function QuickAction({ icon: Icon, bgGradient, bgColor, iconColor, label, onPress, testID }: any) {
  return (
    <Pressable testID={testID} onPress={onPress} style={st.quickAction}>
      {bgGradient ? (
        <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ width: 48, height: 48, borderRadius: 16, alignItems: "center", justifyContent: "center" }}>
          <Icon size={22} color={iconColor} />
        </LinearGradient>
      ) : (
        <View style={{ width: 48, height: 48, borderRadius: 16, backgroundColor: bgColor, alignItems: "center", justifyContent: "center" }}>
          <Icon size={22} color={iconColor} />
        </View>
      )}
      <Text style={{ fontSize: 12, fontWeight: "500", color: C.fg, marginTop: 8, textAlign: "center" }}>{label}</Text>
    </Pressable>
  );
}

function TrialPanel({ tr, patientCount, onPress }: any) {
  const status = tr.status ? tr.status.charAt(0).toUpperCase() + tr.status.slice(1) : "Active";
  return (
    <Pressable testID={`trial-${tr.id}`} onPress={onPress} style={st.trialPanel}>
      <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }} style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 6 }} />
      <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <View style={{ paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: "rgba(240,215,220,0.55)" }}>
          <Text style={{ fontFamily: "monospace" as any, fontSize: 11, fontWeight: "700", color: C.primary }}>{tr.protocol_id}</Text>
        </View>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <View style={{ paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, backgroundColor: "rgba(92,154,110,0.15)" }}>
            <Text style={{ fontSize: 11, fontWeight: "700", color: C.success }}>{status}</Text>
          </View>
          <View style={{ width: 26, height: 26, borderRadius: 13, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" }}>
            <ArrowUpRight size={14} color="rgba(123,95,115,0.7)" />
          </View>
        </View>
      </View>
      <Text style={{ fontSize: 16, fontWeight: "700", color: C.fg, marginBottom: 10 }} numberOfLines={2}>{tr.title}</Text>
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
        {tr.phase ? <Tag bg="rgba(123,107,184,0.10)" fg={C.info} label={tr.phase} /> : null}
        {tr.condition ? <Tag bg="rgba(230,155,92,0.12)" fg={C.accent} label={tr.condition} /> : null}
      </View>
      <View style={{ flexDirection: "row", flexWrap: "wrap", paddingTop: 12, borderTopWidth: 1, borderTopColor: C.border }}>
        {[
          { label: "SPONSOR", val: tr.sponsor_name || "—" },
          { label: "MY PATIENTS", val: `${patientCount} enrolled` },
        ].map(f => (
          <View key={f.label} style={{ width: "50%", marginBottom: 8 }}>
            <Text style={{ fontSize: 9, fontWeight: "700", letterSpacing: 1.2, color: "rgba(123,95,115,0.65)" }}>{f.label}</Text>
            <Text style={{ fontSize: 12, fontWeight: "500", color: C.fg, marginTop: 2 }} numberOfLines={1}>{f.val}</Text>
          </View>
        ))}
      </View>
    </Pressable>
  );
}

function Tag({ bg, fg, label }: any) {
  return <View style={{ paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999, backgroundColor: bg }}><Text style={{ fontSize: 11, fontWeight: "700", color: fg }}>{label}</Text></View>;
}

function ProgressRing({ value, size, stroke, children }: any) {
  const r = (size - stroke) / 2;
  const cir = 2 * Math.PI * r;
  return (
    <View style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}>
      <Svg width={size} height={size} style={{ position: "absolute", transform: [{ rotate: "-90deg" }] }}>
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={C.w20} strokeWidth={stroke} />
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={C.primaryFg} strokeWidth={stroke} strokeLinecap="round" strokeDasharray={`${cir} ${cir}`} strokeDashoffset={cir * (1 - value)} />
      </Svg>
      <View style={{ alignItems: "center" }}>{children}</View>
    </View>
  );
}

function TabItem({ icon: Icon, label, active, onPress, testID }: any) {
  return (
    <Pressable testID={testID} onPress={onPress} style={{ flex: 1, alignItems: "center", justifyContent: "center", paddingVertical: 8 }}>
      <Icon size={22} color={active ? C.primary : C.muted} />
      <Text style={{ fontSize: 10, fontWeight: active ? "700" : "500", color: active ? C.primary : C.muted, marginTop: 4 }}>{label}</Text>
      {active && <View style={{ position: "absolute", top: 0, height: 3, width: 32, backgroundColor: C.primary, borderRadius: 2 }} />}
    </Pressable>
  );
}

const st = StyleSheet.create({
  hero: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 56, overflow: "hidden" },
  heroTop: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 8 },
  eyebrowLight: { color: C.w65, fontSize: 11, fontWeight: "700", letterSpacing: 1.5 },
  heroTitle: { color: C.primaryFg, fontSize: 28, fontWeight: "700", letterSpacing: -0.4 },
  heroSubtitle: { color: C.primaryFg, fontSize: 22, fontWeight: "700", letterSpacing: -0.2, marginTop: 2 },
  iconBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: C.w15, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: C.w20 },
  bellBadge: { position: "absolute", top: -2, right: -2, minWidth: 18, height: 18, paddingHorizontal: 4, borderRadius: 9, backgroundColor: C.destructive, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: C.primaryDeep },
  bellBadgeText: { color: C.primaryFg, fontSize: 10, fontWeight: "700" },
  dayDeck: { flexDirection: "row", alignItems: "center", marginTop: 20 },
  heroChip: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 5, borderRadius: 999, backgroundColor: C.w15, borderWidth: 1, borderColor: C.w15 },
  heroChipText: { color: C.primaryFg, fontSize: 12, fontWeight: "700" },
  statTile: { flex: 1, backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, padding: 14, overflow: "hidden", shadowColor: "#2E1B33", shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 3 }, elevation: 3 },
  quickAction: { flex: 1, alignItems: "center", paddingVertical: 14, paddingHorizontal: 8, backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  trialPanel: { backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, padding: 16, paddingLeft: 18, overflow: "hidden", position: "relative", shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  visitCard: { flex: 1, backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, padding: 14, marginBottom: 12, shadowColor: "#2E1B33", shadowOpacity: 0.04, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 1 },
  updateBtn: { paddingHorizontal: 16, paddingVertical: 8, borderRadius: 999 },
  overdueCard: { backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: "rgba(192,57,43,0.30)", overflow: "hidden", position: "relative", marginBottom: 12 },
  tabBar: { position: "absolute", bottom: 0, left: 0, right: 0, flexDirection: "row", backgroundColor: C.card, borderTopWidth: 1, borderTopColor: C.border, paddingTop: 8, paddingBottom: 24, paddingHorizontal: 8 },
});
