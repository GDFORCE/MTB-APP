import React, { useEffect, useMemo, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, StatusBar, Text, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import Svg, { Path, Circle } from "react-native-svg";
import {
  Bell, Sun, Users, FileText, Stethoscope, ArrowUpRight, ChevronRight, FilePlus2, UserPlus, Send,
  ListTodo, AlertTriangle, ClipboardCheck, Home, MessageCircle, Calendar as CalIcon, User, ShieldCheck,
} from "lucide-react-native";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import { useOrgContext, consoleRouteForType } from "@/src/components/org-admin-kit";

const C = {
  bg: "#FBF2E8", surface: "#F4E5D3", card: "#FEFAF1", fg: "#2E1B33", muted: "#7B5F73", border: "#E6D6C5",
  primary: "#A6213F", primaryDeep: "#6B1437", primaryFg: "#FBF2E8", secondary: "#F0D7DC",
  accent: "#E69B5C", accentFg: "#5A3318", info: "#7B6BB8", violet: "#8E5BB4",
  warning: "#D89A3C", success: "#5C9A6E", destructive: "#C0392B",
  dawnFrom: "#F5C57A", dawnMid: "#E07A4B", dawnTo: "#A6213F",
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

export default function PiDashboard() {
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
  // PI review queue = trials awaiting schedule sign-off (server type schedule_review).
  const reviews = useMemo(() => tasks.filter(t => t.type === "schedule_review"), [tasks]);
  const overdueVisits = useMemo(() => tasks.filter(t => t.type === "overdue_visit"), [tasks]);
  const sponsorCount = useMemo(() => new Set(trials.map((t: any) => t.sponsor_name).filter(Boolean)).size, [trials]);

  const firstName = (user?.full_name || "").split(" ").pop() || "";
  const initials = user?.avatar_initials || (user?.full_name || "").split(" ").map(w => w[0]).slice(0, 2).join("").toUpperCase() || "?";
  const todayLabel = new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  // The queue lists only pending work, so the ring fills once the review queue is clear.
  const dayProgress = loading ? 0 : reviews.length === 0 ? 1 : 0;

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: 110 }} showsVerticalScrollIndicator={false}>
        {/* Dawn hero */}
        <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={pi.hero}>
          <LinearGradient colors={[C.primaryDeep, "rgba(107,20,55,0.55)", "rgba(107,20,55,0)"] as any} style={StyleSheet.absoluteFill} />
          <View style={{ position: "absolute", right: -48, top: -48, width: 240, height: 240, opacity: 0.85 }} pointerEvents="none">
            <Svg viewBox="0 0 200 200" width={240} height={240}>
              <Path d="M30 110 a70 70 0 0 1 140 0" stroke="rgba(255,255,255,0.25)" strokeWidth="1.5" fill="none" />
              <Path d="M52 110 a48 48 0 0 1 96 0" stroke="rgba(255,255,255,0.25)" strokeWidth="1" fill="none" />
              <Circle cx="100" cy="110" r="22" stroke="rgba(255,255,255,0.15)" strokeWidth="1" fill="none" />
            </Svg>
          </View>
          <SafeAreaView edges={["top"]}>
            <View style={pi.heroTop}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={pi.eyebrowLight}>PRINCIPAL INVESTIGATOR</Text>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 4 }}>
                  <Text style={pi.heroTitle}>{firstName ? `Hi, Dr. ${firstName}` : "Hello"}</Text>
                  <Sun size={20} color="rgba(255,255,255,0.80)" />
                </View>
              </View>
              <Pressable testID="pi-bell" onPress={() => router.push("/(app)/notifications")} style={pi.iconBtn}>
                <Bell size={20} color={C.primaryFg} />
                {unread != null && unread > 0 && (
                  <View style={pi.bellBadge}><Text style={pi.bellBadgeText}>{unread > 9 ? "9+" : unread}</Text></View>
                )}
              </Pressable>
              <Pressable testID="pi-avatar" onPress={() => router.push("/(app)/patient/profile")} style={pi.iconBtn}>
                <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 13 }}>{initials}</Text>
              </Pressable>
            </View>
            <View style={pi.dayDeck}>
              <Ring value={dayProgress} size={84} stroke={7}>
                <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 22, lineHeight: 24, fontVariant: ["tabular-nums"] }}>{loading ? "–" : reviews.length}</Text>
                <Text style={{ color: "rgba(255,255,255,0.70)", fontSize: 8, fontWeight: "700", letterSpacing: 1.4, marginTop: 2 }}>REVIEWS</Text>
              </Ring>
              <View style={{ flex: 1, minWidth: 0, marginLeft: 16 }}>
                <Text style={pi.eyebrowLight}>{todayLabel.toUpperCase()}</Text>
                <Text style={pi.heroSubtitle}>Your review queue</Text>
                <View style={{ flexDirection: "row", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                  <View style={pi.heroChip}><ListTodo size={13} color={C.primaryFg} /><Text style={pi.heroChipText}>{loading ? "–" : tasks.length} pending</Text></View>
                  <View style={[pi.heroChip, !loading && overdueVisits.length > 0 && { backgroundColor: "rgba(192,57,43,0.30)" }]}>
                    <AlertTriangle size={13} color={C.primaryFg} /><Text style={pi.heroChipText}>{loading ? "–" : overdueVisits.length} overdue</Text>
                  </View>
                </View>
              </View>
            </View>
          </SafeAreaView>
        </LinearGradient>

        <View style={{ marginTop: -40, paddingHorizontal: 16, paddingBottom: 24 }}>
          {/* Stat tiles */}
          <View style={{ flexDirection: "row", gap: 10 }}>
            <Stat icon={Users} iconColor={C.info} iconBg="rgba(123,107,184,0.12)" glow="rgba(123,107,184,0.20)" value={loading ? "–" : patients.length} label="My Patients" />
            <Stat icon={FileText} iconColor={C.accent} iconBg="rgba(230,155,92,0.15)" glow="rgba(230,155,92,0.20)" value={loading ? "–" : trials.length} label="Trials" />
            <Stat icon={Stethoscope} iconColor={C.violet} iconBg="rgba(142,91,180,0.12)" glow="rgba(142,91,180,0.20)" value={loading ? "–" : sponsorCount} label="Sponsors" />
          </View>

          {/* Quick actions */}
          <Section label="QUICK ACTIONS" />
          <View style={{ flexDirection: "row", gap: 10 }}>
            <QA icon={FilePlus2} bg={C.info} iconColor="#FFFFFF" label="New Trial" onPress={() => router.push("/(app)/sponsor/add-trial")} testID="qa-new-trial" />
            <QA icon={UserPlus} gradient label="Add Patient" onPress={() => router.push("/(app)/clinical/add-patient")} testID="qa-add-patient" />
            <QA icon={Send} bg={C.accent} iconColor={C.accentFg} label="Invite" onPress={() => router.push("/(app)/clinical/invite-patient")} testID="qa-invite" />
          </View>

          {/* Org-admin console entry — only for organization admins */}
          {user?.org_admin && <OrgAdminEntry />}

          {/* My Patients */}
          <Section label="MY PATIENTS" action={
            <Pressable testID="see-all-patients" onPress={() => router.push("/(app)/clinical/patients")} style={{ flexDirection: "row", alignItems: "center" }}>
              <Text style={{ color: C.info, fontSize: 14, fontWeight: "700" }}>See all </Text>
              <ChevronRight size={16} color={C.info} />
            </Pressable>
          } />
          <View style={{ gap: 10 }}>
            {loading && <LoadingCard />}
            {!loading && patients.length === 0 && <EmptyCard text="No patients assigned yet" />}
            {!loading && patients.slice(0, 3).map(p => (
              <Pressable key={p.id} testID={`patient-${p.id}`} onPress={() => router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: p.id } })}>
                <View style={pi.patientCard}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                    <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: C.secondary, alignItems: "center", justifyContent: "center" }}>
                      <Text style={{ color: C.primary, fontWeight: "700", fontSize: 13 }}>{p.avatar_initials}</Text>
                    </View>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={{ color: C.fg, fontSize: 15, fontWeight: "700" }}>{p.full_name}</Text>
                      <Text style={{ color: C.muted, fontSize: 12, marginTop: 2 }}>{p.email}</Text>
                    </View>
                    <View style={{ paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999, backgroundColor: "rgba(92,154,110,0.18)" }}>
                      <Text style={{ color: C.success, fontSize: 11, fontWeight: "700" }}>Active</Text>
                    </View>
                  </View>
                </View>
              </Pressable>
            ))}
          </View>

          {/* Today's Reviews — trials awaiting the PI's schedule sign-off */}
          <Section label="TODAY'S REVIEWS" action={!loading ? <Text style={{ fontSize: 11, fontWeight: "700", color: C.muted, fontVariant: ["tabular-nums"] }}>{reviews.length} PENDING</Text> : undefined} />
          {loading && <LoadingCard />}
          {!loading && reviews.length === 0 && <EmptyCard text="No reviews awaiting sign-off — you're all caught up" />}
          {!loading && (
            <View>
              {reviews.map((r, i) => {
                const isNext = i === 0;
                const last = i === reviews.length - 1;
                const trial = r.trial_id ? trialById[r.trial_id] : null;
                const protocol = trial?.protocol_id || r.title.replace(/^Review visit schedule · /, "");
                return (
                  <View key={r.id} style={{ flexDirection: "row", gap: 12 }}>
                    <View style={{ alignItems: "center", paddingTop: 4 }}>
                      <View style={[
                        { width: 28, height: 28, borderRadius: 14, alignItems: "center", justifyContent: "center", borderWidth: 2, zIndex: 1 },
                        { backgroundColor: C.card, borderColor: isNext ? C.info : C.border },
                      ]}>
                        <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: isNext ? C.info : "rgba(123,95,115,0.30)" }} />
                      </View>
                      {!last && <View style={{ width: 2, flex: 1, marginVertical: 4, borderRadius: 1, backgroundColor: C.border }} />}
                    </View>
                    <View style={[pi.reviewCard, isNext && { borderColor: "rgba(123,107,184,0.40)" }]}>
                      <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 12 }}>
                        <View style={{ flex: 1, minWidth: 0 }}>
                          <View style={{ flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 6 }}>
                            <Text style={{ fontFamily: "monospace" as any, fontSize: 14, fontWeight: "700", color: C.fg }}>{protocol}</Text>
                            {isNext && (
                              <View style={{ flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, backgroundColor: "rgba(123,107,184,0.10)" }}>
                                <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: C.info }} />
                                <Text style={{ fontSize: 10, fontWeight: "700", color: C.info }}>Up next</Text>
                              </View>
                            )}
                          </View>
                          {r.subtitle ? <Text style={{ fontSize: 12, color: C.fg, fontWeight: "600", marginTop: 3 }} numberOfLines={2}>{r.subtitle}</Text> : null}
                          <View style={{ flexDirection: "row", alignItems: "center", marginTop: 6, gap: 6, flexWrap: "wrap" }}>
                            <View style={{ flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: C.surface, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999 }}>
                              <ClipboardCheck size={11} color={C.muted} /><Text style={{ fontSize: 11, fontWeight: "600", color: C.fg }}>Visit schedule</Text>
                            </View>
                          </View>
                        </View>
                        <Pressable testID={`review-${r.id}`} onPress={() => router.push("/(app)/clinical/schedule-review")}>
                          <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={pi.actionBtn}>
                            <Text style={{ color: C.primaryFg, fontSize: 12, fontWeight: "700" }}>Review</Text>
                          </LinearGradient>
                        </Pressable>
                      </View>
                    </View>
                  </View>
                );
              })}
            </View>
          )}

          {/* My Trials */}
          <Section label="MY TRIALS" action={
            <Pressable testID="see-all-trials" onPress={() => router.push("/(app)/clinical/my-trials")} style={{ flexDirection: "row", alignItems: "center" }}>
              <Text style={{ color: C.info, fontSize: 14, fontWeight: "700" }}>See all </Text>
              <ChevronRight size={16} color={C.info} />
            </Pressable>
          } />
          <View style={{ gap: 12 }}>
            {loading && <LoadingCard />}
            {!loading && trials.length === 0 && <EmptyCard text="No trials assigned yet" />}
            {!loading && trials.slice(0, 2).map(tr => {
              const status = tr.status ? tr.status.charAt(0).toUpperCase() + tr.status.slice(1) : "Active";
              return (
                <Pressable key={tr.id} testID={`trial-${tr.id}`} onPress={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: tr.id } })}>
                  <View style={pi.trialPanel}>
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
                    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6 }}>
                      {tr.phase ? <Tag bg="rgba(123,107,184,0.10)" fg={C.info} label={tr.phase} /> : null}
                      {tr.condition ? <Tag bg="rgba(230,155,92,0.12)" fg={C.accent} label={tr.condition} /> : null}
                    </View>
                  </View>
                </Pressable>
              );
            })}
          </View>
        </View>
      </ScrollView>

      <View style={pi.tabBar}>
        <Tab icon={Home} label="Dashboard" active />
        <Tab icon={Users} label="Patients" onPress={() => router.push("/(app)/clinical/patients")} testID="tab-patients" />
        <Tab icon={MessageCircle} label="Messages" onPress={() => router.push("/(app)/chat")} testID="tab-messages" />
        <Tab icon={CalIcon} label="Calendar" onPress={() => router.push({ pathname: "/(app)/clinical/team-calendar", params: { role: "pi" } } as any)} testID="tab-calendar" />
        <Tab icon={User} label="Me" onPress={() => router.push("/(app)/clinical/profile")} testID="tab-me" />
      </View>
    </View>
  );
}

function LoadingCard() {
  return (
    <View style={[pi.reviewCard, { alignItems: "center", justifyContent: "center", paddingVertical: 28, marginBottom: 0 }]}>
      <ActivityIndicator color={C.primary} />
    </View>
  );
}

// Org-admin console shortcut — resolves org type and routes to the matching
// console (site / smo / sponsor). Rendered only when user.org_admin is true.
function OrgAdminEntry() {
  const router = useRouter();
  const { orgType, orgName, loading, error } = useOrgContext();
  if (error) return null;
  const route = consoleRouteForType(orgType || undefined);
  return (
    <>
      <Section label="ORGANIZATION" />
      <Pressable testID="org-admin-console" disabled={loading} onPress={() => router.push(route as any)} style={pi.orgEntry}>
        <View style={pi.orgEntryIcon}><ShieldCheck size={22} color={C.primaryFg} /></View>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={{ fontSize: 15, fontWeight: "700", color: C.fg }}>Org admin console</Text>
          <Text style={{ fontSize: 12, color: C.muted, marginTop: 2 }} numberOfLines={1}>
            {orgName ? `Manage ${orgName} — trials, team & audit` : "Manage trials, team & audit"}
          </Text>
        </View>
        {loading ? <ActivityIndicator color={C.primary} /> : <ChevronRight size={20} color={C.muted} />}
      </Pressable>
    </>
  );
}

function EmptyCard({ text }: { text: string }) {
  return (
    <View style={[pi.reviewCard, { marginBottom: 0 }]}>
      <Text style={{ color: C.muted, fontSize: 13 }}>{text}</Text>
    </View>
  );
}

function Section({ label, action }: any) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 24, marginBottom: 12 }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }} style={{ width: 4, height: 14, borderRadius: 2 }} />
        <Text style={{ color: C.muted, fontSize: 11, fontWeight: "700", letterSpacing: 1.5 }}>{label}</Text>
      </View>
      {action}
    </View>
  );
}

function Stat({ icon: Icon, iconColor, iconBg, glow, value, label }: any) {
  return (
    <View style={pi.statTile}>
      <View style={{ position: "absolute", top: -24, right: -24, width: 64, height: 64, borderRadius: 32, backgroundColor: glow }} />
      <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" }}>
        <View style={{ width: 36, height: 36, borderRadius: 12, backgroundColor: iconBg, alignItems: "center", justifyContent: "center" }}>
          <Icon size={18} color={iconColor} />
        </View>
        <ArrowUpRight size={14} color="rgba(123,95,115,0.45)" />
      </View>
      <Text style={{ fontSize: 30, fontWeight: "700", color: C.fg, marginTop: 8, lineHeight: 32, fontVariant: ["tabular-nums"] }}>{value}</Text>
      <Text style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>{label}</Text>
    </View>
  );
}

function QA({ icon: Icon, gradient, bg, iconColor, label, onPress, testID }: any) {
  return (
    <Pressable testID={testID} onPress={onPress} style={pi.quickAction}>
      {gradient ? (
        <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ width: 48, height: 48, borderRadius: 16, alignItems: "center", justifyContent: "center" }}>
          <Icon size={22} color={C.primaryFg} />
        </LinearGradient>
      ) : (
        <View style={{ width: 48, height: 48, borderRadius: 16, backgroundColor: bg, alignItems: "center", justifyContent: "center" }}>
          <Icon size={22} color={iconColor} />
        </View>
      )}
      <Text style={{ fontSize: 12, fontWeight: "500", color: C.fg, marginTop: 8, textAlign: "center" }}>{label}</Text>
    </Pressable>
  );
}

function Tag({ bg, fg, label }: any) {
  return <View style={{ paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999, backgroundColor: bg }}><Text style={{ fontSize: 11, fontWeight: "700", color: fg }}>{label}</Text></View>;
}

function Ring({ value, size, stroke, children }: any) {
  const r = (size - stroke) / 2;
  const cir = 2 * Math.PI * r;
  return (
    <View style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}>
      <Svg width={size} height={size} style={{ position: "absolute", transform: [{ rotate: "-90deg" }] }}>
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.20)" strokeWidth={stroke} />
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={C.primaryFg} strokeWidth={stroke} strokeLinecap="round" strokeDasharray={`${cir} ${cir}`} strokeDashoffset={cir * (1 - value)} />
      </Svg>
      <View style={{ alignItems: "center" }}>{children}</View>
    </View>
  );
}

function Tab({ icon: Icon, label, active, onPress, testID }: any) {
  return (
    <Pressable testID={testID} onPress={onPress} style={{ flex: 1, alignItems: "center", justifyContent: "center", paddingVertical: 8 }}>
      <Icon size={22} color={active ? C.primary : C.muted} />
      <Text style={{ fontSize: 10, fontWeight: active ? "700" : "500", color: active ? C.primary : C.muted, marginTop: 4 }}>{label}</Text>
      {active && <View style={{ position: "absolute", top: 0, height: 3, width: 32, backgroundColor: C.primary, borderRadius: 2 }} />}
    </Pressable>
  );
}

const pi = StyleSheet.create({
  hero: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 56, overflow: "hidden" },
  heroTop: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 8 },
  eyebrowLight: { color: "rgba(255,255,255,0.65)", fontSize: 11, fontWeight: "700", letterSpacing: 1.5 },
  heroTitle: { color: C.primaryFg, fontSize: 24, fontWeight: "700", letterSpacing: -0.4 },
  heroSubtitle: { color: C.primaryFg, fontSize: 22, fontWeight: "700", letterSpacing: -0.2, marginTop: 2 },
  iconBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: "rgba(255,255,255,0.15)", alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: "rgba(255,255,255,0.20)" },
  bellBadge: { position: "absolute", top: -2, right: -2, minWidth: 18, height: 18, paddingHorizontal: 4, borderRadius: 9, backgroundColor: C.destructive, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: C.primaryDeep },
  bellBadgeText: { color: C.primaryFg, fontSize: 10, fontWeight: "700" },
  dayDeck: { flexDirection: "row", alignItems: "center", marginTop: 20 },
  heroChip: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 5, borderRadius: 999, backgroundColor: "rgba(255,255,255,0.15)", borderWidth: 1, borderColor: "rgba(255,255,255,0.15)" },
  heroChipText: { color: C.primaryFg, fontSize: 12, fontWeight: "700" },
  statTile: { flex: 1, backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, padding: 14, overflow: "hidden", shadowColor: "#2E1B33", shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 3 }, elevation: 3 },
  quickAction: { flex: 1, alignItems: "center", paddingVertical: 14, paddingHorizontal: 8, backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  orgEntry: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, padding: 14, shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  orgEntryIcon: { width: 44, height: 44, borderRadius: 14, backgroundColor: C.primary, alignItems: "center", justifyContent: "center" },
  patientCard: { backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, padding: 14, shadowColor: "#2E1B33", shadowOpacity: 0.04, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 1 },
  reviewCard: { flex: 1, backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, padding: 14, marginBottom: 12, shadowColor: "#2E1B33", shadowOpacity: 0.04, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 1 },
  actionBtn: { paddingHorizontal: 16, paddingVertical: 8, borderRadius: 999 },
  trialPanel: { backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, padding: 16, paddingLeft: 18, overflow: "hidden", shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  tabBar: { position: "absolute", bottom: 0, left: 0, right: 0, flexDirection: "row", backgroundColor: C.card, borderTopWidth: 1, borderTopColor: C.border, paddingTop: 8, paddingBottom: 24, paddingHorizontal: 8 },
});
