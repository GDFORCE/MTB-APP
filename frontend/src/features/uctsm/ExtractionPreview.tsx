import React, { useState } from "react";
import { Modal, Pressable, ScrollView, Text, View } from "react-native";
import { Body, Button, Card, Small } from "@/src/components/ui";
import { colors, spacing } from "@/src/theme/tokens";
import { SimpleScheduleTable } from "./SimpleScheduleTable";
import { toProtocolScheduleRows } from "./presentation";
import type { ScheduleProjection, UniversalSchedule, ValidationIssue } from "./types";

export type ExtractionPreviewData = {
  schedule: UniversalSchedule | null;
  projections: ScheduleProjection[];
  issues: ValidationIssue[];
};

export function ExtractionPreview({ previews }: { previews: ExtractionPreviewData[] }) {
  const [evidence, setEvidence] = useState<string | null>(null);
  const [showIssues, setShowIssues] = useState<Record<number, boolean>>({});
  return <View style={{ gap: spacing.md }}>
    <Body weight="700">Visit schedule</Body>
    <Small>Tap a visit to see activities, notes and protocol source.</Small>
    {previews.map((preview, index) => <View key={preview.schedule?.schedule_version_id || index} style={{ gap: 12 }}>
      {previews.length > 1 && <Body weight="700">{preview.schedule?.schedule_metadata.name || `Schedule ${index + 1}`}</Body>}
      <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 12 }}>
        <Small color={colors.primary}>AI extracted · {preview.schedule?.events.length || 0} visits</Small>
        {!!preview.issues.length && <Pressable accessibilityRole="button" accessibilityState={{ expanded: !!showIssues[index] }}
          onPress={() => setShowIssues((current) => ({ ...current, [index]: !current[index] }))} style={{ paddingVertical: 10 }}>
          <Small color="#87591F">{showIssues[index] ? "Hide" : "Show"} {preview.issues.length} review notes (optional, won't block saving)</Small>
        </Pressable>}
      </View>
      {showIssues[index] && <Card>{preview.issues.map((issue) => <Text key={issue.id} style={{ color: colors.foreground, marginVertical: 4 }}>
        {issue.blocking ? "Review before approval: " : "Note: "}{issue.message}
      </Text>)}</Card>}
      {!preview.schedule && <Small color={colors.destructive}>This schedule could not be extracted. Open the items above, then try the upload again.</Small>}
      {preview.schedule && <SimpleScheduleTable
        rows={toProtocolScheduleRows(preview.schedule, preview.projections)}
        onEvidence={(row) => setEvidence(preview.schedule!.evidence
          .filter((item) => row.evidenceRefs.includes(item.id))
          .map((item) => `Page ${item.page_number || "not recorded"}${item.table_title ? ` · ${item.table_title}` : ""}\n${item.source_text || "No source passage recorded"}`)
          .join("\n\n"))}
      />}
    </View>)}
    <Modal visible={evidence !== null} transparent onRequestClose={() => setEvidence(null)}>
      <View style={{ flex: 1, padding: 24, justifyContent: "center", backgroundColor: "rgba(0,0,0,0.4)" }}>
        <Card style={{ maxHeight: "80%" }}><Body weight="700">Protocol evidence</Body>
          <ScrollView><Text style={{ color: colors.foreground, paddingVertical: 16 }}>{evidence}</Text></ScrollView>
          <Button onPress={() => setEvidence(null)}>Close</Button>
        </Card>
      </View>
    </Modal>
  </View>;
}
