import React, { useEffect, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, StatusBar, Text } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import Svg, { Circle } from "react-native-svg";
import {
  Bell, MessageCircle, ChevronRight, Check, Activity, Clock, Home, FlaskConical, Calendar as CalIcon, User,
} from "lucide-react-native";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";

const C = {
  bg: "#FBF2E8", surface: "#F4E5D3", card: "#FEFAF1", fg: "#2E1B33", muted: "#7B5F73", border: "#E6D6C5",
  primary: "#A6213F", primaryDeep: "#6B1437", primaryFg: "#FBF2E8",
  accent: "#E69B5C", info: "#7B6BB8", violet: "#8E5BB4", warning: "#D89A3C", success: "#5C9A6E",
  dawnFrom: "#F5C57A", dawnMid: "#E07A4B", dawnTo: "#A6213F",
};
const DAWN = [C.dawnFrom, C.dawnMid, C.dawnTo] as const;

const TOTAL = 10;

export default function PatientDashboard() {
  const router = useRouter();
  const { user } = useAuth();
  const [visits, setVisits] = useState<any[]>([]);
  const [notifs, setNotifs] = useState<any[]>([]);
  useEffect(() => { (async () => {
    const [v, n] = await Promise.all([
      api.get("/visits/mine").catch(() => ({ data: [] })),
      api.get("/notifications").catch(() => ({ data: [] })),
    ]);
    setVisits(v.data); setNotifs(n.data);
  })(); }, []);

  const completed = visits.filter(v => v.status === "completed").length;
  const total = visits.length || TOTAL;
  const next = visits.find(v => v.status === "upcoming");
  const pct = Math.round((completed / total) * 100);
  const firstName = (user?.full_name || "Priya").split(" ")[0];
  const initials = user?.avatar_initials || "PK";
  const daysToNext = next ? Math.max(0, Math.ceil((new Date(next.scheduled_date).getTime() - Date.now()) / 86400000)) : 4;
  const nextDate = next ? new Date(next.scheduled_date) : new Date(2025, 4, 23);

  // Calendar mini (current month)
  const calendarMonth = new Date(nextDate.getFullYear(), nextDate.getMonth(), 1);
  const monthVisits: Record<number, "completed" | "upcoming" | "scheduled"> = {};
  visits.forEach(v => {
    const d = new Date(v.scheduled_date);
    if (d.getMonth() === calendarMonth.getMonth() && d.getFullYear() === calendarMonth.getFullYear()) {
      monthVisits[d.getDate()] = v.status === "completed" ? "completed" : v.status === "upcoming" ? "upcoming" : "scheduled";
    }
  });
  const today = new Date().getDate();
  const startDay = calendarMonth.getDay();
  const daysInMonth = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() + 1, 0).getDate();
  const cells: (number | null)[] = Array(startDay).fill(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);

  return (
    <View style={{ flex: 1, backgroundColor: C.bg }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: 110 }} showsVerticalScrollIndicator={false}>
        {/* ── Dawn hero (radial plum → deep plum) ── */}
        <View style={{ backgroundColor: C.primaryDeep, borderBottomLeftRadius: 28, borderBottomRightRadius: 28, overflow: "hidden", paddingHorizontal: 24, paddingTop: 8, paddingBottom: 28 }}>
          {/* Radial-ish plum-to-deep using stacked gradient */}
          <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0.5, y: 0 }} end={{ x: 0.5, y: 1 }} style={StyleSheet.absoluteFill} />
          {/* Corner sun glow top-right */}
          <View pointerEvents="none" style={{ position: "absolute", top: -56, right: -48, width: 180, height: 180, borderRadius: 90, backgroundColor: C.dawnFrom, opacity: 0.30 }} />
          {/* Wide warm dawn rising along bottom */}
          <View pointerEvents="none" style={{ position: "absolute", bottom: -80, left: -50, right: -50, height: 180, borderRadius: 200, backgroundColor: C.dawnMid, opacity: 0.25 }} />
          {/* Drift motes */}
          <View pointerEvents="none" style={{ position: "absolute", top: 40, right: 96, width: 10, height: 10, borderRadius: 5, backgroundColor: "rgba(255,255,255,0.30)" }} />
          <View pointerEvents="none" style={{ position: "absolute", top: 96, left: 48, width: 8, height: 8, borderRadius: 4, backgroundColor: "rgba(255,255,255,0.22)" }} />

          <SafeAreaView edges={["top"]}>
            {/* Greeting + actions */}
            <View style={{ flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={{ color: "rgba(251,242,232,0.80)", fontSize: 11, fontWeight: "700", letterSpacing: 1.5 }}>WELCOME BACK</Text>
                <Text style={{ color: C.primaryFg, fontSize: 30, fontWeight: "700", lineHeight: 36, letterSpacing: -0.6, marginTop: 4 }}>Hi, {firstName}</Text>
                <Text style={{ color: "rgba(251,242,232,0.75)", fontSize: 13, marginTop: 4 }}>Protocol-001 · Dr. Sharma</Text>
              </View>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <Pressable testID="patient-bell" onPress={() => router.push("/(app)/notifications")} style={pst.iconBtn}>
                  <Bell size={20} color={C.primaryFg} />
                  <View style={{ position: "absolute", top: 6, right: 9, width: 8, height: 8, borderRadius: 4, backgroundColor: C.dawnFrom, borderWidth: 2, borderColor: C.primary }} />
                </Pressable>
                <Pressable testID="patient-avatar" onPress={() => router.push("/(app)/patient/profile")} style={[pst.iconBtn, { backgroundColor: "rgba(255,255,255,0.20)" }]}>
                  <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 13 }}>{initials}</Text>
                </Pressable>
              </View>
            </View>

            {/* Progress glass panel */}
            <View style={{ flexDirection: "row", alignItems: "center", gap: 16, marginTop: 20, padding: 16, borderRadius: 24, backgroundColor: "rgba(255,255,255,0.10)", borderWidth: 1, borderColor: "rgba(255,255,255,0.15)" }}>
              <Ring pct={pct} size={72} stroke={7} />
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={{ color: "rgba(251,242,232,0.75)", fontSize: 11, fontWeight: "700", letterSpacing: 1.5 }}>YOUR PROGRESS</Text>
                <Text style={{ color: C.primaryFg, fontSize: 17, fontWeight: "700", marginTop: 4 }}>Visit {completed} of {total} completed</Text>
                <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                  <View style={pst.chip}><Activity size={11} color={C.primaryFg} /><Text style={pst.chipText}>93% adherence</Text></View>
                  <View style={pst.chip}><Text style={pst.chipText}>Next in {daysToNext} days</Text></View>
                </View>
              </View>
            </View>
          </SafeAreaView>
        </View>

        {/* ── 01 · Next visit ── */}
        <View style={{ paddingHorizontal: 16, marginTop: 20 }}>
          <SectionHead index="01" label="NEXT VISIT" />
          <Pressable testID="next-visit-card" onPress={() => router.push("/(app)/patient/my-trial")}>
            <View style={pst.card}>
              <View style={{ flexDirection: "row", gap: 16 }}>
                <LinearGradient colors={DAWN as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={pst.dateBlock}>
                  <Text style={{ color: C.primaryFg, fontSize: 26, fontWeight: "700", lineHeight: 28 }}>{nextDate.getDate()}</Text>
                  <Text style={{ color: "rgba(251,242,232,0.85)", fontSize: 11, fontWeight: "700", letterSpacing: 1.4, marginTop: 4 }}>{nextDate.toLocaleString("en-US", { month: "short" }).toUpperCase()}</Text>
                </LinearGradient>
                <View style={{ flex: 1, minWidth: 0 }}>
                  <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                    <Text style={{ color: C.accent, fontSize: 11, fontWeight: "700", letterSpacing: 1.4 }}>IN {daysToNext} DAYS</Text>
                    <View style={{ paddingHorizontal: 10, paddingVertical: 2, borderRadius: 999, backgroundColor: "rgba(216,154,60,0.15)" }}>
                      <Text style={{ color: C.warning, fontSize: 11, fontWeight: "700" }}>Upcoming</Text>
                    </View>
                  </View>
                  <Text style={{ color: C.fg, fontSize: 17, fontWeight: "700", marginTop: 4 }}>Visit {next?.visit_number || 7} · {next?.name || "Follow-up Visit"}</Text>
                  <Text style={{ color: C.muted, fontSize: 12, marginTop: 4 }}>AIIMS Delhi · Dr. Sharma</Text>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 6 }}>
                    <Clock size={11} color={C.muted} />
                    <Text style={{ color: C.muted, fontSize: 12 }}>Window 20 – 26 {nextDate.toLocaleString("en-US", { month: "short" })}</Text>
                  </View>
                </View>
              </View>
              <View style={{ marginTop: 14, paddingTop: 12, borderTopWidth: 1, borderTopColor: C.border, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                <Text style={{ color: C.muted, fontSize: 12 }}>Protocol-001 · Phase II · Diabetes</Text>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                  <Text style={{ color: C.accent, fontSize: 14, fontWeight: "600" }}>View details</Text>
                  <ChevronRight size={16} color={C.accent} />
                </View>
              </View>
            </View>
          </Pressable>
        </View>

        {/* ── 02 · Calendar mini ── */}
        <View style={{ paddingHorizontal: 16, marginTop: 24 }}>
          <SectionHead index="02" label="CALENDAR" action={
            <Pressable testID="open-calendar" onPress={() => router.push("/(app)/patient/calendar")} style={{ flexDirection: "row", alignItems: "center" }}>
              <Text style={{ color: C.accent, fontSize: 14, fontWeight: "600" }}>Open calendar </Text>
              <ChevronRight size={16} color={C.accent} />
            </Pressable>
          } />
          <Pressable testID="cal-mini" onPress={() => router.push("/(app)/patient/calendar")}>
            <View style={pst.card}>
              <Text style={{ textAlign: "center", color: C.fg, fontSize: 16, fontWeight: "700", marginBottom: 12 }}>
                {calendarMonth.toLocaleString("en-US", { month: "long", year: "numeric" })}
              </Text>
              <View style={{ flexDirection: "row", marginBottom: 4 }}>
                {["S", "M", "T", "W", "T", "F", "S"].map((d, i) => (
                  <View key={i} style={{ flex: 1, alignItems: "center", paddingVertical: 4 }}>
                    <Text style={{ fontSize: 11, fontWeight: "700", color: "rgba(123,95,115,0.70)" }}>{d}</Text>
                  </View>
                ))}
              </View>
              {[0, 1, 2, 3, 4, 5].map(row => (
                <View key={row} style={{ flexDirection: "row", marginBottom: 4 }}>
                  {cells.slice(row * 7, row * 7 + 7).map((day, i) => {
                    const status = day ? monthVisits[day] : undefined;
                    const isToday = day === today && calendarMonth.getMonth() === new Date().getMonth() && calendarMonth.getFullYear() === new Date().getFullYear();
                    const bg = status === "completed" ? "rgba(230,155,92,0.12)" : status === "upcoming" ? "rgba(216,154,60,0.15)" : status === "scheduled" ? "rgba(123,107,184,0.10)" : "transparent";
                    const fg = status === "completed" ? C.accent : status === "upcoming" ? C.warning : status === "scheduled" ? C.info : isToday ? C.info : C.fg;
                    const dot = status === "completed" ? C.accent : status === "upcoming" ? C.warning : status === "scheduled" ? C.info : "transparent";
                    return (
                      <View key={i} style={{ flex: 1, aspectRatio: 1, alignItems: "center", justifyContent: "center", marginHorizontal: 2, borderRadius: 12, backgroundColor: bg, borderWidth: !status && isToday ? 1 : 0, borderColor: C.info }}>
                        {day && <Text style={{ fontSize: 12, fontWeight: "600", color: fg }}>{day}</Text>}
                        {status && <View style={{ position: "absolute", bottom: 4, width: 4, height: 4, borderRadius: 2, backgroundColor: dot }} />}
                      </View>
                    );
                  })}
                </View>
              ))}
              <View style={{ flexDirection: "row", justifyContent: "center", gap: 16, marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: C.border }}>
                <Legend color={C.accent} label="Completed" />
                <Legend color={C.warning} label="Upcoming" />
                <Legend color={C.info} label="Scheduled" />
              </View>
            </View>
          </Pressable>
        </View>

        {/* ── 03 · Notifications ── */}
        <View style={{ paddingHorizontal: 16, marginTop: 24 }}>
          <SectionHead index="03" label="NOTIFICATIONS" action={
            <Pressable testID="see-all-notifs" onPress={() => router.push("/(app)/notifications")}>
              <Text style={{ color: C.accent, fontSize: 14, fontWeight: "600" }}>See all</Text>
            </Pressable>
          } />
          <View style={{ gap: 12 }}>
            {(notifs.length > 0 ? notifs.slice(0, 3) : [
              { id: "x1", kind: "reminder", title: "Visit 7 Tomorrow", body: "Follow-Up Visit at AIIMS Delhi · 23 May 2025", read: false },
              { id: "x2", kind: "message", title: "Message from Dr. Sharma", body: "Please fast for 8 hours before your Visit 7 blood draw.", read: false },
            ]).map(n => {
              const Icon = n.kind === "message" ? MessageCircle : Bell;
              const tone = n.kind === "message" ? C.violet : C.accent;
              return (
                <Pressable key={n.id} testID={`notif-${n.id}`} onPress={() => router.push(n.kind === "message" ? "/(app)/chat" : "/(app)/notifications")}>
                  <View style={[pst.card, { flexDirection: "row", alignItems: "flex-start", gap: 12 }]}>
                    <View style={{ width: 44, height: 44, borderRadius: 16, backgroundColor: tone + "26", alignItems: "center", justifyContent: "center" }}>
                      <Icon size={20} color={tone} />
                    </View>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                        <Text style={{ color: C.fg, fontSize: 15, fontWeight: "600", flex: 1 }} numberOfLines={1}>{n.title}</Text>
                        {!n.read && <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: C.accent, marginLeft: 8 }} />}
                      </View>
                      <Text style={{ color: C.muted, fontSize: 14, marginTop: 2 }} numberOfLines={2}>{n.body}</Text>
                      <Text style={{ color: "rgba(123,95,115,0.70)", fontSize: 11, marginTop: 4 }}>2h ago</Text>
                    </View>
                    <ChevronRight size={16} color="rgba(123,95,115,0.40)" style={{ marginTop: 4 }} />
                  </View>
                </Pressable>
              );
            })}
          </View>
        </View>

        {/* ── 04 · Recent activity ── */}
        <View style={{ paddingHorizontal: 16, marginTop: 24 }}>
          <SectionHead index="04" label="RECENT ACTIVITY" />
          <View style={[pst.card, { padding: 0 }]}>
            {visits.filter(v => v.status === "completed").slice(-2).reverse().map((v, i) => (
              <View key={v.id} style={[{ padding: 16, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }, i > 0 && { borderTopWidth: 1, borderTopColor: C.border }]}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                  <View style={{ width: 36, height: 36, borderRadius: 18, backgroundColor: "rgba(92,154,110,0.15)", alignItems: "center", justifyContent: "center" }}>
                    <Check size={16} color={C.success} strokeWidth={2.5} />
                  </View>
                  <View>
                    <Text style={{ color: C.fg, fontSize: 14, fontWeight: "600" }}>Visit {v.visit_number}</Text>
                    <Text style={{ color: C.muted, fontSize: 13 }}>{new Date(v.scheduled_date).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}</Text>
                  </View>
                </View>
                <View style={{ paddingHorizontal: 12, paddingVertical: 4, borderRadius: 999, backgroundColor: "rgba(92,154,110,0.15)" }}>
                  <Text style={{ color: C.success, fontSize: 12, fontWeight: "600" }}>Done</Text>
                </View>
              </View>
            ))}
            {visits.filter(v => v.status === "completed").length === 0 && (
              <View style={{ padding: 16 }}><Text style={{ color: C.muted, fontSize: 13 }}>No completed visits yet</Text></View>
            )}
          </View>
        </View>
      </ScrollView>

      {/* Bottom nav */}
      <View style={pst.tabBar}>
        <Tab icon={Home} label="Dashboard" active />
        <Tab icon={FlaskConical} label="My Trial" onPress={() => router.push("/(app)/patient/my-trial")} testID="tab-my-trial" />
        <Tab icon={MessageCircle} label="Chat" onPress={() => router.push("/(app)/chat")} testID="tab-chat" />
        <Tab icon={CalIcon} label="Calendar" onPress={() => router.push("/(app)/patient/calendar")} testID="tab-calendar" />
        <Tab icon={User} label="Me" onPress={() => router.push("/(app)/patient/profile")} testID="tab-me" />
      </View>
    </View>
  );
}

function SectionHead({ index, label, action }: any) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 12 }}>
      <Text style={{ color: C.accent, fontSize: 14, fontWeight: "700", fontVariant: ["tabular-nums"] }}>{index}</Text>
      <Text style={{ color: C.primary, fontSize: 11, fontWeight: "700", letterSpacing: 1.5 }}>{label}</Text>
      <View style={{ flex: 1, height: 1, backgroundColor: C.border }} />
      {action}
    </View>
  );
}

function Ring({ pct, size, stroke }: any) {
  const r = (size - stroke) / 2;
  const cir = 2 * Math.PI * r;
  return (
    <View style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}>
      <Svg width={size} height={size} style={{ position: "absolute", transform: [{ rotate: "-90deg" }] }}>
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.22)" strokeWidth={stroke} />
        <Circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.95)" strokeWidth={stroke} strokeLinecap="round" strokeDasharray={`${cir} ${cir}`} strokeDashoffset={cir * (1 - pct / 100)} />
      </Svg>
      <Text style={{ color: C.primaryFg, fontSize: 20, fontWeight: "700", fontVariant: ["tabular-nums"] }}>{pct}%</Text>
    </View>
  );
}

function Legend({ color, label }: any) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
      <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: color }} />
      <Text style={{ fontSize: 11, color: C.muted }}>{label}</Text>
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

const pst = StyleSheet.create({
  iconBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: "rgba(255,255,255,0.15)", alignItems: "center", justifyContent: "center" },
  chip: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 999, backgroundColor: "rgba(255,255,255,0.20)" },
  chipText: { color: C.primaryFg, fontSize: 11, fontWeight: "700" },
  card: { backgroundColor: C.card, borderRadius: 22, borderWidth: 1, borderColor: C.border, padding: 16, shadowColor: "#2E1B33", shadowOpacity: 0.05, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 1 },
  dateBlock: { paddingHorizontal: 16, paddingVertical: 12, borderRadius: 16, alignItems: "center", justifyContent: "center" },
  tabBar: { position: "absolute", bottom: 0, left: 0, right: 0, flexDirection: "row", backgroundColor: C.card, borderTopWidth: 1, borderTopColor: C.border, paddingTop: 8, paddingBottom: 24, paddingHorizontal: 8 },
});
