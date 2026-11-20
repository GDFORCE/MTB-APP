// ADM — Protocol rules waiting for the app (kit "blockers").
//
// Live, admin-gated endpoints (backend/kit_routes.py · admin_router):
//   list ........... GET  /admin/kit/blockers           groups by pattern name, most trials waiting first
//   re-check ....... POST /admin/kit/blockers/recheck   {pattern?}: re-evaluates saved models against the
//                                                       handlers the server now has
//
// When the protocol analysis meets a rule the schedule model cannot hold, it names the rule's shape
// (CONSECUTIVE_MISSED_VISITS_WITHDRAW, ...) and keeps the protocol's words. With no handler for that
// name, the trial can be saved but nobody can be enrolled. A developer adds the handler (backend
// app/kit/patterns.py + frontend src/features/kit/patterns.ts, docs/kit-pattern-handlers.md); after
// it is deployed, one re-check here unblocks every trial waiting on that name.
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StatusBar, StyleSheet, Text as RNText, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { CheckCircle2, Menu, RefreshCcw, RotateCw } from "lucide-react-native";
import { colors as C, fonts } from "@/src/theme/tokens";
import { fetchKitBlockers, recheckKitBlockers, type KitBlockerOverview } from "@/src/features/kit/api";
import { useAdminDrawer } from "./_layout";
import { EmptyCard, ErrorCard, Loading, st } from "./users";

const errMsg = (e: any, fb: string): string => (typeof e?.response?.data?.detail === "string" ? e.response.data.detail : fb);

export default function AdminProtocolRules() {
  const { open } = useAdminDrawer();
  const [data, setData] = useState<KitBlockerOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try { setData(await fetchKitBlockers()); }
    catch (e) { setError(errMsg(e, "Couldn't load the protocol rules waiting for the app.")); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const recheck = async (pattern?: string) => {
    setBusy(pattern || "all"); setResult(null);
    try {
      const r = await recheckKitBlockers(pattern);
      setResult(`Re-checked ${r.checked} protocol${r.checked === 1 ? "" : "s"}: ${r.unblocked.length} can now enrol, ${r.still_blocked} still waiting.`);
      await load();
    } catch (e) { setResult(errMsg(e, "The re-check failed.")); }
    finally { setBusy(null); }
  };

  const groups = data?.groups || [];
  return (
    <View style={{ flex: 1, backgroundColor: C.background }}>
      <StatusBar barStyle="light-content" backgroundColor={C.primaryDeep} />
      <ScrollView contentContainerStyle={{ paddingBottom: 40 }} showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }} tintColor={C.primary} />}>
        <LinearGradient colors={[C.primary, C.primaryDeep] as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={st.hero}>
          <SafeAreaView edges={["top"]}>
            <View style={st.heroTop}>
              <Pressable testID="admin-menu" onPress={open} style={st.iconBtn} hitSlop={8}><Menu size={20} color={C.primaryFg} /></Pressable>
              <View style={{ flex: 1, minWidth: 0 }}>
                <RNText style={st.eyebrow} numberOfLines={1}>PLATFORM ADMIN</RNText>
                <RNText style={st.heroTitle} numberOfLines={1}>Protocol rules waiting</RNText>
              </View>
              <Pressable testID="blockers-refresh" onPress={() => { setRefreshing(true); void load(); }} style={st.iconBtn} hitSlop={8}>
                <RefreshCcw size={18} color={C.primaryFg} />
              </Pressable>
            </View>
            <RNText style={st.heroSub}>
              Rules the app cannot calculate yet, by kind. Trials waiting on one can't enrol patients until a developer adds its calculation.
            </RNText>
          </SafeAreaView>
        </LinearGradient>

        {loading ? <Loading label="Loading…" /> : error ? <ErrorCard message={error} onRetry={load} /> : (
          <View style={{ marginTop: -20, paddingHorizontal: 16 }}>
            <View style={st.tileGrid}>
              <View style={st.tile}><RNText style={[st.tileValue, { color: C.destructive }]}>{data?.blocked_trials ?? 0}</RNText><RNText style={st.tileLabel}>Trials that can't enrol</RNText></View>
              <View style={st.tile}><RNText style={st.tileValue}>{groups.length}</RNText><RNText style={st.tileLabel}>Kinds of rule waiting</RNText></View>
              <View style={st.tile}><RNText style={[st.tileValue, { color: C.success }]}>{data?.handlers.length ?? 0}</RNText><RNText style={st.tileLabel}>Kinds calculated</RNText></View>
            </View>

            <Pressable testID="blockers-recheck-all" onPress={busy ? undefined : () => recheck()} style={[s.recheckAll, busy && { opacity: 0.6 }]}>
              {busy === "all" ? <ActivityIndicator color={C.primaryFg} size="small" /> : <RotateCw size={15} color={C.primaryFg} />}
              <RNText style={s.recheckAllTxt}>Re-check every saved protocol</RNText>
            </Pressable>
            {!!result && <RNText testID="blockers-result" style={s.result}>{result}</RNText>}

            <RNText style={s.section}>Waiting, most trials first</RNText>
            {!groups.length ? <EmptyCard message="No protocol is waiting for a rule the app cannot calculate." /> : (
              <View style={{ gap: 12 }}>
                {groups.map((g) => (
                  <View key={g.pattern} testID={`blocker-${g.pattern}`} style={st.card}>
                    <RNText style={s.pattern}>{g.pattern}</RNText>
                    <RNText style={s.impact}>
                      {g.trials} trial{g.trials === 1 ? "" : "s"} waiting · {g.rules} rule{g.rules === 1 ? "" : "s"}
                      {g.has_handler ? " · the calculation exists, but these rules can't use it" : " · no calculation yet"}
                    </RNText>
                    {g.evidence.map((e, i) => (
                      <View key={`${e.trial_id}-${i}`} style={s.evidence}>
                        <RNText style={s.trial}>{[e.protocol_id, e.trial_title].filter(Boolean).join(" · ") || e.trial_id}{e.version ? ` · model v${e.version}` : ""}</RNText>
                        <RNText style={s.label}>{e.label}</RNText>
                        {!!e.quote && <RNText style={s.quote}>“{e.quote}”</RNText>}
                        <RNText style={s.meta}>
                          {e.page != null ? `p. ${e.page}` : "page not given"}
                          {e.quote_found === false ? " · this passage was not found in the PDF text" : ""}
                        </RNText>
                        {!!e.problem && <RNText style={s.problem}>Why it can't use the calculation: {e.problem}</RNText>}
                      </View>
                    ))}
                    <Pressable testID={`blocker-recheck-${g.pattern}`} onPress={busy ? undefined : () => recheck(g.pattern)} style={[s.recheckOne, busy && { opacity: 0.6 }]}>
                      {busy === g.pattern ? <ActivityIndicator color={C.primary} size="small" /> : <RotateCw size={13} color={C.primary} />}
                      <RNText style={s.recheckOneTxt}>Re-check the trials waiting on this</RNText>
                    </Pressable>
                  </View>
                ))}
              </View>
            )}

            <RNText style={s.section}>Calculated by the app</RNText>
            <View style={[st.card, { gap: 8 }]}>
              {(data?.handlers || []).map((h) => (
                <View key={h.pattern} style={{ flexDirection: "row", gap: 8, alignItems: "flex-start" }}>
                  <CheckCircle2 size={15} color={C.success} style={{ marginTop: 2 }} />
                  <View style={{ flex: 1 }}>
                    <RNText style={s.handler}>{h.pattern}</RNText>
                    <RNText style={s.meta}>{h.title}</RNText>
                  </View>
                </View>
              ))}
            </View>
          </View>
        )}
      </ScrollView>
    </View>
  );
}

const s = StyleSheet.create({
  section: { fontFamily: fonts.heading, fontSize: 15, color: C.primary, marginTop: 20, marginBottom: 10 },
  recheckAll: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, marginTop: 14, height: 44, borderRadius: 999, backgroundColor: C.primary },
  recheckAllTxt: { fontFamily: fonts.bold, fontSize: 14, color: C.primaryFg },
  result: { fontFamily: fonts.medium, fontSize: 13, color: C.foreground, marginTop: 10 },
  pattern: { fontFamily: fonts.bold, fontSize: 14, color: C.foreground },
  impact: { fontFamily: fonts.medium, fontSize: 12, color: C.destructive, marginTop: 4 },
  evidence: { marginTop: 12, paddingTop: 12, borderTopWidth: 1, borderTopColor: C.border, gap: 4 },
  trial: { fontFamily: fonts.semibold, fontSize: 12, color: C.mutedFg },
  label: { fontFamily: fonts.medium, fontSize: 13, color: C.foreground },
  quote: { fontFamily: fonts.regular, fontSize: 13, lineHeight: 19, color: C.foreground, fontStyle: "italic", borderLeftWidth: 3, borderLeftColor: C.border, paddingLeft: 10 },
  meta: { fontFamily: fonts.regular, fontSize: 11, color: C.mutedFg },
  problem: { fontFamily: fonts.regular, fontSize: 12, color: C.warning },
  recheckOne: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 12, alignSelf: "flex-start", paddingHorizontal: 12, height: 32, borderRadius: 999, borderWidth: 1, borderColor: C.border },
  recheckOneTxt: { fontFamily: fonts.bold, fontSize: 12, color: C.primary },
  handler: { fontFamily: fonts.semibold, fontSize: 13, color: C.foreground },
});
