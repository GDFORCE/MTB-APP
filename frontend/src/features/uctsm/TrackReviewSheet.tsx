import React, { useEffect, useState } from "react";
import {
  KeyboardAvoidingView, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from "react-native";

import { Button } from "@/src/components/ui";
import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import {
  REBASE_POLICIES, buildTracksRequest, trackDraftsFromView,
  type LinkDraft, type MembershipChoice, type TrackDraft,
} from "./trackReview";

const blank = (): TrackDraft => ({
  code: "", trackType: "", displayName: "", anchorCode: "", rebasePolicy: "",
  processCode: "", stoppable: "", confirmed: false,
});

/**
 * Reviewed track membership and policy. Nothing is defaulted: a policy is chosen
 * from what the protocol states, an unchosen one keeps the track unconfirmed,
 * and the backend names what is still unresolved after saving.
 */
export function TrackReviewSheet({ visible, view, busy, onClose, onSubmit }: {
  visible: boolean; view: any; busy: boolean; onClose: () => void;
  onSubmit: (body: Record<string, unknown>) => Promise<boolean>;
}) {
  const [tracks, setTracks] = useState<TrackDraft[]>([]);
  const [links, setLinks] = useState<LinkDraft[]>([]);
  const [membership, setMembership] = useState<Record<string, MembershipChoice>>({});
  const [rationale, setRationale] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!visible) return;
    setTracks(trackDraftsFromView(view?.tracks));
    setLinks((view?.track_links || []).map((item: any) => ({
      source: item.source_track_code, target: item.target_track_code, relation: item.relation,
      confirmed: item.review_state === "CONFIRMED" })));
    const next: Record<string, MembershipChoice> = {};
    for (const [event, value] of Object.entries<any>(view?.track_membership || {})) {
      next[event] = { scope: value.scope || "UNCLASSIFIED", trackCode: value.track_code };
    }
    setMembership(next);
    setRationale(""); setError("");
  }, [visible, view?.revision]);

  const events: { code: string; name: string }[] = (view?.rows || []).map((item: any) => ({
    code: item.code, name: item.visit_name }));
  const update = (index: number, patch: Partial<TrackDraft>) =>
    setTracks((current) => current.map((item, at) => (at === index ? { ...item, ...patch } : item)));
  const processes: any[] = view?.processes || [];

  const save = async () => {
    const built = buildTracksRequest(view.revision, rationale, tracks, links, membership);
    if ("error" in built) { setError(built.error); return; }
    setError("");
    if (await onSubmit(built.body)) onClose();
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.backdrop}>
        <View style={styles.sheet} accessibilityViewIsModal>
          <View style={styles.head}>
            <Text style={styles.title} accessibilityRole="header">Tracks and rules</Text>
            <Pressable accessibilityRole="button" onPress={onClose}><Text style={styles.link}>Close</Text></Pressable>
          </View>
          <ScrollView contentContainerStyle={{ gap: 12, paddingBottom: 24 }} keyboardShouldPersistTaps="handled">
            <Text style={styles.muted}>
              A track groups visits that share one rule for what may move them. A name such as
              "Imaging" does not decide that rule - choose it from the protocol.
            </Text>
            {tracks.map((track, index) => (
              <View key={index} style={styles.card}>
                <TextInput style={styles.input} placeholder="Code, e.g. IMAGING" value={track.code}
                  autoCapitalize="characters" onChangeText={(value) => update(index, { code: value.toUpperCase() })}
                  placeholderTextColor={colors.mutedFg} />
                <TextInput style={styles.input} placeholder="Name shown to staff" value={track.displayName}
                  onChangeText={(value) => update(index, { displayName: value })} placeholderTextColor={colors.mutedFg} />
                <TextInput style={styles.input} placeholder="Type, e.g. TREATMENT" value={track.trackType}
                  autoCapitalize="characters" onChangeText={(value) => update(index, { trackType: value.toUpperCase() })}
                  placeholderTextColor={colors.mutedFg} />
                <Text style={styles.label}>What may move this track's visits</Text>
                <View style={styles.chips}>
                  {REBASE_POLICIES.map((policy) => (
                    <Pressable key={policy.value} accessibilityRole="radio"
                      accessibilityState={{ selected: track.rebasePolicy === policy.value }}
                      onPress={() => update(index, { rebasePolicy: policy.value })}
                      style={[styles.chip, track.rebasePolicy === policy.value && styles.chipOn]}>
                      <Text style={styles.chipText}>{policy.label}</Text>
                    </Pressable>))}
                </View>
                {!!track.rebasePolicy && <Text style={styles.muted}>
                  {REBASE_POLICIES.find((policy) => policy.value === track.rebasePolicy)?.hint}</Text>}
                <Text style={styles.label}>Counts from anchor</Text>
                <View style={styles.chips}>
                  {(view?.anchors || []).map((anchor: any) => (
                    <Pressable key={anchor.code} onPress={() => update(index, { anchorCode: anchor.code })}
                      style={[styles.chip, track.anchorCode === anchor.code && styles.chipOn]}>
                      <Text style={styles.chipText}>{anchor.display_name || anchor.code}</Text>
                    </Pressable>))}
                </View>
                {processes.length > 0 && <>
                  <Text style={styles.label}>Pause / stop through process</Text>
                  <View style={styles.chips}>
                    {processes.map((process: any) => (
                      <Pressable key={process.code} onPress={() => update(index, { processCode: process.code })}
                        style={[styles.chip, track.processCode === process.code && styles.chipOn]}>
                        <Text style={styles.chipText}>{process.display_name || process.code}</Text>
                      </Pressable>))}
                  </View>
                </>}
                {!!track.processCode && <>
                  <Text style={styles.label}>May this track be stopped?</Text>
                  <View style={styles.chips}>
                    {([["", "Not stated"], ["yes", "Allowed"], ["no", "Not allowed"]] as const).map(([value, label]) => (
                      <Pressable key={label} accessibilityRole="radio"
                        accessibilityState={{ selected: track.stoppable === value }}
                        onPress={() => update(index, { stoppable: value })}
                        style={[styles.chip, track.stoppable === value && styles.chipOn]}>
                        <Text style={styles.chipText}>{label}</Text>
                      </Pressable>))}
                  </View>
                  <Text style={styles.muted}>Choose from the protocol. "Not stated" keeps Stop unavailable.</Text>
                </>}
                <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: track.confirmed }}
                  onPress={() => update(index, { confirmed: !track.confirmed })}>
                  <Text style={styles.valueStrong}>{track.confirmed ? "☑" : "☐"} I have checked this track against the protocol</Text>
                </Pressable>
                <Pressable accessibilityRole="button" onPress={() =>
                  setTracks((current) => current.filter((_, at) => at !== index))}>
                  <Text style={styles.link}>Remove this track</Text>
                </Pressable>
              </View>))}
            <Button variant="secondary" onPress={() => setTracks((current) => [...current, blank()])}>+ Add a track</Button>

            <Text style={styles.label}>Which track each visit belongs to</Text>
            {events.map((event) => {
              const choice = membership[event.code] || { scope: "UNCLASSIFIED" as const };
              return (
                <View key={event.code} style={styles.card}>
                  <Text style={styles.valueStrong}>{event.name}</Text>
                  <View style={styles.chips}>
                    {tracks.filter((track) => track.code).map((track) => (
                      <Pressable key={track.code}
                        onPress={() => setMembership((current) => ({
                          ...current, [event.code]: { scope: "TRACK", trackCode: track.code } }))}
                        style={[styles.chip, choice.scope === "TRACK" && choice.trackCode === track.code && styles.chipOn]}>
                        <Text style={styles.chipText}>{track.displayName || track.code}</Text>
                      </Pressable>))}
                    <Pressable onPress={() => setMembership((current) => ({
                      ...current, [event.code]: { scope: "GLOBAL" } }))}
                      style={[styles.chip, choice.scope === "GLOBAL" && styles.chipOn]}>
                      <Text style={styles.chipText}>Whole study</Text>
                    </Pressable>
                    <Pressable onPress={() => setMembership((current) => ({
                      ...current, [event.code]: { scope: "UNCLASSIFIED" } }))}
                      style={[styles.chip, choice.scope === "UNCLASSIFIED" && styles.chipOn]}>
                      <Text style={styles.chipText}>Not decided</Text>
                    </Pressable>
                  </View>
                </View>);
            })}

            {tracks.length > 1 && <>
              <Text style={styles.label}>Visits that follow another track</Text>
              {links.map((link, index) => (
                <Text key={index} style={styles.muted}>
                  {link.source} → {link.target} ({link.relation === "FOLLOWS_ACTUAL" ? "actual dates" : "planned dates"})
                </Text>))}
              <Button variant="secondary" onPress={() => {
                const [source, target] = [tracks[0].code, tracks[1].code];
                setLinks((current) => [...current, { source, target, relation: "FOLLOWS_ACTUAL", confirmed: false }]);
              }}>+ Let {tracks[1]?.code || "a track"} follow {tracks[0]?.code}</Button>
            </>}

            <TextInput style={[styles.input, { minHeight: 70 }]} multiline value={rationale}
              onChangeText={setRationale} placeholder="Why? (recorded in the audit trail)"
              placeholderTextColor={colors.mutedFg} accessibilityLabel="Why" />
            {!!error && <Text style={styles.error} accessibilityLiveRegion="polite">{error}</Text>}
            <Button onPress={save} loading={busy} disabled={busy}>Save and revalidate</Button>
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.4)", justifyContent: "flex-end" },
  sheet: { backgroundColor: colors.background, borderTopLeftRadius: radii.lg, borderTopRightRadius: radii.lg,
    padding: spacing.md, maxHeight: "92%" },
  head: { flexDirection: "row", justifyContent: "space-between", marginBottom: 8 },
  title: { fontFamily: fonts.bold, fontSize: 18, color: colors.foreground },
  link: { color: colors.primary, fontFamily: fonts.medium },
  muted: { color: colors.mutedFg, fontFamily: fonts.regular, fontSize: 12 },
  label: { fontFamily: fonts.bold, fontSize: 13, color: colors.foreground },
  card: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10, gap: 8 },
  input: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10,
    fontFamily: fonts.regular, color: colors.foreground },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: { borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, paddingHorizontal: 10, paddingVertical: 6 },
  chipOn: { backgroundColor: colors.primary },
  chipText: { fontFamily: fonts.medium, fontSize: 12, color: colors.foreground },
  valueStrong: { fontFamily: fonts.bold, fontSize: 13, color: colors.foreground },
  error: { color: colors.destructive, fontFamily: fonts.medium, fontSize: 12 },
});
