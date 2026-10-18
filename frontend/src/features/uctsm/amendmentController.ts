/** The patient amendment panel's lifecycle as a plain state machine (no React).
 *
 * Same discipline as the command controller: a response that belongs to a previous
 * patient / request is dropped, one idempotency key per simulation (so a retry after a lost
 * response replays instead of repeating), and a commit followed by a failed reload is
 * reported as SAVED, never as "nothing happened". The server recomputes and rechecks every
 * call; this only keeps the screen honest. */

import { failureOf, type Blocker, type Simulation } from "./amendments.ts";

export type AmendmentApi = {
  status(trialId: string, patientId: string): Promise<any>;
  simulate(trialId: string, patientId: string, body: Record<string, unknown>): Promise<Simulation>;
  confirm(trialId: string, patientId: string, simulationId: string, body: Record<string, unknown>, key: string): Promise<any>;
  execute(trialId: string, patientId: string, decisionId: string): Promise<any>;
  cancel(trialId: string, patientId: string, decisionId: string, reason: string): Promise<any>;
};

export type Identity = { trialId?: string; externalPatientId?: string | null };

export type Phase = "checking" | "ready" | "absent" | "forbidden" | "unavailable";

export type AmendmentSnapshot = {
  phase: Phase;
  status: any | null;
  simulation: Simulation | null;
  busy: boolean;
  error: string;
  blockers: Blocker[];
  notice: string;
  /** a change was committed but the screen could not be refreshed afterwards */
  committedNeedsRefresh: boolean;
};

const EMPTY: AmendmentSnapshot = {
  phase: "checking", status: null, simulation: null, busy: false, error: "", blockers: [],
  notice: "", committedNeedsRefresh: false,
};

export function createAmendmentController(
  api: AmendmentApi, onChange: (snapshot: AmendmentSnapshot) => void,
) {
  let snapshot: AmendmentSnapshot = { ...EMPTY };
  let generation = 0;
  let identity: Identity | null = null;
  let disposed = false;

  const publish = (patch: Partial<AmendmentSnapshot>, gen?: number) => {
    if (disposed || (gen !== undefined && gen !== generation)) return false;
    snapshot = { ...snapshot, ...patch };
    onChange(snapshot);
    return true;
  };
  const ready = () => !!identity?.trialId && !!identity?.externalPatientId;

  async function load(gen: number) {
    const status = await api.status(identity!.trialId!, identity!.externalPatientId!);
    if (gen !== generation) return false;
    publish({ status, phase: "ready" }, gen);
    return true;
  }

  async function probe() {
    if (disposed) return;
    generation += 1;
    const gen = generation;
    snapshot = { ...EMPTY };
    onChange(snapshot);
    if (!ready()) { publish({ phase: "absent" }, gen); return; }
    try {
      await load(gen);
    } catch (failure) {
      const info = failureOf(failure);
      publish({
        phase: info.kind === "forbidden" ? "forbidden" : info.kind === "absent" ? "absent" : "unavailable",
        error: info.kind === "unavailable" ? info.message : "",
      }, gen);
    }
  }

  const fail = (failure: unknown, gen: number) => {
    const info = failureOf(failure);
    publish({ error: info.message, blockers: info.blockers, busy: false }, gen);
  };

  return {
    get snapshot() { return snapshot; },

    setIdentity(next: Identity) {
      if (disposed) return;
      const same = identity && JSON.stringify(identity) === JSON.stringify(next);
      identity = next;
      if (!same) void probe();
    },

    retry() { return probe(); },
    dispose() { disposed = true; generation += 1; },

    /** Read-only: calculates what the move would do and keeps it for confirmation. */
    async simulate(body: Record<string, unknown>) {
      if (disposed || snapshot.busy || snapshot.phase !== "ready" || snapshot.committedNeedsRefresh) return;
      const gen = generation;
      publish({ busy: true, error: "", blockers: [], notice: "", simulation: null }, gen);
      try {
        const simulation = await api.simulate(identity!.trialId!, identity!.externalPatientId!, body);
        publish({ simulation, busy: false, blockers: simulation.blockers || [] }, gen);
      } catch (failure) {
        fail(failure, gen);
      }
    },

    async confirm(body: Record<string, unknown>) {
      const current = snapshot.simulation;
      if (disposed || !current || snapshot.busy || snapshot.phase !== "ready" || !current.can_confirm) return;
      const gen = generation;
      publish({ busy: true, error: "", blockers: [], notice: "" }, gen);
      let result: any;
      try {
        // one key per simulation: a retry after a lost response replays, never repeats
        result = await api.confirm(
          identity!.trialId!, identity!.externalPatientId!, current.simulation_id, body,
          `amend-${current.simulation_id}`);
      } catch (failure) {
        // an unknown outcome keeps the preview so a retry uses the SAME key; a refusal
        // that says the preview is no longer valid (409) makes it unusable
        const info = failureOf(failure);
        const unusable = info.kind === "blocked" || info.kind === "conflict" || info.kind === "integrity";
        publish({ error: info.message, blockers: info.blockers, busy: false,
          ...(unusable ? { simulation: null } : {}) }, gen);
        return;
      }
      if (gen !== generation) return;
      const notice = result.replayed ? "Already recorded - nothing was repeated."
        : result.status === "APPLIED" ? "Done. The patient's schedule was recalculated."
        : result.status === "REMAINING" ? "Recorded: the patient stays on the current version."
        : result.status === "WAITING" ? "Recorded. It will be applied when its point comes."
        : "Recorded.";
      try {
        await load(gen);
        publish({ simulation: null, busy: false, notice }, gen);
      } catch (failure) {
        publish({
          simulation: null, busy: false, committedNeedsRefresh: true,
          notice: "Your decision was saved, but this screen could not refresh. Reload before making another change.",
          error: failureOf(failure).message,
        }, gen);
      }
    },

    discard() {
      if (disposed || snapshot.busy) return;
      publish({ simulation: null, blockers: [], error: "" }, generation);
    },

    async execute(decisionId: string) {
      if (disposed || snapshot.busy || snapshot.phase !== "ready") return;
      const gen = generation;
      publish({ busy: true, error: "", blockers: [], notice: "" }, gen);
      try {
        const result = await api.execute(identity!.trialId!, identity!.externalPatientId!, decisionId);
        await load(gen);
        publish({
          busy: false,
          notice: result.status === "APPLIED" ? "Done. The patient's schedule was recalculated."
            : "Not due yet - nothing was changed.",
        }, gen);
      } catch (failure) {
        fail(failure, gen);
        try { await load(gen); } catch { /* the failure above is the news */ }
      }
    },

    async cancel(decisionId: string, reason: string) {
      if (disposed || snapshot.busy || snapshot.phase !== "ready") return;
      const gen = generation;
      publish({ busy: true, error: "", blockers: [], notice: "" }, gen);
      try {
        await api.cancel(identity!.trialId!, identity!.externalPatientId!, decisionId, reason);
        await load(gen);
        publish({ busy: false, notice: "Cancelled." }, gen);
      } catch (failure) {
        fail(failure, gen);
      }
    },

    async refreshAfterCommit() {
      if (disposed) return;
      const gen = generation;
      try {
        if (await load(gen)) publish({ committedNeedsRefresh: false, error: "" }, gen);
      } catch (failure) {
        publish({ error: failureOf(failure).message }, gen);
      }
    },
  };
}

/** Mount/unmount-safe holder (see the command controller): setup -> cleanup -> setup always
 * leaves a LIVE controller with the latest identity applied. */
export function createAmendmentBinding(api: AmendmentApi) {
  let controller: ReturnType<typeof createAmendmentController> | null = null;
  let latest: Identity | null = null;
  return {
    mount(onChange: (snapshot: AmendmentSnapshot) => void) {
      const instance = createAmendmentController(api, onChange);
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
    simulate: (body: Record<string, unknown>) => controller?.simulate(body) ?? Promise.resolve(),
    confirm: (body: Record<string, unknown>) => controller?.confirm(body) ?? Promise.resolve(),
    discard: () => controller?.discard(),
    execute: (id: string) => controller?.execute(id) ?? Promise.resolve(),
    cancel: (id: string, reason: string) => controller?.cancel(id, reason) ?? Promise.resolve(),
    retry: () => controller?.retry() ?? Promise.resolve(),
    refreshAfterCommit: () => controller?.refreshAfterCommit() ?? Promise.resolve(),
  };
}
