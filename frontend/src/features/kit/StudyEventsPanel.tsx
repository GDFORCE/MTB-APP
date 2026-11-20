import React, { useCallback, useEffect, useState } from "react";
import { Text, TextInput, View, StyleSheet } from "react-native";
import { isIsoDate, type KitModel } from "./index";
import {
  confirmStudyEvent, correctStudyEvent, listStudyEvents, retryStudyEvent,
  type KitCorrectionPreview, type KitStudyEventView,
} from "./api";
import { FlowButton, flowStyles as f } from "./FlowScreen";
import { FOCUS as G } from "./focus";

const STATE_TEXT: Record<string, string> = {
  applied: "Applied", excluded: "Not affected", unresolved: "Data to complete", to_review: "Listed for review",
  review: "Booking to review", unmatched: "Version has no action", already_completed: "Already completed", ended: "Ended before processing",
  withdrawn: "Withdrawn by correction",
};

const newId = () => `se-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

/**
 * Study-wide events of an approved workflow: the study team confirms one (with the date it actually
 * happened); the server applies it to every participant it concerns, on their own workflow version.
 * Site staff see the participants they manage and book from each patient's calendar.
 */
export function StudyEventsPanel({ trialId, role }: { trialId: string; role: string }) {
  const [data, setData] = useState<{ definitions: NonNullable<KitModel["study_events"]>; events: KitStudyEventView[] } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<Record<string, { id: string; date: string; fields: Record<string, string> }>>({});
  const [corrections, setCorrections] = useState<Record<string, { date: string; preview?: KitCorrectionPreview }>>({});

  const load = useCallback(() => {
    listStudyEvents(trialId).then(setData).catch((e: any) => setError(message(e, "Couldn't load the study events.")));
  }, [trialId]);
  useEffect(load, [load]);

  const act = async (work: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await work(); load(); } catch (e: any) { setError(message(e, "Couldn't save.")); } finally { setBusy(false); }
  };

  if (!data || (!data.definitions.length && !data.events.length)) return error ? <Text style={f.error}>{error}</Text> : null;
  return <View style={s.panel}>
    <Text style={f.sectionTitle}>Study-wide events</Text>
    {!!error && <Text style={f.error}>{error}</Text>}
    {data.definitions.filter((d) => d.confirm_roles.includes(role) && !data.events.some((e) => e.type === d.id)).map((d) => {
      const entry = form[d.id] || { id: newId(), date: "", fields: {} };
      const set = (next: Partial<typeof entry>) => setForm((prior) => ({ ...prior, [d.id]: { ...entry, ...next } }));
      const ready = isIsoDate(entry.date) && d.fields.every((x) => (entry.fields[x.id] || "").trim());
      return <View key={d.id} style={s.card}>
        <Text style={f.label}>{d.label}</Text>
        {!!d.blocked && <Text style={f.error}>Cannot be applied yet: {d.blocked}</Text>}
        <Text style={f.hint}>When did it actually happen?</Text>
        <TextInput accessibilityLabel="Effective date" style={f.input} placeholder="YYYY-MM-DD" value={entry.date}
          onChangeText={(date) => set({ date })} />
        {d.fields.map((x) => <View key={x.id}>
          <Text style={f.hint}>{x.label}</Text>
          <TextInput accessibilityLabel={x.label} style={f.input} value={entry.fields[x.id] || ""}
            onChangeText={(value) => set({ fields: { ...entry.fields, [x.id]: value } })} />
        </View>)}
        <FlowButton label="Confirm for the whole study" disabled={busy || !ready || !!d.blocked}
          onPress={() => act(() => confirmStudyEvent(trialId, { id: entry.id, type: d.id, effective_date: entry.date, fields: entry.fields }))} />
      </View>;
    })}
    {data.events.map((e) => {
      const canConfirm = data.definitions.find((d) => d.id === e.type)?.confirm_roles.includes(role);
      const fix = corrections[e.id];
      return <View key={e.id} style={s.card}>
        <Text style={f.label}>{e.label}</Text>
        <Text style={f.hint}>Happened {e.effective_date} · confirmed {String(e.confirmed_at).slice(0, 10)} · revision {e.revision}
          {e.status !== "applied" ? ` · ${e.status === "partial" ? "partly processed" : "processing"}` : ""}</Text>
        {!!e.failed?.length && <Text style={f.error}>Not processed yet: {e.failed.map((x) => x.subject_id).join(", ")}</Text>}
        {canConfirm && e.status !== "applied" && <FlowButton label="Retry processing" variant="secondary" disabled={busy}
          onPress={() => act(() => retryStudyEvent(trialId, e.id))} />}
        {e.effects.map((x) => <Text key={x.id} style={[f.hint, { color: x.state === "applied" ? G.ink : x.state === "unresolved" || x.state === "review" ? G.redInk : G.muted }]}>
          {x.subject_id}{x.site ? ` · ${x.site}` : ""} · {STATE_TEXT[x.state] || x.state}{x.booked ? ` · booked ${x.booked}` : x.task_key ? " · to book" : ""}
          {x.state !== "applied" && x.reason ? ` · ${x.reason}` : ""}
        </Text>)}
        {canConfirm && <>
          <TextInput accessibilityLabel="Corrected effective date" style={f.input} placeholder="Corrected date (YYYY-MM-DD)"
            value={fix?.date || ""} onChangeText={(date) => setCorrections((prior) => ({ ...prior, [e.id]: { date } }))} />
          <FlowButton label="Preview correction" variant="secondary" disabled={busy || !isIsoDate(fix?.date || "")}
            onPress={() => act(async () => {
              const out = await correctStudyEvent(trialId, e.id, { effective_date: fix!.date, fields: e.fields, revision: e.revision, preview: true });
              setCorrections((prior) => ({ ...prior, [e.id]: { date: fix!.date, preview: out.preview } }));
            })} />
          {fix?.preview && <View style={{ gap: 2 }}>
            {(["added", "removed", "kept", "completed_kept"] as const).map((k) => <Text key={k} style={f.hint}>
              {{ added: "Newly affected", removed: "No longer affected", kept: "Still affected", completed_kept: "Completed, kept" }[k]}: {fix.preview![k].map((x) => x.subject_id).join(", ") || "none"}
            </Text>)}
            {!!fix.preview.booked_affected.length && <Text style={f.error}>
              Booked visits that would no longer apply: {fix.preview.booked_affected.map((x) => `${x.subject_id} (${x.booked})`).join(", ")}. They stay visible to their site for review.
            </Text>}
            <FlowButton label={fix.preview.booked_affected.length ? "Acknowledge the booked visits and apply" : "Apply correction"} disabled={busy}
              onPress={() => act(() => correctStudyEvent(trialId, e.id, { effective_date: fix.date, fields: e.fields, revision: e.revision,
                acknowledge_booked: fix.preview!.booked_affected.map((x) => x.effect_id) }))} />
          </View>}
        </>}
      </View>;
    })}
  </View>;
}

function message(e: any, fallback: string) {
  const detail = e?.response?.data?.detail;
  return typeof detail === "string" ? detail : detail?.message || fallback;
}

const s = StyleSheet.create({
  panel: { marginTop: 20, gap: 10 },
  card: { padding: 12, gap: 6, borderWidth: 1, borderColor: G.line, borderRadius: 10 },
});
