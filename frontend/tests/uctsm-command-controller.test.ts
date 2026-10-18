import assert from "node:assert/strict";
import test from "node:test";

import { createCommandController, type CommandApi, type Identity } from "../src/features/uctsm/commandController.ts";

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void; reject: (e: any) => void };
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void; let reject!: (e: any) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

const A: Identity = { trialId: "T", externalPatientId: "A", scheduleVersionId: "v1", displayPlane: "MONGO" };
const B: Identity = { trialId: "T", externalPatientId: "B", scheduleVersionId: "v1", displayPlane: "MONGO" };

function fakeApi(over: Partial<CommandApi> = {}): CommandApi {
  return {
    calendar: async () => ({ schedule_version_id: "v1", calendar: [{ logical_key: "X:0" }] }),
    history: async () => [],
    preview: async () => ({ proposal_id: "p1", changes: [], summary: { changed: 0, unchanged: 0 } }),
    confirm: async () => ({ replayed: false }),
    cancel: async () => ({}),
    ...over,
  };
}

function make(api: CommandApi) {
  const seen: any[] = [];
  const controller = createCommandController(api, (snapshot) => seen.push(snapshot));
  return { controller, seen };
}

test("a delayed response for patient A never lands on patient B", async () => {
  const slow = deferred<any>();
  const api = fakeApi({
    calendar: (trial, patient) => patient === "A" ? slow.promise
      : Promise.resolve({ schedule_version_id: "v1", calendar: [{ logical_key: "B:0" }] }),
  });
  const { controller } = make(api);
  controller.setIdentity(A);
  controller.setIdentity(B);                   // switch while A is still in flight
  await tick();
  assert.equal(controller.snapshot.state, "ready");
  assert.deepEqual(controller.snapshot.calendar, [{ logical_key: "B:0" }]);
  slow.resolve({ schedule_version_id: "v1", calendar: [{ logical_key: "A:0" }] });
  await tick();
  assert.deepEqual(controller.snapshot.calendar, [{ logical_key: "B:0" }]);   // A's data dropped
});

test("a version change resets the preview and the form-bound state", async () => {
  const { controller } = make(fakeApi());
  controller.setIdentity(A);
  await tick();
  await controller.preview({ type: "MARK_MISSED" });
  assert.ok(controller.snapshot.preview);
  controller.setIdentity({ ...A, scheduleVersionId: "v2" });
  assert.equal(controller.snapshot.preview, null);
  await tick();
  assert.equal(controller.snapshot.state, "mismatch");            // plane is on v1, screen on v2
});

test("an obsolete preview or confirm result is ignored after switching patients", async () => {
  const slowPreview = deferred<any>();
  const api = fakeApi({ preview: () => slowPreview.promise });
  const { controller } = make(api);
  controller.setIdentity(A);
  await tick();
  const pending = controller.preview({ type: "MARK_MISSED" });
  controller.setIdentity(B);
  await tick();
  slowPreview.resolve({ proposal_id: "OLD", changes: [] });
  await pending;
  assert.equal(controller.snapshot.preview, null);               // A's preview never shown on B

  const slowConfirm = deferred<any>();
  const api2 = fakeApi({ confirm: () => slowConfirm.promise });
  const second = make(api2);
  second.controller.setIdentity(A);
  await tick();
  await second.controller.preview({ type: "MARK_MISSED" });
  const confirming = second.controller.confirm();
  second.controller.setIdentity(B);
  slowConfirm.resolve({ replayed: false });
  await confirming;
  await tick();
  assert.equal(second.controller.snapshot.notice, "");           // not announced on B
});

test("unmounting stops every later update", async () => {
  const slow = deferred<any>();
  const { controller, seen } = make(fakeApi({ calendar: () => slow.promise }));
  controller.setIdentity(A);
  const before = seen.length;
  controller.dispose();
  slow.resolve({ schedule_version_id: "v1", calendar: [] });
  await tick();
  assert.equal(seen.length, before);
});

test("absent, denied and unavailable are told apart and retry recovers", async () => {
  for (const [status, expected] of [[404, "absent"], [403, "denied"], [503, "unavailable"]] as const) {
    const { controller } = make(fakeApi({ calendar: async () => { throw { response: { status } }; } }));
    controller.setIdentity(A);
    await tick();
    assert.equal(controller.snapshot.state, expected);
  }
  let failing = true;
  const { controller } = make(fakeApi({
    calendar: async () => {
      if (failing) throw { response: { status: 503 } };
      return { schedule_version_id: "v1", calendar: [] };
    } }));
  controller.setIdentity(A);
  await tick();
  assert.equal(controller.snapshot.state, "unavailable");
  failing = false;
  await controller.retry();
  assert.equal(controller.snapshot.state, "ready");
});

test("a screen that reads the main store never enables commands", async () => {
  const { controller } = make(fakeApi());
  controller.setIdentity({ ...A, displayPlane: "SQL" });
  await tick();
  assert.equal(controller.snapshot.state, "mixed");
  await controller.preview({ type: "MARK_MISSED" });
  assert.equal(controller.snapshot.preview, null);
});

test("a commit followed by a failed reload is reported as saved and blocks a second command", async () => {
  let reloads = 0;
  const api = fakeApi({
    calendar: async () => {
      reloads += 1;
      if (reloads >= 2) throw { response: { status: 503 } };
      return { schedule_version_id: "v1", calendar: [] };
    } });
  const { controller } = make(api);
  controller.setIdentity(A);
  await tick();
  await controller.preview({ type: "MARK_MISSED" });
  await controller.confirm();
  assert.equal(controller.snapshot.committedNeedsRefresh, true);
  assert.match(controller.snapshot.notice, /saved/);
  assert.equal(controller.snapshot.preview, null);
  await controller.preview({ type: "MARK_MISSED" });             // refused until refreshed
  assert.equal(controller.snapshot.preview, null);
  reloads = 0;
  await controller.refreshAfterCommit();
  assert.equal(controller.snapshot.committedNeedsRefresh, false);
});

test("a lost confirm response keeps the preview so a retry reuses the same key", async () => {
  const keys: string[] = [];
  let first = true;
  const api = fakeApi({
    confirm: async (_t, _p, _id, key) => {
      keys.push(key);
      if (first) { first = false; throw { message: "network" }; }
      return { replayed: true };
    } });
  const { controller } = make(api);
  controller.setIdentity(A);
  await tick();
  await controller.preview({ type: "MARK_MISSED" });
  await controller.confirm();
  assert.ok(controller.snapshot.preview, "preview retained after an unknown outcome");
  await controller.confirm();
  assert.equal(keys[0], keys[1]);
  assert.match(controller.snapshot.notice, /Already recorded/);
});

test("rapid repeated confirmation sends one request", async () => {
  let calls = 0;
  const gate = deferred<any>();
  const api = fakeApi({ confirm: () => { calls += 1; return gate.promise; } });
  const { controller } = make(api);
  controller.setIdentity(A);
  await tick();
  await controller.preview({ type: "MARK_MISSED" });
  const one = controller.confirm();
  const two = controller.confirm();
  gate.resolve({ replayed: false });
  await Promise.all([one, two]);
  assert.equal(calls, 1);
});

import { createControllerBinding } from "../src/features/uctsm/commandController.ts";

test("a slow cancellation of OLD never clears the NEW preview", async () => {
  const slowCancel = deferred<any>();
  let previews = 0;
  const api = fakeApi({
    preview: async () => ({ proposal_id: previews++ === 0 ? "OLD" : "NEW", changes: [] }),
    cancel: (_t, _p, id) => (id === "OLD" ? slowCancel.promise : Promise.resolve({})),
  });
  const { controller } = make(api);
  controller.setIdentity(A);
  await tick();
  await controller.preview({ type: "MARK_MISSED" });
  assert.equal(controller.snapshot.preview.proposal_id, "OLD");
  const discarding = controller.discard();            // cancellation of OLD is in flight
  await controller.preview({ type: "CANCEL_OCCURRENCE" });
  assert.equal(controller.snapshot.preview.proposal_id, "NEW");
  slowCancel.resolve({});
  await discarding;
  assert.equal(controller.snapshot.preview.proposal_id, "NEW");   // still the active intent
});

test("a disposed controller sends no new request of any kind", async () => {
  const calls: string[] = [];
  const api = fakeApi({
    calendar: async () => { calls.push("calendar"); return { schedule_version_id: "v1", calendar: [] }; },
    history: async () => { calls.push("history"); return []; },
    preview: async () => { calls.push("preview"); return { proposal_id: "p" }; },
    confirm: async () => { calls.push("confirm"); return {}; },
    cancel: async () => { calls.push("cancel"); return {}; },
  });
  const { controller } = make(api);
  controller.setIdentity(A);
  await tick();
  await controller.preview({ type: "MARK_MISSED" });          // retains a ready preview
  controller.dispose();
  const before = calls.length;
  await controller.confirm();
  await controller.preview({ type: "MARK_MISSED" });
  await controller.discard();
  await controller.retry();
  await controller.refreshAfterCommit();
  controller.setIdentity(B);
  await tick();
  assert.equal(calls.length, before, calls.slice(before).join(","));
});

test("setup, cleanup, setup leaves a live controller and the first one is dead", async () => {
  const seen: string[] = [];
  const api = fakeApi({
    calendar: async (_t, patient) => { seen.push(patient); return { schedule_version_id: "v1", calendar: [] }; },
  });
  const binding = createControllerBinding(api);
  const snapshots: any[] = [];
  const first = binding.mount((s) => snapshots.push(["first", s.state]));
  binding.setIdentity(A);
  await tick();
  const firstController = binding.live;
  first();                                                    // cleanup (StrictMode / unmount)
  assert.equal(binding.live, null);
  const second = binding.mount((s) => snapshots.push(["second", s.state]));
  await tick();
  assert.ok(binding.live && binding.live !== firstController);
  assert.equal(binding.live.snapshot.state, "ready");         // identity re-applied to the new instance
  binding.setIdentity(B);
  await tick();
  assert.deepEqual(seen.filter((p) => p === "B").length > 0, true);
  second();
  const calls = seen.length;
  await binding.confirm();                                    // unmounted: a harmless no-op
  binding.setIdentity(A);
  await tick();
  assert.equal(seen.length, calls);
});
