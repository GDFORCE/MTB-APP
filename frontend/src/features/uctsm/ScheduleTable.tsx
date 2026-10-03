import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from "react-native";
import {
  AlertCircle, Building2, ChevronDown, ChevronRight, FileText, FlaskConical,
  Globe, Home, Phone, Pill, Scan, Stethoscope, Video,
} from "lucide-react-native";
import type { LucideIcon } from "lucide-react-native";

import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import type {
  ConditionalRequirementRow, PatientScheduleRow,
  ProtocolScheduleRow, AnchorStatusRow, ConfinementRow, ImpactRow, QualifierDetail,
  RepeatRuleRow, TimelineEntry, VersionContext,
} from "./presentation";
import { classifyActivities, UNSTATED_VISIT_MODE } from "./presentation";

type Props = {
  rows: Array<ProtocolScheduleRow | PatientScheduleRow>;
  patient?: boolean;
  onEvidence?: (row: ProtocolScheduleRow) => void;
};

/** The Activities column already names every activity, classified as
 * Clinical or Administrative - a visit only still needs its own expand row
 * for a footnote the Activities column can't carry inline. */
function hasDetail(row: ProtocolScheduleRow | PatientScheduleRow): boolean {
  return Boolean(row.qualifierDetails?.length);
}

const widths = {
  visit: 74,
  // Doc 10 s21-s23: was a dedicated column; the mode symbol now sits under
  // the visit name instead (see the Name Cell below), so this column
  // absorbed the width freed up by dropping Mode's own.
  name: 220,
  timing: 250,
  window: 130,
  type: 130,
  activities: 240,
  appliesTo: 190,
  expected: 150,
  actual: 150,
  status: 190,
  qualifier: 170,
};

export function ScheduleTable({ rows, patient = false, onEvidence }: Props) {
  const { width } = useWindowDimensions();
  // Complex protocol logic stays hidden until the user asks for it: the main
  // table is a familiar schedule, details live one tap away (UI spec section 5).
  const [expanded, setExpanded] = React.useState<Record<string, boolean>>({});
  const toggle = (id: string) => setExpanded((current) => ({ ...current, [id]: !current[id] }));
  if (!rows.length) return <View style={styles.empty}><Text style={styles.emptyTitle}>No schedule events available</Text><Text style={styles.emptyText}>This schedule does not contain any displayable events.</Text></View>;
  if (width < 760) {
    return <View style={styles.mobileList}>{rows.map((row) => <MobileRow key={row.id} row={row} patient={patient} onEvidence={onEvidence} expanded={!!expanded[row.id]} onToggle={() => toggle(row.id)} />)}</View>;
  }
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator accessibilityLabel="Protocol schedule table">
      <View style={styles.table}>
        <View style={styles.headerRow}>
          <Header label="Visit" width={widths.visit} />
          {/* Doc 10 s21-s23: HOW the visit happens - a distinct question from
              Type below (WHAT kind of occasion it is) - no longer a column of
              its own; the mode symbol sits right under the visit name instead
              (still always visible on the main row, never buried behind the
              expand toggle, and not patient-view-only). */}
          <Header label="Visit Name" width={widths.name} />
          {patient ? <Header label="Expected Date" width={widths.expected} /> : <Header label="Timing" width={widths.timing} />}
          <Header label={patient ? "Allowed Window" : "Window"} width={widths.window} />
          <Header label="Type" width={widths.type} />
          <Header label="Activities" width={widths.activities} />
          {!patient && <Header label="Applies To" width={widths.appliesTo} />}
          {patient && <Header label="Actual Date" width={widths.actual} />}
          <Header label="Status" width={widths.status} />
          <Header label="Notes" width={widths.qualifier} />
        </View>
        {rows.map((row, index) => (
          <React.Fragment key={row.id}>
          <View style={[styles.dataRow, index % 2 === 1 && styles.altRow]}>
            <Cell width={widths.visit}>
              {hasDetail(row)
                ? <Pressable
                    onPress={() => toggle(row.id)}
                    accessibilityRole="button"
                    accessibilityLabel={`${expanded[row.id] ? "Collapse" : "Expand"} ${row.visit} details`}
                    style={styles.expandToggle}>
                    {expanded[row.id]
                      ? <ChevronDown size={14} color={colors.primary} />
                      : <ChevronRight size={14} color={colors.primary} />}
                    <Text style={styles.visit}>{row.visit}</Text>
                  </Pressable>
                : <Text style={styles.visit}>{row.visit}</Text>}
            </Cell>
            <Cell width={widths.name}>
              <Text style={styles.name}>{row.visitName}</Text>
              <ContextLines row={row} />
              <VisitModeValue value={row.visitMode} modeKey={row.visitModeKey} />
              {row.requiresReview && <View style={styles.reviewLine}><AlertCircle size={12} color={colors.warning} /><Text style={styles.reviewText}>Review required</Text></View>}
              {!!row.evidenceRefs.length && onEvidence && <EvidenceButton onPress={() => onEvidence(row)} />}
            </Cell>
            <Cell width={patient ? widths.expected : widths.timing}>
              <Text style={styles.value}>{patient ? (row as PatientScheduleRow).expectedDate : row.timing}</Text>
              {/* Doc C6: a date counted from a planned anchor says so beside the date. */}
              {patient && !!(row as PatientScheduleRow).dateNote && <Text style={styles.reviewText}>{(row as PatientScheduleRow).dateNote}</Text>}
              {patient && <FeasibilityLines row={row as PatientScheduleRow} />}
            </Cell>
            <Cell width={widths.window}><Text style={styles.value}>{patient ? (row as PatientScheduleRow).allowedWindow : row.window}</Text></Cell>
            <Cell width={widths.type}><Text style={styles.value}>{row.type}</Text></Cell>
            <Cell width={widths.activities}><ActivityGroupsView activities={row.activities} empty={patient ? "No applicable activities" : "No activities extracted"} /></Cell>
            {!patient && <Cell width={widths.appliesTo}><Text style={styles.value}>{row.appliesTo}</Text></Cell>}
            {patient && <Cell width={widths.actual}><Text style={styles.value}>{(row as PatientScheduleRow).actualDate}</Text></Cell>}
            <Cell width={widths.status}><Status value={row.status} /></Cell>
            <Cell width={widths.qualifier}>
              <Text style={row.qualifier === "—" ? styles.muted : styles.value}>{row.qualifier}</Text>
              {row.reviewReasons?.map((reason) => <Text key={reason} style={styles.reviewText}>{reason}</Text>)}
            </Cell>
          </View>
          {expanded[row.id] && <DetailPanel row={row} />}
          </React.Fragment>
        ))}
      </View>
    </ScrollView>
  );
}

/** Child panel for one visit: whatever the Activities column itself can't
 * carry inline - the unscheduled reason, a repeating rule, progress against
 * required assessments, and footnotes. Day-wise activity timing used to have
 * its own sub-table here; the Activities column names every activity
 * (grouped Clinical/Administrative) directly on the row now, so that table
 * was a second place saying the same thing and is gone. */
function DetailPanel({ row }: { row: ProtocolScheduleRow | PatientScheduleRow }) {
  const patientRow = row as PatientScheduleRow;
  return (
    <View style={styles.detailPanel}>
      {/* Mode now lives on the main row (both protocol and patient tables) -
          no longer shown here to avoid saying the same thing twice. */}
      {!!patientRow.unscheduledReason && (
        <MobileField label="Reason recorded" value={patientRow.unscheduledReason} />
      )}
      {/* UI spec s4.4: a repeat is a RULE. Saying so on the row stops the
          materialized occurrences from reading as the whole protocol. */}
      {!!row.repeatRule && (
        <MobileField label="Repeating rule" value={row.repeatRule} />
      )}
      {/* Doc 5 s26: "7 of 8 complete", against the REQUIRED assessments only. */}
      {!!patientRow.completion?.required && (
        <MobileField label="Progress" value={patientRow.completion.label} />
      )}
      {!!row.qualifierDetails?.length && <QualifierPanel details={row.qualifierDetails} />}
    </View>
  );
}

/**
 * Doc 3 s4 and s36. Rendered under the schedule so the last materialized visit
 * is never read as the end of the protocol.
 */
export function RepeatRuleFooter({ rows }: { rows: RepeatRuleRow[] }) {
  if (!rows.length) return null;
  return (
    <View style={styles.childBlock}>
      <Text style={styles.childTitle}>Continuing protocol requirements</Text>
      {rows.map((item) => (
        <View key={item.id} style={styles.childRow}>
          <Text style={[styles.value, styles.childActivity]}>{item.eventCode}</Text>
          <Text style={[styles.muted, styles.childRule]}>{item.cadence}</Text>
          <Text style={[styles.muted, styles.childRule]}>{item.continuation}</Text>
          <Text style={[styles.value, styles.childTime]}>{item.nextDate}</Text>
        </View>
      ))}
    </View>
  );
}

/**
 * Doc 1 s3 and s22. A site reading this needs to know which reference dates are
 * still outstanding, so an undated visit reads as "waiting on surgery" rather
 * than as a broken row.
 */
export function AnchorStatusTable({ rows }: { rows: AnchorStatusRow[] }) {
  if (!rows.length) return null;
  return (
    <View style={styles.childBlock}>
      {rows.map((item) => (
        <View key={item.id} style={styles.childRow}>
          <Text style={[styles.value, styles.childActivity]}>{item.anchor}</Text>
          <View style={styles.childRule}>
            <Status value={item.status} compact />
          </View>
          <Text style={[styles.value, styles.childTime]}>{item.date}</Text>
          <Text style={[styles.muted, styles.childRule]}>{item.detail}</Text>
        </View>
      ))}
    </View>
  );
}

type DeviationRow = {
  logical_key: string;
  display_name: string;
  deviation_type: string;
  planned_date?: string | null;
  actual_date?: string | null;
  delta_from_planned_days?: number | null;
  outside_window_days?: number | null;
  reason?: string | null;
};

/**
 * Doc 1 s18 and s26 use both magnitudes, so both are shown: how far the visit
 * moved from its expected date, and how far outside the allowed window it fell.
 * A visit can be days early and still perfectly in window.
 */
export function DeviationTable({ rows }: { rows: DeviationRow[] }) {
  if (!rows.length) return null;
  return (
    <View style={styles.childBlock}>
      <View style={styles.childHeader}>
        <Text style={[styles.childHeaderText, styles.childActivity]}>Visit</Text>
        <Text style={[styles.childHeaderText, styles.childTime]}>Expected</Text>
        <Text style={[styles.childHeaderText, styles.childTime]}>Actual</Text>
        <Text style={[styles.childHeaderText, styles.childRule]}>Deviation</Text>
      </View>
      {rows.map((item) => (
        <View key={item.logical_key} style={styles.childRow}>
          <Text style={[styles.value, styles.childActivity]}>{item.display_name}</Text>
          <Text style={[styles.value, styles.childTime]}>{item.planned_date || "—"}</Text>
          <Text style={[styles.value, styles.childTime]}>{item.actual_date || "—"}</Text>
          <Text style={[styles.muted, styles.childRule]}>
            {describeDeviation(item)}
          </Text>
        </View>
      ))}
    </View>
  );
}

function describeDeviation(item: DeviationRow): string {
  if (item.reason) return item.reason;
  const parts: string[] = [];
  const delta = item.delta_from_planned_days;
  if (typeof delta === "number" && delta !== 0) {
    parts.push(`${Math.abs(delta)} day${Math.abs(delta) === 1 ? "" : "s"} ${delta < 0 ? "early" : "late"}`);
  }
  const outside = item.outside_window_days;
  if (typeof outside === "number" && outside !== 0) {
    parts.push(`${Math.abs(outside)} outside the window`);
  }
  return parts.join(" · ") || item.deviation_type;
}

/**
 * UI spec s4.5. What a schedule change would do, before it is applied.
 *
 * Consequential rows sort first and protected history is labelled, because the
 * question a reviewer is answering is "is anything here wrong", and a list where
 * the important row is twentieth gets skimmed.
 */
export function ImpactPreviewTable({
  rows, summary,
}: { rows: ImpactRow[]; summary?: string }) {
  if (!rows.length) return null;
  return (
    <View style={styles.childBlock}>
      {!!summary && <Text style={[styles.childTitle]}>{summary}</Text>}
      <View style={styles.childHeader}>
        <Text style={[styles.childHeaderText, styles.childActivity]}>Visit</Text>
        <Text style={[styles.childHeaderText, styles.childRule]}>Change</Text>
        <Text style={[styles.childHeaderText, styles.childTime]}>Now</Text>
        <Text style={[styles.childHeaderText, styles.childTime]}>Would be</Text>
      </View>
      {rows.map((item) => (
        <View key={item.id} style={styles.childRow}>
          <Text style={[styles.value, styles.childActivity]}>{item.visit}</Text>
          <Text
            style={[item.consequential ? styles.value : styles.muted, styles.childRule]}>
            {item.change}
          </Text>
          <Text style={[styles.muted, styles.childTime]}>{item.before}</Text>
          <Text style={[styles.value, styles.childTime]}>{item.after}</Text>
        </View>
      ))}
    </View>
  );
}

/**
 * UI spec s4.10 and doc 9 s12-13. Which version produced these dates.
 *
 * Without it, two patients on the same trial showing different visit dates looks
 * like a bug rather than the protocol working as written.
 */
export function VersionContextHeader({ context }: { context: VersionContext | null }) {
  if (!context) return null;
  return (
    <View style={styles.childBlock}>
      <Text style={styles.childTitle}>{context.label}</Text>
      {!!context.detail && <Text style={styles.muted}>{context.detail}</Text>}
    </View>
  );
}

/**
 * UI spec s4.7 and doc 6 s17/s31. One stay, expandable to its study days.
 * Rendering a row per day would read as several separate visits, which is the
 * misreading the confinement model exists to prevent.
 */
export function ConfinementTable({ rows }: { rows: ConfinementRow[] }) {
  const [open, setOpen] = React.useState<Record<string, boolean>>({});
  if (!rows.length) return null;
  return (
    <View style={styles.childBlock}>
      <Text style={styles.childTitle}>Inpatient stays</Text>
      {rows.map((item) => (
        <View key={item.id}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`${open[item.id] ? "Hide" : "Show"} study days for ${item.episode}`}
            onPress={() => setOpen((prior) => ({ ...prior, [item.id]: !prior[item.id] }))}
            style={styles.childRow}>
            {open[item.id]
              ? <ChevronDown size={13} color={colors.primary} />
              : <ChevronRight size={13} color={colors.primary} />}
            <Text style={[styles.value, styles.childActivity]}>{item.episode}</Text>
            <View style={styles.childRule}><Status value={item.status} compact /></View>
            <Text style={[styles.muted, styles.childRule]}>{item.stay}</Text>
          </Pressable>
          {open[item.id] && item.days.map((day) => (
            <View key={day.id} style={[styles.childRow, styles.confinementDay]}>
              <Text style={[styles.value, styles.childActivity]}>{day.label}</Text>
              <Text style={[styles.value, styles.childTime]}>{day.date}</Text>
              <Text style={[styles.muted, styles.childRule]}>{day.activities}</Text>
            </View>
          ))}
        </View>
      ))}
    </View>
  );
}

/** Doc 1 s27. The patient's visits in the order they happen. */
export function PatientTimeline({ entries }: { entries: TimelineEntry[] }) {
  if (!entries.length) return null;
  return (
    <View style={styles.childBlock}>
      {entries.map((item) => (
        <View key={item.id} style={styles.childRow}>
          <View style={[styles.timelineDot, item.past && styles.timelineDotPast]} />
          <Text style={[styles.value, styles.childTime]}>{item.date}</Text>
          <Text style={[styles.value, styles.childActivity]}>{item.visit}</Text>
          <View style={styles.childRule}><Status value={item.status} compact /></View>
          {!!item.detail && <Text style={[styles.muted, styles.childRule]}>{item.detail}</Text>}
        </View>
      ))}
    </View>
  );
}

export function QualifierPanel({ details }: { details: QualifierDetail[] }) {
  return (
    <View style={styles.childBlock}>
      <Text style={styles.childTitle}>Protocol notes</Text>
      {details.map((detail) => (
        <View key={detail.id} style={styles.qualifierItem}>
          <View style={styles.qualifierHead}>
            <Text style={styles.qualifierMarker}>{detail.marker}</Text>
            <Text style={styles.qualifierScope}>Applies to {detail.appliesTo}</Text>
            {!detail.resolved && <Text style={styles.reviewText}>Needs review</Text>}
          </View>
          <Text style={styles.value}>{detail.text}</Text>
        </View>
      ))}
    </View>
  );
}

/**
 * Conditional requirements live in their own compact table (UI spec 4.3). They are
 * deliberately NOT rows in the dated schedule: a condition that never occurs for
 * this patient must never look like a missed visit.
 */
export function ConditionalRequirementsTable({
  rows, onConfirm, title = "Conditional Requirements",
  subtitle = "These apply only if the condition occurs for this patient. Confirming one shows its schedule impact before anything changes.",
}: {
  rows: ConditionalRequirementRow[];
  onConfirm?: (row: ConditionalRequirementRow) => void;
  title?: string;
  subtitle?: string;
}) {
  if (!rows.length) return null;
  return (
    <View style={styles.conditionalBlock}>
      <Text style={styles.conditionalTitle}>{title}</Text>
      <Text style={styles.conditionalSubtitle}>{subtitle}</Text>
      {rows.map((row) => (
        <View key={row.id} style={styles.conditionalRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.name}>{row.condition}</Text>
            {/* Doc I8: a trial/site/country rule never moves a patient visit. */}
            {!!row.scopeBadge && (
              <View style={styles.scopeBadge}>
                <Text style={styles.scopeBadgeText}>{row.scopeBadge}</Text>
              </View>
            )}
            <Text style={styles.muted}>If this occurs: {row.action}</Text>
            <Text style={styles.muted}>{row.timing}</Text>
            {row.requiresReview && (
              <View style={styles.reviewLine}>
                <AlertCircle size={12} color={colors.warning} />
                <Text style={styles.reviewText}>Needs reviewer confirmation</Text>
              </View>
            )}
          </View>
          <View style={styles.conditionalActions}>
            <Status value={row.status} compact />
            {onConfirm && !row.trialLevel && (
              <Pressable
                onPress={() => onConfirm(row)}
                accessibilityRole="button"
                accessibilityLabel={`Record that ${row.condition} occurred`}
                style={styles.conditionalButton}>
                <Text style={styles.conditionalButtonText}>Condition occurred</Text>
              </Pressable>
            )}
          </View>
        </View>
      ))}
    </View>
  );
}

function MobileRow({ row, patient, onEvidence, expanded, onToggle }: { row: ProtocolScheduleRow | PatientScheduleRow; patient: boolean; onEvidence?: (row: ProtocolScheduleRow) => void; expanded: boolean; onToggle: () => void }) {
  const patientRow = row as PatientScheduleRow;
  return (
    <View style={styles.mobileCard} accessibilityLabel={`${row.visit} ${row.visitName}`}>
      <View style={styles.mobileHead}>
        <View style={styles.visitBadge}><Text style={styles.visitBadgeText}>{row.visit}</Text></View>
        <View style={{ flex: 1 }}>
          <Text style={styles.mobileName}>{row.visitName}</Text>
          <ContextLines row={row} />
          {/* Doc 10 s21-s23: always shown, on both the protocol and patient
              table - not patient-view-only, so a reviewer sees it too. Under
              the name now rather than its own field, same as desktop. */}
          <VisitModeValue value={row.visitMode} modeKey={row.visitModeKey} />
          {row.requiresReview && <Text style={styles.reviewText}>Review required</Text>}
        </View>
        {patient && <Status value={patientRow.status} compact />}
      </View>
      <View style={styles.mobileGrid}>
        <MobileField label={patient ? "Expected date" : "Timing"} value={patient ? patientRow.expectedDate : row.timing} note={patient ? patientRow.dateNote : null} />
        <MobileField label={patient ? "Allowed window" : "Window"} value={patient ? patientRow.allowedWindow : row.window} />
        <MobileField label="Type" value={row.type} />
        {!patient && <MobileField label="Applies to" value={row.appliesTo} />}
        {patient && <MobileField label="Actual date" value={patientRow.actualDate} />}
      </View>
      {patient && <FeasibilityLines row={patientRow} />}
      <Text style={styles.mobileLabel}>Activities</Text>
      <ActivityGroupsView activities={row.activities} empty="No activities listed" />
      {row.reviewReasons?.map((reason) => <Text key={reason} style={styles.reviewText}>{reason}</Text>)}
      {!!row.evidenceRefs.length && onEvidence && <EvidenceButton onPress={() => onEvidence(row)} />}
      {hasDetail(row) && (
        <Pressable
          onPress={onToggle}
          accessibilityRole="button"
          accessibilityLabel={`${expanded ? "Hide" : "Show"} details for ${row.visit}`}
          style={styles.evidence}>
          {expanded ? <ChevronDown size={13} color={colors.primary} /> : <ChevronRight size={13} color={colors.primary} />}
          <Text style={styles.evidenceText}>{expanded ? "Hide details" : "Show details"}</Text>
        </Pressable>
      )}
      {expanded && <DetailPanel row={row} />}
    </View>
  );
}

function Header({ label, width }: { label: string; width: number }) { return <View style={[styles.headerCell, { width }]}><Text style={styles.headerText}>{label}</Text></View>; }
function Cell({ width, children }: { width: number; children: React.ReactNode }) { return <View style={[styles.cell, { width }]}>{children}</View>; }
function MobileField({ label, value, note }: { label: string; value: string; note?: string | null }) {
  return (
    <View style={styles.mobileField}>
      <Text style={styles.mobileLabel}>{label}</Text>
      <Text style={styles.value}>{value}</Text>
      {!!note && <Text style={styles.reviewText}>{note}</Text>}
    </View>
  );
}
function EvidenceButton({ onPress }: { onPress: () => void }) { return <Pressable onPress={onPress} style={styles.evidence}><FileText size={13} color={colors.primary} /><Text style={styles.evidenceText}>View source</Text></Pressable>; }

/** Every activity named directly on the row, split Clinical/Administrative -
 * the same keyword classification the legacy operational editor already
 * uses (`classifyActivities`), so no separate expand is needed just to see
 * what a visit involves. */
function ActivityGroupsView({ activities, empty }: { activities: string[]; empty?: string }) {
  const { clinical, administrative } = classifyActivities(activities);
  if (!clinical.length && !administrative.length) {
    return <Text style={styles.muted}>{empty || "—"}</Text>;
  }
  return (
    <View style={styles.activityGroups}>
      {!!clinical.length && (
        <View>
          <Text style={styles.activityGroupLabel}>Clinical</Text>
          {clinical.map((activity) => (
            <View key={activity} style={styles.bulletLine}>
              <Text style={styles.bullet}>•</Text><Text style={styles.value}>{activity}</Text>
            </View>
          ))}
        </View>
      )}
      {!!administrative.length && (
        <View>
          <Text style={styles.activityGroupLabel}>Administrative</Text>
          {administrative.map((activity) => (
            <View key={activity} style={styles.bulletLine}>
              <Text style={styles.bullet}>•</Text><Text style={styles.value}>{activity}</Text>
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

/**
 * The user's own idea: a mode this compact reads faster as a symbol than as
 * a sentence, the same way a phone icon or a hospital cross already means
 * "call" or "site visit" everywhere else. One icon per mode key, so a
 * reviewer scanning a long schedule can tell a telephone contact from a
 * site visit without reading every cell - the text stays, right next to it,
 * for the mode this doesn't have an icon for yet or a screen reader.
 */
const MODE_ICONS: Record<string, LucideIcon> = {
  CLINIC: Building2,
  SITE_VISIT: Building2,
  TELEPHONE: Phone,
  PHONE_CONTACT: Phone,
  VIDEO: Video,
  HOME: Home,
  HOME_NURSE: Stethoscope,
  REMOTE: Globe,
  IMAGING_CENTRE: Scan,
  LABORATORY: FlaskConical,
  PHARMACY: Pill,
};

/** WP2: every permitted period separately, the named missing inputs, or the conflict. */
function FeasibilityLines({ row }: { row: PatientScheduleRow }) {
  const view = row.feasibility;
  const facts = row.factLines || [];
  if (!view && !facts.length) return null;
  return <View accessibilityLabel={view ? `Feasibility: ${view.headline}` : "Occurrence details"}>
    {facts.map((line) => <Text key={line} style={line.includes("outside the permitted") ? styles.reviewText : styles.modeText}>{line}</Text>)}
    {!!view && <>
    {view.status === "INFEASIBLE" && <Text style={styles.reviewText}>{view.headline}{view.conflicts.length ? ` — conflicting rules: ${view.conflicts.join(", ")}` : ""}</Text>}
    {view.status !== "INFEASIBLE" && view.intervals.map((line, index) => <Text key={index} style={styles.modeText}>
      {view.intervals.length > 1 ? `Period ${index + 1}: ` : "Permitted: "}{line}
    </Text>)}
    {view.missing.map((line) => <Text key={line} style={styles.reviewText}>{line}</Text>)}
    </>}
  </View>;
}

/** Doc F1-F5 / E9: "Cycle 3 Day 8", how its start was dated, or the follow-up phase. */
function ContextLines({ row }: { row: ProtocolScheduleRow | PatientScheduleRow }) {
  const lines = (row as PatientScheduleRow).contextLines;
  if (!lines?.length) return null;
  return <>{lines.map((line) => <Text key={line} style={styles.modeText}>{line}</Text>)}</>;
}

function VisitModeValue({ value, modeKey }: { value: string; modeKey?: string | null }) {
  // Nested under the visit name now, not its own column - nothing to show
  // is nothing to render, rather than a stray "—" cluttering the name cell.
  if (!value) return null;
  const unstated = value === UNSTATED_VISIT_MODE;
  if (unstated) {
    return (
      <View style={styles.reviewLine}>
        <AlertCircle size={12} color={colors.warning} />
        <Text style={styles.reviewText}>{value}</Text>
      </View>
    );
  }
  const Icon = modeKey ? MODE_ICONS[modeKey] : undefined;
  return (
    <View style={styles.modeLine}>
      {Icon && <Icon size={13} color={colors.mutedFg} />}
      <Text style={styles.modeText}>{value}</Text>
    </View>
  );
}

function Status({ value, compact = false }: { value: string; compact?: boolean }) {
  // Every WAITING_* state and a provisional date read amber - never the green
  // of a firm scheduled visit, and never anything that looks overdue.
  const pending = value.startsWith("Waiting") || value.startsWith("Awaiting")
    || value.includes("provisional") || value === "Review required";
  const inactive = value === "Not applicable";
  return <View style={[styles.status, compact && styles.statusCompact, pending && styles.statusPending, inactive && styles.statusInactive]}><Text style={[styles.statusText, pending && styles.statusPendingText, inactive && styles.statusInactiveText]}>{value}</Text></View>;
}

const styles = StyleSheet.create({
  confinementDay: { paddingLeft: 22 },
  timelineDot: {
    width: 8, height: 8, borderRadius: 4, marginRight: 8,
    backgroundColor: colors.border,
  },
  timelineDotPast: { backgroundColor: colors.primary },
  table: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, overflow: "hidden", backgroundColor: colors.card },
  headerRow: { flexDirection: "row", backgroundColor: colors.primaryDeep },
  headerCell: { minHeight: 48, paddingHorizontal: 12, justifyContent: "center", borderRightWidth: 1, borderRightColor: "rgba(255,255,255,0.12)" },
  headerText: { color: colors.white, fontFamily: fonts.semibold, fontSize: 12, letterSpacing: 0.2 },
  dataRow: { flexDirection: "row", minHeight: 78, backgroundColor: colors.card, borderTopWidth: 1, borderTopColor: colors.border },
  altRow: { backgroundColor: "#F3F5F9" },
  cell: { paddingHorizontal: 12, paddingVertical: 13, borderRightWidth: 1, borderRightColor: colors.border },
  visit: { fontFamily: fonts.bold, fontSize: 14, color: colors.primary },
  name: { fontFamily: fonts.semibold, fontSize: 14, color: colors.foreground },
  value: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 19, color: colors.foreground, flexShrink: 1 },
  muted: { fontFamily: fonts.regular, fontSize: 13, color: colors.mutedFg },
  bulletLine: { flexDirection: "row", alignItems: "flex-start", gap: 6 },
  bullet: { color: colors.primary, fontSize: 13, lineHeight: 19 },
  activityGroups: { gap: 8 },
  activityGroupLabel: {
    color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 10,
    textTransform: "uppercase", letterSpacing: 0.3, marginBottom: 2,
  },
  evidence: { flexDirection: "row", alignItems: "center", gap: 5, marginTop: 8, alignSelf: "flex-start", minHeight: 28 },
  evidenceText: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 12 },
  reviewLine: { flexDirection: "row", alignItems: "center", gap: 4, marginTop: 5 },
  modeLine: { flexDirection: "row", alignItems: "center", gap: 5, marginTop: 4 },
  modeText: { fontFamily: fonts.regular, fontSize: 12, color: colors.mutedFg },
  reviewText: { color: colors.warning, fontFamily: fonts.medium, fontSize: 11 },
  status: { alignSelf: "flex-start", paddingHorizontal: 9, paddingVertical: 6, borderRadius: radii.pill, backgroundColor: "#E7F4EA" },
  statusCompact: { maxWidth: 120 },
  statusText: { color: "#326A43", fontFamily: fonts.semibold, fontSize: 11 },
  statusPending: { backgroundColor: "#FDF3E1" },
  statusPendingText: { color: "#855416" },
  statusInactive: { backgroundColor: colors.surface },
  statusInactiveText: { color: colors.mutedFg },
  expandToggle: { flexDirection: "row", alignItems: "center", gap: 4, minHeight: 30 },
  detailPanel: { paddingHorizontal: 14, paddingVertical: 12, backgroundColor: colors.background, borderTopWidth: 1, borderTopColor: colors.border, gap: 14 },
  childBlock: { gap: 6 },
  childTitle: { color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 11, textTransform: "uppercase", letterSpacing: 0.4 },
  childHeader: { flexDirection: "row", gap: 10, paddingBottom: 5, borderBottomWidth: 1, borderBottomColor: colors.border },
  childHeaderText: { color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 11 },
  childRow: { flexDirection: "row", gap: 10, alignItems: "center", paddingVertical: 7 },
  childActivity: { width: 170 },
  childRule: { width: 210 },
  childTime: { width: 82 },
  childStatus: { width: 170 },
  qualifierItem: { paddingVertical: 6, gap: 3 },
  qualifierHead: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
  qualifierMarker: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 12 },
  qualifierScope: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 12 },
  conditionalBlock: { marginTop: spacing.lg, padding: spacing.md, borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, backgroundColor: colors.card, gap: 4 },
  conditionalTitle: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 16 },
  conditionalSubtitle: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 12, lineHeight: 18, marginBottom: 8 },
  conditionalRow: { flexDirection: "row", gap: 12, alignItems: "flex-start", paddingVertical: 11, borderTopWidth: 1, borderTopColor: colors.border },
  scopeBadge: { alignSelf: "flex-start", marginVertical: 4, paddingHorizontal: 8, paddingVertical: 3, borderRadius: radii.pill, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.border },
  scopeBadgeText: { color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 11 },
  conditionalActions: { alignItems: "flex-end", gap: 8 },
  conditionalButton: { paddingHorizontal: 11, paddingVertical: 8, borderRadius: radii.sm, backgroundColor: colors.secondary, minHeight: 34, justifyContent: "center" },
  conditionalButtonText: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 12 },
  mobileList: { gap: 12 },
  mobileCard: { padding: spacing.md, borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, backgroundColor: colors.card },
  mobileHead: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  visitBadge: { minWidth: 42, height: 34, paddingHorizontal: 8, borderRadius: radii.sm, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  visitBadgeText: { color: colors.primary, fontFamily: fonts.bold, fontSize: 13 },
  mobileName: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 16, lineHeight: 21 },
  mobileGrid: { marginTop: 14, flexDirection: "row", flexWrap: "wrap", gap: 10 },
  mobileField: { minWidth: "45%", flexGrow: 1, flexBasis: 130, padding: 10, borderRadius: radii.sm, backgroundColor: colors.background },
  mobileLabel: { color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 11, marginBottom: 4, marginTop: 10, textTransform: "uppercase", letterSpacing: 0.4 },
  empty: { padding: 28, alignItems: "center", borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, backgroundColor: colors.card },
  emptyTitle: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 16 },
  emptyText: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 13, textAlign: "center", marginTop: 5 },
});
