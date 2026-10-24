import assert from "node:assert/strict";
import test from "node:test";

import {
  eventSummaries, joinWhen, phaseKey, protocolTimeline, relLabel, renamePhase, spanText, windowText,
} from "../src/features/kit/sections.ts";

const START = "2000-01-01";
const at = (days: number) => new Date(Date.parse(START + "T00:00:00Z") + days * 864e5).toISOString().slice(0, 10);

function visit(id: string, code: string, phase: string, days: number | null, over: Record<string, unknown> = {}) {
  return {
    type: "visit", key: `${id}#1`, visitId: id, code, name: code, phase, modality: "site", status: days == null ? "waiting" : "scheduled",
    date: days == null ? null : at(days), relative: days == null ? "Surgery + 30 d" : null, windowDays: [3, 3],
    studyDay: null, dependsOnDecision: false, until: null, procedures: [{ name: "Vitals" }], timepoints: [], ...over,
  };
}
const def = (id: string, repeat: Record<string, unknown> | null = null) => ({ id, name: `Def ${id}`, repeat, duration_days: null });

test("time and window labels", () => {
  assert.equal(relLabel(0), "Day 0");
  assert.equal(relLabel(-28), "Day −28");
  assert.equal(relLabel(15), "Day 15");
  assert.equal(relLabel(90), "3 mo");
  assert.equal(relLabel(5110), "14 y");
  assert.equal(spanText(5110), "14 years");
  assert.equal(windowText([0, 0]), "exact day");
  assert.equal(windowText([7, 7]), "±7 days");
  assert.equal(windowText([0, 3]), "−0 / +3 days");
  assert.equal(joinWhen("11 y", "14 y"), "11–14 y");
  assert.equal(joinWhen("Day 0", "8 mo"), "Day 0 – 8 mo");
});

test("only case, spaces and hyphens make two labels the same phase", () => {
  assert.equal(phaseKey("Follow-up"), phaseKey("follow up"));
  assert.equal(phaseKey("Treatment"), phaseKey(" treatment "));
  assert.notEqual(phaseKey("Treatment"), phaseKey("Treatment period"));
});

test("the untidy example: matching neighbours join, different words and later repeats stay apart", () => {
  const rows = [
    visit("a", "SCR", "Screening", -14), visit("b", "BL", "Visit", 0), visit("c", "W2", "Treatment", 14),
    visit("d", "W4", "treatment", 28), visit("e", "W8", "Treatment period", 56), visit("f", "EOT", "Follow-up", 84),
    visit("g", "SC", "Follow up", 114), visit("h", "UNS", "Treatment", 119),
  ];
  const model = { visits: rows.map((r) => def(r.visitId)), anchors: [], events: [] } as any;
  const { sections } = protocolTimeline(model, { rows } as any, START, () => null);
  assert.deepEqual(sections.map((s) => [s.label, s.rows.length]), [
    ["Screening", 1], ["Visit", 1], ["Treatment", 2], ["Treatment period", 1], ["Follow-up", 2], ["Treatment", 1],
  ]);
  assert.equal(sections[2].range, "Day 14 – 1 mo · 2 visits");
});

test("renaming a section to its neighbour's name joins them", () => {
  const rows = [visit("c", "W2", "Treatment", 14), visit("e", "W8", "Treatment period", 56)];
  const model = { visits: [{ ...def("c"), phase: "Treatment" }, { ...def("e"), phase: "Treatment period" }], anchors: [], events: [] } as any;
  const renamed = renamePhase(model, ["e"], "Treatment");
  assert.deepEqual(renamed.visits.map((v: any) => v.phase), ["Treatment", "Treatment"]);
  const relabelled = rows.map((r) => ({ ...r, phase: renamed.visits.find((v: any) => v.id === r.visitId).phase }));
  assert.equal(protocolTimeline(renamed, { rows: relabelled } as any, START, () => null).sections.length, 1);
});

test("a repeating visit shows once with its count; waiting and conditional visits are marked", () => {
  const rows = [
    visit("y", "V11", "Follow-up", 4015), visit("y", "V12", "Follow-up", 4380), visit("y", "V13", "Follow-up", 4745), visit("y", "V14", "Follow-up", 5110),
    visit("o1", "OGTT-D1", "Diagnosis", null), visit("o2", "OGTT-D2", "Diagnosis", null, { dependsOnDecision: true }),
  ].map((r, i) => ({ ...r, key: `${r.visitId}#${i}` }));
  const model = { visits: [def("y", { every_days: 365, open_ended: false }), def("o1"), def("o2")], anchors: [], events: [] } as any;
  const t = protocolTimeline(model, { rows } as any, START, (id) => (id === "o2" ? "First OGTT = Yes" : null));
  const [follow, dx] = t.sections;
  assert.equal(follow.rows.length, 1);
  assert.equal(follow.rows[0].when, "11–14 y");
  assert.equal(follow.rows[0].name, "V11–V14 · Def y");
  const coded = protocolTimeline({ ...model, visits: [{ ...def("y", { every_days: 365 }), code: "V21-24" }, def("o1"), def("o2")] },
    { rows } as any, START, () => null);
  assert.equal(coded.sections[0].rows[0].name, "V21-24 · Def y");
  assert.equal(follow.rows[0].dot, "repeat");
  assert.ok(follow.rows[0].tags.some((tag) => tag.text === "Repeats ×4"));
  assert.equal(follow.range, "11–14 y · 4 visits");
  assert.equal(dx.range, "Waits for a date · 2 visits");
  assert.equal(dx.rows[0].dot, "wait");
  assert.ok(dx.rows[1].tags.some((tag) => tag.text === "Only if First OGTT = Yes"));
  assert.equal(t.visitCount, 4);
  assert.equal(t.waitingCount, 2);
  assert.equal(spanText(t.spanDays), "14 years");
});

test("event effects", () => {
  const model = { anchors: [{ id: "pd", label: "Progression" }], events: [
    { id: "w", label: "Withdrawal", ends_schedule: true, visits: [] },
    { id: "s", label: "Suspected diabetes", ends_schedule: false, visits: [{}, {}] },
    { id: "p", label: "Progression", ends_schedule: false, visits: [], sets_anchor: "pd" },
  ] } as any;
  assert.deepEqual(eventSummaries(model).map((e) => e.effect), ["Ends schedule", "Adds 2 visits", "Sets Progression"]);
});
