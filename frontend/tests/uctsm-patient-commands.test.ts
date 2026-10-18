import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCommandPreview, commandErrorMessage, describeRow, orderForConfirmation,
  type CommandForm, type ImpactRow,
} from "../src/features/uctsm/patientCommands.ts";

const base: CommandForm = { type: "RECORD_ACTUAL", effectiveAt: "2026-01-15", reason: "given late" };

test("a recorded actual states its dates and never carries an actor or permission", () => {
  const built = buildCommandPreview({ ...base, logicalKey: "C1D1:0", visitDate: "2026-01-08",
    treatmentDate: "2026-01-15" }, "2026-12-31");
  assert.ok("body" in built);
  const body = (built as any).body;
  assert.equal(body.type, "RECORD_ACTUAL");
  assert.equal(body.treatment_date, "2026-01-15");
  for (const forbidden of ["actor_id", "grants", "capability"]) assert.ok(!(forbidden in body));
});

test("missing pieces are reported in plain words, one at a time", () => {
  assert.match((buildCommandPreview({ ...base, reason: " " }) as any).error, /Why this is being done/);
  assert.match((buildCommandPreview({ ...base, logicalKey: "C1D1:0" }) as any).error, /visit date, the treatment date/);
  assert.match((buildCommandPreview({ type: "OVERRIDE_OCCURRENCE", logicalKey: "I:0", targetDate: "2026-02-20",
    effectiveAt: "2026-01-12", reason: "r" }) as any).error, /Who authorised/);
  assert.match((buildCommandPreview({ type: "REBASE_TRACK", trackCode: "T", anchorCode: "B", newValue: "soon",
    effectiveAt: "2026-01-12", reason: "r" }) as any).error, /YYYY-MM-DD/);
  assert.match((buildCommandPreview({ type: "REVERSE", effectiveAt: "2026-01-12", reason: "r" }) as any).error,
    /command to reverse/);
});

const row = (over: Partial<ImpactRow>): ImpactRow => ({
  logical_key: "I:0", event_code: "I", changed: false, change_kind: "NONE",
  protocol: { old_target: "2026-01-31", new_target: "2026-01-31",
    old_window: { earliest: null, latest: null }, new_window: { earliest: "2026-01-28", latest: "2026-02-03" } },
  status: { old: "RESOLVED", new: "RESOLVED" }, operational: { before: {}, after: {} },
  track: { code: "IMAGING", display_name: "Imaging" }, reason: "unchanged", ...over,
});

test("protocol target, appointment and actual are three separate values", () => {
  const view = describeRow(row({ changed: true, change_kind: "OPERATIONAL", operational: { before: {},
    after: { appointment: { date: "2026-03-25", outside_protocol_window: true }, actual: { visit: "2026-03-26" } } } }));
  assert.equal(view.protocolTarget, "2026-01-31");
  assert.equal(view.appointment, "2026-03-25");
  assert.equal(view.actual, "2026-03-26");
  assert.deepEqual(view.flags, ["Appointment is outside the protocol window"]);
  assert.equal(view.protocolWas, null);                 // a booking never reads as a moved target
});

test("changed visits come first and unaffected ones are still listed", () => {
  const ordered = orderForConfirmation([row({ event_code: "A" }), row({ event_code: "B", changed: true })]);
  assert.deepEqual(ordered.map((item) => item.event_code), ["B", "A"]);
});

test("errors read in plain language and a stale preview says to preview again", () => {
  assert.match(commandErrorMessage(409, { code: "PROPOSAL_STALE", message: "x" }), /Preview again/);
  assert.match(commandErrorMessage(403, "no"), /not allowed/);
  assert.match(commandErrorMessage(0, null), /Nothing was changed/);
});

import { buildTracksRequest, trackDraftsFromView } from "../src/features/uctsm/trackReview.ts";

const draft = (over: any = {}) => ({ code: "IMAGING", trackType: "IMAGING", displayName: "Imaging",
  anchorCode: "BASELINE", rebasePolicy: "FIXED_FROM_ANCHOR", processCode: "", stoppable: "" as const,
  confirmed: true, ...over });

test("track review never confirms a track whose policy was not chosen", () => {
  const refused = buildTracksRequest("rev", "protocol s4", [draft({ rebasePolicy: "" })], [], {});
  assert.match((refused as any).error, /state what may move/);
  assert.match((buildTracksRequest("rev", " ", [draft()], [], {}) as any).error, /Say why/);
});

test("membership must name a track that exists and links connect two real tracks", () => {
  assert.match((buildTracksRequest("rev", "r", [draft()], [], { DAY_1: { scope: "TRACK", trackCode: "NOPE" } }) as any).error,
    /does not exist/);
  assert.match((buildTracksRequest("rev", "r", [draft()], [{ source: "IMAGING", target: "LABS",
    relation: "FOLLOWS_ACTUAL", confirmed: false }], {}) as any).error, /two different existing tracks/);
});

test("a valid review builds the typed body and round-trips from the view", () => {
  const built: any = buildTracksRequest("rev1", "protocol s4", [draft()], [], {
    DAY_1: { scope: "TRACK", trackCode: "IMAGING" }, SCREENING: { scope: "GLOBAL" } });
  assert.equal(built.body.expected_revision, "rev1");
  assert.equal(built.body.tracks[0].rebase_policy, "FIXED_FROM_ANCHOR");
  assert.equal(built.body.tracks[0].review_state, "CONFIRMED");
  assert.deepEqual(built.body.membership.SCREENING, { scope: "GLOBAL", track_code: null });
  const again = trackDraftsFromView([{ code: "IMAGING", track_type: "IMAGING", display_name: "Imaging",
    anchor_code: "BASELINE", rebase_policy: "FIXED_FROM_ANCHOR", review_state: "CONFIRMED", stoppable: null }]);
  assert.equal(again[0].confirmed, true);
  assert.equal(again[0].stoppable, "");
});

import { PLANE_MESSAGES, classifyProbeError, commandEligibility, planeConsistency } from "../src/features/uctsm/patientCommands.ts";

test("only a 404 means the patient is absent; 403 and service failures are told apart", () => {
  assert.equal(classifyProbeError(404), "absent");
  assert.equal(classifyProbeError(403), "denied");
  assert.equal(classifyProbeError(401), "denied");
  assert.equal(classifyProbeError(503), "unavailable");
  assert.equal(classifyProbeError(undefined), "unavailable");
  assert.notEqual(PLANE_MESSAGES.denied, PLANE_MESSAGES.absent);
});

test("commands are enabled only when the plane and the screen show the same schedule version", () => {
  assert.equal(planeConsistency("v1", "v1"), "ready");
  assert.equal(planeConsistency("v1", "v2"), "mismatch");
  assert.equal(planeConsistency(null, "v2"), "mismatch");
  assert.equal(planeConsistency("v1", null), "mismatch");
});

test("a screen that reads the main store never enables commands, even on equal versions", () => {
  assert.equal(commandEligibility("SQL", "v1", "v1"), "mixed");
  assert.equal(commandEligibility("MONGO", "v1", "v1"), "ready");
  assert.equal(commandEligibility("MONGO", "v1", "v2"), "mismatch");
  assert.match(PLANE_MESSAGES.mixed, /switched off/);
});
