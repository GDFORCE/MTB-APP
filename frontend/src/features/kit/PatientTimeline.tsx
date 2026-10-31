import React, { useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { fonts } from "@/src/theme/tokens";
import { computeKitSchedule, condText, todayIso, type KitDecisionRow, type KitModel, type KitParticipant, type KitVisitRow } from "./index";
import { FOCUS as G } from "./focus";
import { patientTimeline, type PDot, type PTone, type PatientRow } from "./patientSections";

export type PatientTimelineData = ReturnType<typeof patientTimeline>;

/** The patient's schedule, plus the same schedule without events and without recorded visits to show what changed. */
export function usePatientTimeline(model: KitModel | null | undefined, siteAnswers: Record<string, string> | undefined, participant: KitParticipant | null | undefined) {
  return useMemo<PatientTimelineData | null>(() => {
    if (!model || !participant) return null;
    try {
      const site = siteAnswers || {};
      const current = computeKitSchedule(model, site, participant);
      const withoutEvents = computeKitSchedule(model, site, { ...participant, events: [] });
      const baseline = computeKitSchedule(model, site, { ...participant, events: [], actuals: {}, checks: {} });
      const conditionOf = (visitId: string) => {
        const def = model.visits.find((v) => v.id === visitId);
        if (!def?.include_if?.length) return null;
        return String(condText(model, def.include_if)).replace(/= true\b/gi, "= Yes").replace(/= false\b/gi, "= No");
      };
      return patientTimeline({ model, participant, current, withoutEvents, baseline, today: todayIso(), conditionOf });
    } catch {
      return null;
    }
  }, [model, siteAnswers, participant]);
}

const TONE: Record<PTone, { bg: string; fg: string }> = {
  green: { bg: G.tag, fg: G.tagInk }, amber: { bg: G.amberBg, fg: G.amberInk }, violet: { bg: G.violetBg, fg: G.violetInk },
  red: { bg: G.redBg, fg: G.redInk }, grey: { bg: "#EEF1EE", fg: "#3F4A44" },
};
const DAY_COLOR: Partial<Record<PDot, string>> = {
  done: G.accent, overdue: G.redInk, wait: G.amberText, decision: G.violetInk, event: G.redInk, struck: "#8C9A92",
};

type Actions = {
  onPressVisit?: (row: KitVisitRow) => void;
  onAnswer?: (row: KitDecisionRow, value: string) => void;
  onPressDecision?: (row: KitDecisionRow) => void;
  onSetAnchor?: (anchorId: string) => void;
  onPressEvent?: (eventKey: string) => void;
};

function Row({ row, last, actions }: { row: PatientRow; last: boolean; actions: Actions }) {
  const press = row.kind === "visit" && row.visit && actions.onPressVisit ? () => actions.onPressVisit!(row.visit!)
    : row.kind === "decision" && row.decision?.answer && actions.onPressDecision ? () => actions.onPressDecision!(row.decision!)
    : row.kind === "event" && row.eventKey && actions.onPressEvent ? () => actions.onPressEvent!(row.eventKey!)
    : undefined;
  const openDecision = row.kind === "decision" && !row.decision?.answer;
  return <Pressable disabled={!press} onPress={press} accessibilityRole={press ? "button" : undefined}
    style={[s.row, row.dot === "next" && s.rowNext]}>
    <View style={s.dateCol}>
      {!!row.year && <Text style={s.year}>{row.year}</Text>}
      <Text style={[s.day, { color: DAY_COLOR[row.dot] || G.ink }]}>{row.day}</Text>
      {!!row.mon && <Text style={s.mon}>{row.mon}</Text>}
    </View>
    <View style={s.railCol}>
      <View style={DOTS[row.dot]} />
      {!last && <View style={row.dot === "wait" ? s.railWait : row.dot === "done" ? s.railDone : s.rail} />}
    </View>
    <View style={s.content}>
      {openDecision ? <View style={s.decisionCard}>
        <Text style={s.decisionEyebrow}>Decision{row.decision?.at ? ` · at ${row.decision.at}` : ""}</Text>
        <Text style={s.decisionText}>{row.name}</Text>
        {!!row.decision?.help && <Text style={s.meta}>{row.decision.help}</Text>}
        {actions.onAnswer && row.decision!.kind === "number" && actions.onPressDecision
          ? <View style={s.answers}>
              <Pressable accessibilityRole="button" style={s.answer} onPress={() => actions.onPressDecision!(row.decision!)}>
                <Text style={s.answerText}>Enter value</Text>
              </Pressable>
            </View>
          : actions.onAnswer
          ? <View style={s.answers}>
              {row.decision!.options.map((o) => <Pressable key={o.value} accessibilityRole="button" style={s.answer}
                onPress={() => actions.onAnswer!(row.decision!, o.value)}>
                <Text style={s.answerText}>{o.label}</Text>
              </Pressable>)}
            </View>
          : <Text style={[s.meta, { marginTop: 6 }]}>The study team answers this.</Text>}
      </View> : <>
        <Text style={[s.name, row.kind === "cancelled" && s.struck]}>{row.name}</Text>
        <Text style={[s.meta, row.dot === "overdue" && { color: G.redInk }]}>{row.meta}</Text>
        {!!row.tags.length && <View style={s.tags}>
          {row.tags.map((tag) => <Text key={tag.text} style={[s.tag, { backgroundColor: TONE[tag.tone].bg, color: TONE[tag.tone].fg }]}>{tag.text}</Text>)}
        </View>}
      </>}
    </View>
  </Pressable>;
}

/**
 * A patient's visits on one green timeline, grouped by phase. Leave the actions out for a read-only
 * view; `maxRows` shows only the first rows with a "Show all" toggle.
 */
export function PatientTimeline({ data, maxRows, ...actions }: { data: PatientTimelineData; maxRows?: number } & Actions) {
  const [all, setAll] = useState(false);
  const shownCta = new Set<string>();
  const total = data.sections.reduce((n, sec) => n + sec.rows.length, 0);
  let budget = maxRows && !all ? maxRows : Infinity;
  const sections = data.sections.map((sec) => {
    const rows = sec.rows.slice(0, Math.max(0, budget));
    budget -= rows.length;
    return { ...sec, rows };
  }).filter((sec) => sec.rows.length);
  return <View>
    {sections.map((section) => <View key={section.key} style={s.section}>
      <View style={s.sectionHead}>
        <Text style={s.sectionTitle}>{section.label}</Text>
        <Text style={s.range}>{section.range}</Text>
      </View>
      {section.rows.map((row, index) => {
        const anchor = row.waitingAnchor && !shownCta.has(row.waitingAnchor)
          ? data.pendingAnchors.find((a) => a.id === row.waitingAnchor) : undefined;
        if (anchor) shownCta.add(anchor.id);
        return <React.Fragment key={row.key}>
          {anchor && <View style={s.ctaRow}>
            <View style={s.dateCol}><Text style={[s.day, { color: G.amberText }]}>?</Text></View>
            <View style={s.railCol}><View style={DOTS.anchor} /><View style={s.railWait} /></View>
            <View style={[s.content, { paddingBottom: 16 }]}>
              <View style={s.cta}>
                <Text style={s.ctaTitle}>{anchor.label} · date not set</Text>
                <Text style={s.ctaText}>{anchor.waiting} visit{anchor.waiting === 1 ? " waits" : "s wait"} for it.</Text>
                {actions.onSetAnchor
                  ? <Pressable accessibilityRole="button" style={s.ctaButton} onPress={() => actions.onSetAnchor!(anchor.id)}>
                      <Text style={s.ctaButtonText}>Set date</Text>
                    </Pressable>
                  : <Text style={[s.ctaText, { marginTop: 4 }]}>The study team adds it when it is known.</Text>}
              </View>
            </View>
          </View>}
          <Row row={row} last={index === section.rows.length - 1} actions={actions} />
        </React.Fragment>;
      })}
    </View>)}
    {!!maxRows && total > maxRows && <Pressable accessibilityRole="button" onPress={() => setAll((v) => !v)} style={s.more}>
      <Text style={s.moreText}>{all ? "Show less" : `Show all ${total}`}</Text>
    </Pressable>}
  </View>;
}

const DOTS = StyleSheet.create({
  done: { width: 12, height: 12, borderRadius: 6, backgroundColor: G.accent, marginTop: 5 },
  next: { width: 14, height: 14, borderRadius: 7, borderWidth: 3, borderColor: G.accent, backgroundColor: "#FFFFFF", marginTop: 4 },
  todo: { width: 12, height: 12, borderRadius: 6, borderWidth: 2, borderColor: "#8C9A92", backgroundColor: "#FFFFFF", marginTop: 5 },
  overdue: { width: 12, height: 12, borderRadius: 6, borderWidth: 2, borderColor: G.redInk, backgroundColor: G.redBg, marginTop: 5 },
  wait: { width: 12, height: 12, borderRadius: 6, borderWidth: 2, borderStyle: "dashed", borderColor: "#B07A1E", backgroundColor: "#FFFFFF", marginTop: 5 },
  decision: { width: 11, height: 11, backgroundColor: G.violet, transform: [{ rotate: "45deg" }], marginTop: 6 },
  event: { width: 11, height: 11, backgroundColor: "#B4432A", transform: [{ rotate: "45deg" }], marginTop: 6 },
  struck: { width: 12, height: 12, borderRadius: 6, backgroundColor: "#C3CCC6", marginTop: 5 },
  anchor: { width: 14, height: 14, borderRadius: 3, borderWidth: 2, borderStyle: "dashed", borderColor: "#B07A1E", marginTop: 4 },
});

const s = StyleSheet.create({
  more: { alignItems: "center", paddingVertical: 12 },
  moreText: { color: G.accent, fontFamily: fonts.semibold, fontSize: 14 },
  section: { paddingTop: 24 },
  sectionHead: { flexDirection: "row", alignItems: "flex-end", justifyContent: "space-between", gap: 12, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: G.track, marginBottom: 14 },
  sectionTitle: { color: G.ink, fontFamily: fonts.semibold, fontSize: 18, flexShrink: 1 },
  range: { color: G.muted, fontFamily: fonts.regular, fontSize: 12, textAlign: "right", flexShrink: 1 },
  row: { flexDirection: "row", gap: 12 },
  rowNext: { backgroundColor: G.selected, borderRadius: 12, marginHorizontal: -10, paddingHorizontal: 10, paddingTop: 8 },
  ctaRow: { flexDirection: "row", gap: 12 },
  dateCol: { width: 40, alignItems: "flex-end" },
  year: { color: G.accent, fontFamily: fonts.bold, fontSize: 10, letterSpacing: 0.5 },
  day: { fontFamily: fonts.bold, fontSize: 17, lineHeight: 20 },
  mon: { color: G.muted, fontFamily: fonts.semibold, fontSize: 10, textTransform: "uppercase" },
  railCol: { width: 14, alignItems: "center" },
  rail: { flex: 1, width: 2, backgroundColor: G.line, marginTop: 4 },
  railDone: { flex: 1, width: 2, backgroundColor: G.accent, marginTop: 4 },
  railWait: { flex: 1, width: 0, borderLeftWidth: 2, borderStyle: "dashed", borderColor: G.amberLine, marginTop: 4 },
  content: { flex: 1, minWidth: 0, paddingBottom: 14 },
  name: { color: G.ink, fontFamily: fonts.semibold, fontSize: 15, lineHeight: 20 },
  struck: { color: G.muted, textDecorationLine: "line-through" },
  meta: { color: G.muted, fontFamily: fonts.regular, fontSize: 12, lineHeight: 17, marginTop: 2 },
  tags: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 6 },
  tag: { fontFamily: fonts.medium, fontSize: 11, borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3, overflow: "hidden" },
  decisionCard: { borderWidth: 1.5, borderColor: "#B9AEE6", backgroundColor: "#FBFAFF", borderRadius: 12, padding: 12 },
  decisionEyebrow: { color: G.violetInk, fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 1.2, textTransform: "uppercase" },
  decisionText: { color: G.ink, fontFamily: fonts.semibold, fontSize: 16, lineHeight: 22, marginTop: 4 },
  answers: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 10 },
  answer: { flexGrow: 1, minWidth: 90, minHeight: 44, borderRadius: 10, borderWidth: 1, borderColor: G.violet, alignItems: "center", justifyContent: "center", paddingHorizontal: 12, backgroundColor: "#FFFFFF" },
  answerText: { color: G.violetInk, fontFamily: fonts.semibold, fontSize: 15 },
  cta: { borderWidth: 1, borderStyle: "dashed", borderColor: G.amberLine, backgroundColor: "#FDF7EA", borderRadius: 12, padding: 12 },
  ctaTitle: { color: "#5A3C00", fontFamily: fonts.semibold, fontSize: 15 },
  ctaText: { color: G.amberInk, fontFamily: fonts.regular, fontSize: 12, lineHeight: 17, marginTop: 2 },
  ctaButton: { alignSelf: "flex-start", marginTop: 10, minHeight: 40, paddingHorizontal: 16, borderRadius: 10, backgroundColor: G.accent, justifyContent: "center" },
  ctaButtonText: { color: "#FFFFFF", fontFamily: fonts.semibold, fontSize: 14 },
});
