/** P5-01 reviewed track membership and policy (pure request building). */

export const REBASE_POLICIES: { value: string; label: string; hint: string }[] = [
  { value: "FIXED_FROM_ANCHOR", label: "Fixed from the anchor",
    hint: "Targets count from the anchor only; nothing moves them." },
  { value: "PREVIOUS_PLANNED_OCCURRENCE", label: "Each from the previous planned visit", hint: "A chain of planned dates." },
  { value: "PREVIOUS_ACTUAL_OCCURRENCE", label: "Each from the previous actual visit", hint: "A chain that follows what happened." },
  { value: "MANUAL_REBASE", label: "Only when a person rebases it", hint: "Moves only by an explicit rebase command." },
  { value: "NO_REBASE", label: "Never rebased", hint: "No command may move it." },
];

export type TrackDraft = {
  code: string; trackType: string; displayName: string; anchorCode: string;
  rebasePolicy: string; processCode: string; stoppable: "" | "yes" | "no"; confirmed: boolean;
};
export type MembershipChoice = { scope: "TRACK" | "GLOBAL" | "UNCLASSIFIED"; trackCode?: string | null };
export type LinkDraft = { source: string; target: string; relation: "FOLLOWS_ACTUAL" | "FOLLOWS_PLANNED"; confirmed: boolean };

const CODE = /^[A-Z][A-Z0-9_]*$/;

export function trackDraftsFromView(tracks: any[] | undefined): TrackDraft[] {
  return (tracks || []).map((item) => ({
    code: item.code, trackType: item.track_type, displayName: item.display_name,
    anchorCode: item.anchor_code || "", rebasePolicy: item.rebase_policy || "",
    processCode: item.process_code || "",
    stoppable: item.stoppable === true ? "yes" : item.stoppable === false ? "no" : "",
    confirmed: item.review_state === "CONFIRMED",
  }));
}

export function buildTracksRequest(
  revision: string, rationale: string, tracks: TrackDraft[], links: LinkDraft[],
  membership: Record<string, MembershipChoice>,
): { body: Record<string, unknown> } | { error: string } {
  if (!rationale.trim()) return { error: "Say why these track rules are correct." };
  const seen = new Set<string>();
  for (const track of tracks) {
    if (!CODE.test(track.code)) return { error: `Track code "${track.code}" must be capitals, digits and underscores.` };
    if (seen.has(track.code)) return { error: `Two tracks use the code ${track.code}.` };
    seen.add(track.code);
    if (!track.displayName.trim()) return { error: `Give track ${track.code} a name.` };
    if (!CODE.test(track.trackType)) return { error: `Track ${track.code} needs a type such as TREATMENT or IMAGING.` };
    if (track.confirmed && !track.rebasePolicy) {
      return { error: `Track ${track.code} cannot be confirmed until you state what may move its visits.` };
    }
  }
  for (const [event, choice] of Object.entries(membership)) {
    if (choice.scope === "TRACK" && !(choice.trackCode && seen.has(choice.trackCode))) {
      return { error: `${event} names a track that does not exist.` };
    }
  }
  for (const link of links) {
    if (!seen.has(link.source) || !seen.has(link.target) || link.source === link.target) {
      return { error: "A cross-track rule must connect two different existing tracks." };
    }
  }
  return {
    body: {
      expected_revision: revision, rationale: rationale.trim(),
      tracks: tracks.map((track) => ({
        code: track.code, track_type: track.trackType, display_name: track.displayName.trim(),
        anchor_code: track.anchorCode || null, rebase_policy: track.rebasePolicy || null,
        process_code: track.processCode || null,
        stoppable: track.stoppable === "" ? null : track.stoppable === "yes",
        review_state: track.confirmed ? "CONFIRMED" : "PROPOSED",
      })),
      track_links: links.map((link) => ({
        source_track_code: link.source, target_track_code: link.target, relation: link.relation,
        review_state: link.confirmed ? "CONFIRMED" : "PROPOSED",
      })),
      membership: Object.fromEntries(Object.entries(membership).map(([event, choice]) => [
        event, { scope: choice.scope, track_code: choice.scope === "TRACK" ? choice.trackCode : null }])),
    },
  };
}
