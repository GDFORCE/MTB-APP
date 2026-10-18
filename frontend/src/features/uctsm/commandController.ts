/** The patient-command panel's lifecycle as a plain state machine (no React).
 *
 * It owns every rule the audit asked to be proven: responses that belong to a
 * previous patient / version / request are dropped, identity-bound state is
 * reset on a switch, one retry key per proposal, failure classes are kept apart,
 * and a commit followed by a failed reload is reported as SAVED (never as
 * "nothing happened") and blocks a second command until the screen refreshes.
 * The React panel renders ``snapshot`` and forwards user actions here. */

import {
  classifyProbeError, commandEligibility, commandErrorMessage, type PlaneState,
} from "./patientCommands.ts";

export type CommandApi = {
  calendar(trialId: string, patientId: string): Promise<{ schedule_version_id: string | null; calendar: any[] }>;
  history(trialId: string, patientId: string): Promise<any[]>;
  preview(trialId: string, patientId: string, body: Record<string, unknown>): Promise<any>;
  confirm(trialId: string, patientId: string, proposalId: string, key: string): Promise<any>;
  cancel(trialId: string, patientId: string, proposalId: string, reason: string): Promise<any>;
};

export type Identity = {
  trialId?: string; externalPatientId?: string | null; scheduleVersionId?: string | null;
  displayPlane: "SQL" | "MONGO";
};

export type Snapshot = {
  state: PlaneState;
  calendar: any[];
  history: any[];
  preview: any | null;
  busy: boolean;
  error: string;
  notice: string;
  /** a command was committed but the screen could not be refreshed afterwards */
  committedNeedsRefresh: boolean;
};

const EMPTY: Snapshot = {
  state: "checking", calendar: [], history: [], preview: null, busy: false, error: "", notice: "",
  committedNeedsRefresh: false,
};

const errorOf = (failure: any) => commandErrorMessage(
  failure?.response?.status ?? failure?.status ?? 0,
  failure?.response?.data?.detail ?? failure?.detail ?? failure?.message);

export function createCommandController(api: CommandApi, onChange: (snapshot: Snapshot) => void) {
  let snapshot: Snapshot = { ...EMPTY };
  let generation = 0;
  let identity: Identity | null = null;
  let disposed = false;

  const publish = (patch: Partial<Snapshot>, gen?: number) => {
    if (disposed || (gen !== undefined && gen !== generation)) return false;
    snapshot = { ...snapshot, ...patch };
    onChange(snapshot);
    return true;
  };

  const ready = () => !!identity?.trialId && !!identity?.externalPatientId;

  async function load(gen: number): Promise<PlaneState | null> {
    const [plane, history] = await Promise.all([
      api.calendar(identity!.trialId!, identity!.externalPatientId!),
      api.history(identity!.trialId!, identity!.externalPatientId!)]);
    if (gen !== generation) return null;
    publish({ calendar: plane.calendar, history }, gen);
    return commandEligibility(identity!.displayPlane, identity!.scheduleVersionId, plane.schedule_version_id);
  }

  async function probe() {
    if (disposed) return;                      // a disposed controller starts NO request
    generation += 1;
    const gen = generation;
    snapshot = { ...EMPTY };
    onChange(snapshot);
    if (!ready()) { publish({ state: "absent" }, gen); return; }
    try {
      const verdict = await load(gen);
      if (verdict) publish({ state: verdict }, gen);
    } catch (failure: any) {
      publish({ state: classifyProbeError(failure?.response?.status ?? failure?.status) }, gen);
    }
  }

  return {
    get snapshot() { return snapshot; },

    /** Called whenever the patient, trial, displayed version or plane changes. */
    setIdentity(next: Identity) {
      if (disposed) return;
      const same = identity && JSON.stringify(identity) === JSON.stringify(next);
      identity = next;
      if (!same) void probe();
    },

    retry() { return probe(); },

    dispose() { disposed = true; generation += 1; },

    async preview(body: Record<string, unknown>) {
      if (disposed || snapshot.busy || snapshot.state !== "ready" || snapshot.committedNeedsRefresh) return;
      const gen = generation;
      publish({ busy: true, error: "", notice: "" }, gen);
      try {
        const result = await api.preview(identity!.trialId!, identity!.externalPatientId!, body);
        publish({ preview: result, busy: false }, gen);
      } catch (failure) {
        publish({ error: errorOf(failure), busy: false }, gen);
      }
    },

    async confirm() {
      const current = snapshot.preview;
      if (disposed || !current || snapshot.busy || snapshot.state !== "ready") return;
      const gen = generation;
      publish({ busy: true, error: "", notice: "" }, gen);
      let result: any;
      try {
        // one key per proposal: a retry after a lost response replays, never repeats
        result = await api.confirm(
          identity!.trialId!, identity!.externalPatientId!, current.proposal_id,
          `cmd-${current.proposal_id}`);
      } catch (failure) {
        // the outcome is unknown: keep the preview so retrying uses the SAME key
        publish({ error: errorOf(failure), busy: false }, gen);
        return;
      }
      if (gen !== generation) return;            // switched away: not this screen's result
      try {
        const verdict = await load(gen);
        if (verdict === null) return;
        publish({
          preview: null, busy: false, state: verdict === "ready" ? "ready" : verdict,
          notice: result.replayed ? "Already recorded - nothing was repeated."
            : "Recorded and the schedule was recalculated.",
        }, gen);
      } catch (failure) {
        // COMMITTED, but the refresh failed: say so, and block a duplicate command.
        publish({
          preview: null, busy: false, committedNeedsRefresh: true,
          notice: "Your change was saved, but this screen could not refresh. Reload before making another change.",
          error: errorOf(failure),
        }, gen);
      }
    },

    async refreshAfterCommit() {
      if (disposed) return;
      const gen = generation;
      try {
        const verdict = await load(gen);
        if (verdict) publish({ state: verdict, committedNeedsRefresh: false, error: "" }, gen);
      } catch (failure) {
        publish({ error: errorOf(failure) }, gen);
      }
    },

    async discard() {
      const current = snapshot.preview;
      if (disposed || !current || snapshot.busy) return;
      const gen = generation;
      // The discard is bound to THIS proposal. A slower cancellation of an older
      // preview must never clear a newer one that replaced it meanwhile.
      publish({ preview: null }, gen);
      try {
        await api.cancel(identity!.trialId!, identity!.externalPatientId!, current.proposal_id,
          "Cancelled from the schedule screen");
      } catch { /* the preview simply expires */ }
    },
  };
}

/** A mount/unmount-safe holder for the controller, so React's setup -> cleanup ->
 * setup (StrictMode, fast refresh) always ends with a LIVE instance: ``mount``
 * creates a fresh controller and re-applies the latest identity; ``unmount``
 * disposes it for good. Actions on an unmounted binding are no-ops. */
export function createControllerBinding(api: CommandApi) {
  let controller: ReturnType<typeof createCommandController> | null = null;
  let latest: Identity | null = null;
  return {
    mount(onChange: (snapshot: Snapshot) => void) {
      const instance = createCommandController(api, onChange);
      controller = instance;
      if (latest) instance.setIdentity(latest);
      return () => {
        instance.dispose();
        if (controller === instance) controller = null;
      };
    },
    setIdentity(next: Identity) {
      latest = next;
      controller?.setIdentity(next);
    },
    get live() { return controller; },
    preview: (body: Record<string, unknown>) => controller?.preview(body) ?? Promise.resolve(),
    confirm: () => controller?.confirm() ?? Promise.resolve(),
    discard: () => controller?.discard() ?? Promise.resolve(),
    retry: () => controller?.retry() ?? Promise.resolve(),
    refreshAfterCommit: () => controller?.refreshAfterCommit() ?? Promise.resolve(),
  };
}
