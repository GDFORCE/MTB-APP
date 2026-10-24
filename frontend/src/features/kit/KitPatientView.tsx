import React, { useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { Trash2 } from "lucide-react-native";
import { fonts } from "@/src/theme/tokens";
import type { KitPatientRecord } from "./api";
import { isoParts, todayIso, type KitDecisionRow, type KitParticipant, type KitVisitRow } from "./index";
import {
  answer, dmyToIso, formatDmyInput, isoToDmy, logEvent, recordVisit, removeEvent, setAnchorDate,
} from "./participant";
import { FOCUS as G } from "./focus";
import { FlowButton, FlowScreen, FlowSheet, flowStyles as f } from "./FlowScreen";
import { PatientTimeline, usePatientTimeline } from "./PatientTimeline";

type Sheet =
  | { kind: "visit"; row: KitVisitRow }
  | { kind: "decision"; row: KitDecisionRow }
  | { kind: "anchor"; id: string }
  | { kind: "events" };

const short = (iso: string) => { const p = isoParts(iso); return `${+p.day} ${p.month}`; };

function DateField({ value, onChange, testID }: { value: string; onChange: (value: string) => void; testID?: string }) {
  return <TextInput testID={testID} value={value} onChangeText={(v) => onChange(formatDmyInput(v))} placeholder="DD/MM/YYYY"
    placeholderTextColor={G.muted} keyboardType="number-pad" maxLength={10} style={f.input} />;
}

function Chips({ options, value, onChange }: { options: { value: string; label: string }[]; value?: string | null; onChange: (v: string) => void }) {
  return <View style={f.chips}>
    {options.map((o) => {
      const on = value === o.value;
      return <Pressable key={o.value} accessibilityRole="radio" accessibilityState={{ selected: on }} onPress={() => onChange(o.value)} style={[f.chip, on && f.chipOn]}>
        <Text style={[f.chipText, on && f.chipTextOn]}>{o.label}</Text>
      </Pressable>;
    })}
  </View>;
}

function Banner({ tone, title, text, action, onAction }: {
  tone: "red" | "amber" | "violet" | "green"; title: string; text?: string; action?: string; onAction?: () => void;
}) {
  const c = { red: [G.redBg, "#F0C3B7", "#6E2414"], amber: ["#FDF7EA", "#E9CF97", "#5A3C00"], violet: [G.violetBg, "#CFC6EE", "#3B2F70"], green: [G.tag, "#C7D9BC", "#2A4721"] }[tone];
  return <View accessibilityRole="summary" style={[s.banner, { backgroundColor: c[0], borderColor: c[1] }]}>
    <Text style={[s.bannerText, { color: c[2] }]}><Text style={{ fontFamily: fonts.semibold }}>{title}</Text>{text ? ` ${text}` : ""}</Text>
    {!!action && <Pressable accessibilityRole="button" onPress={onAction} hitSlop={8}><Text style={[s.bannerAction, { color: c[2] }]}>{action}</Text></Pressable>}
  </View>;
}

/**
 * One patient's schedule on the trial's reviewed protocol model: dates are computed from the saved
 * record, and every change (visit done, decision, date, event) goes back through `persist` as a new
 * record. `record` null with no `loadError` shows a spinner.
 */
export function KitPatientView({ record, loadError, canEdit, onBack, persist }: {
  record: KitPatientRecord | null; loadError?: string; canEdit: boolean; onBack: () => void;
  persist: (participant: KitParticipant) => Promise<void>;
}) {
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [saving, setSaving] = useState(false);
  const [sheetError, setSheetError] = useState("");
  const [saveError, setSaveError] = useState("");
  const error = loadError || saveError;
  const data = usePatientTimeline(record?.model, record?.site_answers, record?.participant);

  const open = (next: Sheet) => { setSheetError(""); setSheet(next); };
  const save = async (participant: KitParticipant) => {
    setSaving(true);
    setSheetError("");
    setSaveError("");
    try {
      await persist(participant);
      setSheet(null);
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      const message = typeof detail === "string" ? detail : "Couldn't save. Try again.";
      if (sheet) setSheetError(message); else setSaveError(message);
    } finally {
      setSaving(false);
    }
  };

  const title = record?.patient.subject_id || record?.patient.full_name || "Patient";
  const p = record?.participant;
  const endingEvent = p?.events?.find((e) => record?.model.events.find((d) => d.id === e.type)?.ends_schedule);
  const heading = !data ? undefined
    : data.ended ? `Schedule ended ${short(data.ended.date || todayIso())}`
    : data.next ? `Next: ${data.next.code} on ${short(data.next.date!)}`
    : data.overdue ? `${data.overdue} overdue visit${data.overdue === 1 ? "" : "s"}`
    : data.done === data.total ? "All visits done"
    : data.pendingAnchors.length ? `Waiting for the ${data.pendingAnchors[0].label.toLowerCase()} date` : "No visit dated yet";

  return <FlowScreen title={title} onBack={onBack}
    right={canEdit && record ? <Pressable accessibilityRole="button" onPress={() => open({ kind: "events" })} style={s.eventButton}>
      <Text style={s.eventButtonText}>+ Event</Text>
    </Pressable> : undefined}
    eyebrow={record?.trial.protocol_id || record?.trial.title || undefined} heading={heading}
    sub={data ? `${data.done} of ${data.total} visits done` : undefined}>
    {!record && !error && <ActivityIndicator color={G.accent} style={{ marginTop: 32 }} />}
    {!!error && <Text style={[f.error, { marginTop: 16 }]}>{error}</Text>}
    {record && !data && <Text style={[f.error, { marginTop: 16 }]}>This schedule could not be calculated.</Text>}
    {record && data && p && <>
      <View style={s.banners}>
        {data.ended && <Banner tone="red" title={`${data.ended.label} logged ${short(data.ended.date || todayIso())}.`}
          text={data.cancelled ? `${data.cancelled} planned visit${data.cancelled === 1 ? " was" : "s were"} cancelled.` : "Later visits were cancelled."}
          action={canEdit && endingEvent ? "Undo" : undefined} onAction={() => endingEvent && save(removeEvent(p, endingEvent.key))} />}
        {!data.ended && data.cancelled > 0 && <Banner tone="red" title={`${data.cancelled} visit${data.cancelled === 1 ? "" : "s"} cancelled`} text="by a logged event." />}
        {data.overdue > 0 && <Banner tone="red" title={`${data.overdue} visit${data.overdue === 1 ? " is" : "s are"} overdue.`} text="Their window has closed." />}
        {data.moved > 0 && <Banner tone="amber" title={`${data.moved} visit${data.moved === 1 ? "" : "s"} moved`}
          text={data.late ? `after ${data.late === 1 ? "a visit was" : "visits were"} done off the planned day.` : "from the plan."} />}
        {data.added > 0 && !data.ended && <Banner tone="green" title={`${data.added} visit${data.added === 1 ? "" : "s"} added`} text="by a logged event." />}
        {data.pendingDecisions > 0 && <Banner tone="violet" title={`${data.pendingDecisions} decision${data.pendingDecisions === 1 ? "" : "s"} to answer.`}
          text="Some visits depend on it." />}
      </View>
      {!!sheetError && !sheet && <Text style={f.error}>{sheetError}</Text>}
      <PatientTimeline data={data}
        onPressVisit={canEdit ? (row) => open({ kind: "visit", row }) : undefined}
        onAnswer={canEdit ? (row, value) => save(answer(p, row.questionId, value)) : undefined}
        onPressDecision={canEdit ? (row) => open({ kind: "decision", row }) : undefined}
        onSetAnchor={canEdit ? (anchorId) => open({ kind: "anchor", id: anchorId }) : undefined}
        onPressEvent={canEdit ? () => open({ kind: "events" }) : undefined} />
      {canEdit && <SetDates record={record} onSet={(anchorId) => open({ kind: "anchor", id: anchorId })} />}
    </>}

    {sheet?.kind === "visit" && p && <VisitSheet row={sheet.row} saving={saving} error={sheetError} onClose={() => setSheet(null)}
      onSave={(date) => save(recordVisit(p, sheet.row.key, date))} />}
    {sheet?.kind === "decision" && p && <FlowSheet visible title={sheet.row.text} onClose={() => setSheet(null)}>
      {!!sheet.row.help && <Text style={f.hint}>{sheet.row.help}</Text>}
      <Chips options={sheet.row.options} value={sheet.row.answer} onChange={(value) => save(answer(p, sheet.row.questionId, value))} />
      <FlowButton label="Clear answer" variant="secondary" disabled={saving} onPress={() => save(answer(p, sheet.row.questionId, null))} />
      {!!sheetError && <Text style={f.error}>{sheetError}</Text>}
    </FlowSheet>}
    {sheet?.kind === "anchor" && p && record && <AnchorSheet label={record.model.anchors.find((a) => a.id === sheet.id)?.label || sheet.id}
      date={p.answers["@" + sheet.id] || null} saving={saving} error={sheetError} onClose={() => setSheet(null)}
      onSave={(date) => save(setAnchorDate(p, sheet.id, date))} />}
    {sheet?.kind === "events" && p && record && <EventSheet record={record} saving={saving} error={sheetError} onClose={() => setSheet(null)}
      onRemove={(key) => save(removeEvent(p, key))} onLog={(event) => save(logEvent(p, event))} />}
  </FlowScreen>;
}

/** Dates already entered for "ask" anchors, so they can be corrected. Pending ones show on the timeline. */
function SetDates({ record, onSet }: { record: KitPatientRecord; onSet: (anchorId: string) => void }) {
  const set = record.model.anchors.filter((a) => a.kind === "ask" && record.participant.answers["@" + a.id]);
  if (!set.length) return null;
  return <View style={s.setDates}>
    <Text style={f.sectionTitle}>Dates entered</Text>
    {set.map((a) => <Pressable key={a.id} accessibilityRole="button" onPress={() => onSet(a.id)} style={s.setDate}>
      <Text style={s.setDateLabel}>{a.label}</Text>
      <Text style={s.setDateValue}>{short(record.participant.answers["@" + a.id])}</Text>
      <Text style={s.link}>Change</Text>
    </Pressable>)}
  </View>;
}

function VisitSheet({ row, saving, error, onClose, onSave }: {
  row: KitVisitRow; saving: boolean; error: string; onClose: () => void; onSave: (date: string | null) => void;
}) {
  const [date, setDate] = useState(isoToDmy(row.actual || todayIso()));
  const [showProcs, setShowProcs] = useState(false);
  const iso = dmyToIso(date);
  const plan = row.planned ? `Planned ${short(row.planned)}` : row.relative || "";
  const window = row.windowStart && row.windowEnd && row.windowStart !== row.windowEnd ? ` · window ${short(row.windowStart)} – ${short(row.windowEnd)}` : "";
  return <FlowSheet visible title={`${row.code} · ${row.name}`} onClose={onClose}>
    {!!(plan + window) && <Text style={f.hint}>{plan + window}</Text>}
    <View style={{ gap: 6 }}>
      <Text style={f.label}>Date of visit</Text>
      <DateField value={date} onChange={setDate} testID="kit-visit-date" />
      {row.movesOthers && <Text style={f.hint}>Later visits move with this date.</Text>}
    </View>
    {!!error && <Text style={f.error}>{error}</Text>}
    <FlowButton label={row.actual ? "Update date" : "Mark done"} loading={saving} disabled={!iso} onPress={() => onSave(iso)} />
    {!!row.actual && <FlowButton label="Undo" variant="secondary" disabled={saving} onPress={() => onSave(null)} />}
    {!!row.procedures.length && <Pressable accessibilityRole="button" onPress={() => setShowProcs((v) => !v)}>
      <Text style={s.link}>{showProcs ? "Hide" : "Show"} assessments ({row.procedures.length})</Text>
    </Pressable>}
    {showProcs && row.procedures.map((proc, i) => <Text key={`${proc.name}-${i}`} style={[f.hint, { color: G.ink }]}>
      • {proc.name}{proc.conditional ? " (if applicable)" : ""}
    </Text>)}
  </FlowSheet>;
}

function AnchorSheet({ label, date, saving, error, onClose, onSave }: {
  label: string; date: string | null; saving: boolean; error: string; onClose: () => void; onSave: (date: string | null) => void;
}) {
  const [value, setValue] = useState(date ? isoToDmy(date) : "");
  const iso = dmyToIso(value);
  return <FlowSheet visible title={`${label} date`} onClose={onClose}>
    <Text style={f.hint}>Visits that count from it get their dates from this.</Text>
    <DateField value={value} onChange={setValue} />
    {!!error && <Text style={f.error}>{error}</Text>}
    <FlowButton label="Save date" loading={saving} disabled={!iso} onPress={() => onSave(iso)} />
    {!!date && <FlowButton label="Clear" variant="secondary" disabled={saving} onPress={() => onSave(null)} />}
  </FlowSheet>;
}

function EventSheet({ record, saving, error, onClose, onLog, onRemove }: {
  record: KitPatientRecord; saving: boolean; error: string; onClose: () => void;
  onLog: (event: { type: string; date: string; answers: Record<string, string> }) => void; onRemove: (key: string) => void;
}) {
  const logged = record.participant.events || [];
  const available = record.model.events.filter((e) => e.repeatable || !logged.some((l) => l.type === e.id));
  const [type, setType] = useState<string | null>(null);
  const [date, setDate] = useState(isoToDmy(todayIso()));
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const definition = record.model.events.find((e) => e.id === type);
  const iso = dmyToIso(date);
  const complete = !!definition && !!iso && definition.questions.every((q) => answers[q.id]);
  const labelOf = (t: string) => record.model.events.find((e) => e.id === t)?.label || t;
  return <FlowSheet visible title="Events" onClose={onClose}>
    <Text style={f.hint}>An event can add visits or end the schedule.</Text>
    {logged.map((e) => <View key={e.key} style={s.logged}>
      <Text style={s.loggedText}>{labelOf(e.type)} · {short(e.date)}</Text>
      <Pressable disabled={saving} onPress={() => onRemove(e.key)} hitSlop={10} accessibilityRole="button" accessibilityLabel="Remove event">
        <Trash2 size={18} color={G.redInk} />
      </Pressable>
    </View>)}
    {available.length ? <>
      <Text style={f.label}>Log an event</Text>
      <Chips options={available.map((e) => ({ value: e.id, label: e.label }))} value={type} onChange={(value) => { setType(value); setAnswers({}); }} />
      {definition && <>
        {definition.ends_schedule && <Text style={[f.hint, { color: G.redInk }]}>This ends the schedule: later visits are cancelled.</Text>}
        <View style={{ gap: 6 }}><Text style={f.label}>Date</Text><DateField value={date} onChange={setDate} /></View>
        {definition.questions.map((q) => <View key={q.id} style={{ gap: 8 }}>
          <Text style={f.label}>{q.text || q.label}</Text>
          <Chips options={q.kind === "boolean" ? [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }] : q.options}
            value={answers[q.id]} onChange={(v) => setAnswers((a) => ({ ...a, [q.id]: v }))} />
        </View>)}
        <FlowButton label="Log event" loading={saving} disabled={!complete} onPress={() => iso && onLog({ type: definition.id, date: iso, answers })} />
      </>}
    </> : <Text style={f.hint}>No other events can be logged.</Text>}
    {!!error && <Text style={f.error}>{error}</Text>}
  </FlowSheet>;
}

const s = StyleSheet.create({
  eventButton: { minHeight: 36, paddingHorizontal: 12, borderRadius: 999, borderWidth: 1, borderColor: G.line, justifyContent: "center" },
  eventButtonText: { color: G.accent, fontFamily: fonts.semibold, fontSize: 13 },
  banners: { gap: 8, marginTop: 16 },
  banner: { flexDirection: "row", alignItems: "center", gap: 10, borderWidth: 1, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12 },
  bannerText: { flex: 1, fontFamily: fonts.regular, fontSize: 13, lineHeight: 18 },
  bannerAction: { fontFamily: fonts.semibold, fontSize: 13 },
  link: { color: G.accent, fontFamily: fonts.semibold, fontSize: 14 },
  setDates: { marginTop: 24, gap: 4 },
  setDate: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: G.softLine },
  setDateLabel: { flex: 1, color: G.ink, fontFamily: fonts.medium, fontSize: 14 },
  setDateValue: { color: G.ink, fontFamily: fonts.semibold, fontSize: 14 },
  logged: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: 12, paddingHorizontal: 12, borderRadius: 10, backgroundColor: G.selected },
  loggedText: { color: G.ink, fontFamily: fonts.medium, fontSize: 14 },
});
