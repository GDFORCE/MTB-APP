import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import {
  CalendarDays,
  ChevronRight,
  FlaskConical,
  MapPin,
  Plus,
  RefreshCw,
  Search,
  UserRound,
} from "lucide-react-native";
import { colors, spacing, radii, dawnGradient } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card, Button } from "@/src/components/ui";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Rise } from "@/src/components/Rise";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";

type Trial = {
  id: string;
  protocol_id?: string;
  title?: string;
  phase?: string;
  condition?: string;
  drug?: string;
  status?: string;
  recruitment_status?: string;
  enrolled_count?: number;
  target_enrollment?: number | null;
  site_names?: string[];
  site_count?: number;
  created_by_name?: string;
  created_by_role?: string;
  created_at?: string;
};

const ALL = "all";

export default function MyTrials() {
  const router = useRouter();
  const { user } = useAuth();
  const [trials, setTrials] = useState<Trial[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [phase, setPhase] = useState(ALL);
  const [status, setStatus] = useState(ALL);

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const response = await api.get("/trials");
      setTrials(Array.isArray(response.data) ? response.data : []);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Couldn't load your trials.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const phases = useMemo(
    () => [ALL, ...Array.from(new Set(trials.map(t => t.phase).filter(Boolean) as string[]))],
    [trials],
  );
  const statuses = useMemo(
    () => [ALL, ...Array.from(new Set(trials.map(t => t.recruitment_status || t.status).filter(Boolean) as string[]))],
    [trials],
  );
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return trials.filter(t => {
      const searchable = [t.protocol_id, t.title, t.condition, t.drug, ...(t.site_names || [])]
        .filter(Boolean).join(" ").toLowerCase();
      const state = t.recruitment_status || t.status || "";
      return (!needle || searchable.includes(needle))
        && (phase === ALL || t.phase === phase)
        && (status === ALL || state === status);
    });
  }, [phase, query, status, trials]);

  const canAdd = user?.role === "sponsor" || user?.role === "cro" || user?.role === "pi";

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow="Clinical · Active studies"
        title="My Trials"
        right={(
          <Pressable testID="trials-refresh" onPress={() => load(true)} disabled={refreshing} hitSlop={10}>
            {refreshing
              ? <ActivityIndicator size="small" color={colors.primaryFg} />
              : <RefreshCw size={19} color={colors.primaryFg} />}
          </Pressable>
        )}
      />
      <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">
        <Rise>
          <View style={s.search}>
            <Search size={17} color={colors.mutedFg} />
            <TextInput
              testID="trial-search"
              value={query}
              onChangeText={setQuery}
              placeholder="Search protocol, trial, drug or site"
              placeholderTextColor={colors.mutedFg}
              style={s.searchInput}
            />
          </View>
        </Rise>

        <Rise delay={60}>
          <FilterRow label="Phase" values={phases} selected={phase} onSelect={setPhase} />
          <FilterRow label="Status" values={statuses} selected={status} onSelect={setStatus} />
        </Rise>

        {canAdd && (
          <Rise delay={100}>
            <Button testID="add-trial" onPress={() => router.push("/(app)/sponsor/add-trial")}>
              <View style={s.buttonInner}>
                <Plus size={16} color={colors.primaryFg} />
                <Small color={colors.primaryFg} weight="700">Add Trial</Small>
              </View>
            </Button>
          </Rise>
        )}

        {loading ? (
          <View style={s.state}><ActivityIndicator color={colors.primary} /><Small>Loading your trials…</Small></View>
        ) : error ? (
          <Card style={s.stateCard}>
            <Small color={colors.destructive} weight="700">{error}</Small>
            <Button variant="secondary" style={{ marginTop: spacing.md }} onPress={() => load()}>
              <Small color={colors.primary} weight="700">Retry</Small>
            </Button>
          </Card>
        ) : filtered.length === 0 ? (
          <Card style={s.stateCard}>
            <FlaskConical size={28} color={colors.mutedFg + "88"} />
            <Body weight="700" style={{ marginTop: spacing.sm }}>
              {trials.length ? "No matching trials" : "No trials assigned"}
            </Body>
            <Small style={{ marginTop: 4, textAlign: "center" }}>
              {trials.length ? "Try changing your search or filters." : "Assigned studies will appear here."}
            </Small>
          </Card>
        ) : filtered.map((trial, index) => (
          <Rise key={trial.id} delay={140 + Math.min(index, 8) * 55}>
            <TrialCard
              trial={trial}
              onPress={() => router.push({ pathname: "/(app)/clinical/trial-summary", params: { id: trial.id } })}
            />
          </Rise>
        ))}
      </ScrollView>
    </ScreenContainer>
  );
}

function FilterRow({ label, values, selected, onSelect }: {
  label: string;
  values: string[];
  selected: string;
  onSelect: (value: string) => void;
}) {
  return (
    <View style={{ marginBottom: spacing.sm }}>
      <Eyebrow style={{ marginBottom: 6 }}>{label}</Eyebrow>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 7 }}>
        {values.map(value => {
          const active = value === selected;
          const countLabel = value === ALL ? "All" : value;
          return (
            <Pressable
              key={value}
              onPress={() => onSelect(value)}
              style={[s.filterChip, active && s.filterChipActive]}
            >
              <Small color={active ? colors.primaryFg : colors.foreground} weight="700" style={{ textTransform: "capitalize" }}>
                {countLabel}
              </Small>
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

function TrialCard({ trial, onPress }: { trial: Trial; onPress: () => void }) {
  const enrolled = trial.enrolled_count || 0;
  const target = trial.target_enrollment || 0;
  const pct = target ? Math.min(100, Math.round((enrolled / target) * 100)) : 0;
  const sites = trial.site_names || [];
  const state = trial.recruitment_status || trial.status || "active";
  const created = trial.created_at
    ? new Date(trial.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
    : "";

  return (
    <Pressable testID={`trial-card-${trial.id}`} onPress={onPress} style={({ pressed }) => pressed && { opacity: 0.86 }}>
      <View style={{ marginBottom: spacing.md }}>
        <LinearGradient colors={dawnGradient as any} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={s.hero}>
          <View style={s.heroTop}>
            <View style={s.heroChip}><Small color={colors.primaryFg} weight="700" style={{ fontFamily: "monospace" as any }}>{trial.protocol_id || "No protocol"}</Small></View>
            <View style={s.heroChip}><Small color={colors.primaryFg} weight="700" style={{ textTransform: "capitalize" }}>{state}</Small></View>
          </View>
          <H1 color={colors.primaryFg} style={s.title}>{trial.title || "Untitled trial"}</H1>
          <Small color={colors.overlay25} style={{ marginTop: 4 }}>
            {[trial.phase, trial.condition, trial.drug].filter(Boolean).join(" · ") || "Trial details pending"}
          </Small>
        </LinearGradient>
        <Card style={s.cardBody}>
          <View style={s.metrics}>
            <Metric label="Enrolled" value={target ? `${enrolled}/${target}` : String(enrolled)} />
            <Metric label="Sites" value={String(trial.site_count ?? sites.length)} />
            <Metric label="Progress" value={target ? `${pct}%` : "—"} accent />
          </View>
          {target > 0 && (
            <View style={s.progressTrack}>
              <View style={[s.progressFill, { width: `${pct}%` }]} />
            </View>
          )}
          <View style={s.metaList}>
            <View style={s.metaRow}>
              <MapPin size={14} color={colors.accent} />
              <Small numberOfLines={1} style={{ flex: 1 }}>{sites.length ? sites.join(", ") : "Site assignment pending"}</Small>
            </View>
            {!!trial.created_by_name && (
              <View style={s.metaRow}>
                <UserRound size={14} color={colors.mutedFg} />
                <Small numberOfLines={1} style={{ flex: 1 }}>
                  Created by {trial.created_by_name}{trial.created_by_role ? ` · ${trial.created_by_role.toUpperCase()}` : ""}
                </Small>
              </View>
            )}
            {!!created && (
              <View style={s.metaRow}>
                <CalendarDays size={14} color={colors.mutedFg} />
                <Small>{created}</Small>
              </View>
            )}
          </View>
          <View style={s.footer}>
            <Small weight="700" color={colors.accent}>View full trial summary</Small>
            <ChevronRight size={15} color={colors.accent} />
          </View>
        </Card>
      </View>
    </Pressable>
  );
}

function Metric({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <View style={{ flex: 1 }}>
      <Eyebrow color={colors.mutedFg}>{label}</Eyebrow>
      <Body weight="700" color={accent ? colors.accent : colors.foreground} style={{ marginTop: 2, fontSize: 14 }}>{value}</Body>
    </View>
  );
}

const s = StyleSheet.create({
  content: { padding: spacing.md, paddingBottom: spacing.xxl },
  search: {
    height: 46,
    paddingHorizontal: 14,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.card,
    flexDirection: "row",
    alignItems: "center",
    gap: 9,
    marginBottom: spacing.md,
  },
  searchInput: { flex: 1, color: colors.foreground, paddingVertical: 0 },
  filterChip: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: radii.pill,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.card,
  },
  filterChipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  buttonInner: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7 },
  state: { minHeight: 180, alignItems: "center", justifyContent: "center", gap: spacing.sm },
  stateCard: { minHeight: 150, alignItems: "center", justifyContent: "center", marginTop: spacing.md },
  hero: { borderTopLeftRadius: radii.xl, borderTopRightRadius: radii.xl, padding: spacing.md },
  heroTop: { flexDirection: "row", justifyContent: "space-between", gap: spacing.sm },
  heroChip: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: radii.pill, backgroundColor: colors.overlay20 },
  title: { fontSize: 18, marginTop: spacing.sm },
  cardBody: { borderTopLeftRadius: 0, borderTopRightRadius: 0, borderTopWidth: 0, marginBottom: 0 },
  metrics: { flexDirection: "row", gap: spacing.md },
  progressTrack: { height: 5, borderRadius: 4, backgroundColor: colors.secondary, overflow: "hidden", marginTop: 10 },
  progressFill: { height: "100%", borderRadius: 4, backgroundColor: colors.accent },
  metaList: { gap: 7, paddingTop: spacing.sm, marginTop: spacing.sm, borderTopWidth: 1, borderColor: colors.border },
  metaRow: { flexDirection: "row", alignItems: "center", gap: 7 },
  footer: { flexDirection: "row", justifyContent: "flex-end", alignItems: "center", gap: 4, marginTop: spacing.sm },
});
