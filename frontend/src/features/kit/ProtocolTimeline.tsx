import React, { useMemo } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { Pencil } from "lucide-react-native";
import { fonts } from "@/src/theme/tokens";
import { computeKitSchedule, condText, type KitModel } from "./index";
import { FOCUS as G } from "./focus";
import {
  eventSummaries, protocolTimeline, spanText, type Dot, type TimelineSection, type Tone,
} from "./sections";

// Any fixed date works: the protocol view only shows each visit's offset from the start.
const START = "2000-01-01";

export type ProtocolTimelineData = ReturnType<typeof protocolTimeline> & {
  events: ReturnType<typeof eventSummaries>; startLabel: string; title: string;
};

/** The protocol's schedule laid out for a stand-in patient who has answered nothing. */
export function useProtocolTimeline(model: KitModel | null | undefined, siteAnswers?: Record<string, string>) {
  return useMemo<ProtocolTimelineData | null>(() => {
    if (!model) return null;
    const start = model.anchors.find((a) => a.kind === "start");
    if (!start) return null;
    try {
      const schedule = computeKitSchedule(model, siteAnswers || {}, { startAnchor: start.id, startDate: START, answers: {} });
      const conditionOf = (visitId: string) => {
        const def = model.visits.find((v) => v.id === visitId);
        if (!def?.include_if?.length) return null;
        return String(condText(model, def.include_if)).replace(/= true\b/gi, "= Yes").replace(/= false\b/gi, "= No");
      };
      const timeline = protocolTimeline(model, schedule, START, conditionOf);
      const total = timeline.visitCount;
      return {
        ...timeline, events: eventSummaries(model), startLabel: start.label,
        title: `${total} visit${total === 1 ? "" : "s"}${timeline.spanDays ? ` over ${spanText(timeline.spanDays)}` : ""}`,
      };
    } catch {
      return null;
    }
  }, [model, siteAnswers]);
}

const TONE: Record<Tone, { bg: string; fg: string }> = {
  green: { bg: G.tag, fg: G.tagInk }, amber: { bg: G.amberBg, fg: G.amberInk },
  violet: { bg: G.violetBg, fg: G.violetInk }, red: { bg: G.redBg, fg: G.redInk },
};

function DotMark({ dot }: { dot: Dot }) {
  return <View style={DOTS[dot]} />;
}

function Section({ section, onRename }: { section: TimelineSection; onRename?: (section: TimelineSection) => void }) {
  return <View style={s.section}>
    <View style={s.sectionHead}>
      <Pressable disabled={!onRename} onPress={() => onRename?.(section)} accessibilityRole={onRename ? "button" : undefined}
        accessibilityLabel={onRename ? `Rename section ${section.label}` : undefined} hitSlop={8} style={s.sectionTitleRow}>
        <Text style={s.sectionTitle}>{section.label}</Text>
        {onRename && <Pencil size={14} color={G.muted} />}
      </Pressable>
      <Text style={s.range}>{section.range}</Text>
    </View>
    {section.rows.map((row, index) => {
      const last = index === section.rows.length - 1;
      return <View key={row.key} style={s.row}>
        <Text style={[s.when, row.dot === "wait" && { color: G.amberText }, row.dot === "decision" && { color: G.violetInk }]}>{row.when}</Text>
        <View style={s.railCol}>
          <DotMark dot={row.dot} />
          {!last && <View style={row.dot === "wait" ? s.railWait : s.rail} />}
        </View>
        <View style={s.content}>
          <Text style={s.name}>{row.name}</Text>
          <Text style={s.meta}>{row.meta}</Text>
          {!!row.tags.length && <View style={s.tags}>
            {row.tags.map((tag) => <Text key={tag.text} style={[s.tag, { backgroundColor: TONE[tag.tone].bg, color: TONE[tag.tone].fg }]}>{tag.text}</Text>)}
          </View>}
          {!!row.timepoints.length && <View style={s.tags}>
            {row.timepoints.map((tp, i) => <Text key={`${tp}-${i}`} style={s.timepoint}>{tp}</Text>)}
          </View>}
        </View>
      </View>;
    })}
  </View>;
}

/** Phase sections on one timeline, then the events that change a patient's schedule. */
export function ProtocolTimeline({ data, onRename }: { data: ProtocolTimelineData; onRename?: (section: TimelineSection) => void }) {
  return <View>
    {data.sections.map((section) => <Section key={section.key} section={section} onRename={onRename} />)}
    {!!data.events.length && <View style={s.section}>
      <Text style={[s.sectionTitle, s.eventsTitle]}>If something happens</Text>
      {data.events.map((event) => <View key={event.id} style={s.event}>
        <Text style={s.eventLabel}>{event.label}</Text>
        <Text style={[s.tag, { backgroundColor: TONE[event.tone].bg, color: TONE[event.tone].fg }]}>{event.effect}</Text>
      </View>)}
    </View>}
  </View>;
}

const DOTS = StyleSheet.create({
  site: { width: 12, height: 12, borderRadius: 6, backgroundColor: G.accent, marginTop: 4 },
  remote: { width: 12, height: 12, borderRadius: 6, borderWidth: 2, borderColor: G.accent, backgroundColor: "#FFFFFF", marginTop: 4 },
  decision: { width: 11, height: 11, backgroundColor: G.violet, transform: [{ rotate: "45deg" }], marginTop: 5 },
  repeat: { width: 14, height: 14, borderRadius: 4, backgroundColor: G.accent, marginTop: 3 },
  wait: { width: 12, height: 12, borderRadius: 6, borderWidth: 2, borderStyle: "dashed", borderColor: "#B07A1E", backgroundColor: "#FFFFFF", marginTop: 4 },
});

const s = StyleSheet.create({
  section: { paddingTop: 26 },
  sectionHead: { flexDirection: "row", alignItems: "flex-end", justifyContent: "space-between", gap: 12, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: G.track, marginBottom: 14 },
  sectionTitleRow: { flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 1 },
  sectionTitle: { color: G.ink, fontFamily: fonts.semibold, fontSize: 18 },
  eventsTitle: { paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: G.track },
  range: { color: G.muted, fontFamily: fonts.regular, fontSize: 12, textAlign: "right", flexShrink: 1 },
  row: { flexDirection: "row", gap: 12 },
  when: { width: 52, textAlign: "right", paddingTop: 3, color: G.muted, fontFamily: fonts.semibold, fontSize: 12 },
  railCol: { width: 14, alignItems: "center" },
  rail: { flex: 1, width: 2, backgroundColor: G.line, marginTop: 4 },
  railWait: { flex: 1, width: 0, borderLeftWidth: 2, borderStyle: "dashed", borderColor: G.amberLine, marginTop: 4 },
  content: { flex: 1, minWidth: 0, paddingBottom: 14 },
  name: { color: G.ink, fontFamily: fonts.semibold, fontSize: 15, lineHeight: 20 },
  meta: { color: G.muted, fontFamily: fonts.regular, fontSize: 12, lineHeight: 17, marginTop: 2 },
  tags: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 6 },
  tag: { fontFamily: fonts.medium, fontSize: 11, borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3, overflow: "hidden" },
  timepoint: { fontFamily: fonts.medium, fontSize: 11, color: G.ink, borderWidth: 1, borderColor: G.line, borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2 },
  event: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: G.softLine },
  eventLabel: { flex: 1, color: G.ink, fontFamily: fonts.medium, fontSize: 14 },
});
