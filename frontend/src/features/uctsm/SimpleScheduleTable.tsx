import React, { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { AlertCircle, ChevronDown, ChevronRight, FileText } from "lucide-react-native";
import { colors, fonts, radii } from "@/src/theme/tokens";
import { classifyActivities, compactScheduleWindow, type ProtocolScheduleRow } from "./presentation";
import { QualifierPanel } from "./ScheduleTable";

/** A phone-sized table; full protocol meaning is retained in expandable rows. */
export function SimpleScheduleTable({ rows, onEvidence }: {
  rows: ProtocolScheduleRow[];
  onEvidence?: (row: ProtocolScheduleRow) => void;
}) {
  const sections = [
    { label: "Scheduled visits", rows: rows.filter((row) => !row.isConditional && !row.repeatRule) },
    { label: "Repeating visits", rows: rows.filter((row) => !row.isConditional && !!row.repeatRule) },
    { label: "Visits when a condition is met", rows: rows.filter((row) => row.isConditional) },
  ].filter((section) => section.rows.length);
  if (!sections.length) return <VisitRows rows={rows} onEvidence={onEvidence} />;
  return <View style={{ gap: 16 }}>{sections.map((section) => <View key={section.label} style={{ gap: 8 }}>
    {(sections.length > 1 || section.label !== "Scheduled visits") && <Text style={styles.name}>{section.label}</Text>}
    <VisitRows rows={section.rows} onEvidence={onEvidence} />
  </View>)}</View>;
}

function VisitRows({ rows, onEvidence }: {
  rows: ProtocolScheduleRow[];
  onEvidence?: (row: ProtocolScheduleRow) => void;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  if (!rows.length) return <View style={styles.empty}><Text style={styles.value}>No visits were extracted. Try uploading the protocol again.</Text></View>;
  return <View style={styles.table} accessibilityLabel="Visit schedule table">
    <View style={styles.header}>
      <Text style={[styles.heading, styles.nameColumn]}>Visit</Text>
      <Text style={[styles.heading, styles.timingColumn]}>Timing</Text>
      <Text style={[styles.heading, styles.windowColumn]}>Window</Text>
    </View>
    {rows.map((row, index) => {
      const open = !!expanded[row.id];
      const classified = classifyActivities(row.activities);
      // Prefer the extracted activity classification when it is available.
      for (const activity of row.activityRows || []) {
        if (activity.category === "Administrative") {
          classified.clinical = classified.clinical.filter((name) => name !== activity.activity);
          if (!classified.administrative.includes(activity.activity)) classified.administrative.push(activity.activity);
        }
      }
      const groups = [
        { category: "Clinical", items: classified.clinical },
        { category: "Administrative", items: classified.administrative },
      ].filter((group) => group.items.length);
      return <React.Fragment key={row.id}>
        <Pressable
          testID={`schedule-visit-${row.id}`}
          accessibilityRole="button"
          accessibilityState={{ expanded: open }}
          accessibilityLabel={`${row.visitName}. ${row.timing}. Window: ${row.window}. ${open ? "Hide" : "Show"} details`}
          onPress={() => setExpanded((current) => ({ ...current, [row.id]: !current[row.id] }))}
          style={({ pressed }) => [styles.row, index % 2 === 1 && styles.alternate, pressed && styles.pressed]}>
          <View style={[styles.nameColumn, styles.nameCell]}>
            {open ? <ChevronDown size={16} color={colors.primary} /> : <ChevronRight size={16} color={colors.mutedFg} />}
            <View style={styles.flex}>
              <Text style={styles.name}>{row.visitName}</Text>
              {row.visit !== row.visitName && <Text style={styles.code}>{row.visit}</Text>}
              <Text style={row.activities.length ? styles.code : styles.noActivities}>
                {row.activities.length ? `${row.activities.length} activit${row.activities.length === 1 ? "y" : "ies"}` : "No activities extracted"}
              </Text>
              {row.requiresReview && <View style={styles.check}><AlertCircle size={11} color={colors.warning} /><Text style={styles.checkText}>Check details</Text></View>}
            </View>
          </View>
          <Text style={[styles.value, styles.timingColumn]}>{row.timing}</Text>
          <Text style={[styles.value, styles.windowColumn]}>{row.window === "—" ? "Not stated" : compactScheduleWindow(row.window)}</Text>
        </Pressable>
        {open && <View testID={`schedule-details-${row.id}`} style={styles.details}>
          <View style={styles.facts}>
            <Detail label="Visit type" value={row.type} />
            <Detail label="How to attend" value={row.visitMode} />
            <Detail label="Applies to" value={row.appliesTo} />
            <Detail label="Status" value={row.status} />
          </View>
          {!!row.repeatRule && <Detail label="Repeats" value={row.repeatRule} />}
          {!!row.sourceTiming?.length && <View style={styles.reasons}>
            <Text style={styles.label}>Extracted protocol wording</Text>
            {row.sourceTiming.map((statement, statementIndex) => <View key={statementIndex} style={{ gap: 3 }}>
              <Text style={styles.value}>{statement.text}</Text>
              {!!statement.page && <Text style={styles.muted}>Page {statement.page}</Text>}
            </View>)}
            <Text style={styles.checkText}>The timing rule needs clarification before dates can be calculated.</Text>
          </View>}
          <Text style={styles.label}>Activities</Text>
          {groups.length ? groups.map((group) => <View key={group.category} style={styles.group}>
            <Text style={styles.category}>{group.category}</Text>
            {group.items.map((name, activityIndex) => {
              const activity = row.activityRows?.find((item) => item.activity === name);
              return <View key={`${name}-${activityIndex}`} style={styles.activity}>
                <Text style={styles.activityName}>{name}</Text>
                {!!activity && <>
                  <Text style={styles.muted}>{activity.requiredness}</Text>
                  {activity.timingRule !== "—" && <Text style={styles.value}>
                    {activity.timingRule}{activity.anchor !== "—" ? ` · ${activity.anchor}` : ""}
                  </Text>}
                  {activity.window !== "—" && <Text style={styles.muted}>Window: {activity.window}</Text>}
                  {!!activity.review && !["—", "OK"].includes(activity.review) && <Text style={styles.checkText}>{activity.review}</Text>}
                </>}
              </View>;
            })}
          </View>) : <Text style={styles.muted}>No activities extracted.</Text>}
          {!!row.qualifierDetails.length && <QualifierPanel details={row.qualifierDetails} />}
          {!!row.reviewReasons?.length && <View style={styles.reasons}>
            <Text style={styles.label}>Items to check</Text>
            {row.reviewReasons.map((reason) => <Text key={reason} style={styles.value}>{reason}</Text>)}
          </View>}
          {!!onEvidence && <Pressable onPress={() => onEvidence(row)} accessibilityRole="button" style={styles.source}>
            <FileText size={15} color={colors.primary} /><Text style={styles.sourceText}>View protocol source</Text>
          </Pressable>}
        </View>}
      </React.Fragment>;
    })}
  </View>;
}

function Detail({ label, value }: { label: string; value: string }) {
  return <View style={styles.fact}><Text style={styles.label}>{label}</Text><Text style={styles.value}>{value}</Text></View>;
}

const styles = StyleSheet.create({
  table: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.lg, overflow: "hidden", backgroundColor: colors.card },
  header: { flexDirection: "row", backgroundColor: colors.surface, paddingHorizontal: 10, paddingVertical: 14, gap: 10 },
  heading: { fontFamily: fonts.semibold, color: colors.foreground, fontSize: 13 },
  nameColumn: { flex: 1.55, minWidth: 0 },
  timingColumn: { flex: 1, minWidth: 0 },
  windowColumn: { flex: 0.65, minWidth: 0 },
  row: { flexDirection: "row", alignItems: "flex-start", gap: 10, paddingHorizontal: 10, paddingVertical: 17, minHeight: 68, borderTopWidth: 1, borderTopColor: colors.border },
  nameCell: { flexDirection: "row", alignItems: "flex-start", gap: 5 },
  name: { color: colors.foreground, fontFamily: fonts.semibold, fontSize: 14, lineHeight: 20 },
  value: { color: colors.foreground, fontFamily: fonts.regular, fontSize: 13, lineHeight: 19 },
  code: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 11, marginTop: 3 },
  noActivities: { color: colors.warning, fontFamily: fonts.regular, fontSize: 11, marginTop: 3 },
  flex: { flex: 1 },
  check: { flexDirection: "row", gap: 4, alignItems: "center", marginTop: 4 },
  checkText: { color: "#87591F", fontFamily: fonts.regular, fontSize: 11, lineHeight: 16 },
  alternate: { backgroundColor: "#FCF5EA" },
  pressed: { backgroundColor: colors.surface },
  details: { padding: 16, gap: 10, backgroundColor: "#FFF9EF", borderTopWidth: 1, borderTopColor: colors.border },
  facts: { flexDirection: "row", flexWrap: "wrap", gap: 14 },
  fact: { flexGrow: 1, flexBasis: "42%", gap: 3 },
  label: { color: colors.mutedFg, fontFamily: fonts.semibold, fontSize: 12 },
  group: { gap: 3 },
  category: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 11 },
  muted: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 12, lineHeight: 18 },
  activity: { paddingTop: 8, borderTopWidth: 1, borderTopColor: colors.border, gap: 2 },
  activityName: { fontFamily: fonts.medium, fontSize: 13, color: colors.foreground },
  reasons: { gap: 6, padding: 10, backgroundColor: "#FFF0D6", borderRadius: radii.sm },
  source: { flexDirection: "row", alignItems: "center", gap: 6, paddingVertical: 12, minHeight: 44 },
  sourceText: { color: colors.primary, fontFamily: fonts.semibold, fontSize: 13 },
  empty: { padding: 20, borderRadius: radii.lg, backgroundColor: colors.card },
});
