import React, { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import {
  AlertCircle, CheckCircle2, ChevronDown, ChevronRight, FileText, HelpCircle, Info, Pencil,
} from "lucide-react-native";

import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import {
  QUESTION_ACTION_LABELS, ROW_FILTERS, citationLine, coverageCountsText, dispositionSummary,
  extractionBadge, filterCounts, filterRows, groupRows, missingReferenceLine, questionResolution,
  rowAccessibilityLabel, statusIndicator, windowTone, footnoteHeading,
  type FootnoteFinding, type ReviewIssue, type ReviewRow, type ReviewView, type RowFilter, type SharedQuestion,
  type SourceCoverage,
} from "./visitSchedule";

const TONES = {
  success: { bg: "#E7F4EA", fg: "#1E6B3E" },
  warning: { bg: "#FDF3E1", fg: "#7A4A0C" },
  danger: { bg: "#FDECEC", fg: "#9C2424" },
  info: { bg: colors.secondary, fg: colors.secondaryFg },
};

/** Header: title badge, counts and an expandable explanation (design doc). */
export function VisitScheduleSummary({ view }: { view: ReviewView }) {
  const [explain, setExplain] = useState(false);
  const badge = extractionBadge(view);
  const tone = TONES[badge.tone];
  const { summary } = view;
  return <View style={styles.summary}>
    <View style={styles.summaryTop}>
      <View style={{ flex: 1, minWidth: 180 }}>
        <Text style={styles.scheduleName}>{view.schedule_name}</Text>
        <Text style={styles.muted}>Version {view.version_number} · {view.status.toLowerCase().replace(/_/g, " ")}</Text>
      </View>
      <View style={[styles.badge, { backgroundColor: tone.bg }]} accessibilityLabel={`Status: ${badge.label}`}>
        <Text style={[styles.badgeText, { color: tone.fg }]}>{badge.label}</Text>
      </View>
    </View>
    <Text style={styles.counts} accessibilityLabel={
      `${summary.displayed_rows} visits, ${summary.pending_rows} pending, ${summary.ready_rows} ready. `
      + `${summary.blocking_issues} blocking findings and ${summary.shared_questions} shared questions.`}>
      {summary.displayed_rows} visits · {summary.pending_rows} pending · {summary.ready_rows} ready
    </Text>
    <Text style={styles.muted}>
      {summary.blocking_issues} blocking finding{summary.blocking_issues === 1 ? "" : "s"} ·{" "}
      {summary.shared_questions} shared question{summary.shared_questions === 1 ? "" : "s"}
      {summary.warnings ? ` · ${summary.warnings} note${summary.warnings === 1 ? "" : "s"}` : ""}
    </Text>
    <Pressable accessibilityRole="button" accessibilityState={{ expanded: explain }}
      accessibilityLabel={explain ? "Hide explanation" : "What do these counts mean?"}
      onPress={() => setExplain((value) => !value)} style={styles.linkRow}>
      <Info size={14} color={colors.primary} />
      <Text style={styles.link}>{explain ? "Hide explanation" : "What do these counts mean?"}</Text>
    </Pressable>
    {explain && <View style={styles.explain}>
      <Text style={styles.value}>Counts are protocol visit rows, not patient appointments. A repeating visit is one row however many times it occurs.</Text>
      <Text style={styles.value}>Pending rows still have a question to resolve. Ready rows have none — Ready does not mean approved.</Text>
      <Text style={styles.value}>Findings and shared questions are counted separately: one question can affect many rows. Document-level notes are always shown, whatever the filter.</Text>
      <Text style={styles.value}>This is draft protocol timing. Patient dates are calculated only after approval, from each patient&apos;s own recorded dates.</Text>
    </View>}
  </View>;
}

export function FilterChips({ rows, value, onChange }: {
  rows: ReviewRow[]; value: RowFilter; onChange: (next: RowFilter) => void;
}) {
  const counts = filterCounts(rows);
  return <View style={styles.chips} accessibilityRole="tablist">
    {ROW_FILTERS.map((item) => {
      const selected = value === item.key;
      return <Pressable key={item.key} accessibilityRole="tab" accessibilityState={{ selected }}
        accessibilityLabel={`${item.label}, ${counts[item.key]} rows`}
        onPress={() => onChange(item.key)} style={[styles.chip, selected && styles.chipSelected]}>
        <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{item.label} ({counts[item.key]})</Text>
      </Pressable>;
    })}
  </View>;
}

export function DocumentIssues({ issues }: { issues: ReviewIssue[] }) {
  if (!issues.length) return null;
  return <View style={styles.docIssues} accessibilityLabel="Document-level notes">
    <Text style={styles.sectionLabel}>Document-level notes</Text>
    {issues.map((issue) => <View key={issue.id} style={styles.docIssue}>
      {issue.blocking ? <AlertCircle size={16} color={colors.warning} /> : <Info size={16} color={colors.info} />}
      <View style={{ flex: 1 }}>
        <Text style={styles.valueStrong}>{issue.blocking ? "Blocks approval · " : "Note · "}{issue.heading}</Text>
        <Text style={styles.value}>{issue.message}</Text>
        {issue.citations.map((item, index) => <Text key={index} style={styles.muted}>
          {item.page_number ? `Page ${item.page_number}: ` : ""}{item.quote || ""}
        </Text>)}
      </View>
    </View>)}
  </View>;
}

/** Document-level Source coverage panel (extraction accountability ledger). */
export function SourceCoveragePanel({ coverage }: { coverage?: SourceCoverage | null }) {
  const [open, setOpen] = useState(false);
  if (!coverage) return null;
  const tone = coverage.blocking ? TONES.warning : coverage.status === "COMPLETE" ? TONES.success : TONES.info;
  const unlinked = coverage.statements.not_linked_to_a_visit;
  const statusText = coverage.status === "COMPLETE" ? "Complete"
    : coverage.status === "NOT_ASSESSED" ? "Not assessed" : coverage.status.toLowerCase().replace(/_/g, " ");
  return <View style={styles.docIssues} accessibilityLabel="Source coverage">
    <View style={styles.summaryTop}>
      <Text style={[styles.sectionLabel, { flex: 1 }]}>Source coverage</Text>
      <View style={[styles.badge, { backgroundColor: tone.bg }]}>
        <Text style={[styles.badgeText, { color: tone.fg }]}>{statusText}{coverage.blocking ? " · blocks approval" : ""}</Text>
      </View>
    </View>
    <Text style={styles.value}>{coverage.message}</Text>
    <Text style={styles.muted}>Pages: {coverageCountsText(coverage.counts)}</Text>
    <Text style={styles.muted}>Statements ({coverage.statements.total}): {dispositionSummary(coverage.statements.dispositions)}</Text>
    <Pressable accessibilityRole="button" accessibilityState={{ expanded: open }}
      accessibilityLabel={open ? "Hide coverage details" : "Show coverage details"}
      onPress={() => setOpen((value) => !value)} style={styles.linkRow}>
      <Text style={styles.link}>{open ? "Hide" : "Show"} gaps, missing references and other statements</Text>
    </Pressable>
    {open && <View style={{ gap: 10 }}>
      <Block title={`Gaps (${coverage.gaps.length})`}>
        {coverage.gaps.length ? coverage.gaps.map((gap) => <Text key={gap.unit_id} style={styles.value}>
          • {gap.page ? `Page ${gap.page}` : gap.unit_id} — {gap.state.toLowerCase().replace(/_/g, " ")}{gap.rationale ? `: ${gap.rationale}` : ""}
        </Text>) : <Text style={styles.muted}>No gaps recorded.</Text>}
        {!!coverage.unaccounted_units.length && <Text style={styles.warn}>Not accounted for: {coverage.unaccounted_units.join(", ")}</Text>}
      </Block>
      <Block title={`Missing references (${coverage.missing_references.length})`}>
        {coverage.missing_references.length ? coverage.missing_references.map((reference, index) =>
          <Text key={index} style={reference.required ? styles.warn : styles.value}>• {missingReferenceLine(reference)}</Text>)
          : <Text style={styles.muted}>None.</Text>}
      </Block>
      <Block title={`Statements not tied to a visit (${unlinked.length})`}>
        {unlinked.length ? unlinked.map((item) => <View key={item.statement_id} style={styles.constraint}>
          <Text style={item.uncompiled ? styles.warn : styles.valueStrong}>{item.disposition_label}</Text>
          {!!item.what && <Text style={styles.value}>{item.what}</Text>}
          {!!item.reason && <Text style={styles.muted}>{item.reason}</Text>}
          {item.citations.map((citation, index) => <Text key={index} style={styles.muted}>{citationLine(citation)}</Text>)}
        </View>) : <Text style={styles.muted}>Every statement is linked to a visit.</Text>}
      </Block>
      {coverage.notes.map((note, index) => <Text key={index} style={styles.muted}>{note}</Text>)}
    </View>}
  </View>;
}

export type QuestionDraft = { action: string; anchorCode?: string; displayName?: string; anchorType?: string; rationale: string };

export function SharedQuestionsPanel({ questions, onResolve, onShowRow, busy, editable }: {
  questions: SharedQuestion[];
  onResolve: (question: SharedQuestion, draft: QuestionDraft) => void;
  onShowRow: (rowId: string) => void;
  busy: boolean;
  editable: boolean;
}) {
  const [drafts, setDrafts] = useState<Record<string, QuestionDraft>>({});
  if (!questions.length) return null;
  return <View style={styles.questions}>
    <Text style={styles.sectionLabel}>Questions to resolve ({questions.length})</Text>
    {questions.map((question) => {
      const resolution = questionResolution(question);
      const actions = resolution.actions;
      const draft = drafts[question.id] || { action: actions[0] || "", rationale: "" };
      const set = (patch: Partial<QuestionDraft>) => setDrafts((current) => ({ ...current, [question.id]: { ...draft, ...patch } }));
      return <View key={question.id} style={styles.question}>
        <View style={styles.questionHead}>
          <HelpCircle size={18} color={question.blocking ? colors.warning : colors.info} />
          <Text style={[styles.valueStrong, { flex: 1 }]}>{question.title}</Text>
        </View>
        <Text style={styles.muted}>{question.blocking ? "Blocks approval" : "Note"} · affects {question.affected_rows.length} visit{question.affected_rows.length === 1 ? "" : "s"}</Text>
        <View style={styles.affected}>
          {question.affected_rows.map((item) => <Pressable key={item.id} accessibilityRole="link"
            accessibilityLabel={`Show ${item.visit_name}`} onPress={() => onShowRow(item.id)} style={styles.affectedChip}>
            <Text style={styles.link}>{item.visit_name}</Text>
          </Pressable>)}
        </View>
        {question.issues.map((issue) => <Text key={issue.id} style={styles.value}>• {issue.message}</Text>)}
        {!!question.explanation && <Text style={styles.value}>{question.explanation}</Text>}
        {!actions.length && <Text style={question.acknowledgeable === false ? styles.warn : styles.muted}>{resolution.note}</Text>}
        {editable && !!actions.length && <View style={{ gap: 8 }}>
          {actions.length > 1 && <View style={styles.chips}>
            {actions.map((action) => <Choice key={action} label={QUESTION_ACTION_LABELS[action] || action}
              selected={draft.action === action} onPress={() => set({ action })} />)}
          </View>}
          {(draft.action === "CHOOSE_ORIGIN" || draft.action === "REMAP_ANCHOR") && <View style={styles.chips}>
            {(question.options || []).map((option) => <Choice key={option.code} label={option.display_name}
              selected={draft.anchorCode === option.code} onPress={() => set({ anchorCode: option.code })} />)}
          </View>}
          {draft.action === "DEFINE_ANCHOR" && <>
            <Field label="What event is this?" value={draft.displayName || ""} onChange={(displayName) => set({ displayName })} placeholder="e.g. Surgery" />
            <Field label="Event type" value={draft.anchorType || ""} onChange={(anchorType) => set({ anchorType })} placeholder="e.g. PROCEDURE" />
          </>}
          <Field label="Why is this right? (recorded)" value={draft.rationale} onChange={(rationale) => set({ rationale })}
            placeholder="Cite the protocol section or decision" />
          <Pressable accessibilityRole="button" disabled={busy} onPress={() => onResolve(question, draft)}
            style={[styles.action, busy && { opacity: 0.5 }]}>
            <Text style={styles.actionText}>{QUESTION_ACTION_LABELS[draft.action] || "Resolve"} for all {question.affected_rows.length}</Text>
          </Pressable>
        </View>}
      </View>;
    })}
  </View>;
}

/** Footnotes the system read but could not turn into a rule. A reviewer either enters the rule
 * it states or records, with a reason, that it has no scheduling effect. */
export function FootnotePanel({ findings, onResolve, busy, editable }: {
  findings: FootnoteFinding[];
  onResolve: (finding: FootnoteFinding, reason: string) => void;
  busy: boolean;
  editable: boolean;
}) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  if (!findings.length) return null;
  return <View style={styles.questions} accessibilityLabel="Footnotes to review">
    <Text style={styles.sectionLabel}>Footnotes to review ({findings.length})</Text>
    {findings.map((finding) => <View key={finding.footnote_id} style={styles.question}>
      <Text style={styles.valueStrong}>{footnoteHeading(finding)}</Text>
      {!!finding.text && <Text style={styles.value}>"{finding.text}"</Text>}
      <Text style={styles.muted}>Blocks approval until it is interpreted or recorded as having no scheduling effect.</Text>
      {finding.verification !== "TEXT_MATCHED" && <Text style={styles.warn}>The footnote text could not be verified on its page; check the protocol before deciding.</Text>}
      {editable && <View style={{ gap: 8 }}>
        <Field label="What does it say, and why is there no schedule rule? (recorded)" value={reasons[finding.footnote_id] || ""}
          onChange={(value) => setReasons((current) => ({ ...current, [finding.footnote_id]: value }))} multiline
          placeholder="e.g. Page 23: assessments for unscheduled visits are at the investigator's discretion; no timing is stated" />
        <Pressable accessibilityRole="button" disabled={busy} onPress={() => onResolve(finding, reasons[finding.footnote_id] || "")}
          style={[styles.action, busy && { opacity: 0.5 }]}>
          <Text style={styles.actionText}>Record: no scheduling effect</Text>
        </Pressable>
        <Text style={styles.muted}>If it does state a rule, edit the visit it applies to instead.</Text>
      </View>}
    </View>)}
  </View>;
}

export function VisitTable({ view, filter, expanded, onToggle, onEdit, editable }: {
  view: ReviewView;
  filter: RowFilter;
  expanded: Record<string, boolean>;
  onToggle: (rowId: string) => void;
  onEdit: (row: ReviewRow) => void;
  editable: boolean;
}) {
  const rows = filterRows(view.rows, filter);
  if (!view.rows.length) {
    return <View style={styles.empty}><Text style={styles.value}>No visits are in this draft yet. Retry the extraction, or add a visit manually.</Text></View>;
  }
  if (!rows.length) {
    return <View style={styles.empty}><Text style={styles.value}>{filter === "PENDING" ? "No rows are pending." : "No rows are ready yet."}</Text></View>;
  }
  const questions = new Map(view.shared_review_questions.map((item) => [item.id, item]));
  return <View style={{ gap: 14 }}>
    {groupRows(view, rows).map((group) => <View key={group.id} style={{ gap: 6 }}>
      {view.groups.length > 1 && <Text style={styles.sectionLabel}>{group.label}</Text>}
      <View style={styles.table} accessibilityLabel={`${group.label} table`}>
        <View style={styles.header} accessibilityRole="header">
          <Text style={[styles.heading, styles.statusCol]}>Status</Text>
          <Text style={[styles.heading, styles.nameCol]}>Visit name</Text>
          <Text style={[styles.heading, styles.timingCol]}>Timing</Text>
          <Text style={[styles.heading, styles.windowCol]}>Window</Text>
        </View>
        {group.rows.map((row, index) => {
          const open = !!expanded[row.id];
          const status = statusIndicator(row);
          const tone = TONES[status.tone];
          const wTone = windowTone(row.window_status);
          return <React.Fragment key={row.id}>
            <Pressable testID={`visit-row-${row.code}`} accessibilityRole="button"
              accessibilityState={{ expanded: open }} accessibilityLabel={rowAccessibilityLabel(row, open)}
              accessibilityHint={status.description}
              onPress={() => onToggle(row.id)}
              style={({ pressed }) => [styles.row, index % 2 === 1 && styles.alternate, pressed && styles.pressed]}>
              <View style={[styles.statusCol, styles.statusCell]}>
                <View style={[styles.statusPill, { backgroundColor: tone.bg }]}>
                  {status.icon === "alert" ? <AlertCircle size={12} color={tone.fg} /> : <CheckCircle2 size={12} color={tone.fg} />}
                  <Text style={[styles.statusText, { color: tone.fg }]}>{status.label}</Text>
                </View>
              </View>
              <View style={[styles.nameCol, styles.nameCell]}>
                {open ? <ChevronDown size={15} color={colors.primary} /> : <ChevronRight size={15} color={colors.mutedFg} />}
                <View style={{ flex: 1 }}>
                  <Text style={styles.name}>{row.visit_name}</Text>
                  {row.origin === "MANUAL" && <Text style={styles.muted}>Added manually</Text>}
                  {!!row.unsupported && <View style={styles.unsupported} accessibilityLabel="Uses a rule MTB cannot execute yet">
                    <AlertCircle size={11} color={TONES.danger.fg} />
                    <Text style={styles.unsupportedText}>Not executable yet</Text>
                  </View>}
                </View>
              </View>
              <Text style={[styles.value, styles.timingCol]}>{row.timing_summary}</Text>
              <Text style={[styles.value, styles.windowCol,
                wTone === "warning" && { color: TONES.warning.fg, fontFamily: fonts.semibold },
                wTone === "muted" && { color: colors.mutedFg }]}>{row.window_summary}</Text>
            </Pressable>
            {open && <RowDetails row={row} questions={row.question_refs.map((id) => questions.get(id)).filter(Boolean) as SharedQuestion[]}
              onEdit={editable ? () => onEdit(row) : undefined} />}
          </React.Fragment>;
        })}
      </View>
    </View>)}
  </View>;
}

function RowDetails({ row, questions, onEdit }: { row: ReviewRow; questions: SharedQuestion[]; onEdit?: () => void }) {
  const { details } = row;
  return <View testID={`visit-details-${row.code}`} style={styles.details}>
    <View style={styles.facts}>
      <Fact label="Applies to" value={row.applicability_summary} />
      <Fact label="How to attend" value={details.allowed_visit_modes.length ? details.allowed_visit_modes.join(" or ") : details.visit_mode || "Not stated"} />
      <Fact label="Protocol label" value={row.protocol_label} />
    </View>
    {!!row.pending_reasons.length && <Block title="Why this is pending">
      {row.pending_reasons.map((reason) => <Text key={reason} style={styles.value}>• {reason}</Text>)}
    </Block>}
    <Block title={`Assessments (${details.activities.length})`}>
      {details.activities.length ? details.activities.map((activity) => <View key={activity.id} style={styles.activity}>
        <Text style={styles.valueStrong}>{activity.name}</Text>
        <Text style={styles.muted}>{activity.requiredness.toLowerCase()} · {activity.applicability_summary}</Text>
        {!!activity.timing_summary && <Text style={styles.value}>{activity.timing_summary}</Text>}
        {activity.conditions.map((item) => <Text key={item} style={styles.value}>Only if {item}</Text>)}
        {activity.footnotes.map((note, index) => <Text key={index} style={styles.muted}>
          Footnote {note.marker || ""} ({note.resolved ? "resolved" : "unresolved"}): {note.text}
        </Text>)}
        {activity.needs_review && <Text style={styles.warn}>Needs review</Text>}
      </View>) : <Text style={styles.muted}>No assessments are listed for this visit.</Text>}
    </Block>
    <Block title="Counted from">
      {details.anchors.length ? details.anchors.map((anchor) => <Text key={anchor.code} style={styles.value}>
        {anchor.display_name} — {anchor.accepts === "PLANNED_OR_ACTUAL" ? "planned or actual date" : "actual date only"}
        {anchor.defined ? "" : " (not defined in this draft)"} · {anchor.roles.join(", ")}
      </Text>) : <Text style={styles.muted}>No anchor (fixed date or on demand).</Text>}
    </Block>
    {!!details.recurrence && <Block title="Repetition"><Text style={styles.value}>{details.recurrence.summary}</Text></Block>}
    <Block title={`All rules and constraints (${details.constraints.length})`}>
      {details.constraints.map((item) => <View key={item.id} style={styles.constraint}>
        <Text style={styles.muted}>{item.kind.replace(/_/g, " ").toLowerCase()}{item.in_compact_summary ? "" : " · not shown in the row"}
          {!item.supported ? " · not executable yet" : ""}{item.conflicting ? " · conflicts with another rule" : ""}</Text>
        <Text style={[styles.value, (!item.supported || item.conflicting) && styles.warn]}>{item.summary}</Text>
        {(item.source_statements || []).map((source) => <View key={source.statement_id} style={{ paddingLeft: 8 }}>
          <Text style={source.uncompiled || source.missing ? styles.warn : styles.muted}>
            From statement{source.relation ? ` (${source.relation === "OR" ? "alternative" : "applies together"})` : ""}:{" "}
            {source.missing ? "not in the extraction ledger" : source.disposition_label}{source.what ? ` — ${source.what}` : ""}
          </Text>
          {(source.citations || []).map((citation, index) => <Text key={index} style={styles.muted}>{citationLine(citation)}</Text>)}
        </View>)}
      </View>)}
    </Block>
    {!!details.additional_restrictions?.length && <Block title={`Additional restrictions (${details.additional_restrictions.length})`}>
      {details.additional_restrictions.map((item, index) => <View key={item.fact_id || index} style={styles.constraint}>
        <Text style={item.status === "COMPILED" ? styles.value : styles.warn}>{item.summary}</Text>
        {item.citations.map((citation, cIndex) => <Text key={cIndex} style={styles.muted}>{citationLine(citation)}</Text>)}
      </View>)}
    </Block>}
    {!!details.statements?.length && <Block title={`Protocol statements (${details.statements.length})`}>
      {details.statements.map((item) => <View key={item.statement_id} style={styles.constraint}>
        <Text style={item.uncompiled ? styles.warn : styles.valueStrong}>{item.disposition_label}</Text>
        {!!item.what && <Text style={styles.value}>{item.what}</Text>}
        {!!item.reason && <Text style={styles.muted}>{item.reason}</Text>}
        {item.citations.map((citation, index) => <Text key={index} style={styles.muted}>{citationLine(citation)}</Text>)}
      </View>)}
    </Block>}
    <Block title="Source evidence">
      {details.evidence.length ? details.evidence.map((item) => <View key={item.id} style={styles.evidence}>
        <FileText size={14} color={colors.primary} />
        <View style={{ flex: 1 }}>
          <Text style={styles.muted}>
            {item.is_reviewer_decision ? "Reviewer decision" : item.missing ? "Missing source reference"
              : [item.page_number ? `Page ${item.page_number}` : null, item.section_title, item.table_title].filter(Boolean).join(" · ") || "Protocol source"}
          </Text>
          {!!item.quote && <Text style={styles.quote}>“{item.quote}”</Text>}
        </View>
      </View>) : <Text style={styles.warn}>No source evidence is linked.</Text>}
    </Block>
    {(!!row.issues.length || !!questions.length) && <Block title="Questions to resolve">
      {row.issues.map((issue) => <Text key={issue.id} style={issue.blocking ? styles.warn : styles.value}>
        {issue.blocking ? "Blocks approval: " : "Note: "}{issue.message}
      </Text>)}
      {questions.map((question) => <Text key={question.id} style={styles.value}>Shared question: {question.title}</Text>)}
    </Block>}
    {!!details.manual && <Block title="Added manually">
      <Text style={styles.value}>{details.manual.rationale}</Text>
      <Text style={styles.muted}>Review state: {(details.manual.review_state || "").toLowerCase().replace(/_/g, " ")}</Text>
    </Block>}
    {!!onEdit && <Pressable accessibilityRole="button" accessibilityLabel={`Edit ${row.visit_name}`} onPress={onEdit} style={styles.editButton}>
      <Pencil size={15} color={colors.primary} /><Text style={styles.link}>Edit this visit</Text>
    </Pressable>}
  </View>;
}

export function Choice({ label, selected, onPress }: { label: string; selected: boolean; onPress: () => void }) {
  return <Pressable accessibilityRole="radio" accessibilityState={{ selected }} accessibilityLabel={label}
    onPress={onPress} style={[styles.chip, selected && styles.chipSelected]}>
    <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{selected ? "✓ " : ""}{label}</Text>
  </Pressable>;
}

export function Field({ label, value, onChange, placeholder, keyboardType, multiline }: {
  label: string; value: string; onChange: (value: string) => void; placeholder?: string;
  keyboardType?: "default" | "numbers-and-punctuation"; multiline?: boolean;
}) {
  return <View style={{ gap: 4, flexGrow: 1, minWidth: 90 }}>
    <Text style={styles.fieldLabel}>{label}</Text>
    <TextInput accessibilityLabel={label} value={value} onChangeText={onChange} placeholder={placeholder}
      placeholderTextColor={colors.mutedFg} keyboardType={keyboardType} multiline={multiline}
      style={[styles.input, multiline && { minHeight: 64, textAlignVertical: "top" }]} />
  </View>;
}

function Fact({ label, value }: { label: string; value: string }) {
  return <View style={styles.fact}><Text style={styles.fieldLabel}>{label}</Text><Text style={styles.value}>{value}</Text></View>;
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return <View style={styles.block}><Text style={styles.fieldLabel}>{title}</Text>{children}</View>;
}

const styles = StyleSheet.create({
  summary: { gap: 6, padding: spacing.md, borderRadius: radii.lg, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border },
  summaryTop: { flexDirection: "row", flexWrap: "wrap", gap: 8, alignItems: "flex-start" },
  scheduleName: { fontFamily: fonts.semibold, fontSize: 17, color: colors.foreground },
  badge: { paddingHorizontal: 10, paddingVertical: 6, borderRadius: radii.pill },
  badgeText: { fontFamily: fonts.semibold, fontSize: 12 },
  counts: { fontFamily: fonts.semibold, fontSize: 14, color: colors.foreground },
  linkRow: { flexDirection: "row", alignItems: "center", gap: 6, minHeight: 36 },
  link: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 13 },
  explain: { gap: 6, padding: 10, backgroundColor: colors.surface, borderRadius: radii.md },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { paddingHorizontal: 14, paddingVertical: 9, minHeight: 40, justifyContent: "center", borderRadius: radii.pill, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  chipSelected: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { fontFamily: fonts.semibold, fontSize: 13, color: colors.foreground },
  chipTextSelected: { color: colors.primaryFg },
  docIssues: { gap: 8, padding: 12, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  docIssue: { flexDirection: "row", gap: 8, alignItems: "flex-start" },
  sectionLabel: { fontFamily: fonts.semibold, fontSize: 15, color: colors.foreground },
  questions: { gap: 10 },
  question: { gap: 8, padding: 12, borderRadius: radii.lg, borderWidth: 1, borderColor: "#EBCB95", backgroundColor: "#FFFBF3" },
  questionHead: { flexDirection: "row", gap: 8, alignItems: "flex-start" },
  affected: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  affectedChip: { paddingHorizontal: 10, paddingVertical: 6, borderRadius: radii.pill, backgroundColor: colors.secondary, minHeight: 32, justifyContent: "center" },
  action: { backgroundColor: colors.primary, borderRadius: radii.md, paddingVertical: 12, paddingHorizontal: 14, alignItems: "center", minHeight: 44, justifyContent: "center" },
  actionText: { color: colors.primaryFg, fontFamily: fonts.semibold, fontSize: 14, textAlign: "center" },
  table: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, overflow: "hidden", backgroundColor: colors.card },
  header: { flexDirection: "row", backgroundColor: colors.surface, paddingHorizontal: 8, paddingVertical: 12, gap: 6 },
  heading: { fontFamily: fonts.semibold, color: colors.foreground, fontSize: 12 },
  statusCol: { flex: 0.8, minWidth: 0 },
  nameCol: { flex: 1.3, minWidth: 0 },
  timingCol: { flex: 1.4, minWidth: 0 },
  windowCol: { flex: 0.8, minWidth: 0 },
  row: { flexDirection: "row", alignItems: "flex-start", gap: 6, paddingHorizontal: 8, paddingVertical: 14, minHeight: 60, borderTopWidth: 1, borderTopColor: colors.border },
  alternate: { backgroundColor: "#F7F9FC" },
  pressed: { backgroundColor: colors.surface },
  statusCell: { alignItems: "flex-start" },
  statusPill: { flexDirection: "row", alignItems: "center", gap: 3, paddingHorizontal: 6, paddingVertical: 3, borderRadius: radii.pill, flexShrink: 1 },
  statusText: { fontFamily: fonts.semibold, fontSize: 11, flexShrink: 1 },
  nameCell: { flexDirection: "row", alignItems: "flex-start", gap: 3 },
  name: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 13, lineHeight: 18 },
  value: { color: colors.foreground, fontFamily: fonts.regular, fontSize: 13, lineHeight: 19 },
  valueStrong: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 13, lineHeight: 19 },
  muted: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 12, lineHeight: 17 },
  warn: { color: TONES.warning.fg, fontFamily: fonts.semibold, fontSize: 12, lineHeight: 17 },
  quote: { color: colors.foreground, fontFamily: fonts.regular, fontSize: 12, lineHeight: 18, fontStyle: "italic" },
  details: { padding: 14, gap: 12, backgroundColor: "#F9FAFC", borderTopWidth: 1, borderTopColor: colors.border },
  facts: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  fact: { flexGrow: 1, flexBasis: "40%", gap: 2 },
  fieldLabel: { color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 12 },
  block: { gap: 5 },
  activity: { gap: 2, paddingTop: 6, borderTopWidth: 1, borderTopColor: colors.border },
  constraint: { gap: 1, paddingVertical: 3 },
  evidence: { flexDirection: "row", gap: 6, alignItems: "flex-start" },
  unsupported: { flexDirection: "row", alignItems: "center", gap: 3, marginTop: 3, alignSelf: "flex-start", paddingHorizontal: 5, paddingVertical: 2, borderRadius: radii.pill, backgroundColor: "#FDECEC" },
  unsupportedText: { color: "#9C2424", fontFamily: fonts.semibold, fontSize: 10 },
  editButton: { flexDirection: "row", gap: 6, alignItems: "center", minHeight: 44 },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10, color: colors.foreground, fontFamily: fonts.regular, backgroundColor: colors.card },
  empty: { padding: 18, borderRadius: radii.lg, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border },
});
