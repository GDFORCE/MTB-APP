import React, { useEffect, useState } from "react";
import { View, ScrollView, TextInput, Pressable, StyleSheet, StatusBar, Text, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Search, ChevronLeft } from "lucide-react-native";
import { api } from "@/src/api/client";

const C = {
  bg: "#F4E5D3", surface: "#F4E5D3", card: "#FEFAF1", fg: "#2E1B33", muted: "#7B5F73", border: "#E6D6C5",
  primary: "#A6213F", primaryDeep: "#6B1437", primaryFg: "#FFFFFF",
  accent: "#E69B5C", info: "#7B6BB8", success: "#5C9A6E", destructive: "#C0392B",
};

// Derived per-patient status — computed by the backend from visit instances.
type DerivedStatus = "active" | "overdue" | "completed" | "no_visits";

type NextVisit = { id: string; name: string; seq: number; scheduled_date: string; status: string };
type PatientRow = {
  id: string;
  full_name: string;
  avatar_initials?: string;
  status: DerivedStatus;
  next_visit: NextVisit | null;
};

const STATUS_META: Record<DerivedStatus, { label: string; bg: string; fg: string }> = {
  active: { label: "Active", bg: "rgba(230,155,92,0.12)", fg: C.accent },
  overdue: { label: "⚠ Overdue", bg: "rgba(192,57,43,0.12)", fg: C.destructive },
  completed: { label: "Completed", bg: "rgba(92,154,110,0.14)", fg: C.success },
  no_visits: { label: "No visits", bg: "rgba(123,95,115,0.10)", fg: C.muted },
};

const FILTERS: { id: string; label: string }[] = [
  { id: "all", label: "All" },
  { id: "active", label: "Active" },
  { id: "overdue", label: "Overdue" },
  { id: "completed", label: "Completed" },
];

function fmtDate(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function nextVisitLabel(nv: NextVisit | null): string {
  if (!nv) return "No upcoming visits";
  const date = fmtDate(nv.scheduled_date);
  return date ? `${nv.name} · ${date}` : nv.name;
}

export default function PatientList() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [active, setActive] = useState<string>("all");
  const [rows, setRows] = useState<PatientRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { (async () => {
    setLoading(true); setError(null);
    try {
      const r = await api.get("/patients");
      const mapped: PatientRow[] = (r.data || []).map((p: any) => ({
        id: p.id,
        full_name: p.full_name || "Unknown",
        avatar_initials: p.avatar_initials,
        status: (p.status as DerivedStatus) || "no_visits",
        next_visit: p.next_visit || null,
      }));
      setRows(mapped);
    } catch {
      setError("Couldn't load patients. Pull to retry.");
      setRows([]);
    } finally {
      setLoading(false);
    }
  })(); }, []);

  const counts = {
    all: rows.length,
    active: rows.filter(r => r.status === "active").length,
    overdue: rows.filter(r => r.status === "overdue").length,
    completed: rows.filter(r => r.status === "completed").length,
  };

  const filtered = rows
    .filter(r => active === "all" || r.status === active)
    .filter(r => q === "" || r.full_name.toLowerCase().includes(q.toLowerCase()));

  return (
    <View style={{ flex: 1, backgroundColor: C.surface }}>
      <StatusBar barStyle="dark-content" backgroundColor={C.surface} />
      <SafeAreaView edges={["top"]} style={{ backgroundColor: C.surface }}>
        <View style={s.appBar}>
          <Pressable testID="back" onPress={() => router.back()} hitSlop={10} style={s.backBtn}>
            <ChevronLeft size={24} color={C.fg} />
          </Pressable>
          <Text style={s.appBarTitle}>Patients</Text>
          <View style={{ width: 40 }} />
        </View>
      </SafeAreaView>

      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: 110 }} showsVerticalScrollIndicator={false}>
        {/* Search */}
        <View style={{ paddingHorizontal: 16, paddingTop: 12 }}>
          <View style={s.search}>
            <Search size={20} color={C.muted} />
            <TextInput
              testID="patient-search"
              value={q}
              onChangeText={setQ}
              placeholder="Search by name..."
              placeholderTextColor={C.muted}
              style={{ flex: 1, color: C.fg, fontSize: 15 }}
            />
          </View>
        </View>

        {/* Filter chips */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 12, paddingBottom: 12, gap: 8 }}>
          {FILTERS.map(f => {
            const on = active === f.id;
            const count = (counts as any)[f.id];
            return (
              <Pressable key={f.id} testID={`filter-${f.id}`} onPress={() => setActive(f.id)} style={[s.chip, on ? s.chipActive : s.chipIdle, { flexShrink: 0 }]}>
                <Text style={[s.chipText, { color: on ? C.primaryFg : C.muted }]}>{f.label} {count}</Text>
              </Pressable>
            );
          })}
        </ScrollView>

        {/* Patient list */}
        <View style={{ paddingHorizontal: 16 }}>
          {loading ? (
            <View style={{ padding: 40, alignItems: "center" }}>
              <ActivityIndicator color={C.primary} />
            </View>
          ) : error ? (
            <View style={{ padding: 24, alignItems: "center" }}>
              <Text style={{ color: C.destructive, textAlign: "center" }}>{error}</Text>
            </View>
          ) : (
            <View style={s.listCard}>
              {filtered.map((p, i) => {
                const meta = STATUS_META[p.status];
                return (
                  <Pressable
                    key={p.id}
                    testID={`patient-${p.id}`}
                    onPress={() => router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: p.id } })}
                    style={[s.row, i > 0 && { borderTopWidth: 1, borderTopColor: C.border }]}
                  >
                    <View style={s.avatar}>
                      <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 13 }}>{p.avatar_initials || p.full_name.slice(0, 2).toUpperCase()}</Text>
                    </View>
                    <View style={{ flex: 1, minWidth: 0 }}>
                      <Text style={{ color: C.fg, fontSize: 15, fontWeight: "700" }} numberOfLines={1}>{p.full_name}</Text>
                      <Text style={{ color: C.muted, fontSize: 13, marginTop: 2 }} numberOfLines={1}>{nextVisitLabel(p.next_visit)}</Text>
                    </View>
                    <View style={[s.statusPill, { backgroundColor: meta.bg }]}>
                      <Text style={{ color: meta.fg, fontSize: 11, fontWeight: "600" }}>{meta.label}</Text>
                    </View>
                  </Pressable>
                );
              })}
              {filtered.length === 0 && (
                <View style={{ padding: 24, alignItems: "center" }}>
                  <Text style={{ color: C.muted }}>
                    {rows.length === 0 ? "No patients enrolled yet" : "No patients match your filters"}
                  </Text>
                </View>
              )}
            </View>
          )}
        </View>
      </ScrollView>

      {/* FAB */}
      <Pressable testID="add-patient-fab" onPress={() => router.push("/(app)/clinical/add-patient")} style={s.fab}>
        <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 14 }}>Add Patient</Text>
      </Pressable>
    </View>
  );
}

const s = StyleSheet.create({
  appBar: { flexDirection: "row", alignItems: "center", paddingHorizontal: 8, paddingVertical: 8 },
  backBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  appBarTitle: { flex: 1, fontSize: 18, fontWeight: "700", color: C.fg, textAlign: "center" },
  search: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingVertical: 12, borderRadius: 999, backgroundColor: C.border },
  chip: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999 },
  chipIdle: { backgroundColor: C.card, borderWidth: 1, borderColor: C.border },
  chipActive: { backgroundColor: C.primary },
  chipText: { fontSize: 13, fontWeight: "600" },
  listCard: { backgroundColor: C.card, borderRadius: 18, overflow: "hidden", borderWidth: 1, borderColor: C.border },
  row: { padding: 14, flexDirection: "row", alignItems: "center", gap: 12 },
  avatar: { width: 40, height: 40, borderRadius: 20, backgroundColor: C.primary, alignItems: "center", justifyContent: "center" },
  statusPill: { paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999 },
  fab: { position: "absolute", bottom: 28, right: 16, height: 48, paddingHorizontal: 20, borderRadius: 24, backgroundColor: C.primary, alignItems: "center", justifyContent: "center", shadowColor: "#2E1B33", shadowOpacity: 0.25, shadowRadius: 12, shadowOffset: { width: 0, height: 4 }, elevation: 8 },
});
