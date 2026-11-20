import React, { useEffect, useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { computeKitSchedule, isIsoDate, type KitModel, type KitParticipant, type KitVisitRow } from "./index";
import { previewKitWorkflow, type KitWorkflowInput, type KitWorkflow, type WorkflowBlock, type WorkflowPreview } from "./api";
import { FlowButton, FlowSheet, flowStyles as f } from "./FlowScreen";
import { FOCUS as G } from "./focus";
import { adaptiveInputs } from "./adaptive";

export function workflowError(error: any) {
  const detail = error?.response?.data?.detail;
  return typeof detail === "string" ? detail : detail?.message || "Could not prepare the workflow.";
}

export function useWorkflowPreview(input: KitWorkflowInput | null) {
  const [result, setResult] = useState<{ input: KitWorkflowInput; preview?: WorkflowPreview; error?: string }>();
  useEffect(() => {
    if (!input) return;
    let active = true;
    const timer = setTimeout(() => {
      previewKitWorkflow(input).then(
        (preview) => { if (active) setResult({ input, preview }); },
        (error) => { if (active) setResult({ input, error: workflowError(error) }); },
      );
    }, 300);
    return () => { active = false; clearTimeout(timer); };
  }, [input]);
  return input && result ? { ...result, current: result.input === input } : undefined;
}

export function WorkflowReview({ workflow, model, site, previous, onEdit, onResolve, resolutions = {} }: {
  workflow: KitWorkflow; model: KitModel; site: Record<string, string>; previous?: KitWorkflow;
  onEdit?: (key: string, operation: Record<string, unknown>) => void;
  onResolve?: (key: string, text: string) => void; resolutions?: Record<string, string>;
}) {
  const [tab, setTab] = useState<"Workflow" | "Visits and activities" | "Sample calendar">("Workflow");
  const [selected, select] = useState<string | null>(null);
  const [start, setStart] = useState("2030-01-01");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [events, setEvents] = useState<NonNullable<KitParticipant["events"]>>([]);
  const [eventType, setEventType] = useState("");
  const [eventDate, setEventDate] = useState("");
  const [eventAnswers, setEventAnswers] = useState<Record<string, string>>({});
  const [actuals, setActuals] = useState<Record<string, string>>({});
  const [times, setTimes] = useState<Record<string, string>>({});
  const [findings, setFindings] = useState<Record<string, Record<string, string>>>({});
  const [withheld, setWithheld] = useState<Record<string, string>>({});
  const [sampleVisit, setSampleVisit] = useState<KitVisitRow | null>(null);
  const [operation, setOperation] = useState("");
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const blocks = useMemo(() => new Map(workflow.blocks.map((b) => [b.id, b])), [workflow]);
  const old = useMemo(() => new Map(previous?.blocks.map((b) => [b.id, b]) || []), [previous]);
  const block = selected ? blocks.get(selected) : undefined;
  const anchor = model.anchors.find((a) => a.kind === "start");
  const calendar = useMemo(() => {
    if (tab !== "Sample calendar" || !anchor || !isIsoDate(start)) return null;
    try {
      const validAnswers = Object.fromEntries(Object.entries(answers).filter(([id, value]) => value && (!id.startsWith("@") || isIsoDate(value))));
      const sampleSite = { ...site };
      for (const date of model.anchors) {
        if (date.scope === "study" && validAnswers[`@${date.id}`]) sampleSite[`@${date.id}`] = validAnswers[`@${date.id}`];
      }
      return computeKitSchedule(model, sampleSite, {
        startAnchor: anchor.id, startDate: start,
        answers: validAnswers, today: start, events,
        actuals: Object.fromEntries(Object.entries(actuals).filter(([, value]) => isIsoDate(value))),
        doseTimes: Object.fromEntries(Object.entries(times).filter(([, value]) => /^([01]\d|2[0-3]):[0-5]\d$/.test(value))),
        withheld,
        // Sample findings and recommendations for history-based intervals.
        observations: Object.fromEntries(Object.entries(findings).map(([key, inputs]) => [key, Object.fromEntries(
          Object.entries(inputs).filter(([, value]) => value !== "" && Number.isFinite(Number(value)) || /^(yes|no)$/i.test(value))
            .map(([id, value]) => [id, { value: /^(yes|no)$/i.test(value) ? value.toLowerCase() : Number(value) }]))])),
      });
    } catch {
      return null;
    }
  }, [tab, anchor, start, model, site, answers, events, actuals, times, findings, withheld]);
  const show = (id: string) => { setEditing(false); setError(""); select(id); };
  const apply = () => {
    try {
      const parsed = JSON.parse(operation);
      if (!parsed || Array.isArray(parsed) || parsed.id !== block?.operation.id) {
        throw new Error("Keep the block ID and enter a JSON object.");
      }
      onEdit?.(block!.id, parsed);
      select(null);
    } catch (e: any) {
      setError(e.message || "Enter valid JSON.");
    }
  };
  const renderIssue = (issue: KitWorkflow["issues"][number]) => <View key={issue.id} style={s.row}>
    <Text style={s.label}>{issue.text}</Text>
    {!!issue.why && <Text style={f.hint}>{issue.why}</Text>}
    {!!issue.quote && <Text style={f.hint}>{issue.page ? `Page ${issue.page} · ` : ""}“{issue.quote}”</Text>}
    {onResolve ? <TextInput accessibilityLabel="Confirmed interpretation" style={f.input} multiline
      placeholder="Record the confirmed interpretation" placeholderTextColor={G.muted}
      value={resolutions[issue.id] || ""} onChangeText={(text) => onResolve(issue.id, text)} />
      : !!issue.resolution && <Text style={f.hint}>{issue.resolution.text}</Text>}
  </View>;

  return <View style={s.workspace}>
    {!!(model.protocol.number || model.protocol.version || model.protocol.date) && <Text style={f.hint}>
      {[model.protocol.number, model.protocol.version, model.protocol.date].filter(Boolean).join(" · ")}
    </Text>}
    <View style={f.chips}>
      {(["Workflow", "Visits and activities", "Sample calendar"] as const).map((name) =>
        <Pressable key={name} accessibilityRole="tab" accessibilityState={{ selected: tab === name }}
          onPress={() => setTab(name)} style={[f.chip, tab === name && f.chipOn]}>
          <Text style={[f.chipText, tab === name && f.chipTextOn]}>{name}</Text>
        </Pressable>)}
    </View>
    {tab === "Workflow" && <>
      {workflow.errors.map((message, i) => <Text key={i} style={f.error}>{message}</Text>)}
      {workflow.issues.filter((issue) => !issue.blocks.length).map(renderIssue)}
      {workflow.blocks.map((item) => {
        const original = old.get(item.id);
        const change = !previous ? "" : !original ? "Added · " :
          JSON.stringify(original.operation) === JSON.stringify(item.operation) ? "" : "Changed · ";
        const issueCount = workflow.issues.filter((issue) => issue.blocks.includes(item.id) && !issue.resolution).length;
        return <Pressable key={item.id} accessibilityRole="button" onPress={() => show(item.id)} style={s.row}>
          <Text style={s.label}>{item.label}</Text>
          <Text style={f.hint}>{change}{item.trigger}{issueCount ? ` · ${issueCount} to resolve` : ""}</Text>
          {item.summary?.filter((line) => /^(Condition|Precedence|Blocked|Generated calculation|Test FAILS)/.test(line)).map((line) =>
            <Text key={line} style={[f.hint, line.startsWith("Blocked") && { color: G.redInk }]}>{line}</Text>)}
          {!!item.inputs.length && <Text style={f.hint}>
            {item.inputs.map((edge) => blocks.get(edge.block)?.label || edge.block).join(" + ")} → {item.label}
          </Text>}
          {!!item.paths.length && <Text style={f.hint}>
            {item.label} → {item.paths.map((edge) => blocks.get(edge.block)?.label || edge.block).join(", ")}
          </Text>}
        </Pressable>;
      })}
      {previous?.blocks.filter((item) => !blocks.has(item.id)).map((item) =>
        <Text key={item.id} style={f.hint}>Removed · {item.label}</Text>)}
    </>}
    {tab === "Visits and activities" && workflow.blocks.filter((b) => ["visit", "event_visit"].includes(b.kind)).map((item) => {
      const row = item.operation as unknown as KitModel["visits"][number];
      return <Pressable key={item.id} accessibilityRole="button" style={s.row} onPress={() => show(item.id)}>
        <Text style={s.label}>{row.code} · {item.label}</Text>
        <Text style={f.hint}>{row.offset_days} days from {blocks.get(item.inputs[0]?.block)?.label || "its trigger"}
          {row.window_stated === false ? " · Window unstated" : ` · Window −${row.window_before}/+${row.window_after} days`}</Text>
        {row.procedures.map((proc, i) => <Text key={i} style={f.hint}>{proc.name}{proc.source ? ` · ${proc.source}` : ""}</Text>)}
      </Pressable>;
    })}
    {tab === "Sample calendar" && <>
      <Text style={f.label}>{anchor?.label || "Starting date"}</Text>
      <TextInput accessibilityLabel="Sample starting date" style={f.input} value={start} onChangeText={setStart} placeholder="YYYY-MM-DD" />
      {model.anchors.filter((a) => a.kind === "ask").map((a) => <View key={a.id}>
        <Text style={f.label}>{a.label}</Text>
        <TextInput accessibilityLabel={a.label} style={f.input} value={answers[`@${a.id}`] || ""} placeholder="YYYY-MM-DD"
          onChangeText={(value) => setAnswers((prior) => ({ ...prior, [`@${a.id}`]: value }))} />
      </View>)}
      {model.questions.filter((q) => q.scope === "participant" || q.scope === "decision").map((q) => <View key={q.id}>
        <Text style={f.label}>{q.text}</Text>
        {q.kind === "number" ? <TextInput accessibilityLabel={q.text} style={f.input} keyboardType="numeric" value={answers[q.id] || ""}
          onChangeText={(value) => setAnswers((prior) => ({ ...prior, [q.id]: value }))} />
          : <View style={f.chips}>{questionOptions(q).map((choice) => <Pressable key={choice.value} accessibilityRole="radio"
            accessibilityState={{ selected: answers[q.id] === choice.value }} style={[f.chip, answers[q.id] === choice.value && f.chipOn]}
            onPress={() => setAnswers((prior) => ({ ...prior, [q.id]: prior[q.id] === choice.value ? "" : choice.value }))}>
            <Text style={[f.chipText, answers[q.id] === choice.value && f.chipTextOn]}>{choice.label}</Text>
          </Pressable>)}</View>}
      </View>)}
      {!!model.events.length && <View style={s.row}>
        <Text style={s.label}>Try a protocol event</Text>
        <View style={f.chips}>{model.events.map((event) => <Pressable key={event.id} accessibilityRole="radio"
          accessibilityState={{ selected: eventType === event.id }} style={[f.chip, eventType === event.id && f.chipOn]}
          onPress={() => { setEventType(event.id); setEventAnswers({}); }}>
          <Text style={[f.chipText, eventType === event.id && f.chipTextOn]}>{event.label}</Text>
        </Pressable>)}</View>
        {!!eventType && <>
          <TextInput accessibilityLabel="Sample event date" style={f.input} value={eventDate} onChangeText={setEventDate} placeholder="YYYY-MM-DD" />
          {model.events.find((event) => event.id === eventType)?.questions.map((q) => <View style={s.row} key={q.id}>
            <Text style={s.label}>{q.text}</Text>
            {q.kind === "number" ? <TextInput accessibilityLabel={q.text} style={f.input} keyboardType="numeric" value={eventAnswers[q.id] || ""}
              onChangeText={(value) => setEventAnswers((prior) => ({ ...prior, [q.id]: value }))} />
              : <View style={f.chips}>{questionOptions(q).map((option) => <Pressable key={option.value} accessibilityRole="radio"
                accessibilityState={{ selected: eventAnswers[q.id] === option.value }}
                style={[f.chip, eventAnswers[q.id] === option.value && f.chipOn]}
                onPress={() => setEventAnswers((prior) => ({ ...prior, [q.id]: option.value }))}>
                <Text style={[f.chipText, eventAnswers[q.id] === option.value && f.chipTextOn]}>{option.label}</Text>
              </Pressable>)}</View>}
          </View>)}
          <FlowButton label="Add sample event" variant="secondary" disabled={!isIsoDate(eventDate)}
            onPress={() => {
              setEvents((prior) => [...prior.filter((e) => e.type !== eventType || model.events.find((d) => d.id === eventType)?.repeatable),
                { key: `sample-${Date.now()}`, type: eventType, date: eventDate, answers: eventAnswers }]);
              setEventDate(""); setEventAnswers({});
            }} />
        </>}
        {events.map((event) => <Pressable key={event.key} accessibilityRole="button" onPress={() => setEvents((prior) => prior.filter((e) => e.key !== event.key))}>
          <Text style={f.hint}>Remove {model.events.find((e) => e.id === event.type)?.label} · {event.date}</Text>
        </Pressable>)}
      </View>}
      {calendar?.warnings.map((message, i) => <Text key={i} style={f.error}>{message}</Text>)}
      {calendar?.rows.map((row) => <View key={row.key} style={s.row}>
        <Text style={s.label}>{row.type === "visit" ? `${row.code} · ${row.name}` : row.text}</Text>
        <Text style={f.hint}>{row.type === "decision" ? (row.answer || "Pending input") : calendarWindow(row)}</Text>
        {row.type === "visit" && row.adaptive && <Text style={f.hint}>{row.adaptive.text}</Text>}
        {row.type === "visit" && row.combined?.map((c) => <Text key={c.key} style={f.hint}>Includes {c.code} (target {c.planned})</Text>)}
        {row.type === "visit" && <Pressable accessibilityRole="button" onPress={() => setSampleVisit(row)}>
          <Text style={[f.hint, { color: G.accent }]}>{row.actual ? `Actual ${row.actual} · Edit sample inputs` : "Try an actual date or dose time"}</Text>
        </Pressable>}
        {row.type === "visit" && row.timepoints.map((point, i) => <Text key={i} style={f.hint}>{point.label} · {point.at} · {point.window}</Text>)}
      </View>)}
      {!calendar && <Text style={f.hint}>Enter a valid starting date to calculate the calendar.</Text>}
    </>}
    {sampleVisit && <FlowSheet visible title={sampleVisit.name} onClose={() => setSampleVisit(null)}>
      <Text style={f.label}>Sample actual date</Text>
      <TextInput accessibilityLabel="Sample actual date" style={f.input} value={actuals[sampleVisit.key] || ""} placeholder="YYYY-MM-DD"
        onChangeText={(value) => setActuals((prior) => ({ ...prior, [sampleVisit.key]: value }))} />
      <Text style={f.label}>Sample dose time</Text>
      <TextInput accessibilityLabel="Sample dose time" style={f.input} value={times[sampleVisit.key] || ""} placeholder="HH:MM"
        onChangeText={(value) => setTimes((prior) => ({ ...prior, [sampleVisit.key]: value }))} />
      {adaptiveInputs(model, sampleVisit.visitId).map((input) => <View key={input.id}>
        <Text style={f.label}>Sample: {input.label}</Text>
        <TextInput accessibilityLabel={`Sample ${input.label}`} style={f.input} value={findings[sampleVisit.key]?.[input.id] || ""}
          placeholder={input.kind === "boolean" ? "yes / no" : "Number"}
          onChangeText={(value) => setFindings((prior) => ({ ...prior, [sampleVisit.key]: { ...prior[sampleVisit.key], [input.id]: value } }))} />
      </View>)}
      <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: !!withheld[sampleVisit.key] }}
        onPress={() => setWithheld((prior) => {
          const next = { ...prior };
          if (next[sampleVisit.key]) delete next[sampleVisit.key]; else next[sampleVisit.key] = "sample";
          return next;
        })}>
        <Text style={f.hint}>{withheld[sampleVisit.key] ? "☑" : "☐"} Dose not given at this visit</Text>
      </Pressable>
      <FlowButton label="See calendar" onPress={() => setSampleVisit(null)} />
    </FlowSheet>}
    {block && <FlowSheet visible title={block.label} onClose={() => select(null)}>
      <Text style={f.hint}>{block.scope} · {block.trigger}</Text>
      {block.summary?.map((line) => <Text key={line} style={[f.hint, { color: line.startsWith("Blocked") ? G.redInk : G.ink }]}>{line}</Text>)}
      {[...block.inputs, ...block.paths].map((edge, i) => <Pressable key={i} accessibilityRole="button" onPress={() => show(edge.block)}>
        <Text style={s.label}>{edge.relation} · {blocks.get(edge.block)?.label || edge.block}{edge.basis ? ` · ${edge.basis}` : ""}</Text>
      </Pressable>)}
      {block.prompts.map((prompt, i) => <View key={i} style={s.row}>
        <Text style={s.label}>{prompt.text}</Text>
        <Text style={f.hint}>{[prompt.type, prompt.roles.join(" / "), ...prompt.options.map((o) => o.label)].join(" · ")}</Text>
      </View>)}
      {block.outputs?.map((output, i) => <Text key={i} style={f.hint}>
        {[output.kind.replaceAll("_", " "), output.label || output.key, output.source].filter(Boolean).join(" · ")}
      </Text>)}
      <Text style={f.label}>Protocol source</Text>
      <Text style={f.hint}>{sourceText(block)}</Text>
      {workflow.issues.filter((issue) => issue.blocks.includes(block.id)).map(renderIssue)}
      {editing ? <>
        <TextInput accessibilityLabel="Block operation" style={[f.input, s.editor]} value={operation} onChangeText={setOperation} multiline autoCorrect={false} />
        {!!error && <Text style={f.error}>{error}</Text>}
        <FlowButton label="Apply block edit" onPress={apply} />
      </> : <>
        <Text style={s.logic}>{JSON.stringify(block.operation, null, 2)}</Text>
        {!!onEdit && <FlowButton label="Edit block" onPress={() => {
          setOperation(JSON.stringify(block.operation, null, 2)); setEditing(true);
        }} />}
      </>}
    </FlowSheet>}
  </View>;
}

function sourceText(block: WorkflowBlock) {
  const { page, reference, quote } = block.source;
  return [page && `Page ${page}`, typeof reference === "string" ? reference : reference && JSON.stringify(reference),
    quote && `“${quote}”`].filter(Boolean).join(" · ") || "Source location missing";
}

function questionOptions(q: KitModel["questions"][number]) {
  return q.kind === "boolean" ? [{ value: "yes", label: "Yes" }, { value: "no", label: "No" }] : q.options;
}

function calendarWindow(row: KitVisitRow) {
  if (!row.planned || row.dependsOnDecision) return `Pending input · ${row.relative || "Review the required answers"}`;
  if (row.windowStated === false) return `Target ${row.planned} · Window unstated`;
  return `Target ${row.planned} · ${row.windowStart} to ${row.windowEnd}`;
}

const s = StyleSheet.create({
  workspace: { marginVertical: 12, gap: 10 },
  row: { padding: 12, gap: 4, borderWidth: 1, borderColor: G.line, borderRadius: 10 },
  label: { fontWeight: "600", color: G.ink },
  logic: { fontFamily: "monospace", fontSize: 12, color: G.ink },
  editor: { minHeight: 200, textAlignVertical: "top", fontFamily: "monospace" },
});
