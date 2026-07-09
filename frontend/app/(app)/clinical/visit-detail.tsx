import React, { useCallback, useEffect, useState } from "react";
import { View, ScrollView, StyleSheet, Pressable, Modal, TextInput, ActivityIndicator } from "react-native";
import { useRouter, useLocalSearchParams } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { Phone, MessageCircle, Calendar as CalIcon, Check, X, FileText } from "lucide-react-native";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { api } from "@/src/api/client";

type Instance = {
  id: string;
  name: string;
  seq: number;
  visit_number?: number;
  scheduled_date: string;
  status: string;
  note?: string;
};

// Statuses the PATCH /visit-instances/{id} endpoint accepts; surfaced as chips.
const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "scheduled", label: "Scheduled" },
  { value: "upcoming", label: "Upcoming" },
  { value: "completed", label: "Completed" },
  { value: "missed", label: "Missed" },
];

function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function pillFor(status: string): { label: string; bg: string; fg: string } {
  switch (status) {
    case "completed": return { label: "Done", bg: colors.success + "22", fg: colors.success };
    case "missed": return { label: "Missed", bg: colors.mutedFg + "22", fg: colors.mutedFg };
    case "overdue": return { label: "Overdue", bg: colors.destructive + "22", fg: colors.destructive };
    default: return { label: "Upcoming", bg: colors.warning + "22", fg: colors.warning };
  }
}

// Clinical (PI/CRC) view of a patient — demographics + a live visit timeline
// backed by per-patient visit instances, with working mutations.
export default function ClinicalVisitDetail() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [patient, setPatient] = useState<any | null>(null);
  const [trial, setTrial] = useState<any | null>(null);
  const [visits, setVisits] = useState<Instance[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Update-visit bottom sheet state.
  const [editing, setEditing] = useState<Instance | null>(null);
  const [form, setForm] = useState<{ dateISO: string; status: string; note: string }>({ dateISO: "", status: "", note: "" });
  const [saving, setSaving] = useState(false);
  // Error surfaced INSIDE the update sheet (the full-screen modal covers the
  // main-scroll error card, so a save failure must render within the sheet).
  const [sheetError, setSheetError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true); setError(null);
    try {
      const [detail, timeline] = await Promise.all([
        api.get(`/patients/${id}`),
        api.get(`/patients/${id}/visits`),
      ]);
      setPatient(detail.data);
      setTrial(detail.data?.trial || null);
      setVisits(timeline.data || []);
    } catch {
      setError("Couldn't load this patient. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  const applyLocal = (instId: string, patch: Partial<Instance>) =>
    setVisits(list => list.map(v => (v.id === instId ? { ...v, ...patch } : v)));

  // Optimistic PATCH: apply immediately, reconcile with server, revert on error.
  const markComplete = async (inst: Instance) => {
    const prev = visits;
    applyLocal(inst.id, { status: "completed" });
    try {
      const r = await api.patch(`/visit-instances/${inst.id}`, { status: "completed" });
      applyLocal(inst.id, r.data);
    } catch {
      setVisits(prev);
      setError("Couldn't mark the visit complete. Please try again.");
    }
  };

  const openSheet = (inst: Instance) => {
    setError(null);
    setSheetError(null);
    setForm({ dateISO: (inst.scheduled_date || "").slice(0, 10), status: inst.status, note: inst.note || "" });
    setEditing(inst);
  };

  const saveUpdate = async () => {
    if (!editing) return;
    const patch: Record<string, string> = {};
    if (form.status && form.status !== editing.status) patch.status = form.status;
    if (form.note !== (editing.note || "")) patch.note = form.note;
    if (form.dateISO && form.dateISO !== (editing.scheduled_date || "").slice(0, 10)) patch.scheduled_date = form.dateISO;
    if (Object.keys(patch).length === 0) { setEditing(null); return; }

    setSaving(true);
    setSheetError(null);
    const prev = visits;
    // Optimistic local apply (server returns canonical window/date on success).
    applyLocal(editing.id, {
      ...(patch.status ? { status: patch.status } : {}),
      ...(patch.note !== undefined ? { note: patch.note } : {}),
      ...(patch.scheduled_date ? { scheduled_date: `${patch.scheduled_date}T00:00:00Z` } : {}),
    });
    try {
      const r = await api.patch(`/visit-instances/${editing.id}`, patch);
      applyLocal(editing.id, r.data);
      setEditing(null);
    } catch {
      // Revert the optimistic change and surface the error inside the sheet so
      // the user can correct (e.g. a bad date) and retry without losing input.
      setVisits(prev);
      setSheetError("Couldn't save the visit update. Check the date (YYYY-MM-DD) and try again.");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Patient record" title="Loading…" />
        <View style={{ padding: spacing.xxl, alignItems: "center" }}><ActivityIndicator color={colors.primary} /></View>
      </ScreenContainer>
    );
  }
  if (!patient) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Patient record" title="Not found" />
        <View style={{ padding: spacing.md }}>
          <Small color={colors.destructive}>{error || "This patient could not be loaded."}</Small>
          <Button testID="retry" variant="secondary" style={{ marginTop: spacing.md }} onPress={load}><Small weight="700" color={colors.primary}>Retry</Small></Button>
        </View>
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      <ScreenHeader eyebrow="Patient record" title={patient.full_name} />
      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.md }}>
        <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.hero}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
            <View style={s.avatar}><Body weight="700" color={colors.primary} style={{ fontSize: 22 }}>{patient.avatar_initials || (patient.full_name || "?").slice(0, 2).toUpperCase()}</Body></View>
            <View style={{ flex: 1 }}>
              <H1 color={colors.primaryFg} style={{ fontSize: 18 }}>{patient.full_name}</H1>
              <Small color={colors.overlay25}>{trial?.protocol_id || "—"} · Enrolled {fmtDate(patient.enrolled_date)}</Small>
            </View>
          </View>
          <View style={{ flexDirection: "row", gap: 8, marginTop: spacing.md }}>
            <Pressable testID="contact-call" style={s.heroBtn} onPress={() => router.push("/(app)/chat")}><Phone size={14} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">Call</Small></Pressable>
            <Pressable testID="contact-chat" style={s.heroBtn} onPress={() => router.push("/(app)/chat")}><MessageCircle size={14} color={colors.primaryFg} /><Small color={colors.primaryFg} weight="700">Chat</Small></Pressable>
          </View>
        </LinearGradient>

        {error && (
          <Card style={{ borderColor: colors.destructive + "55" }}>
            <Small color={colors.destructive} weight="700">{error}</Small>
          </Card>
        )}

        <Card>
          <Eyebrow style={{ marginBottom: spacing.sm }}>Contact info</Eyebrow>
          <Row label="Email" value={patient.email || "—"} />
          <Row label="Phone" value={patient.phone || "—"} />
          <Row label="Enrolled" value={fmtDate(patient.enrolled_date)} last />
        </Card>

        <View>
          <Eyebrow style={{ marginBottom: spacing.sm }}>Visit timeline</Eyebrow>
          {visits.length === 0 && (
            <Card><Small>No visits scheduled for this patient yet.</Small></Card>
          )}
          {visits.map((v) => {
            const done = v.status === "completed";
            const pill = pillFor(v.status);
            const seq = v.seq ?? v.visit_number ?? 0;
            return (
              <Card key={v.id} style={{ marginBottom: spacing.sm }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                  <View style={[s.node, done && { backgroundColor: colors.accent, borderColor: colors.accent }]}>
                    {done ? <Check size={14} color={colors.primaryFg} /> : <Small weight="700">{seq}</Small>}
                  </View>
                  <View style={{ flex: 1 }}>
                    <Body weight="700">Visit {seq} · {v.name}</Body>
                    <View style={{ flexDirection: "row", gap: 4, alignItems: "center", marginTop: 2 }}>
                      <CalIcon size={11} color={colors.mutedFg} /><Small>{fmtDate(v.scheduled_date)}</Small>
                    </View>
                  </View>
                  <View style={[s.pill, { backgroundColor: pill.bg }]}>
                    <Small weight="700" color={pill.fg}>{pill.label}</Small>
                  </View>
                </View>
                <View style={{ flexDirection: "row", gap: 8, marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderColor: colors.border }}>
                  {!done && (
                    <Button testID={`mark-${v.id}-done`} variant="primary" style={{ flex: 1, paddingVertical: 10 }} onPress={() => markComplete(v)}><Small weight="700" color={colors.primaryFg}>Mark complete</Small></Button>
                  )}
                  <Button testID={`update-${v.id}`} variant="secondary" style={{ flex: 1, paddingVertical: 10 }} onPress={() => openSheet(v)}><Small weight="700" color={colors.primary}>Update visit</Small></Button>
                </View>
              </Card>
            );
          })}
        </View>

        <Button testID="view-records" variant="secondary"><View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><FileText size={14} color={colors.primary} /><Small weight="700" color={colors.primary}>View clinical records</Small></View></Button>
      </ScrollView>

      {/* ── Update Visit bottom sheet ─────────────────────────── */}
      <Modal visible={!!editing} transparent animationType="slide" onRequestClose={() => setEditing(null)}>
        <Pressable style={s.backdrop} onPress={() => (saving ? null : setEditing(null))} />
        <View style={s.sheet}>
          <View style={s.grabber} />
          <View style={{ flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", marginBottom: spacing.md }}>
            <View>
              <H1 style={{ fontSize: 18 }}>Update Visit</H1>
              <Small>{editing ? `Visit ${editing.seq ?? editing.visit_number ?? ""} · ${editing.name}` : ""}</Small>
            </View>
            <Pressable testID="sheet-close" onPress={() => setEditing(null)} hitSlop={10}><X size={20} color={colors.mutedFg} /></Pressable>
          </View>

          <ScrollView contentContainerStyle={{ gap: spacing.md, paddingBottom: spacing.md }} keyboardShouldPersistTaps="handled">
            {/* Inline save error — rendered inside the sheet so it stays visible
                above the modal while the user fixes their input and retries. */}
            {sheetError && (
              <View testID="sheet-error" style={s.sheetError}>
                <Small color={colors.destructive} weight="700">{sheetError}</Small>
              </View>
            )}

            {/* Trial context (read-only) */}
            <View style={s.context}>
              {[
                { label: "Protocol ID", val: trial?.protocol_id || "—" },
                { label: "Phase", val: trial?.phase || "—" },
                { label: "Indication", val: trial?.condition || "—" },
              ].map((f) => (
                <View key={f.label} style={{ flex: 1 }}>
                  <Eyebrow style={{ fontSize: 9 }}>{f.label}</Eyebrow>
                  <Small weight="700" color={colors.foreground} style={{ marginTop: 2 }}>{f.val}</Small>
                </View>
              ))}
            </View>

            {/* Visit date */}
            <View>
              <Small weight="700" style={{ marginBottom: 6 }}>Visit Date</Small>
              <TextInput
                testID="sheet-date"
                value={form.dateISO}
                onChangeText={(t) => setForm(f => ({ ...f, dateISO: t }))}
                placeholder="YYYY-MM-DD"
                placeholderTextColor={colors.mutedFg}
                style={s.input}
              />
            </View>

            {/* Status */}
            <View>
              <Small weight="700" style={{ marginBottom: 6 }}>Status</Small>
              <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                {STATUS_OPTIONS.map((o) => {
                  const on = form.status === o.value;
                  return (
                    <Pressable key={o.value} testID={`status-${o.value}`} onPress={() => setForm(f => ({ ...f, status: o.value }))} style={[s.statusChip, on ? { backgroundColor: colors.primary, borderColor: colors.primary } : { borderColor: colors.border }]}>
                      <Small weight="700" color={on ? colors.primaryFg : colors.mutedFg}>{o.label}</Small>
                    </Pressable>
                  );
                })}
              </View>
            </View>

            {/* Remarks */}
            <View>
              <Small weight="700" style={{ marginBottom: 6 }}>Remarks</Small>
              <TextInput
                testID="sheet-note"
                value={form.note}
                onChangeText={(t) => setForm(f => ({ ...f, note: t }))}
                placeholder="Add any notes about this visit…"
                placeholderTextColor={colors.mutedFg}
                multiline
                style={[s.input, { height: 84, textAlignVertical: "top" }]}
              />
            </View>

            <View style={{ flexDirection: "row", gap: 8 }}>
              <Button testID="sheet-cancel" variant="secondary" style={{ flex: 1 }} onPress={() => setEditing(null)}><Small weight="700" color={colors.primary}>Cancel</Small></Button>
              <Button testID="sheet-save" variant="dawn" style={{ flex: 1 }} loading={saving} onPress={saveUpdate}><Small weight="700" color={colors.primaryFg}>Save Update</Small></Button>
            </View>
          </ScrollView>
        </View>
      </Modal>
    </ScreenContainer>
  );
}

function Row({ label, value, last }: { label: string; value: string; last?: boolean }) {
  return (
    <View style={[{ flexDirection: "row", justifyContent: "space-between", paddingVertical: 8 }, !last && { borderBottomWidth: 1, borderBottomColor: colors.border }]}>
      <Small>{label}</Small>
      <Small weight="700" color={colors.foreground}>{value}</Small>
    </View>
  );
}

const s = StyleSheet.create({
  hero: { borderRadius: radii.xl, padding: spacing.md },
  avatar: { width: 56, height: 56, borderRadius: 28, backgroundColor: colors.card, alignItems: "center", justifyContent: "center" },
  heroBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 10, borderRadius: 999, backgroundColor: colors.overlay20 },
  node: { width: 32, height: 32, borderRadius: 16, borderWidth: 2, borderColor: colors.border, backgroundColor: colors.card, alignItems: "center", justifyContent: "center" },
  pill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 },
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.4)" },
  sheet: { backgroundColor: colors.card, borderTopLeftRadius: radii.xl, borderTopRightRadius: radii.xl, padding: spacing.md, maxHeight: "88%" },
  grabber: { alignSelf: "center", width: 40, height: 4, borderRadius: 999, backgroundColor: colors.border, marginBottom: spacing.md },
  context: { flexDirection: "row", gap: spacing.sm, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, padding: spacing.sm },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, backgroundColor: colors.background, paddingHorizontal: 12, paddingVertical: 10, color: colors.foreground, fontSize: 15 },
  statusChip: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999, borderWidth: 1, backgroundColor: colors.card },
  sheetError: { backgroundColor: colors.destructive + "14", borderWidth: 1, borderColor: colors.destructive + "55", borderRadius: radii.md, padding: spacing.sm },
});
