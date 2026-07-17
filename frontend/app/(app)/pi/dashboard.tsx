import React, { useCallback, useEffect, useMemo, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, StatusBar, Text, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import Svg, { Path, Circle } from "react-native-svg";
import {
  Bell, Sun, Users, FileText, Stethoscope, ArrowUpRight, ChevronRight, FilePlus2, UserPlus,
  ListTodo, AlertTriangle, ClipboardCheck, Home, MessageCircle, Calendar as CalIcon, User, ShieldCheck,
} from "lucide-react-native";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import { useOrgContext, consoleRouteForType } from "@/src/components/org-admin-kit";
import {
  AnimatedCount,
  ClinicalDashboard,
  ClinicalDashboardTask,
  ClinicalDashboardVisit,
  DashboardReveal,
  useAnimatedProgress,
} from "@/src/features/clinical/dashboard";

const C = {
  bg: "#FBF2E8", surface: "#F4E5D3", card: "#FEFAF1", fg: "#2E1B33", muted: "#7B5F73", border: "#E6D6C5",
  primary: "#A6213F", primaryDeep: "#6B1437", primaryFg: "#FBF2E8", secondary: "#F0D7DC",
  accent: "#E69B5C", accentFg: "#5A3318", info: "#7B6BB8", violet: "#8E5BB4",
  warning: "#D89A3C", success: "#5C9A6E", destructive: "#C0392B",
  dawnFrom: "#F5C57A", dawnMid: "#E07A4B", dawnTo: "#A6213F",
};
const DAWN = [C.dawnFrom, C.dawnMid, C.dawnTo] as const;

// GET /api/tasks item — action queue computed server-side for site staff.
type Task = ClinicalDashboardTask;
type TeamVisit = ClinicalDashboardVisit;

export default function PiDashboard() {
  const router = useRouter();
  const { user } = useAuth();
  // SMO and Site accounts share the de-identified OPERATIONAL org dashboard;
  // governance stays in the separate org-admin console (org_admin entry only).
  const isSmo = user?.role === "smo" || user?.role === "site";
  const unread = useUnreadCount();
  const [trials, setTrials] = useState<any[]>([]);
  const [patients, setPatients] = useState<any[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [upcomingVisits, setUpcomingVisits] = useState<TeamVisit[]>([]);
  const [siteCount, setSiteCount] = useState(0);
  const [smoSponsorCount, setSmoSponsorCount] = useState(0);
  const [dashboard, setDashboard] = useState<ClinicalDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
    if (isSmo) {
      const response = await api.get(user?.role === "site" ? "/site/dashboard" : "/smo/dashboard");
      const data = response.data || {};
      setTrials(Array.isArray(data.trials) ? data.trials : []);
      setPatients(Array.isArray(data.subjects) ? data.subjects : []);
      setTasks(Array.isArray(data.tasks) ? data.tasks : []);
      setSiteCount(Number(data.totals?.sites || 0));
      setSmoSponsorCount(Number(data.totals?.sponsors || 0));
      return;
    }
    const response = await api.get<ClinicalDashboard>("/pi/dashboard");
    const data = response.data;
    setDashboard(data);
    setTrials(Array.isArray(data.trials) ? data.trials : []);
    setPatients(Array.isArray(data.patients) ? data.patients : []);
    setTasks(Array.isArray(data.tasks) ? data.tasks : []);
    setUpcomingVisits(Array.isArray(data.upcoming_visits) ? data.upcoming_visits : []);
    setSiteCount(Number(data.totals?.sites || 0));
    setSmoSponsorCount(Number(data.totals?.sponsors || 0));
    } catch (error: any) {
      setLoadError(error?.response?.data?.detail || "Couldn't load your dashboard.");
    } finally {
      setLoading(false);
    }
  }, [isSmo, user?.role]);

  useEffect(() => { loadDashboard(); }, [loadDashboard]);

  const trialById = useMemo(() => Object.fromEntries(trials.map((t: any) => [t.id, t])), [trials]);
  // PI review queue = trials awaiting schedule sign-off (server type schedule_review).
  const reviews = useMemo(() => tasks.filter(t => t.type === "schedule_review"), [tasks]);
  const overdueVisits = useMemo(() => tasks.filter(t => t.type === "overdue_visit"), [tasks]);
  const sponsorCount = useMemo(
    () => isSmo ? smoSponsorCount : (dashboard?.totals.sponsors
      ?? new Set(trials.map((t: any) => t.sponsor_name).filter(Boolean)).size),
    [dashboard?.totals.sponsors, isSmo, smoSponsorCount, trials],
  );
  const siteTotal = useMemo(
    () => isSmo ? siteCount : (dashboard?.totals.sites
      ?? new Set(upcomingVisits.map(visit => visit.site).filter(Boolean)).size),
    [dashboard?.totals.sites, isSmo, siteCount, upcomingVisits],
  );

  const firstName = (user?.full_name || "").split(" ").pop() || "";
  const initials = user?.avatar_initials || (user?.full_name || "").split(" ").map(w => w[0]).slice(0, 2).join("").toUpperCase() || "?";
  const todayLabel = new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  const todayIso = new Date().toISOString().slice(0, 10);
  const visitsToday = useMemo(
    () => upcomingVisits.filter(visit => visit.scheduled_date?.slice(0, 10) === todayIso),
    [todayIso, upcomingVisits],
  );
  const completedToday = dashboard?.today.completed
    ?? visitsToday.filter(visit => visit.status === "completed").length;
  const totalToday = dashboard?.today.total ?? visitsToday.length;
  const dayProgress = loading || totalToday === 0 ? 0 : completedToday / totalToday;
  const weekLoad = useMemo(() => {
    const today = new Date();
    const monday = new Date(today);
    monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
    monday.setHours(0, 0, 0, 0);
    return Array.from({ length: 7 }, (_, index) => {
      const date = new Date(monday);
      date.setDate(monday.getDate() + index);
      const next = new Date(date);
      next.setDate(date.getDate() + 1);
      const count = tasks.filter((task) => {
        if (!task.due) return false;
        const due = new Date(task.due);
        return due >= date && due < next;
      }).length;
      return {
        label: date.toLocaleDateString("en-GB", { weekday: "short" }).slice(0, 2),
        count,
        today: date.toDateString() === today.toDateString(),
      };
    });
  }, [tasks]);

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
                <Text style={pi.eyebrowLight}>{user?.role === "site" ? "SITE · OPERATIONS" : isSmo ? "SMO · SITE MANAGEMENT" : "PRINCIPAL INVESTIGATOR"}</Text>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 4 }}>
                  <Text style={pi.heroTitle}>{firstName ? `Hi, ${isSmo ? firstName : `Dr. ${firstName}`}` : "Hello"}</Text>
                  <Sun size={20} color="rgba(255,255,255,0.80)" />
                </View>
              </View>
              <Pressable testID="pi-bell" onPress={() => router.push("/(app)/notifications")} style={pi.iconBtn}>
                <Bell size={20} color={C.primaryFg} />
                {unread != null && unread > 0 && (
                  <View style={pi.bellBadge}><Text style={pi.bellBadgeText}>{unread > 9 ? "9+" : unread}</Text></View>
                )}
              </Pressable>
              <Pressable testID="pi-avatar" onPress={() => router.push("/(app)/clinical/profile")} style={pi.iconBtn}>
                <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 13 }}>{initials}</Text>
              </Pressable>
            </View>
            <View style={pi.dayDeck}>
              <Ring value={dayProgress} size={84} stroke={7}>
                <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 22, lineHeight: 24, fontVariant: ["tabular-nums"] }}>
                  {loading ? "–" : isSmo ? reviews.length : `${completedToday}/${visitsToday.length}`}
                </Text>
                <Text style={{ color: "rgba(255,255,255,0.70)", fontSize: 8, fontWeight: "700", letterSpacing: 1.4, marginTop: 2 }}>
                  {isSmo ? "REVIEWS" : "VISITS"}
                </Text>
              </Ring>
              <View style={{ flex: 1, minWidth: 0, marginLeft: 16 }}>
                <Text style={pi.eyebrowLight}>{todayLabel.toUpperCase()}</Text>
                <Text style={pi.heroSubtitle}>{isSmo ? "Your network today" : "Your review queue"}</Text>
                <View style={{ flexDirection: "row", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                  <View style={pi.heroChip}><ListTodo size={13} color={C.primaryFg} /><Text style={pi.heroChipText}>{loading ? "–" : (dashboard?.today.pending ?? tasks.length)} pending</Text></View>
                  <View style={[pi.heroChip, !loading && overdueVisits.length > 0 && { backgroundColor: "rgba(192,57,43,0.30)" }]}>
                    <AlertTriangle size={13} color={C.primaryFg} /><Text style={pi.heroChipText}>{loading ? "–" : (dashboard?.today.overdue ?? overdueVisits.length)} overdue</Text>
                  </View>
                </View>
              </View>
            </View>
          </SafeAreaView>
        </LinearGradient>

        <View style={{ marginTop: -40, paddingHorizontal: 16, paddingBottom: 24 }}>
          {!!loadError && <DashboardError message={loadError} onRetry={loadDashboard} />}
          {/* Stat tiles */}
          <DashboardReveal delay={40} style={{ flexDirection: "row", gap: 10, flexWrap: "wrap" }}>
            <Stat compact icon={FileText} iconColor={C.info} iconBg="rgba(123,107,184,0.12)" glow="rgba(123,107,184,0.20)" value={loading ? null : trials.length} label="Total Trials" onPress={() => router.push("/(app)/clinical/my-trials")} />
            <Stat compact icon={Stethoscope} iconColor={C.accent} iconBg="rgba(230,155,92,0.15)" glow="rgba(230,155,92,0.20)" value={loading ? null : sponsorCount} label="Sponsors" />
            <Stat compact icon={Users} iconColor={C.violet} iconBg="rgba(142,91,180,0.12)" glow="rgba(142,91,180,0.20)" value={loading ? null : patients.length} label="Patients" onPress={() => router.push("/(app)/clinical/patients")} />
            <Stat compact icon={Home} iconColor={C.success} iconBg="rgba(92,154,110,0.14)" glow="rgba(92,154,110,0.18)" value={loading ? null : siteTotal} label="Sites" />
          </DashboardReveal>

          {/* Quick actions */}
          <Section label="QUICK ACTIONS" />
          <View style={{ flexDirection: "row", gap: 10 }}>
            {(isSmo || loading || dashboard?.capabilities.can_create_trial) && <QA
              icon={FilePlus2}
              bg={C.info}
              iconColor="#FFFFFF"
              label="New Trial"
              onPress={() => router.push(isSmo ? (user?.role === "site" ? "/(app)/org-admin/site" : "/(app)/org-admin/smo") : "/(app)/sponsor/add-trial")}
              testID="qa-new-trial"
            />}
            {(isSmo || loading || dashboard?.capabilities.can_add_patient) && <QA icon={UserPlus} gradient label="Add Patient" onPress={() => router.push("/(app)/clinical/add-patient")} testID="qa-add-patient" />}
          </View>

          {/* Org-admin console entry — only for organization admins */}
          {user?.org_admin && <OrgAdminEntry />}

          <Section label="THIS WEEK" action={
            <Pressable onPress={() => router.push({ pathname: "/(app)/clinical/team-calendar", params: { role: "pi" } } as any)}>
              <Text style={{ color: C.info, fontSize: 13, fontWeight: "700" }}>View week</Text>
            </Pressable>
          } />
          <WeekLoadChart days={weekLoad} />

          {/* Upcoming visits */}
          <Section label="UPCOMING VISITS" action={
            <Text style={{ fontSize: 11, fontWeight: "700", color: C.muted, fontVariant: ["tabular-nums"] }}>
              {isSmo ? `${tasks.filter(task => task.type === "visit_today").length} TODAY` : `${completedToday}/${visitsToday.length} DONE`}
            </Text>
          } />
          <View style={{ gap: 10 }}>
            {loading && <LoadingCard />}
            {!loading && (isSmo ? patients : upcomingVisits).length === 0 && <EmptyCard text="No upcoming visits this week" />}
            {!loading && (isSmo ? patients : upcomingVisits).slice(0, 3).map((p: any) => (
              <Pressable
                key={p.id}
                testID={`upcoming-visit-${p.id}`}
                onPress={() => isSmo
                  ? p.trial_id && router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: p.trial_id } })
                  : router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: p.patient_id } })}
              >
                <View style={pi.patientCard}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                    <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: C.secondary, alignItems: "center", justifyContent: "center" }}>
                      <Text style={{ color: C.primary, fontWeight: "700", fontSize: 13 }}>{isSmo ? p.avatar_initials : p.patient_initials}</Text>
                    </View>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={{ color: C.fg, fontSize: 15, fontWeight: "700" }}>
                        {isSmo ? p.subject_id : `${p.subject_label || "Subject"} · ${p.patient_initials || "P"}`}
                      </Text>
                      <Text style={{ color: C.muted, fontSize: 12, marginTop: 2 }}>
                        {isSmo
                          ? [p.site, p.pi_name].filter(Boolean).join(" · ")
                          : [
                              p.protocol_id,
                              p.name,
                              p.scheduled_date
                                ? new Date(p.scheduled_date).toLocaleString("en-GB", { weekday: "short", hour: "numeric", minute: "2-digit" })
                                : "",
                            ].filter(Boolean).join(" · ")}
                      </Text>
                    </View>
                    <View style={{ paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999, backgroundColor: p.status === "completed" ? "rgba(92,154,110,0.18)" : "rgba(123,107,184,0.12)" }}>
                      <Text style={{ color: p.status === "completed" ? C.success : C.info, fontSize: 11, fontWeight: "700" }}>
                        {p.status === "completed" ? "Done" : "Upcoming"}
                      </Text>
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
                        <Pressable
                          testID={`review-${r.id}`}
                          onPress={() => router.push({
                            pathname: "/(app)/clinical/schedule-review",
                            params: {
                              id: r.schedule_review_id || "",
                              trialId: r.trial_id || "",
                            },
                          })}
                        >
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
        <Tab icon={FileText} label="My Trials" onPress={() => router.push("/(app)/clinical/my-trials")} testID="tab-trials" />
        <Tab icon={MessageCircle} label="Messages" onPress={() => router.push("/(app)/chat")} testID="tab-messages" />
        <Tab icon={CalIcon} label="Calendar" onPress={() => router.push({ pathname: "/(app)/clinical/team-calendar", params: { role: isSmo ? "smo" : "pi" } } as any)} testID="tab-calendar" />
        <Tab icon={User} label="Me" onPress={() => router.push("/(app)/clinical/profile")} testID="tab-me" />
      </View>
    </View>
  );
}

function WeekLoadChart({ days }: { days: { label: string; count: number; today: boolean }[] }) {
  const max = Math.max(1, ...days.map((day) => day.count));
  const total = days.reduce((sum, day) => sum + day.count, 0);
  return (
    <View style={pi.weekCard}>
      <View style={pi.weekHeader}>
        <View>
          <Text style={pi.weekTitle}>{total} scheduled item{total === 1 ? "" : "s"}</Text>
          <Text style={pi.weekSubtitle}>Network and visit workload</Text>
        </View>
        <CalIcon size={20} color={C.info} />
      </View>
      <View style={pi.weekBars}>
        {days.map((day) => (
          <View key={day.label} style={pi.weekDay}>
            <Text style={[pi.weekCount, day.today && { color: C.primary }]}>{day.count}</Text>
            <View style={pi.weekTrack}>
              <LinearGradient
                colors={DAWN as any}
                start={{ x: 0, y: 1 }}
                end={{ x: 0, y: 0 }}
                style={[
                  pi.weekFill,
                  { height: `${Math.max(day.count ? 20 : 5, (day.count / max) * 100)}%` },
                  !day.today && { opacity: 0.62 },
                ]}
              />
            </View>
            <Text style={[pi.weekLabel, day.today && pi.weekLabelToday]}>{day.label}</Text>
          </View>
        ))}
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

function DashboardError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <View style={pi.errorCard}>
      <AlertTriangle size={18} color={C.destructive} />
      <View style={{ flex: 1 }}>
        <Text style={pi.errorTitle}>Dashboard couldn’t load</Text>
        <Text style={pi.errorCopy}>{message}</Text>
      </View>
      <Pressable testID="pi-dashboard-retry" onPress={onRetry} style={pi.retryButton}>
        <Text style={pi.retryText}>Retry</Text>
      </Pressable>
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

function Stat({ icon: Icon, iconColor, iconBg, glow, value, label, compact, onPress }: any) {
  return (
    <Pressable disabled={!onPress} onPress={onPress} style={[pi.statTile, compact && { flexBasis: "47%", flexGrow: 1 }]}>
      <View style={{ position: "absolute", top: -24, right: -24, width: 64, height: 64, borderRadius: 32, backgroundColor: glow }} />
      <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" }}>
        <View style={{ width: 36, height: 36, borderRadius: 12, backgroundColor: iconBg, alignItems: "center", justifyContent: "center" }}>
          <Icon size={18} color={iconColor} />
        </View>
        <ArrowUpRight size={14} color="rgba(123,95,115,0.45)" />
      </View>
      {value == null
        ? <Text style={{ fontSize: 30, fontWeight: "700", color: C.fg, marginTop: 8, lineHeight: 32 }}>–</Text>
        : <AnimatedCount value={value} style={{ fontSize: 30, fontWeight: "700", color: C.fg, marginTop: 8, lineHeight: 32, fontVariant: ["tabular-nums"] }} />}
      <Text style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>{label}</Text>
    </Pressable>
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
  const animatedValue = useAnimatedProgress(value);
  const r = (size - stroke) / 2;
  const cir = 2 * Math.PI * r;
  return (
    <View style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}>
      <Svg width={size} height={size} style={{ position: "absolute", transform: [{ rotate: "-90deg" }] }}>
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.20)" strokeWidth={stroke} />
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={C.primaryFg} strokeWidth={stroke} strokeLinecap="round" strokeDasharray={`${cir} ${cir}`} strokeDashoffset={cir * (1 - animatedValue)} />
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
  weekCard: { padding: 15, borderRadius: 21, borderWidth: 1, borderColor: C.border, backgroundColor: C.card, shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 7, shadowOffset: { width: 0, height: 2 }, elevation: 2 },
  weekHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  weekTitle: { fontSize: 13, fontWeight: "700", color: C.fg },
  weekSubtitle: { marginTop: 2, fontSize: 10, color: C.muted },
  weekBars: { height: 112, marginTop: 15, flexDirection: "row", alignItems: "flex-end", gap: 7 },
  weekDay: { flex: 1, height: "100%", alignItems: "center" },
  weekCount: { height: 17, fontSize: 9, fontWeight: "700", color: C.muted },
  weekTrack: { flex: 1, width: "100%", overflow: "hidden", justifyContent: "flex-end", borderRadius: 8, backgroundColor: C.surface },
  weekFill: { width: "100%", borderRadius: 8 },
  weekLabel: { marginTop: 6, fontSize: 9.5, fontWeight: "600", color: C.muted },
  weekLabelToday: { color: C.primary, fontWeight: "800" },
  patientCard: { backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, padding: 14, shadowColor: "#2E1B33", shadowOpacity: 0.04, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 1 },
  reviewCard: { flex: 1, backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, padding: 14, marginBottom: 12, shadowColor: "#2E1B33", shadowOpacity: 0.04, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 1 },
  errorCard: { marginBottom: 12, padding: 13, flexDirection: "row", alignItems: "center", gap: 10, borderRadius: 16, borderWidth: 1, borderColor: "rgba(192,57,43,0.28)", backgroundColor: "rgba(192,57,43,0.08)" },
  errorTitle: { color: C.destructive, fontSize: 13, fontWeight: "700" },
  errorCopy: { marginTop: 2, color: C.muted, fontSize: 11 },
  retryButton: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999, backgroundColor: C.destructive },
  retryText: { color: C.primaryFg, fontSize: 11, fontWeight: "700" },
  actionBtn: { paddingHorizontal: 16, paddingVertical: 8, borderRadius: 999 },
  trialPanel: { backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, padding: 16, paddingLeft: 18, overflow: "hidden", shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  tabBar: { position: "absolute", bottom: 0, left: 0, right: 0, flexDirection: "row", backgroundColor: C.card, borderTopWidth: 1, borderTopColor: C.border, paddingTop: 8, paddingBottom: 24, paddingHorizontal: 8 },
});
