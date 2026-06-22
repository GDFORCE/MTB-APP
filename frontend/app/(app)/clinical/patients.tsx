import React, { useEffect, useState } from "react";
import { View, ScrollView, TextInput, Pressable, StyleSheet } from "react-native";
import { useRouter } from "expo-router";
import { Search, Plus, ChevronRight, Filter } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, Body, Small, Card } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

export default function PatientList() {
  const router = useRouter();
  const [patients, setPatients] = useState<any[]>([]);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<"all" | "active" | "screening">("all");
  useEffect(() => { (async () => { const r = await api.get("/patients"); setPatients(r.data); })(); }, []);

  const filtered = patients.filter(p => (filter === "all" || p.status === filter) && (q === "" || p.full_name.toLowerCase().includes(q.toLowerCase()) || p.email.toLowerCase().includes(q.toLowerCase())));

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow={`${patients.length} enrolled`} title="Patients" right={
        <Pressable testID="add-patient-fab" onPress={() => router.push("/(app)/clinical/add-patient")} style={s.fab}><Plus size={18} color={colors.primaryFg} /></Pressable>
      } />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }}>
        <View style={s.searchBox}>
          <Search size={18} color={colors.mutedFg} />
          <TextInput testID="patient-search" placeholder="Search patients…" value={q} onChangeText={setQ} style={{ flex: 1, color: colors.foreground }} placeholderTextColor={colors.mutedFg} />
        </View>
        <View style={s.chips}>
          {(["all", "active", "screening"] as const).map(f => (
            <Pressable key={f} testID={`filter-${f}`} onPress={() => setFilter(f)} style={[s.chip, filter === f && { backgroundColor: colors.primary, borderColor: colors.primary }]}>
              <Small weight="700" color={filter === f ? colors.primaryFg : colors.mutedFg} style={{ textTransform: "capitalize" }}>{f}</Small>
            </Pressable>
          ))}
          <View style={{ flex: 1 }} />
          <Pressable style={s.chip}><Filter size={14} color={colors.mutedFg} /></Pressable>
        </View>

        {filtered.length === 0 ? <Card><Small>No patients found</Small></Card> : filtered.map(p => (
          <Pressable key={p.id} testID={`patient-${p.id}`} onPress={() => router.push({ pathname: "/(app)/clinical/visit-detail", params: { id: p.id } })}>
            <Card style={{ marginBottom: spacing.sm }}>
              <View style={{ flexDirection: "row", alignItems: "center" }}>
                <View style={s.avatar}><Body weight="700" color={colors.primary}>{p.avatar_initials}</Body></View>
                <View style={{ flex: 1, marginLeft: 12 }}>
                  <Body weight="700">{p.full_name}</Body>
                  <Small style={{ marginTop: 2 }}>{p.email}</Small>
                  <Small color={colors.mutedFg} style={{ marginTop: 2 }}>Enrolled {p.enrolled_date}</Small>
                </View>
                <View style={[s.statusPill, { backgroundColor: colors.success + "22" }]}><Small weight="700" color={colors.success}>Active</Small></View>
                <ChevronRight size={18} color={colors.mutedFg} style={{ marginLeft: 6 }} />
              </View>
            </Card>
          </Pressable>
        ))}
      </ScrollView>
    </ScreenContainer>
  );
}

const s = StyleSheet.create({
  fab: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.overlay20, alignItems: "center", justifyContent: "center" },
  searchBox: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 14, paddingVertical: 10, borderRadius: radii.md, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, marginBottom: spacing.md },
  chips: { flexDirection: "row", gap: 8, marginBottom: spacing.md, alignItems: "center" },
  chip: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  statusPill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 },
});
