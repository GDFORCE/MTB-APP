import assert from "node:assert/strict";
import test from "node:test";

import {
  createAmendmentBinding, createAmendmentController, type AmendmentApi,
} from "../src/features/uctsm/amendmentController.ts";
import {
  buildPoint, consequences, decisionFor, describeBlocker, describeDecision, describeMapping,
  describePoint, describeVisit, failureOf, groupVisits, mappingActions, parseRules, pendingMappings,
  type MappingRow, type Simulation, type VisitImpact,
} from "../src/features/uctsm/amendments.ts";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
function deferred<T>() {
  let resolve!: (v: T) => void; let reject!: (e: any) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

// ------------------------------------------------------------------- the effective point
test("a point is built only from what the user explicitly chose", () => {
  assert.deepEqual(buildPoint({ mode: "ADOPT_IMMEDIATELY" }), { point: { mode: "ADOPT_IMMEDIATELY" } });
  assert.deepEqual(buildPoint({ mode: "REMAIN_ON_V1" }), { point: { mode: "REMAIN_ON_V1" } });
  assert.deepEqual(
    buildPoint({ mode: "ADOPT_AT_EVENT", eventCode: " C1D1 ", occurrenceIndex: "2" }),
    { point: { mode: "ADOPT_AT_EVENT", event_code: "C1D1", occurrence_index: 2 } });
  assert.deepEqual(
    buildPoint({ mode: "ADOPT_AT_CYCLE", blockCode: "TREATMENT", cycleNumber: "3" }),
    { point: { mode: "ADOPT_AT_CYCLE", repeat_block_code: "TREATMENT", cycle_number: 3 } });
  assert.deepEqual(buildPoint({ mode: "ADOPT_ON_DATE", onDate: "2027-03-01" }),
    { point: { mode: "ADOPT_ON_DATE", on_date: "2027-03-01" } });
});

test("an incomplete or impossible point is explained, not sent", () => {
  for (const form of [
    { mode: "ADOPT_AT_EVENT" as const, eventCode: "", occurrenceIndex: "0" },
    { mode: "ADOPT_AT_EVENT" as const, eventCode: "C1D1", occurrenceIndex: "-1" },
    { mode: "ADOPT_AT_EVENT" as const, eventCode: "C1D1", occurrenceIndex: "x" },
    { mode: "ADOPT_AT_CYCLE" as const, blockCode: "T", cycleNumber: "0" },
    { mode: "ADOPT_AT_CYCLE" as const, blockCode: "", cycleNumber: "1" },
    { mode: "ADOPT_ON_DATE" as const, onDate: "01/03/2027" },
    { mode: "ADOPT_ON_DATE" as const, onDate: "2027-02-30" },
    { mode: "ADOPT_ON_DATE" as const, onDate: "" },
    { mode: "WHENEVER" as any },
  ]) {
    const built = buildPoint(form);
    assert.ok("error" in built && built.error.length > 10, JSON.stringify(form));
  }
});

test("staying is a REMAIN, anything else an ADOPT, and a return is a RETURN", () => {
  assert.equal(decisionFor("REMAIN_ON_V1"), "REMAIN");
  assert.equal(decisionFor("ADOPT_ON_DATE"), "ADOPT");
  assert.equal(decisionFor("ADOPT_IMMEDIATELY", "RETURN"), "RETURN");
  assert.equal(describePoint({ mode: "ADOPT_AT_CYCLE", repeat_block_code: "TX", cycle_number: 3 }),
    "Move at the actual start of cycle 3 of TX");
  assert.equal(describePoint({ mode: "ADOPT_AT_EVENT", event_code: "C1D1", occurrence_index: 0 }),
    "Move when C1D1:0 is performed");
});

// ------------------------------------------------------------------------ simulation view
const visit = (over: Partial<VisitImpact>): VisitImpact => ({
  from_key: "A:0", to_key: "A:0", category: "UNCHANGED", old_target: "2026-01-01", new_target: "2026-01-01",
  old_status: "RESOLVED", new_status: "RESOLVED", protected: false, reason: "", ...over });

test("visits are grouped for a decision, with removed and protected kept apart", () => {
  const groups = groupVisits([
    visit({ category: "UNCHANGED" }), visit({ from_key: "B:0", to_key: "B:0", category: "MOVED" }),
    visit({ from_key: "C:0", to_key: "C:0", category: "PROTECTED" }),
    visit({ from_key: null, to_key: "N:0", category: "ADDED" }),
    visit({ from_key: "R:0", to_key: null, category: "REMOVED" }),
    visit({ from_key: "W:0", to_key: "W:0", category: "WAITING" }),
    visit({ from_key: "X:0", to_key: "X:0", category: "BLOCKED" })]);
  assert.deepEqual(groups.map((group) => group.id),
    ["changed", "added", "protected", "waiting", "blocked", "removed", "unchanged"]);
  assert.ok(groups.every((group) => group.rows.length === 1));
});

test("a renamed visit shows both identities and what moved", () => {
  const view = describeVisit(visit({
    from_key: "IMAGING_BASELINE:0", to_key: "IMAGING_BL:0", category: "MOVED",
    old_target: "2026-01-31", new_target: "2026-02-03", old_earliest: "2026-01-28", old_latest: "2026-02-03",
    new_earliest: "2026-01-31", new_latest: "2026-02-06", reason: "the new version dates this visit differently" }));
  assert.equal(view.title, "IMAGING_BASELINE:0 → IMAGING_BL:0");
  assert.ok(view.lines.includes("Target: 2026-01-31 → 2026-02-03"));
  assert.ok(view.lines.some((line) => line.startsWith("Window: 2026-01-28 to 2026-02-03 → 2026-01-31 to 2026-02-06")));
});

test("the consequences are stated plainly before anyone confirms", () => {
  const lines = consequences({
    summary: { MOVED: 2, ADDED: 1, PROTECTED: 3, REMOVED: 1, UNCHANGED: 4 }, carried_fact_count: 3,
    retained_in_earlier_version: [{ logical_key: "R:0" }],
    warnings: [{ code: "VERSION_UNVERIFIED_LEGACY" }] } as any);
  assert.ok(lines.includes("2 visit(s) get a different date or window"));
  assert.ok(lines.includes("3 recorded visit(s) are kept exactly as they happened"));
  assert.ok(lines.some((line) => line.includes("originals stay on the current version")));
  assert.ok(lines.some((line) => line.includes("stay in the current version's history")));
  assert.ok(lines.includes("A version was approved before content checks existed"));
});

// ------------------------------------------------------------------------------ blockers
test("every blocker the server can raise is explained and tells you what to do", () => {
  const codes = [
    "MAPPING_UNREVIEWED", "MAPPING_RULES_MISSING", "MAPPING_SOURCE_COVERAGE", "MAPPING_TARGET_COVERAGE",
    "MAPPING_REVIEW_NOT_RECORDED", "PAIR_NOT_PREPARED", "PAIR_STALE", "VERSION_UNVERIFIED_LEGACY",
    "FACT_UNMAPPED", "CARRY_CONFLICT", "ACTIVE_COMMANDS_NOT_CARRIED", "ACTIVITY_RECORDS_NOT_CARRIED",
    "UNSCHEDULED_NOT_CARRIED", "ASSIGNMENT_NOT_IN_TARGET", "TARGET_HAS_HISTORY", "NOT_YET_EFFECTIVE",
    "DATE_NOT_FUTURE", "POINT_EVENT_UNKNOWN", "POINT_OCCURRENCE_MISSING", "POINT_CYCLE_UNKNOWN",
    "POINT_CYCLE_MISSING", "NOTHING_TO_RETURN_FROM", "OPEN_DECISION_EXISTS", "SIMULATION_EXPIRED",
    "STALE_INPUT", "IMPACT_CHANGED", "INPUT_CHANGED", "PATIENT_MOVED"];
  for (const code of codes) {
    const view = describeBlocker({ code });
    assert.ok(view.title.length > 5 && !/_/.test(view.title), code);
    assert.ok(view.action.length > 5 && view.action !== "Resolve this before continuing.", code);
  }
  assert.equal(describeBlocker({ code: "SOMETHING_NEW" }).title, "something new");
});

test("a refusal is translated by kind and never shown raw", () => {
  const blocked = failureOf({ response: { status: 409, data: { detail: {
    code: "AMENDMENT_BLOCKED", message: "x", blockers: [{ code: "MAPPING_UNREVIEWED" }] } } } });
  assert.equal(blocked.kind, "blocked");
  assert.equal(blocked.blockers.length, 1);
  assert.match(blocked.message, /Nothing was changed/);
  assert.equal(failureOf({ response: { status: 403 } }).kind, "forbidden");
  assert.equal(failureOf({ response: { status: 404 } }).kind, "absent");
  assert.equal(failureOf({ response: { status: 409, data: { detail: { code: "CONFLICT" } } } }).kind, "conflict");
  assert.equal(failureOf({ response: { status: 409, data: { detail: { code: "AMENDMENT_INTEGRITY" } } } }).kind, "integrity");
  assert.equal(failureOf({ response: { status: 409, data: { detail: { code: "PLANE_NOT_AUTHORITATIVE" } } } }).kind, "authority");
  assert.equal(failureOf({ response: { status: 422, data: { detail: "bad" } } }).message, "bad");
  assert.equal(failureOf({}).kind, "unavailable");
  assert.equal(failureOf({ response: { status: 503 } }).kind, "unavailable");
});

// ------------------------------------------------------------------------------- mapping
const row = (over: Partial<MappingRow>): MappingRow => ({
  id: "m1", mapping_type: "RENAMED", source_codes: ["A"], target_codes: ["B"], review_status: "PROPOSED",
  confidence: 0.8, provenance: "AUTO_NAME_MATCH", rationale: "", occurrence_rules: [], ...over });

test("only the actions that make sense for a mapping are offered", () => {
  assert.deepEqual(mappingActions(row({})), ["CONFIRM", "REJECT"]);
  assert.deepEqual(mappingActions(row({ mapping_type: "SPLIT", target_codes: ["B", "C"] })), ["SET_RULES", "REJECT"]);
  assert.deepEqual(
    mappingActions(row({ mapping_type: "SPLIT", target_codes: ["B", "C"], occurrence_rules: [
      { source_code: "A", source_index: 0, target_code: "B", target_index: 0 }] })),
    ["CONFIRM", "SET_RULES", "REJECT"]);
  assert.deepEqual(mappingActions(row({ review_status: "CONFIRMED" })), ["REJECT"]);
  assert.deepEqual(mappingActions(row({ review_status: "REJECTED" })), []);
  assert.equal(describeMapping(row({ mapping_type: "MERGED", source_codes: ["A", "B"], target_codes: ["C"] })),
    "Merged into one: A + B → C");
  assert.equal(pendingMappings({ mappings: [row({}), row({ review_status: "CONFIRMED" })] }).length, 1);
});

test("occurrence rules are parsed strictly", () => {
  assert.deepEqual(parseRules("WEEK_4:0>WEEK_4A:0, WEEK_4:1 > WEEK_4B:0"), { rules: [
    { source_code: "WEEK_4", source_index: 0, target_code: "WEEK_4A", target_index: 0 },
    { source_code: "WEEK_4", source_index: 1, target_code: "WEEK_4B", target_index: 0 }] });
  for (const bad of ["", "A>B", "A:0>B", "A:x>B:0", "A:0->B:0"]) {
    assert.ok("error" in parseRules(bad), bad);
  }
});

test("a decision is described with its outcome", () => {
  assert.equal(describeDecision({ decision_type: "ADOPT", status: "WAITING", mode: "ADOPT_ON_DATE",
    adoption_point: { mode: "ADOPT_ON_DATE", on_date: "2027-03-01" } }), "Move on 2027-03-01 · Waiting for its point");
  assert.equal(describeDecision({ decision_type: "REMAIN", status: "REMAINING", mode: "REMAIN_ON_V1" }),
    "Stay on the current version · Staying on the current version");
  assert.equal(describeDecision({ decision_type: "RETURN", status: "APPLIED", mode: "ADOPT_IMMEDIATELY" }),
    "Return to the earlier version · Applied");
});

// ------------------------------------------------------------------------- the controller
const SIM = (over: Partial<Simulation> = {}): Simulation => ({
  simulation_id: "s1", expires_at: "x", can_confirm: true, direction: "ADOPT", summary: {}, visits: [],
  blockers: [], warnings: [], carried_fact_count: 0, retained_in_earlier_version: [],
  adoption_point: { mode: "ADOPT_IMMEDIATELY" },
  from: { schedule_version_id: "v1", version_number: 1, status: "APPROVED", effective_from: null, approved_at: null, content_hash: "a", verification: "VERIFIED" },
  to: { schedule_version_id: "v2", version_number: 2, status: "APPROVED", effective_from: null, approved_at: null, content_hash: "b", verification: "VERIFIED" },
  ...over });

function fakeApi(over: Partial<AmendmentApi> = {}): AmendmentApi {
  return {
    status: async () => ({ current_version: { version_number: 1 }, available: [{ schedule_version_id: "v2" }],
      decisions: [], open_decision: null, can_return: false }),
    simulate: async () => SIM(),
    confirm: async () => ({ status: "APPLIED", replayed: false }),
    execute: async () => ({ status: "WAITING" }),
    cancel: async () => ({ status: "CANCELLED" }),
    ...over };
}
const A = { trialId: "T", externalPatientId: "A" };
const B = { trialId: "T", externalPatientId: "B" };
function make(api: AmendmentApi) {
  const controller = createAmendmentController(api, () => {});
  return controller;
}

test("a delayed status for patient A never lands on patient B", async () => {
  const slow = deferred<any>();
  const controller = make(fakeApi({
    status: (trial, patient) => patient === "A" ? slow.promise
      : Promise.resolve({ current_version: { version_number: 7 }, available: [], decisions: [] }) }));
  controller.setIdentity(A);
  controller.setIdentity(B);
  await tick();
  assert.equal(controller.snapshot.status.current_version.version_number, 7);
  slow.resolve({ current_version: { version_number: 1 }, available: [], decisions: [] });
  await tick();
  assert.equal(controller.snapshot.status.current_version.version_number, 7);
});

test("forbidden, absent and unreachable are different states", async () => {
  for (const [status, phase] of [[403, "forbidden"], [404, "absent"], [503, "unavailable"]] as const) {
    const controller = make(fakeApi({ status: async () => { throw { response: { status } }; } }));
    controller.setIdentity(A);
    await tick();
    assert.equal(controller.snapshot.phase, phase, String(status));
  }
});

test("a preview with blockers cannot be confirmed and says why", async () => {
  let confirmed = 0;
  const controller = make(fakeApi({
    simulate: async () => SIM({ can_confirm: false, blockers: [{ code: "MAPPING_UNREVIEWED" }] }),
    confirm: async () => { confirmed += 1; return {}; } }));
  controller.setIdentity(A);
  await tick();
  await controller.simulate({ adoption_point: { mode: "ADOPT_IMMEDIATELY" } });
  assert.equal(controller.snapshot.blockers[0].code, "MAPPING_UNREVIEWED");
  await controller.confirm({ decision: "ADOPT", reason: "go" });
  assert.equal(confirmed, 0);
});

test("one idempotency key per simulation, so a retry replays", async () => {
  const keys: string[] = [];
  let attempt = 0;
  const controller = make(fakeApi({
    confirm: async (_t, _p, sim, _body, key) => {
      keys.push(key);
      attempt += 1;
      if (attempt === 1) throw { response: { status: 503 } };      // the response was lost
      return { status: "APPLIED", replayed: true };
    } }));
  controller.setIdentity(A);
  await tick();
  await controller.simulate({});
  await controller.confirm({ decision: "ADOPT", reason: "go" });
  assert.ok(controller.snapshot.simulation, "an unknown outcome keeps the preview");
  await controller.confirm({ decision: "ADOPT", reason: "go" });
  assert.deepEqual(keys, ["amend-s1", "amend-s1"]);
  assert.equal(controller.snapshot.notice, "Already recorded - nothing was repeated.");
  assert.equal(controller.snapshot.simulation, null);
});

test("a refusal that invalidates the preview drops it and lists what to resolve", async () => {
  const controller = make(fakeApi({
    confirm: async () => { throw { response: { status: 409, data: { detail: {
      code: "AMENDMENT_BLOCKED", message: "stale", blockers: [{ code: "STALE_INPUT", path: "context_fingerprint" }] } } } }; } }));
  controller.setIdentity(A);
  await tick();
  await controller.simulate({});
  await controller.confirm({ decision: "ADOPT", reason: "go" });
  assert.equal(controller.snapshot.simulation, null);
  assert.equal(controller.snapshot.blockers[0].code, "STALE_INPUT");
  assert.match(controller.snapshot.error, /Nothing was changed/);
});

test("a commit followed by a failed reload is reported as saved and blocks a second change", async () => {
  let calls = 0;
  const controller = make(fakeApi({
    status: async () => { calls += 1; if (calls > 1) throw { response: { status: 503 } }; return { available: [], decisions: [] }; } }));
  controller.setIdentity(A);
  await tick();
  await controller.simulate({});
  await controller.confirm({ decision: "ADOPT", reason: "go" });
  assert.equal(controller.snapshot.committedNeedsRefresh, true);
  assert.match(controller.snapshot.notice, /saved/);
  let simulated = 0;
  const original = controller.snapshot;
  await controller.simulate({});
  assert.equal(controller.snapshot, original);                         // refused while stale
  void simulated;
});

test("switching patient discards the preview and the in-flight confirmation result", async () => {
  const slow = deferred<any>();
  const controller = make(fakeApi({ confirm: () => slow.promise }));
  controller.setIdentity(A);
  await tick();
  await controller.simulate({});
  const pending = controller.confirm({ decision: "ADOPT", reason: "go" });
  controller.setIdentity(B);
  await tick();
  assert.equal(controller.snapshot.simulation, null);
  slow.resolve({ status: "APPLIED" });
  await pending;
  assert.equal(controller.snapshot.notice, "");                        // A's result never lands on B
});

test("the binding survives setup -> cleanup -> setup and ignores actions once unmounted", async () => {
  const binding = createAmendmentBinding(fakeApi());
  binding.setIdentity(A);
  const seen: string[] = [];
  const stop = binding.mount((snapshot) => seen.push(snapshot.phase));
  await tick();
  stop();
  const stopAgain = binding.mount((snapshot) => seen.push(snapshot.phase));
  await tick();
  assert.ok(binding.live);
  assert.equal(binding.live!.snapshot.phase, "ready");
  stopAgain();
  assert.equal(binding.live, null);
  await binding.simulate({});                                          // a no-op, not a crash
});

test("executing a decision that is not due says so and changes nothing", async () => {
  const controller = make(fakeApi());
  controller.setIdentity(A);
  await tick();
  await controller.execute("d1");
  assert.equal(controller.snapshot.notice, "Not due yet - nothing was changed.");
  await controller.cancel("d1", "no longer needed");
  assert.equal(controller.snapshot.notice, "Cancelled.");
});
