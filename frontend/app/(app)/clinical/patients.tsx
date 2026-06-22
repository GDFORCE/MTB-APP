import React, { useEffect, useState } from "react";
import { View, ScrollView, TextInput, Pressable, StyleSheet, StatusBar, Text } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Search, ChevronLeft } from "lucide-react-native";
import { api } from "@/src/api/client";

const C = {
  bg: "#F4E5D3", surface: "#F4E5D3", card: "#FEFAF1", fg: "#2E1B33", muted: "#7B5F73", border: "#E6D6C5",
  primary: "#A6213F", primaryDeep: "#6B1437", primaryFg: "#FFFFFF",
  accent: "#E69B5C", info: "#7B6BB8", destructive: "#C0392B",
};

type Status = "Scheduled" | "Overdue" | "Active" | "Screen Fail" | "Withdrawn";

export default function PatientList() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [active, setActive] = useState<string>("all");
  const [rows, setRows] = useState<any[]>([]);

  useEffect(() => { (async () => {
    try {
      const r = await api.get("/patients");
      // Map backend → display, supplementing demo subjects when needed.
      const baseDemo = [
        { id: "SUBJ-001", initials: "PK", name: "Priya K.", visit: "Visit 3 · 23 May", status: "Scheduled" as Status },
        { id: "SUBJ-002", initials: "RS", name: "Rahul S.", visit: "Visit 1 · Today", status: "Overdue" as Status },
        { id: "SUBJ-003", initials: "AM", name: "Anjali M.", visit: "Visit 5 · 2 Jun", status: "Active" as Status },
        { id: "SUBJ-004", initials: "VG", name: "Vikram G.", visit: "—", status: "Screen Fail" as Status },
        { id: "SUBJ-005", initials: "NK", name: "Neha K.", visit: "—", status: "Withdrawn" as Status },
      ];
      const fromApi = r.data.slice(0, 7).map((p: any, i: number) => ({
        id: `SUBJ-${String(i + 1).padStart(3, "0")}`,
        initials: p.avatar_initials, name: p.full_name, visit: "Visit 2 · 5 Jun", status: "Active" as Status,
        _backendId: p.id,
      }));
      setRows([...baseDemo.slice(0, 2), ...fromApi, ...baseDemo.slice(3)]);
    } catch { setRows([]); }
  })(); }, []);

  const counts = {
    all: rows.length,
    active: rows.filter(r => r.status === "Active" || r.status === "Scheduled").length,
    "screen-fail": rows.filter(r => r.status === "Screen Fail").length,
    withdrawn: rows.filter(r => r.status === "Withdrawn").length,
  };
  const filters = [
    { id: "all", label: "All" },
    { id: "active", label: "Active" },
    { id: "screen-fail", label: "Screen Fail" },
    { id: "withdrawn", label: "Withdrawn" },
  ];
  const filtered = rows.filter(r => {
    if (active === "all") return true;
    if (active === "active") return r.status === "Active" || r.status === "Scheduled";
    if (active === "screen-fail") return r.status === "Screen Fail";
    if (active === "withdrawn") return r.status === "Withdrawn";
    return true;
  }).filter(r => q === "" || r.id.toLowerCase().includes(q.toLowerCase()) || r.name.toLowerCase().includes(q.toLowerCase()));

  const statusStyle = (st: Status) => {
    switch (st) {
      case "Scheduled": return { bg: "rgba(123,107,184,0.10)", fg: C.primary };
      case "Overdue": return { bg: "rgba(192,57,43,0.10)", fg: C.destructive };
      case "Active": return { bg: "rgba(230,155,92,0.10)", fg: C.accent };
      case "Screen Fail": return { bg: "rgba(192,57,43,0.10)", fg: C.destructive };
      case "Withdrawn": return { bg: "rgba(123,95,115,0.10)", fg: C.muted };
    }
  };

  return (
    <View style={{ flex: 1, backgroundColor: C.surface }}>
      <StatusBar barStyle="dark-content" backgroundColor={C.surface} />
      <SafeAreaView edges={["top"]} style={{ backgroundColor: C.surface }}>
        {/* AppBar — light surface, plum back, title centered-ish */}
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
              placeholder="Search by Subject ID..."
              placeholderTextColor={C.muted}
              style={{ flex: 1, color: C.fg, fontSize: 15 }}
            />
          </View>
        </View>

        {/* Filter chips */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 12, paddingBottom: 12, gap: 8 }}>
          {filters.map(f => {
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
          <View style={s.listCard}>
            {filtered.map((p, i) => {
              const st = statusStyle(p.status);
              return (
                <Pressable
                  key={p.id}
                  testID={`patient-${p.id}`}
                  onPress={() => router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: p._backendId || p.id } })}
                  style={[s.row, i > 0 && { borderTopWidth: 1, borderTopColor: C.border }]}
                >
                  <View style={s.avatar}>
                    <Text style={{ color: C.primaryFg, fontWeight: "700", fontSize: 13 }}>{p.initials}</Text>
                  </View>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Text style={{ color: C.fg, fontSize: 15, fontWeight: "700" }}>{p.id}</Text>
                    <Text style={{ color: C.muted, fontSize: 13, marginTop: 2 }} numberOfLines={1}>{p.name} • {p.visit}</Text>
                  </View>
                  <View style={[s.statusPill, { backgroundColor: st.bg }]}>
                    <Text style={{ color: st.fg, fontSize: 11, fontWeight: "600" }}>
                      {p.status === "Overdue" ? "⚠ Overdue" : p.status}
                    </Text>
                  </View>
                </Pressable>
              );
            })}
            {filtered.length === 0 && (
              <View style={{ padding: 24, alignItems: "center" }}>
                <Text style={{ color: C.muted }}>No patients match your filters</Text>
              </View>
            )}
          </View>
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
