import React, { useEffect, useRef, useState } from "react";
import { View, ScrollView, Pressable, StyleSheet, ActivityIndicator, Animated } from "react-native";
import { useRouter } from "expo-router";
import { Calendar as CalIcon, Check, AlertCircle } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

// CRC/PI daily schedule review — today's expected visits, each approvable /
// flaggable against the trial's schedule-review endpoints (Task 1.2).
type Row = {
  id: string;
  full_name: string;
  avatar_initials?: string;
  trial_id?: string;
  visit: string;
  time: string;
  reviewState?: "approved" | "flagged";
};

function fmtTime(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

export default function ScheduleReview() {
  const router = useRouter();
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [toast, setToast] = useState<{ msg: string; kind: "ok" | "err" } | null>(null);
  const toastY = useRef(new Animated.Value(60)).current;
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await api.get("/patients");
        if (!alive) return;
        const mapped: Row[] = (r.data as any[])
          .filter(p => p.next_visit)   // real next visit from the backend
          .map(p => ({
            id: p.next_visit.id || p.id,
            full_name: p.full_name,
            avatar_initials: p.avatar_initials,
            trial_id: p.trial_id,
            visit: p.next_visit.name || "Visit",
            time: fmtTime(p.next_visit.scheduled_date),
          }));
        setRows(mapped);
      } catch {
        if (alive) setError("Couldn't load today's schedule. Please try again.");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  const showToast = (msg: string, kind: "ok" | "err") => {
    setToast({ msg, kind });
    Animated.spring(toastY, { toValue: 0, useNativeDriver: true }).start();
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => {
      Animated.timing(toastY, { toValue: 60, duration: 200, useNativeDriver: true }).start(() => setToast(null));
    }, 2600);
  };

  const review = async (row: Row, action: "approve" | "flag") => {
    if (!row.trial_id || busy[row.id]) return;
    const prev = row.reviewState;
    const optimistic = action === "approve" ? "approved" : "flagged";
    setRows(rs => rs.map(r => (r.id === row.id ? { ...r, reviewState: optimistic } : r)));
    setBusy(b => ({ ...b, [row.id]: true }));
    try {
      if (action === "approve") {
        await api.post(`/schedules/${row.trial_id}/approve`);
        showToast(`Approved · ${row.full_name}`, "ok");
      } else {
        await api.post(`/schedules/${row.trial_id}/flag`, { reason: `Flagged during schedule review — ${row.visit}` });
        showToast(`Flagged · ${row.full_name}`, "ok");
      }
    } catch (e: any) {
      // revert optimistic state
      setRows(rs => rs.map(r => (r.id === row.id ? { ...r, reviewState: prev } : r)));
      const msg = e?.response?.status === 403
        ? "You don't have permission to review schedules."
        : "Couldn't save your review. Please try again.";
      showToast(msg, "err");
    } finally {
      setBusy(b => ({ ...b, [row.id]: false }));
    }
  };

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Coordinator queue" title="Schedule Review" />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }}>
        <Card>
          <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
            <View>
              <Eyebrow color={colors.accent}>Today</Eyebrow>
              <Body weight="700" style={{ marginTop: 2, fontSize: 17 }}>{new Date().toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" })}</Body>
              <Small style={{ marginTop: 2 }}>{rows.length} visits scheduled</Small>
            </View>
            <View style={s.bigIcon}><CalIcon size={26} color={colors.primary} /></View>
          </View>
        </Card>

        <Eyebrow style={{ marginTop: spacing.lg, marginBottom: spacing.sm }}>Awaiting review</Eyebrow>

        {loading && (
          <View style={{ paddingVertical: spacing.lg, alignItems: "center" }}><ActivityIndicator color={colors.primary} /></View>
        )}
        {!loading && error && (
          <Card><Small color={colors.destructive}>{error}</Small></Card>
        )}
        {!loading && !error && rows.length === 0 && (
          <Card><Small>No visits awaiting review today.</Small></Card>
        )}

        {rows.map(v => (
          <Card key={v.id} style={{ marginBottom: spacing.sm }}>
            <View style={{ flexDirection: "row", alignItems: "center" }}>
              <View style={s.avatar}><Body weight="700" color={colors.primary}>{v.avatar_initials}</Body></View>
              <View style={{ flex: 1, marginLeft: 12 }}>
                <Body weight="700">{v.full_name}</Body>
                <Small style={{ marginTop: 2 }}>{v.visit}{v.time ? ` · ${v.time}` : ""}</Small>
              </View>
              {v.reviewState && (
                <View style={[s.badge, { backgroundColor: v.reviewState === "approved" ? colors.success + "1A" : colors.warning + "1A" }]}>
                  <Small weight="700" color={v.reviewState === "approved" ? colors.success : colors.warning} style={{ textTransform: "capitalize" as any }}>{v.reviewState}</Small>
                </View>
              )}
            </View>
            <View style={{ flexDirection: "row", gap: 8, marginTop: spacing.sm, paddingTop: spacing.sm, borderTopWidth: 1, borderColor: colors.border }}>
              <Pressable testID={`approve-${v.id}`} disabled={busy[v.id]} onPress={() => review(v, "approve")} style={[s.action, { backgroundColor: colors.success, opacity: busy[v.id] ? 0.6 : 1 }]}>
                <Check size={14} color={colors.successFg} /><Small color={colors.successFg} weight="700">Approve</Small>
              </Pressable>
              <Pressable testID={`flag-${v.id}`} disabled={busy[v.id]} onPress={() => review(v, "flag")} style={[s.action, { backgroundColor: colors.warning + "1A", borderWidth: 1, borderColor: colors.warning + "55", opacity: busy[v.id] ? 0.6 : 1 }]}>
                <AlertCircle size={14} color={colors.warning} /><Small color={colors.warning} weight="700">Flag</Small>
              </Pressable>
              <Pressable testID={`open-${v.id}`} onPress={() => router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: v.id } })} style={[s.action, { backgroundColor: colors.surface }]}>
                <Small color={colors.primary} weight="700">Open</Small>
              </Pressable>
            </View>
          </Card>
        ))}
      </ScrollView>

      {toast && (
        <Animated.View testID="schedule-toast" style={[s.toast, { transform: [{ translateY: toastY }], backgroundColor: toast.kind === "ok" ? colors.foreground : colors.destructive }]}>
          <Small color={colors.white} weight="700">{toast.msg}</Small>
        </Animated.View>
      )}
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  bigIcon: { width: 56, height: 56, borderRadius: 28, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  action: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 10, borderRadius: 999 },
  badge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 },
  toast: { position: "absolute", left: spacing.md, right: spacing.md, bottom: spacing.lg, paddingVertical: 12, paddingHorizontal: 16, borderRadius: radii.md, alignItems: "center" },
});
