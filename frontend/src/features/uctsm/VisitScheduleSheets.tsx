import React, { useEffect, useState } from "react";
import { KeyboardAvoidingView, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { Button } from "@/src/components/ui";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import { Choice, Field } from "./VisitScheduleReview";
import {
  RECURRENCE_BASES, TIME_UNITS, buildAddVisitRequest, buildEditRequest, timingFormFromRule,
  type ActualSourceValue, type RecurrenceBasisForm, type ReviewRow, type ReviewView, type TimeUnit,
  type TimingForm, type WindowForm,
} from "./visitSchedule";

function Sheet({ visible, title, onClose, children }: {
  visible: boolean; title: string; onClose: () => void; children: React.ReactNode;
}) {
  return <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.backdrop}>
      <View style={styles.sheet} accessibilityViewIsModal>
        <View style={styles.sheetHead}>
          <Text style={styles.title} accessibilityRole="header">{title}</Text>
          <Pressable accessibilityRole="button" accessibilityLabel="Close" onPress={onClose} style={styles.close}>
            <Text style={styles.link}>Close</Text>
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={{ gap: 12, paddingBottom: 24 }} keyboardShouldPersistTaps="handled">
          {children}
        </ScrollView>
      </View>
    </KeyboardAvoidingView>
  </Modal>;
}

function TimingEditor({ value, onChange, anchors }: {
  value: TimingForm; onChange: (next: TimingForm) => void; anchors: ReviewView["anchors"];
}) {
  return <View style={{ gap: 8 }}>
    <Text style={styles.label}>Counted from</Text>
    <View style={styles.chips}>
      {anchors.map((anchor) => <Choice key={anchor.code} label={anchor.display_name}
        selected={value.anchor === anchor.code} onPress={() => onChange({ ...value, anchor: anchor.code })} />)}
    </View>
    {!anchors.length && <Text style={styles.muted}>This draft has no anchors yet. Resolve the anchor question first.</Text>}
    <View style={styles.chips}>
      <Choice label="Offset (e.g. 28 days after)" selected={value.kind === "OFFSET"} onPress={() => onChange({ ...value, kind: "OFFSET" })} />
      <Choice label="Clinical day (Day N)" selected={value.kind === "PROTOCOL_DAY"} onPress={() => onChange({ ...value, kind: "PROTOCOL_DAY" })} />
    </View>
    <Field label={value.kind === "PROTOCOL_DAY" ? "Day number" : "Amount (negative = before)"} value={value.value}
      onChange={(next) => onChange({ ...value, value: next })} keyboardType="numbers-and-punctuation" />
    {value.kind === "OFFSET" && <View style={styles.chips}>
      {TIME_UNITS.map((unit) => <Choice key={unit} label={unit.toLowerCase() + "s"} selected={value.unit === unit}
        onPress={() => onChange({ ...value, unit })} />)}
    </View>}
  </View>;
}

function WindowEditor({ value, onChange }: { value: WindowForm; onChange: (next: WindowForm) => void }) {
  return <View style={{ gap: 8 }}>
    <Text style={styles.muted}>Leave both blank when the protocol states no window (shown as Not stated).</Text>
    <View style={styles.row}>
      <Field label="Before" value={value.before} onChange={(before) => onChange({ ...value, before })} keyboardType="numbers-and-punctuation" />
      <Field label="After" value={value.after} onChange={(after) => onChange({ ...value, after })} keyboardType="numbers-and-punctuation" />
    </View>
    <View style={styles.chips}>
      {(["DAY", "WEEK", "HOUR"] as TimeUnit[]).map((unit) => <Choice key={unit} label={unit.toLowerCase() + "s"}
        selected={value.unit === unit} onPress={() => onChange({ ...value, unit })} />)}
    </View>
  </View>;
}

function windowFormFromRule(rule: unknown): WindowForm {
  const window = rule as Record<string, any> | null;
  if (!window?.before) return { before: "", after: "", unit: "DAY" };
  return { before: String(window.before.value), after: String(window.after.value), unit: window.before.unit || "DAY" };
}

export function EditVisitSheet({ row, view, busy, onClose, onSubmit, canCorrect = true }: {
  row: ReviewRow | null; view: ReviewView; busy: boolean; onClose: () => void;
  onSubmit: (row: ReviewRow, body: Record<string, unknown>) => Promise<boolean>;
  /** From /authorization/me (schedule.correct). Informational: the server decides. */
  canCorrect?: boolean;
}) {
  const [editBasis, setEditBasis] = useState(false);
  const [basis, setBasis] = useState<RecurrenceBasisForm>({ basis: "", actualSource: "" });
  const [name, setName] = useState("");
  const [editTiming, setEditTiming] = useState(false);
  const [timing, setTiming] = useState<TimingForm>({ kind: "OFFSET", anchor: "", value: "", unit: "DAY" });
  const [editWindow, setEditWindow] = useState(false);
  const [window, setWindow] = useState<WindowForm>({ before: "", after: "", unit: "DAY" });
  const [confirmRow, setConfirmRow] = useState(false);
  const [rationale, setRationale] = useState("");
  const [error, setError] = useState("");

  const timingRule = row?.details.constraints.find((item) => item.kind === "TIMING")?.rule;
  const windowRule = row?.details.constraints.find((item) => item.kind === "WINDOW")?.rule;
  const simpleTiming = row ? timingFormFromRule(timingRule, view.anchors[0]?.code || "") : null;

  useEffect(() => {
    if (!row) return;
    setName(row.visit_name); setEditTiming(false); setEditWindow(false); setConfirmRow(false);
    setRationale(""); setError(""); setEditBasis(false);
    const stated = (row.details.recurrence?.rule || {}) as Record<string, any>;
    setBasis({ basis: stated.basis_status === "UNCONFIRMED" ? "" : (stated.basis || ""),
               actualSource: stated.actual_source || "" });
    setTiming(simpleTiming || { kind: "OFFSET", anchor: view.anchors[0]?.code || "", value: "", unit: "DAY" });
    setWindow(windowFormFromRule(windowRule));
  }, [row?.id]);

  if (!row) return null;
  const save = async () => {
    const built = buildEditRequest(row, {
      visitName: name, timing: editTiming ? timing : null, window: editWindow ? window : null,
      confirmRow, rationale, recurrence: editBasis ? basis : null,
    }, view.revision);
    if (built.error) { setError(built.error); return; }
    setError("");
    if (await onSubmit(row, built.body!)) onClose();
  };
  return <Sheet visible title={`Edit ${row.visit_name}`} onClose={onClose}>
    <Field label="Visit name" value={name} onChange={setName} />
    <Text style={styles.muted}>Changing the name never changes when the visit happens.</Text>
    <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: editTiming }}
      onPress={() => setEditTiming((value) => !value)} style={styles.toggle}>
      <Text style={styles.valueStrong}>{editTiming ? "☑" : "☐"} Change the timing rule</Text>
    </Pressable>
    <Text style={styles.muted}>Current: {row.timing_summary}</Text>
    {editTiming && <TimingEditor value={timing} onChange={setTiming} anchors={view.anchors} />}
    {editTiming && !simpleTiming && <Text style={styles.warn}>
      This visit uses a timing rule this form cannot show; saving replaces it with the offset or day entered above.
    </Text>}
    <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: editWindow }}
      onPress={() => setEditWindow((value) => !value)} style={styles.toggle}>
      <Text style={styles.valueStrong}>{editWindow ? "☑" : "☐"} Change the window</Text>
    </Pressable>
    <Text style={styles.muted}>Current: {row.window_summary}</Text>
    {editWindow && <WindowEditor value={window} onChange={setWindow} />}
    {row.details.recurrence?.type === "INTERVAL" && <>
      <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: editBasis, disabled: !canCorrect }}
        disabled={!canCorrect} onPress={() => setEditBasis((value) => !value)} style={styles.toggle}>
        <Text style={styles.valueStrong}>{editBasis ? "☑" : "☐"} State what each repeat counts from</Text>
      </Pressable>
      <Text style={styles.muted}>Current: {row.details.recurrence.summary}</Text>
      {!canCorrect && <Text style={styles.warn}>
        Your account cannot correct schedule rules. Ask someone with that permission to state this.
      </Text>}
      {editBasis && canCorrect && <View style={{ gap: 8 }}>
        <View style={styles.chips}>
          {RECURRENCE_BASES.map((option) => <Choice key={option.value} label={option.label}
            selected={basis.basis === option.value}
            onPress={() => setBasis((current) => ({ ...current, basis: option.value }))} />)}
        </View>
        {!!basis.basis && <Text style={styles.muted}>
          {RECURRENCE_BASES.find((option) => option.value === basis.basis)?.hint}
        </Text>}
        {basis.basis === "PREVIOUS_ACTUAL_OCCURRENCE" && <View style={styles.chips}>
          {([["ACTUAL_VISIT", "Actual visit date"], ["ACTUAL_TREATMENT", "Actual treatment date"]] as
            [ActualSourceValue, string][]).map(([value, label]) => <Choice key={value} label={label}
            selected={basis.actualSource === value}
            onPress={() => setBasis((current) => ({ ...current, actualSource: value }))} />)}
        </View>}
      </View>}
    </>}
    <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: confirmRow }}
      onPress={() => setConfirmRow((value) => !value)} style={styles.toggle}>
      <Text style={styles.valueStrong}>{confirmRow ? "☑" : "☐"} I have checked this visit against the protocol</Text>
    </Pressable>
    <Field label="Why? (recorded in the audit trail)" value={rationale} onChange={setRationale} multiline
      placeholder="e.g. Protocol section 6.1 states Day 1 is first dose" />
    {!!error && <Text style={styles.error} accessibilityLiveRegion="polite">{error}</Text>}
    <Button onPress={save} loading={busy} disabled={busy}>Save and revalidate</Button>
  </Sheet>;
}

export function AddVisitSheet({ visible, view, busy, onClose, onSubmit }: {
  visible: boolean; view: ReviewView; busy: boolean; onClose: () => void;
  onSubmit: (body: Record<string, unknown>) => Promise<boolean>;
}) {
  const origin = view.anchors.find((item) => item.is_origin)?.code || view.anchors[0]?.code || "";
  const [name, setName] = useState("");
  const [timing, setTiming] = useState<TimingForm>({ kind: "OFFSET", anchor: origin, value: "", unit: "DAY" });
  const [window, setWindow] = useState<WindowForm>({ before: "", after: "", unit: "DAY" });
  const [activities, setActivities] = useState("");
  const [rationale, setRationale] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!visible) return;
    setName(""); setActivities(""); setRationale(""); setError("");
    setTiming({ kind: "OFFSET", anchor: origin, value: "", unit: "DAY" });
    setWindow({ before: "", after: "", unit: "DAY" });
  }, [visible]);
  const save = async () => {
    const built = buildAddVisitRequest({ visitName: name, timing, window, activities, rationale }, view.revision);
    if (built.error) { setError(built.error); return; }
    setError("");
    if (await onSubmit(built.body!)) onClose();
  };
  return <Sheet visible={visible} title="Add Visit" onClose={onClose}>
    <Text style={styles.muted}>A visit added here is recorded as manually added, with your reason, and is not presented as protocol-extracted.</Text>
    <Field label="Visit name" value={name} onChange={setName} placeholder="e.g. Week 2 phone check" />
    <TimingEditor value={timing} onChange={setTiming} anchors={view.anchors} />
    <Text style={styles.label}>Window</Text>
    <WindowEditor value={window} onChange={setWindow} />
    <Field label="Assessments (comma separated)" value={activities} onChange={setActivities} multiline />
    <Field label="Why is this visit added? (recorded)" value={rationale} onChange={setRationale} multiline
      placeholder="e.g. Amendment letter 3, section 2" />
    {!!error && <Text style={styles.error} accessibilityLiveRegion="polite">{error}</Text>}
    <Button onPress={save} loading={busy} disabled={busy}>Add and revalidate</Button>
  </Sheet>;
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(15,27,45,0.45)" },
  sheet: { maxHeight: "92%", backgroundColor: colors.background, borderTopLeftRadius: radii.xl, borderTopRightRadius: radii.xl, padding: spacing.md },
  sheetHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 10, gap: 10 },
  title: { fontFamily: fonts.semibold, fontSize: 18, color: colors.foreground, flex: 1 },
  close: { minHeight: 44, justifyContent: "center", paddingHorizontal: 8 },
  link: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 14 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  row: { flexDirection: "row", gap: 10 },
  label: { color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 12 },
  muted: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 12, lineHeight: 17 },
  valueStrong: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 14 },
  warn: { color: "#7A4A0C", fontFamily: fonts.semibold, fontSize: 12 },
  error: { color: colors.destructive, fontFamily: fonts.semibold, fontSize: 13 },
  toggle: { minHeight: 44, justifyContent: "center" },
});
