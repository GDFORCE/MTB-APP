import React, { useCallback, useEffect, useMemo, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, StatusBar, Text as RNText, ActivityIndicator, RefreshControl } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import Svg, { Path, Circle } from "react-native-svg";
import {
  Bell, Sun, FlaskConical, Users, CheckCircle2, AlertTriangle, ArrowUpRight,
  ChevronRight, FilePlus2, Share2, ClipboardCheck,
  Home, MessageCircle, User, RefreshCcw,
} from "lucide-react-native";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { useUnreadCount } from "@/src/hooks/use-unread-count";
import { colors as C, dawnGradient } from "@/src/theme/tokens";

const DAWN = dawnGradient;
// White overlays for the plum/gradient hero — same ladder the sibling dashboards use.
const W = { w10: "rgba(255,255,255,0.10)", w15: "rgba(255,255,255,0.15)", w20: "rgba(255,255,255,0.20)", w25: "rgba(255,255,255,0.25)", w55: "rgba(255,255,255,0.55)", w65: "rgba(255,255,255,0.65)", w70: "rgba(255,255,255,0.70)", w80: "rgba(255,255,255,0.80)" };

// GET /api/trials list item (extended in Task 4.2 with enrolled_count /
// target_enrollment / optional schedule_status).
type Trial = {
  id: string;
  protocol_id?: string;
  title?: string;
  phase?: string;
  condition?: string;
  status?: string;
  sponsor_name?: string;
  enrolled_count?: number;
  target_enrollment?: number | null;
  schedule_status?: "approved" | "flagged" | string;
};

// Trial lifecycle status → chip tone. Unknown states fall back to muted.
function statusTone(status?: string): { bg: string; fg: string; label: string } {
  const s = (status || "active").toLowerCase();
  const label = s.charAt(0).toUpperCase() + s.slice(1);
  if (s === "active") return { bg: "rgba(92,154,110,0.15)", fg: C.success, label };
  if (s === "completed") return { bg: "rgba(123,107,184,0.15)", fg: C.info, label };
  if (s === "terminated" || s === "closed") return { bg: "rgba(192,57,43,0.12)", fg: C.destructive, label };
  return { bg: "rgba(123,95,115,0.12)", fg: C.mutedFg, label };
}

// schedule_status → chip. Only 'approved' / 'flagged' render; anything else is
// omitted so we never fabricate a review state the backend didn't store.
function scheduleChip(status?: string): { bg: string; fg: string; label: string; icon: any } | null {
  if (status === "approved") return { bg: "rgba(92,154,110,0.14)", fg: C.success, label: "Approved", icon: CheckCircle2 };
  if (status === "flagged") return { bg: "rgba(192,57,43,0.12)", fg: C.destructive, label: "Flagged", icon: AlertTriangle };
  return null;
}

export default function SponsorDashboard() {
  const router = useRouter();
  const { user } = useAuth();
  const unread = useUnreadCount();
  const [trials, setTrials] = useState<Trial[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const r = await api.get("/trials");
      setTrials(Array.isArray(r.data) ? r.data : []);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Couldn't load your portfolio. Pull to retry.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };

  // ── Portfolio roll-ups (derived from live data — never hardcoded) ──
  const totalTrials = trials.length;
  const activeTrials = useMemo(() => trials.filter(t => (t.status || "active").toLowerCase() === "active").length, [trials]);
  const totalEnrolled = useMemo(() => trials.reduce((n, t) => n + (t.enrolled_count || 0), 0), [trials]);
  const flaggedCount = useMemo(() => trials.filter(t => t.schedule_status === "flagged").length, [trials]);
  // Enrolment coverage only where a target actually exists — no invented denominators.
  const withTarget = useMemo(() => trials.filter(t => typeof t.target_enrollment === "number" && (t.target_enrollment as number) > 0), [trials]);
  const totalTarget = useMemo(() => withTarget.reduce((n, t) => n + (t.target_enrollment as number), 0), [withTarget]);
  const enrolledOfTarget = useMemo(() => withTarget.reduce((n, t) => n + (t.enrolled_count || 0), 0), [withTarget]);
  const hasTargets = withTarget.length > 0 && totalTarget > 0;
  const enrollPct = hasTargets ? Math.min(100, Math.round((enrolledOfTarget / totalTarget) * 100)) : 0;
  // Ring: enrolment coverage when targets exist; otherwise the share of active trials.
  const ringValue = loading ? 0 : hasTargets ? enrollPct / 100 : totalTrials ? activeTrials / totalTrials : 0;

  const fullName = user?.full_name || "";
  const firstName = fullName.split(" ")[0] || "";
  const initials = user?.avatar_initials || fullName.split(" ").map(w => w[0]).slice(0, 2).join("").toUpperCase() || "?";
  const org = user?.organization || "";

  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingBottom: 110 }}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={C.primary} />}
      >
        {/* ── Hero — dawn gradient + sunrise arcs + portfolio deck ── */}
        <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
          <LinearGradient colors={[C.primaryDeep, "rgba(107,20,55,0.55)", "rgba(107,20,55,0)"] as any} style={StyleSheet.absoluteFill} />
          <View style={{ position: "absolute", right: -48, top: -48, width: 240, height: 240, opacity: 0.85 }} pointerEvents="none">
            <Svg viewBox="0 0 200 200" width={240} height={240}>
              <Path d="M30 110 a70 70 0 0 1 140 0" stroke={W.w25} strokeWidth="1.5" fill="none" />
              <Path d="M52 110 a48 48 0 0 1 96 0" stroke={W.w25} strokeWidth="1" fill="none" />
              <Circle cx="100" cy="110" r="22" stroke={W.w15} strokeWidth="1" fill="none" />
            </Svg>
          </View>

          <SafeAreaView edges={["top"]}>
            <View style={st.heroTop}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={st.eyebrowLight} numberOfLines={1}>SPONSOR{org ? ` · ${org}` : ""}</Text>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginTop: 4 }}>
                  <Text style={st.heroTitle}>{firstName ? `Hi, ${firstName}` : "Hello"}</Text>
                  <Sun size={20} color={W.w80} />
                </View>
              </View>
              <Pressable testID="sponsor-bell" onPress={() => router.push("/(app)/notifications")} style={st.iconBtn}>
                <Bell size={20} color={C.primaryFg} />
                {unread != null && unread > 0 && (
                  <View style={st.bellBadge}><Text style={st.bellBadgeText}>{unread > 9 ? "9+" : unread}</Text></View>
                )}
              </Pressable>
              <Pressable testID="sponsor-avatar" onPress={() => router.push("/(app)/clinical/profile")} style={st.iconBtn}>
                <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 13 }}>{initials}</Text>
              </Pressable>
            </View>

            {/* Portfolio deck */}
            <View style={st.dayDeck}>
              <ProgressRing value={ringValue} size={84} stroke={7}>
                <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 20, lineHeight: 22, fontVariant: ["tabular-nums"] }}>
                  {loading ? "–" : hasTargets ? `${enrollPct}%` : activeTrials}
                </Text>
                <Text style={{ color: W.w70, fontSize: 8, fontWeight: "700", letterSpacing: 1.4, marginTop: 2 }}>
                  {hasTargets ? "ENROLLED" : "ACTIVE"}
                </Text>
              </ProgressRing>
              <View style={{ flex: 1, minWidth: 0, marginLeft: 16 }}>
                <Text style={st.eyebrowLight}>PORTFOLIO</Text>
                <Text style={st.heroSubtitle}>{totalTrials} {totalTrials === 1 ? "trial" : "trials"} in flight</Text>
                <View style={{ flexDirection: "row", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                  <View style={st.heroChip}><FlaskConical size={13} color={C.primaryFg} /><Text style={st.heroChipText}>{loading ? "–" : activeTrials} active</Text></View>
                  <View style={st.heroChip}><Users size={13} color={C.primaryFg} /><Text style={st.heroChipText}>{loading ? "–" : totalEnrolled} enrolled</Text></View>
                  {!loading && flaggedCount > 0 && (
                    <View style={[st.heroChip, { backgroundColor: "rgba(192,57,43,0.30)" }]}>
                      <AlertTriangle size={13} color={C.primaryFg} /><Text style={st.heroChipText}>{flaggedCount} flagged</Text>
                    </View>
                  )}
                </View>
              </View>
            </View>
          </SafeAreaView>
        </LinearGradient>

        {/* ── Body floats up into hero ── */}
        <View style={{ marginTop: -40, paddingHorizontal: 16, paddingBottom: 24 }}>
          {/* Portfolio summary tiles */}
          <View style={{ flexDirection: "row", gap: 8 }}>
            <StatTile icon={FlaskConical} iconColor={C.info} iconBg="rgba(123,107,184,0.12)" glow="rgba(123,107,184,0.20)" value={loading ? "–" : totalTrials} label="Trials" />
            <StatTile icon={CheckCircle2} iconColor={C.success} iconBg="rgba(92,154,110,0.14)" glow="rgba(92,154,110,0.20)" value={loading ? "–" : activeTrials} label="Active" />
            <StatTile icon={Users} iconColor={C.violet} iconBg="rgba(142,91,180,0.12)" glow="rgba(142,91,180,0.20)" value={loading ? "–" : totalEnrolled} label="Enrolled" />
            <StatTile icon={AlertTriangle} iconColor={flaggedCount > 0 ? C.destructive : C.accent} iconBg={flaggedCount > 0 ? "rgba(192,57,43,0.12)" : "rgba(230,155,92,0.14)"} glow="rgba(230,155,92,0.20)" value={loading ? "–" : flaggedCount} label="Flagged" />
          </View>

          {/* Quick actions — Add Trial routes to the FROZEN add-trial screen, untouched. */}
          <SectionLabel label="QUICK ACTIONS" />
          <View style={{ flexDirection: "row", gap: 10 }}>
            <QuickAction icon={FilePlus2} bgGradient={false} bgColor={C.info} iconColor="#FFFFFF" label="Add Trial" onPress={() => router.push("/(app)/sponsor/add-trial")} testID="open-add-trial" />
            <QuickAction icon={Share2} bgGradient bgColor={undefined} iconColor={C.primaryFg} label="Share Schedule" onPress={() => router.push("/(app)/sponsor/share-schedule")} testID="open-share-schedule" />
            <QuickAction icon={ClipboardCheck} bgGradient={false} bgColor={C.accent} iconColor={C.accentFg} label="Review" onPress={() => router.push("/(app)/clinical/schedule-review")} testID="open-schedule-review" />
          </View>

          {/* Trials */}
          <SectionLabel label="MY TRIALS" action={
            <Pressable testID="see-all-trials" onPress={() => router.push("/(app)/clinical/my-trials")} style={{ flexDirection: "row", alignItems: "center" }}>
              <Text style={{ color: C.info, fontSize: 14, fontWeight: "700" }}>See all </Text>
              <ChevronRight size={16} color={C.info} />
            </Pressable>
          } />

          <View style={{ gap: 12 }}>
            {loading && <LoadingCard />}
            {!loading && error && <ErrorCard text={error} onRetry={load} />}
            {!loading && !error && trials.length === 0 && (
              <EmptyState onAdd={() => router.push("/(app)/sponsor/add-trial")} />
            )}
            {!loading && !error && trials.map(t => (
              <TrialCard
                key={t.id}
                t={t}
                onOpen={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: t.id } })}
                onShare={() => router.push({ pathname: "/(app)/sponsor/share-schedule", params: { id: t.id } })}
                onReview={() => router.push("/(app)/clinical/schedule-review")}
              />
            ))}
          </View>
        </View>
      </ScrollView>

      {/* Bottom nav */}
      <View style={st.tabBar}>
        <TabItem icon={Home} label="Dashboard" active />
        <TabItem icon={FlaskConical} label="Trials" onPress={() => router.push("/(app)/clinical/my-trials")} testID="tab-trials" />
        <TabItem icon={MessageCircle} label="Messages" onPress={() => router.push("/(app)/chat")} testID="tab-messages" />
        <TabItem icon={Bell} label="Alerts" badge={unread ?? 0} onPress={() => router.push("/(app)/notifications")} testID="tab-alerts" />
        <TabItem icon={User} label="Me" onPress={() => router.push("/(app)/clinical/profile")} testID="tab-me" />
      </View>
    </View>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────
function Text(props: any) {
  return <RNText {...props} style={[{ color: C.foreground }, props.style]} />;
}

function LoadingCard() {
  return (
    <View style={[st.card, { alignItems: "center", justifyContent: "center", paddingVertical: 32 }]}>
      <ActivityIndicator color={C.primary} />
      <Text style={{ color: C.mutedFg, fontSize: 12, marginTop: 10 }}>Loading portfolio…</Text>
    </View>
  );
}

function ErrorCard({ text, onRetry }: { text: string; onRetry: () => void }) {
  return (
    <View style={[st.card, { borderColor: "rgba(192,57,43,0.30)" }]}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: "rgba(192,57,43,0.12)", alignItems: "center", justifyContent: "center" }}>
          <AlertTriangle size={20} color={C.destructive} />
        </View>
        <Text style={{ flex: 1, color: C.foreground, fontSize: 13, fontWeight: "600" }}>{text}</Text>
      </View>
      <Pressable testID="portfolio-retry" onPress={onRetry} style={{ flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 10, borderRadius: 999, backgroundColor: C.surface }}>
        <RefreshCcw size={15} color={C.primary} />
        <Text style={{ color: C.primary, fontSize: 13, fontWeight: "700" }}>Retry</Text>
      </Pressable>
    </View>
  );
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <View style={[st.card, { alignItems: "center", paddingVertical: 28 }]}>
      <View style={{ width: 52, height: 52, borderRadius: 26, backgroundColor: C.secondary, alignItems: "center", justifyContent: "center" }}>
        <FlaskConical size={24} color={C.primary} />
      </View>
      <Text style={{ fontSize: 15, fontWeight: "700", color: C.foreground, marginTop: 12 }}>No trials yet</Text>
      <Text style={{ fontSize: 12, color: C.mutedFg, marginTop: 4, textAlign: "center" }}>Add your first protocol to start tracking enrolment.</Text>
      <Pressable testID="empty-add-trial" onPress={onAdd} style={{ marginTop: 14 }}>
        <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 18, paddingVertical: 10, borderRadius: 999 }}>
          <FilePlus2 size={16} color={C.primaryFg} />
          <Text style={{ color: C.primaryFg, fontSize: 13, fontWeight: "700" }}>Add Trial</Text>
        </LinearGradient>
      </Pressable>
    </View>
  );
}

function SectionLabel({ label, action }: { label: string; action?: React.ReactNode }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 24, marginBottom: 12 }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }} style={{ width: 4, height: 14, borderRadius: 2 }} />
        <Text style={{ color: C.mutedFg, fontSize: 11, fontWeight: "700", letterSpacing: 1.5 }}>{label}</Text>
      </View>
      {action}
    </View>
  );
}

function StatTile({ icon: Icon, iconColor, iconBg, glow, value, label }: any) {
  return (
    <View style={st.statTile}>
      <View style={{ position: "absolute", top: -20, right: -20, width: 56, height: 56, borderRadius: 28, backgroundColor: glow }} />
      <View style={{ width: 32, height: 32, borderRadius: 10, backgroundColor: iconBg, alignItems: "center", justifyContent: "center" }}>
        <Icon size={16} color={iconColor} />
      </View>
      <Text style={{ fontSize: 24, fontWeight: "700", color: C.foreground, marginTop: 8, lineHeight: 26, fontVariant: ["tabular-nums"] }}>{value}</Text>
      <Text style={{ fontSize: 10, color: C.mutedFg, marginTop: 1 }}>{label}</Text>
    </View>
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
      <Text style={{ fontSize: 12, fontWeight: "500", color: C.foreground, marginTop: 8, textAlign: "center" }}>{label}</Text>
    </Pressable>
  );
}

function TrialCard({ t, onOpen, onShare, onReview }: { t: Trial; onOpen: () => void; onShare: () => void; onReview: () => void }) {
  const tone = statusTone(t.status);
  const sched = scheduleChip(t.schedule_status);
  const enrolled = t.enrolled_count || 0;
  const target = typeof t.target_enrollment === "number" && t.target_enrollment > 0 ? t.target_enrollment : null;
  const pct = target ? Math.min(100, Math.round((enrolled / target) * 100)) : null;

  return (
    <View style={st.trialPanel}>
      <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }} style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 6 }} />
      <Pressable testID={`trial-${t.id}`} onPress={onOpen}>
        <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <View style={{ paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: "rgba(240,215,220,0.55)" }}>
            <Text style={{ fontFamily: "monospace" as any, fontSize: 11, fontWeight: "700", color: C.primary }}>{t.protocol_id || t.id.slice(0, 8)}</Text>
          </View>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            {sched && (
              <View style={{ flexDirection: "row", alignItems: "center", gap: 3, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, backgroundColor: sched.bg }}>
                <sched.icon size={11} color={sched.fg} />
                <Text style={{ fontSize: 11, fontWeight: "700", color: sched.fg }}>{sched.label}</Text>
              </View>
            )}
            <View style={{ paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, backgroundColor: tone.bg }}>
              <Text style={{ fontSize: 11, fontWeight: "700", color: tone.fg }}>{tone.label}</Text>
            </View>
            <View style={{ width: 26, height: 26, borderRadius: 13, backgroundColor: C.surface, alignItems: "center", justifyContent: "center" }}>
              <ArrowUpRight size={14} color="rgba(123,95,115,0.7)" />
            </View>
          </View>
        </View>

        <Text style={{ fontSize: 16, fontWeight: "700", color: C.foreground, marginBottom: 10 }} numberOfLines={2}>{t.title || "Untitled trial"}</Text>

        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
          {t.phase ? <Tag bg="rgba(123,107,184,0.10)" fg={C.info} label={t.phase} /> : null}
          {t.condition ? <Tag bg="rgba(230,155,92,0.12)" fg={C.accent} label={t.condition} /> : null}
        </View>

        {/* Enrolment progress — bar only when a target exists; else an honest count. */}
        <View style={{ marginBottom: 4 }}>
          <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
            <Text style={{ fontSize: 11, fontWeight: "700", letterSpacing: 1, color: "rgba(123,95,115,0.75)" }}>ENROLMENT</Text>
            {target ? (
              <Text style={{ fontSize: 12, fontWeight: "700", color: C.foreground, fontVariant: ["tabular-nums"] }}>{enrolled}/{target} · {pct}%</Text>
            ) : (
              <Text style={{ fontSize: 12, fontWeight: "700", color: C.foreground, fontVariant: ["tabular-nums"] }}>{enrolled} enrolled · no target</Text>
            )}
          </View>
          {target ? (
            <View style={{ height: 8, borderRadius: 999, backgroundColor: "rgba(123,95,115,0.12)", overflow: "hidden" }}>
              <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={{ height: "100%", width: `${pct ?? 0}%`, borderRadius: 999 }} />
            </View>
          ) : (
            <View style={{ height: 8, borderRadius: 999, backgroundColor: "rgba(123,95,115,0.10)" }} />
          )}
        </View>
      </Pressable>

      {/* Share + review shortcuts */}
      <View style={{ flexDirection: "row", gap: 8, marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: C.border }}>
        <Pressable testID={`share-${t.id}`} onPress={onShare} style={st.cardBtn}>
          <Share2 size={14} color={C.primary} />
          <Text style={{ fontSize: 12, fontWeight: "700", color: C.primary }}>Share</Text>
        </Pressable>
        <Pressable testID={`review-${t.id}`} onPress={onReview} style={st.cardBtn}>
          <ClipboardCheck size={14} color={C.info} />
          <Text style={{ fontSize: 12, fontWeight: "700", color: C.info }}>Review</Text>
        </Pressable>
      </View>
    </View>
  );
}

function Tag({ bg, fg, label }: any) {
  return <View style={{ paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999, backgroundColor: bg }}><Text style={{ fontSize: 11, fontWeight: "700", color: fg }}>{label}</Text></View>;
}

function ProgressRing({ value, size, stroke, children }: any) {
  const r = (size - stroke) / 2;
  const cir = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(1, value));
  return (
    <View style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}>
      <Svg width={size} height={size} style={{ position: "absolute", transform: [{ rotate: "-90deg" }] }}>
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={W.w20} strokeWidth={stroke} />
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={C.primaryFg} strokeWidth={stroke} strokeLinecap="round" strokeDasharray={`${cir} ${cir}`} strokeDashoffset={cir * (1 - v)} />
      </Svg>
      <View style={{ alignItems: "center" }}>{children}</View>
    </View>
  );
}

function TabItem({ icon: Icon, label, active, onPress, testID, badge }: any) {
  return (
    <Pressable testID={testID} onPress={onPress} style={{ flex: 1, alignItems: "center", justifyContent: "center", paddingVertical: 8 }}>
      <View>
        <Icon size={22} color={active ? C.primary : C.mutedFg} />
        {badge != null && badge > 0 && (
          <View style={{ position: "absolute", top: -6, right: -10, minWidth: 16, height: 16, paddingHorizontal: 3, borderRadius: 8, backgroundColor: C.destructive, alignItems: "center", justifyContent: "center" }}>
            <Text style={{ color: C.destructiveFg, fontSize: 9, fontWeight: "700" }}>{badge > 9 ? "9+" : badge}</Text>
          </View>
        )}
      </View>
      <Text style={{ fontSize: 10, fontWeight: active ? "700" : "500", color: active ? C.primary : C.mutedFg, marginTop: 4 }}>{label}</Text>
      {active && <View style={{ position: "absolute", top: 0, height: 3, width: 32, backgroundColor: C.primary, borderRadius: 2 }} />}
    </Pressable>
  );
}

const st = StyleSheet.create({
  hero: { paddingHorizontal: 16, paddingTop: 8, paddingBottom: 56, overflow: "hidden" },
  heroTop: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 8 },
  eyebrowLight: { color: W.w65, fontSize: 11, fontWeight: "700", letterSpacing: 1.5 },
  heroTitle: { color: C.primaryFg, fontSize: 28, fontWeight: "700", letterSpacing: -0.4 },
  heroSubtitle: { color: C.primaryFg, fontSize: 22, fontWeight: "700", letterSpacing: -0.2, marginTop: 2 },
  iconBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: W.w15, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: W.w20 },
  bellBadge: { position: "absolute", top: -2, right: -2, minWidth: 18, height: 18, paddingHorizontal: 4, borderRadius: 9, backgroundColor: C.destructive, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: C.primaryDeep },
  bellBadgeText: { color: C.primaryFg, fontSize: 10, fontWeight: "700" },
  dayDeck: { flexDirection: "row", alignItems: "center", marginTop: 20 },
  heroChip: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 5, borderRadius: 999, backgroundColor: W.w15, borderWidth: 1, borderColor: W.w15 },
  heroChipText: { color: C.primaryFg, fontSize: 12, fontWeight: "700" },
  statTile: { flex: 1, backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, padding: 12, overflow: "hidden", shadowColor: "#2E1B33", shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 3 }, elevation: 3 },
  quickAction: { flex: 1, alignItems: "center", paddingVertical: 14, paddingHorizontal: 8, backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  trialPanel: { backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, padding: 16, paddingLeft: 18, overflow: "hidden", position: "relative", shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  card: { backgroundColor: C.card, borderRadius: 18, borderWidth: 1, borderColor: C.border, padding: 16, shadowColor: "#2E1B33", shadowOpacity: 0.04, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 1 },
  cardBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 9, borderRadius: 999, backgroundColor: C.surface },
  tabBar: { position: "absolute", bottom: 0, left: 0, right: 0, flexDirection: "row", backgroundColor: C.card, borderTopWidth: 1, borderTopColor: C.border, paddingTop: 8, paddingBottom: 24, paddingHorizontal: 8 },
});
