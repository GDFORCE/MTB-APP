import React, { useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { Trash2 } from "lucide-react-native";
import { fonts } from "@/src/theme/tokens";
import type { KitPatientRecord } from "./api";
import {
  computeKitSchedule, isoParts, ruleIfText, todayIso, type KitDecisionRow, type KitMeasure, type KitParticipant, type KitSchedule, type KitVisitRow,
} from "./index";
import {
  anchorDateProblems, answer, dmyToIso, enterableAnchors, formatDmyInput, formatTimeInput, isoToDmy, logEvent, parseMeasured, recordCheck, recordVisit,
  removeCheck, removeEvent, setAnchorDate, setBaseline, setDoseLevel, setVisitTime, validTime,
} from "./participant";
import { FOCUS as G } from "./focus";
import { FlowButton, FlowScreen, FlowSheet, flowStyles as f } from "./FlowScreen";
import { PatientTimeline, usePatientTimeline } from "./PatientTimeline";

type Sheet =
  | { kind: "visit"; row: KitVisitRow }
  | { kind: "decision"; row: KitDecisionRow }
  | { kind: "anchor"; id: string }
  | { kind: "baseline"; id: string }
  | { kind: "events" }
  | { kind: "confirm"; participant: KitParticipant; issues: string[] };

type Issue = KitSchedule["issues"][number];
const issueKey = (i: Issue) => `${i.rule}|${i.kind}|${i.text}`;

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
export function KitPatientView({ record, loadError, canEdit, onBack, persist, moveToLatest }: {
  record: KitPatientRecord | null; loadError?: string; canEdit: boolean; onBack: () => void;
  persist: (participant: KitParticipant) => Promise<void>;
  /** Put the patient on the trial's newer reviewed model (offered when `record.latest` is set). */
  moveToLatest?: () => Promise<void>;
}) {
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [saving, setSaving] = useState(false);
  const [sheetError, setSheetError] = useState("");
  const [saveError, setSaveError] = useState("");
  const error = loadError || saveError;
  const data = usePatientTimeline(record?.model, record?.site_answers, record?.participant);

  const open = (next: Sheet) => { setSheetError(""); setSheet(next); };
  const scheduleOf = (participant: KitParticipant) =>
    record ? computeKitSchedule(record.model, record.site_answers || {}, participant) : null;
  // A change that gives a rule an impossible date is refused; one outside what the protocol allows
  // (a deviation that really happened) is saved only after the user confirms it.
  const save = async (participant: KitParticipant, confirmed = false) => {
    if (!confirmed && record) {
      let fresh: Issue[] = [];
      try {
        const before = new Set((scheduleOf(record.participant)?.issues || []).map(issueKey));
        fresh = (scheduleOf(participant)?.issues || []).filter((i) => !before.has(issueKey(i)) && i.kind !== "info");
      } catch { /* the schedule itself reports a calculation failure */ }
      const impossible = fresh.find((i) => i.kind === "order");
      if (impossible) { setSheetError(impossible.text.replace(", so it was not applied. Check the date.", ". Check the date.")); return; }
      if (fresh.length) { setSheetError(""); setSheet({ kind: "confirm", participant, issues: fresh.map((i) => i.text) }); return; }
    }
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
        {canEdit && record.latest && <LatestModelBanner record={record} saving={saving}
          onMove={moveToLatest ? async () => {
            setSaving(true); setSaveError("");
            try { await moveToLatest(); } catch (e: any) {
              const detail = e?.response?.data?.detail;
              setSaveError(typeof detail === "string" ? detail : "Couldn't move this patient. Try again.");
            } finally { setSaving(false); }
          } : undefined} />}
        {data.ended && <Banner tone="red" title={`${data.ended.label} logged ${short(data.ended.date || todayIso())}.`}
          text={data.cancelled ? `${data.cancelled} planned visit${data.cancelled === 1 ? " was" : "s were"} cancelled.` : "Later visits were cancelled."}
          action={canEdit && endingEvent ? "Undo" : undefined} onAction={() => endingEvent && save(removeEvent(p, endingEvent.key))} />}
        {data.warnings.map((w) => <Banner key={w} tone="amber" title="Check:" text={w} />)}
        {data.stopped.map((g) => <Banner key={g.label} tone="red" title={`${g.count} visit${g.count === 1 ? "" : "s"} stopped`}
          text={`by ${g.label}${g.date ? ` (${short(g.date)})` : ""}.`} />)}
        {(() => {
          const byEvent = data.cancelled - data.stopped.reduce((n, g) => n + g.count, 0);
          return !data.ended && byEvent > 0 ? <Banner tone="red" title={`${byEvent} visit${byEvent === 1 ? "" : "s"} cancelled`} text="by a logged event." /> : null;
        })()}
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
      {canEdit && <SetDates record={record} schedule={scheduleOf(p)} onSet={(anchorId) => open({ kind: "anchor", id: anchorId })} />}
      {canEdit && <Baselines record={record} onSet={(measureId) => open({ kind: "baseline", id: measureId })} />}
    </>}

    {sheet?.kind === "visit" && p && <VisitSheet row={sheet.row} saving={saving} error={sheetError} onClose={() => setSheet(null)}
      onSave={(date) => save(recordVisit(p, sheet.row.key, date))}
      onCheck={(date, value, values) => save(recordCheck(p, sheet.row.key, date, value, values))}
      onRemoveCheck={(index) => save(removeCheck(p, sheet.row.key, index))}
      onSetDose={(doseId, level) => save(setDoseLevel(p, sheet.row.key, doseId, level))}
      onTime={(time) => save(setVisitTime(p, sheet.row.key, time))} />}
    {sheet?.kind === "baseline" && p && record && <BaselineSheet measure={record.model.measures?.find((m) => m.id === sheet.id)}
      value={p.baselines?.[sheet.id] ?? null} saving={saving} error={sheetError} onClose={() => setSheet(null)}
      onSave={(value) => save(setBaseline(p, sheet.id, value))} />}
    {sheet?.kind === "decision" && p && <FlowSheet visible title={sheet.row.text} onClose={() => setSheet(null)}>
      {!!sheet.row.help && <Text style={f.hint}>{sheet.row.help}</Text>}
      {sheet.row.kind === "number"
        ? <NumberAnswer value={sheet.row.answer} saving={saving} onSave={(value) => save(answer(p, sheet.row.questionId, value))} />
        : <Chips options={sheet.row.options} value={sheet.row.answer} onChange={(value) => save(answer(p, sheet.row.questionId, value))} />}
      <FlowButton label="Clear answer" variant="secondary" disabled={saving} onPress={() => save(answer(p, sheet.row.questionId, null))} />
      {!!sheetError && <Text style={f.error}>{sheetError}</Text>}
    </FlowSheet>}
    {sheet?.kind === "confirm" && <FlowSheet visible title="Outside the protocol" onClose={() => setSheet(null)}>
      {sheet.issues.map((text) => <Text key={text} style={[f.hint, { color: G.redInk }]}>{text}</Text>)}
      <Text style={f.hint}>Save it only if this is what actually happened; it will be shown as a deviation.</Text>
      {!!sheetError && <Text style={f.error}>{sheetError}</Text>}
      <FlowButton label="Save anyway" loading={saving} onPress={() => save(sheet.participant, true)} />
      <FlowButton label="Go back" variant="secondary" disabled={saving} onPress={() => setSheet(null)} />
    </FlowSheet>}
    {sheet?.kind === "anchor" && p && record && <AnchorSheet label={record.model.anchors.find((a) => a.id === sheet.id)?.label || sheet.id}
      effects={(record.model.rules || []).filter((r) => "anchor" in r.when && r.when.anchor === sheet.id && r.effect).map((r) => r.effect)}
      window={windowText(scheduleOf(p)?.ruleWindows.find((w) => w.anchor === sheet.id))}
      date={p.answers["@" + sheet.id] || null} saving={saving} error={sheetError} onClose={() => setSheet(null)}
      onSave={(date) => {
        const next = setAnchorDate(p, sheet.id, date);
        const problem = anchorDateProblems(record.model, p.startAnchor, p.startDate, next.answers)[sheet.id];
        if (problem) setSheetError(problem); else save(next);
      }} />}
    {sheet?.kind === "events" && p && record && <EventSheet record={record} saving={saving} error={sheetError} onClose={() => setSheet(null)}
      onRemove={(key) => save(removeEvent(p, key))} onLog={(event) => save(logEvent(p, event))} />}
  </FlowScreen>;
}

/**
 * The protocol dates a site enters (randomisation / Day 1, surgery, ...), added here once they
 * happen. Entered ones can be corrected; ones counted from another date show that date and can be
 * replaced by the real one. Missing ones also show on the timeline where visits wait for them.
 */
function SetDates({ record, schedule, onSet }: { record: KitPatientRecord; schedule: KitSchedule | null; onSet: (anchorId: string) => void }) {
  const answers = record.participant.answers;
  const list = enterableAnchors(record.model);
  if (!list.length) return null;
  return <View style={s.setDates}>
    <Text style={f.sectionTitle}>Protocol dates</Text>
    {list.map((a) => {
      const entered = answers["@" + a.id];
      const counted = schedule?.anchors.find((x) => x.id === a.id)?.date || null;
      return <Pressable key={a.id} accessibilityRole="button" onPress={() => onSet(a.id)} style={s.setDate}>
        <Text style={s.setDateLabel}>{a.label}</Text>
        <Text style={[s.setDateValue, !entered && { color: G.amberText }]}>
          {entered ? short(entered) : counted ? `${short(counted)} (counted)` : "Not set"}
        </Text>
        <Text style={s.link}>{entered ? "Change" : "Add date"}</Text>
      </Pressable>;
    })}
  </View>;
}

/** "ANC 0.8 · Creatinine 1.2" for the values entered with a check answer. */
function valuesText(check: NonNullable<KitVisitRow["check"]>, values?: Record<string, number>) {
  return Object.entries(values || {}).map(([id, v]) => {
    const m = check.measures.find((x) => x.id === id);
    return m?.kind === "grade" ? `${m.label} grade ${v}` : `${m?.label || id} ${v}`;
  }).join(" · ");
}

function answerText(check: NonNullable<KitVisitRow["check"]>, a: NonNullable<KitVisitRow["check"]>["answers"][number]) {
  const values = valuesText(check, a.values);
  // With a dosing table, the row that decided it; otherwise Met / Not met.
  const result = a.outcomeText ? a.outcomeText.split(" → ").pop()!.replace(/^./, (c) => c.toUpperCase())
    : a.outcome === "skip" ? "Skip (not made up)" : a.outcome === "reduce" ? "Reduced dose this time"
    : a.result === "skip" ? "Skip (not made up)"
    : a.result === "yes" ? (a.by === "values" ? "Met" : "Yes")
    : a.result === "no" ? (a.failed.length ? `Not met (${a.failed.join(", ")})` : check.until ? "Not met yet" : "No") : "Can't tell yet";
  return [short(a.date), values, result].filter(Boolean).join(" · ");
}

/** A decision answered with a number ("Eosinophil count, × ULN"). */
function NumberAnswer({ value, saving, onSave }: { value: string | null; saving: boolean; onSave: (value: string) => void }) {
  const [text, setText] = useState(value ?? "");
  const parsed = parseMeasured(text, "value");
  return <View style={{ gap: 8 }}>
    <TextInput value={text} onChangeText={setText} keyboardType="decimal-pad" placeholder="Value" placeholderTextColor={G.muted}
      style={f.input} testID="kit-decision-value" />
    {!!text && parsed == null && <Text style={f.error}>Enter a number.</Text>}
    <FlowButton label="Save answer" loading={saving} disabled={parsed == null || String(parsed) === value} onPress={() => parsed != null && onSave(String(parsed))} />
  </View>;
}

/** Measures whose limits compare with the patient's baseline ("creatinine ≤ 1.5 × baseline"). */
function baselineMeasures(model: KitPatientRecord["model"]) {
  const used = new Set<string>();
  const walk = (l: any) => { if (Array.isArray(l?.any)) l.any.forEach(walk); else if (l?.baseline_factor != null) used.add(l.measure); };
  for (const v of model.visits) {
    for (const l of v.check?.limits || []) walk(l);
    for (const c of v.check?.dose_changes || []) for (const l of c.if) walk(l);
  }
  return (model.measures || []).filter((m) => used.has(m.id));
}

/** The baseline values the protocol's limits compare with, entered once per patient. */
function Baselines({ record, onSet }: { record: KitPatientRecord; onSet: (measureId: string) => void }) {
  const list = baselineMeasures(record.model);
  if (!list.length) return null;
  return <View style={s.setDates}>
    <Text style={f.sectionTitle}>Baseline values</Text>
    {list.map((m) => {
      const value = record.participant.baselines?.[m.id];
      return <Pressable key={m.id} accessibilityRole="button" onPress={() => onSet(m.id)} style={s.setDate}>
        <Text style={s.setDateLabel}>{m.label}{m.unit ? ` (${m.unit})` : ""}</Text>
        <Text style={[s.setDateValue, value == null && { color: G.amberText }]}>{value == null ? "Not set" : String(value)}</Text>
        <Text style={s.link}>{value == null ? "Add" : "Change"}</Text>
      </Pressable>;
    })}
  </View>;
}

function BaselineSheet({ measure, value, saving, error, onClose, onSave }: {
  measure: KitMeasure | undefined; value: number | null; saving: boolean; error: string; onClose: () => void;
  onSave: (value: number | null) => void;
}) {
  const [text, setText] = useState(value == null ? "" : String(value));
  if (!measure) return null;
  const parsed = parseMeasured(text, measure.kind);
  return <FlowSheet visible title={`Baseline ${measure.label}`} onClose={onClose}>
    {!!measure.baseline && <Text style={f.hint}>The protocol's baseline: {measure.baseline}.</Text>}
    <TextInput value={text} onChangeText={setText} keyboardType="decimal-pad" placeholder={measure.unit || "Value"}
      placeholderTextColor={G.muted} style={f.input} testID="kit-baseline-value" />
    {!!text && parsed == null && <Text style={f.error}>Enter a number{measure.kind === "grade" ? " from 0 to 5" : ""}.</Text>}
    {!!error && <Text style={f.error}>{error}</Text>}
    <FlowButton label="Save" loading={saving} disabled={parsed == null} onPress={() => onSave(parsed)} />
    {value != null && <FlowButton label="Clear" variant="secondary" disabled={saving} onPress={() => onSave(null)} />}
  </FlowSheet>;
}

function VisitSheet({ row, saving, error, onClose, onSave, onCheck, onRemoveCheck, onSetDose, onTime }: {
  row: KitVisitRow; saving: boolean; error: string; onClose: () => void; onSave: (date: string | null) => void;
  onCheck: (date: string, value: "yes" | "no" | "skip" | "reduce" | null, values?: Record<string, number>) => void; onRemoveCheck: (index: number) => void;
  onSetDose: (doseId: string, level: string | null) => void; onTime: (time: string | null) => void;
}) {
  const [date, setDate] = useState(isoToDmy(row.actual || todayIso()));
  const [time, setTime] = useState(row.time || "");
  const timeOk = validTime(time);
  const [checkDate, setCheckDate] = useState(isoToDmy(todayIso()));
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [showProcs, setShowProcs] = useState(false);
  const iso = dmyToIso(date);
  const checkIso = dmyToIso(checkDate);
  const check = row.check;
  // Asked until it is answered Yes (or skipped); a "No" holds the visit and the question comes back.
  // A repeat-until check is asked at each occurrence until it is met.
  const canAnswer = !!check && row.status !== "done" && (check.state === "ask" || check.state === "held" || check.state === "again");
  const canSkip = !!check?.actions.some((a) => a.do === "skip");
  const reduceRow = check?.actions.find((a) => a.do === "reduce");
  const measured = check?.measures.length ? check.measures : null;
  const values: Record<string, number> = {};
  let badValue = false;
  for (const m of measured || []) {
    if (!typed[m.id]) continue;
    const n = parseMeasured(typed[m.id], m.kind);
    if (n == null) badValue = true; else values[m.id] = n;
  }
  const hasValues = Object.keys(values).length > 0;
  // Values are enough when the limits can be worked out; otherwise (no limits, or a baseline still
  // missing) staff also answer Yes or No.
  const byHand = !measured || (!check?.limits.length && !check?.actions.length) || check.needsBaseline.length > 0;
  const plan = row.planned ? `Planned ${short(row.planned)}` : row.relative || "";
  const window = row.windowStart && row.windowEnd && row.windowStart !== row.windowEnd ? ` · window ${short(row.windowStart)} – ${short(row.windowEnd)}` : "";
  return <FlowSheet visible title={`${row.code} · ${row.name}`} onClose={onClose}>
    {!!(plan + window) && <Text style={f.hint}>{plan + window}</Text>}
    {row.notes.map((note) => <Text key={note} style={[f.hint, { color: G.amberText }]}>{note}</Text>)}
    {check && <View style={s.check}>
      <Text style={f.label}>Before this visit: {check.text}</Text>
      {!!check.limits.length && <Text style={f.hint}>{check.until ? "Comes back until" : "Goes ahead if"} {check.limits.join(" and ")}.</Text>}
      {!!check.actions.length && <View style={{ gap: 2 }}>
        <Text style={f.hint}>What happens, from the values entered:</Text>
        {check.actions.map((a) => <Text key={a.text} style={[f.hint, { color: G.ink }]}>• {a.text}</Text>)}
      </View>}
      {!!check.help && <Text style={f.hint}>{check.help}</Text>}
      {check.answers.map((a, i) => <View key={`${a.date}-${i}`} style={s.logged}>
        <Text style={[s.loggedText, { flex: 1 }]}>{answerText(check, a)}</Text>
        <Pressable disabled={saving} onPress={() => onRemoveCheck(i)} hitSlop={10} accessibilityRole="button" accessibilityLabel="Remove answer">
          <Trash2 size={18} color={G.redInk} />
        </Pressable>
      </View>)}
      {canAnswer && <>
        <View style={{ gap: 6 }}>
          <Text style={f.label}>Date checked</Text>
          <DateField value={checkDate} onChange={setCheckDate} testID="kit-check-date" />
        </View>
        {measured?.map((m) => <View key={m.id} style={{ gap: 6 }}>
          <Text style={f.label}>{m.label}{m.unit ? ` (${m.unit})` : ""}{m.kind === "grade" ? " grade" : ""}</Text>
          {m.kind === "grade"
            ? <Chips options={["0", "1", "2", "3", "4", "5"].map((g) => ({ value: g, label: g }))} value={typed[m.id]}
              onChange={(g) => setTyped((t) => ({ ...t, [m.id]: t[m.id] === g ? "" : g }))} />
            : <TextInput value={typed[m.id] || ""} onChangeText={(v) => setTyped((t) => ({ ...t, [m.id]: v }))} keyboardType="decimal-pad"
              placeholder="Value" placeholderTextColor={G.muted} style={f.input} testID={`kit-check-value-${m.id}`} />}
        </View>)}
        {badValue && <Text style={f.error}>Enter numbers only.</Text>}
        {!!check.needsBaseline.length && <Text style={f.hint}>
          Enter the baseline {check.needsBaseline.join(", ")} under Baseline values to work this out; until then answer by hand.
        </Text>}
        {!byHand && <FlowButton label="Save values" loading={saving} disabled={!checkIso || !hasValues || badValue}
          onPress={() => checkIso && onCheck(checkIso, null, values)} />}
        {byHand && <>
          <FlowButton label={check.until ? "Met (this is the last one)" : "Yes"} loading={saving} disabled={!checkIso || badValue}
            onPress={() => checkIso && onCheck(checkIso, "yes", values)} />
          <FlowButton label={check.until ? "Not yet (comes back next time)" : "No, hold this visit"} variant="secondary"
            disabled={saving || !checkIso || badValue} onPress={() => checkIso && onCheck(checkIso, "no", values)} />
          {!check.until && reduceRow && <FlowButton label={`Give ${reduceRow.percent}% this time`} variant="secondary"
            disabled={saving || !checkIso || badValue} onPress={() => checkIso && onCheck(checkIso, "reduce", values)} />}
          {!check.until && canSkip && <FlowButton label="Skip this dose (not made up)" variant="secondary"
            disabled={saving || !checkIso || badValue} onPress={() => checkIso && onCheck(checkIso, "skip", values)} />}
        </>}
      </>}
      {!!check.source && <Text style={f.hint}>From the protocol: {check.source}</Text>}
    </View>}
    {row.doses.map((d) => <View key={d.dose} style={s.check}>
      <Text style={f.label}>{d.label}: {d.levelLabel || "level not stated"}</Text>
      {!!d.changed && !d.manual && <Text style={f.hint}>Changed here: {d.changed}.</Text>}
      <Chips options={d.levels.map((l) => ({ value: l.id, label: l.label }))} value={d.manual ? d.level : null}
        onChange={(level) => onSetDose(d.dose, level)} />
      <Text style={f.hint}>{d.manual ? "Set by staff; later doses continue from it." : "Choose a level only if staff changed the dose here."}</Text>
      {d.manual && <FlowButton label="Use the protocol's level" variant="secondary" disabled={saving} onPress={() => onSetDose(d.dose, null)} />}
    </View>)}
    <View style={{ gap: 6 }}>
      <Text style={f.label}>Date of visit</Text>
      <DateField value={date} onChange={setDate} testID="kit-visit-date" />
      {row.movesOthers && <Text style={f.hint}>Later visits move with this date.</Text>}
    </View>
    {row.timeKind && <View style={{ gap: 6 }}>
      <Text style={f.label}>{row.timeKind === "checkin" ? "Check-in time" : "Dose time"}</Text>
      <TextInput testID="kit-visit-time" value={time} onChangeText={(v) => setTime(formatTimeInput(v))} placeholder="HH:MM"
        placeholderTextColor={G.muted} keyboardType="number-pad" maxLength={5} style={f.input} />
      {!!time && !timeOk && <Text style={f.error}>Enter the time as HH:MM, e.g. 20:30.</Text>}
      <FlowButton label={row.time ? "Update time" : "Save time"} variant="secondary" disabled={saving || !timeOk || time === row.time}
        onPress={() => timeOk && onTime(timeOk)} />
      {!!row.time && <FlowButton label="Clear time" variant="secondary" disabled={saving} onPress={() => onTime(null)} />}
    </View>}
    {!!error && <Text style={f.error}>{error}</Text>}
    <FlowButton label={row.actual ? "Update date" : "Mark done"} loading={saving} disabled={!iso} onPress={() => onSave(iso)} />
    {!!row.actual && <FlowButton label="Undo" variant="secondary" disabled={saving} onPress={() => onSave(null)} />}
    {!!row.procedures.length && <Pressable accessibilityRole="button" onPress={() => setShowProcs((v) => !v)}>
      <Text style={s.link}>{showProcs ? "Hide" : "Show"} assessments ({row.procedures.length})</Text>
    </Pressable>}
    {showProcs && row.procedures.map((proc, i) => <Text key={`${proc.name}-${i}`} style={[f.hint, { color: G.ink }]}>
      • {proc.name}{procedureTags(proc)}
    </Text>)}
  </FlowSheet>;
}

/**
 * The trial's model was reviewed again after this patient was enrolled. They stay on their version
 * until staff move them; the banner says how many visit dates the move changes, or why it can't.
 */
function LatestModelBanner({ record, saving, onMove }: { record: KitPatientRecord; saving: boolean; onMove?: () => void }) {
  const latest = record.latest!;
  const changed = (() => {
    if (latest.problem) return null;
    try {
      const dated = (m: KitPatientRecord["model"], site: Record<string, string>) => new Map(computeKitSchedule(m, site, record.participant).rows
        .filter((r): r is KitVisitRow => r.type === "visit").map((r) => [r.key, r.date]));
      const now = dated(record.model, record.site_answers || {}), next = dated(latest.model, latest.site_answers || {});
      let n = 0;
      for (const key of new Set([...now.keys(), ...next.keys()])) if (now.get(key) !== next.get(key)) n++;
      return n;
    } catch { return null; }
  })();
  const title = `Protocol model version ${latest.version} is available (this patient uses version ${record.model_version ?? 1}).`;
  const text = latest.problem ? `Their record doesn't fit it yet: ${latest.problem}`
    : changed === 0 ? "Moving keeps every visit date." : changed ? `Moving changes ${changed} visit date${changed === 1 ? "" : "s"}.` : "";
  return <Banner tone="violet" title={title} text={text}
    action={!latest.problem && onMove && !saving ? "Move" : undefined} onAction={onMove} />;
}

/** " (if clinically indicated · results from the last 3 days can be used)" for one assessment. */
function procedureTags(proc: KitVisitRow["procedures"][number]): string {
  const tags = [
    proc.need === "if_indicated" ? "if clinically indicated" : proc.need === "optional" ? "optional" : null,
    proc.conditional ? `if ${proc.condition || "applicable"}` : null,
    proc.reuseWithinDays ? `results from the last ${proc.reuseWithinDays} day${proc.reuseWithinDays === 1 ? "" : "s"} can be used` : null,
    proc.withinBefore ? `within ${"hours" in proc.withinBefore ? `${proc.withinBefore.hours} h` : `${proc.withinBefore.days} days`} before${proc.withinBefore.from ? `, from ${short(proc.withinBefore.from)}` : ""}` : null,
  ].filter(Boolean);
  return tags.length ? ` (${tags.join(" · ")})` : "";
}

/** "Allowed 9 Jan 2027 – 30 Apr 2027, not on the P1W4 day (8 Jan)." */
function windowText(w: KitSchedule["ruleWindows"][number] | undefined): string | null {
  if (!w || (!w.from && !w.to && !w.notOn)) return null;
  const long = (iso: string) => { const q = isoParts(iso); return `${+q.day} ${q.month} ${q.year}`; };
  const range = w.from && w.to ? `${long(w.from)} – ${long(w.to)}` : w.from ? `from ${long(w.from)}` : w.to ? `until ${long(w.to)}` : "";
  const notOn = w.notOn ? `not on the ${w.notOn.code} day${w.notOn.date ? ` (${short(w.notOn.date)})` : ""}` : "";
  return `Allowed ${[range, notOn].filter(Boolean).join(", ")}.`;
}

function AnchorSheet({ label, effects, window, date, saving, error, onClose, onSave }: {
  label: string; effects: string[]; window: string | null; date: string | null; saving: boolean; error: string; onClose: () => void; onSave: (date: string | null) => void;
}) {
  const [value, setValue] = useState(date ? isoToDmy(date) : "");
  const iso = dmyToIso(value);
  return <FlowSheet visible title={`${label} date`} onClose={onClose}>
    <Text style={f.hint}>Visits that count from it get their dates from this.</Text>
    {effects.map((text) => <Text key={text} style={[f.hint, { color: G.redInk }]}>{text}</Text>)}
    {!!window && <Text style={[f.hint, { color: G.ink }]}>{window}</Text>}
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
        {(record.model.rules || []).filter((r) => "event" in r.when && r.when.event === definition.id && r.effect)
          .map((r) => { const when = ruleIfText(record.model, r); return <Text key={r.id} style={f.hint}>{when ? `${when}: ` : ""}{r.effect}</Text>; })}
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
  check: { gap: 8, padding: 12, borderRadius: 12, borderWidth: 1, borderColor: G.line },
});
