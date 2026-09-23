import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { ChevronRight } from "lucide-react-native";

import { colors, fonts, radii, spacing } from "@/src/theme/tokens";
import { getCanonicalSchedules, getEnrolmentOptions, getUniversalSchedule } from "./api";
import type { CanonicalScheduleRow } from "./api";
import type { UniversalSchedule } from "./types";
import { toEnrolmentFields, toEnrolmentReadiness } from "./presentation";
import type { EnrolmentField } from "./presentation";

export type ProtocolAssignment = {
  /** Dimension name -> selected option code, e.g. { ARM: "ARM_B", PART: "A" }. */
  values: Record<string, string>;
  /** The same selections as protocol display names, for the operational path. */
  labels: Record<string, string>;
  scheduleVersionId: string | null;
  scheduleDefinitionId?: string | null;
  anchors?: UniversalSchedule["anchors"];
  originCode?: string | null;
  /** Every dimension this protocol defines. Empty for a simple trial. */
  dimensionTypes: string[];
  /** True once every dimension the protocol requires has been chosen. */
  complete: boolean;
  ready: boolean;
  message: string;
};

export const EMPTY_ASSIGNMENT: ProtocolAssignment = {
  values: {}, labels: {}, scheduleVersionId: null, dimensionTypes: [],
  complete: false, ready: false, message: "Select an approved protocol schedule.",
};

type Props = {
  trialId: string | null;
  onChange: (assignment: ProtocolAssignment) => void;
};

/**
 * Screen B - the protocol-driven part of patient enrolment.
 *
 * Only the groupings THIS protocol defines are asked for. A form that shows Arm,
 * Cohort, Part and Sequence to every trial teaches a site to put something in a
 * field that does not apply to their study, and an assignment that does not
 * exist in the protocol produces a schedule nobody can locate in it.
 *
 * Selections narrow what follows: choosing Part A leaves only Part A's cohorts
 * offered, because the alternative is letting someone record a cohort that
 * cannot exist inside the part they just chose.
 */
export function ProtocolAssignmentFields({ trialId, onChange }: Props) {
  const [scheduleVersionId, setScheduleVersionId] = useState<string | null>(null);
  const [readiness, setReadiness] = useState({ ready: false, message: "" });
  const [fields, setFields] = useState<EnrolmentField[]>([]);
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [schedules, setSchedules] = useState<CanonicalScheduleRow[]>([]);
  const [anchors, setAnchors] = useState<UniversalSchedule["anchors"]>([]);
  const [originCode, setOriginCode] = useState<string | null>(null);
  const [optionsLoaded, setOptionsLoaded] = useState(false);
  const request = useRef(0);
  const notify = useRef(onChange);
  notify.current = onChange;

  useEffect(() => {
    let alive = true;
    setScheduleVersionId(null);
    setFields([]);
    setSelected({});
    setSchedules([]);
    setAnchors([]);
    setOptionsLoaded(false);
    request.current += 1;
    setReadiness({ ready: false, message: "" });
    if (!trialId) return () => { alive = false; };
    (async () => {
      setLoading(true);
      try {
        const index = await getCanonicalSchedules(trialId);
        if (!alive) return;
        const current = index.schedules.filter((item) => item.is_current_protocol_version !== false);
        const available = current.filter((item) => item.status === "APPROVED"
          && (!item.effective_from || item.effective_from <= new Date().toISOString().slice(0, 10)))
          .sort((a, b) => b.version_number - a.version_number)
          .filter((item, index, all) => all.findIndex((other) => other.schedule_definition_id === item.schedule_definition_id) === index);
        setSchedules(available);
        if (available.length > 1) {
          setReadiness({ ready: false, message: "Select the approved Schedule of Assessments for this patient." });
          return;
        }
        const next = toEnrolmentReadiness(current);
        setReadiness({ ready: next.ready, message: next.message });
        setScheduleVersionId(next.scheduleVersionId);
      } catch {
        // A trial with no canonical side yet keeps the operational enrolment
        // path exactly as it was; this block adds fields, it never removes any.
        if (alive) setReadiness({ ready: false, message: "Could not load the approved schedule. Reload this screen to try again." });
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [trialId]);

  const loadOptions = useCallback(async (versionId: string, current: Record<string, string>) => {
    const generation = ++request.current;
    setLoading(true);
    setOptionsLoaded(false);
    try {
      const [response, schedule] = await Promise.all([
        getEnrolmentOptions(versionId, current), getUniversalSchedule(versionId),
      ]);
      if (generation !== request.current) return;
      setFields(toEnrolmentFields(response.dimensions));
      setAnchors(schedule.anchors);
      setOriginCode(schedule.schedule_metadata.day_numbering?.day_1_anchor.code
        || schedule.anchors.find((anchor) => anchor.derivation_rule?.role === "SCHEDULE_ORIGIN")?.code || null);
      setOptionsLoaded(true);
    } catch {
      if (generation !== request.current) return;
      setReadiness({ ready: false, message: "Protocol assignment could not be loaded. Reload before enrolling this patient." });
    } finally {
      if (generation === request.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!scheduleVersionId) return;
    void loadOptions(scheduleVersionId, selected);
  }, [scheduleVersionId, selected, loadOptions]);

  const assignment = useMemo<ProtocolAssignment>(() => {
    const labels: Record<string, string> = {};
    for (const field of fields) {
      const code = selected[field.id];
      const option = field.options.find((item) => item.value === code);
      if (option) labels[field.id] = option.label;
    }
    const required = fields.filter((field) => !field.disabled && field.options.length > 1);
    return {
      values: selected,
      labels,
      scheduleVersionId,
      scheduleDefinitionId: schedules.find((item) => item.schedule_version_id === scheduleVersionId)?.schedule_definition_id,
      anchors,
      originCode,
      dimensionTypes: fields.map((field) => field.id),
      complete: readiness.ready && optionsLoaded && !loading && required.every((field) => !!selected[field.id]),
      ready: readiness.ready && optionsLoaded && !loading,
      message: readiness.message,
    };
  }, [fields, selected, scheduleVersionId, readiness, schedules, anchors, originCode, optionsLoaded, loading]);

  useEffect(() => { notify.current(assignment); }, [assignment]);

  // A dimension with exactly one possible value is not a question. Recording it
  // silently keeps the protocol's own structure without asking a site to
  // "choose" the only option there is.
  useEffect(() => {
    const auto: Record<string, string> = {};
    for (const field of fields) {
      if (field.disabled || field.options.length !== 1) continue;
      if (!selected[field.id]) auto[field.id] = field.options[0].value;
    }
    if (Object.keys(auto).length) setSelected((current) => ({ ...current, ...auto }));
  }, [fields, selected]);

  const choose = (dimension: string, code: string) => {
    setOpen(null);
    setSelected((current) => {
      if (current[dimension] === code) return current;
      // Clearing the dependants matters: a cohort chosen under Part A must not
      // survive a switch to Part B, where it does not exist.
      const next: Record<string, string> = {};
      for (const [key, value] of Object.entries(current)) {
        const field = fields.find((item) => item.id === key);
        if (key === dimension || !field?.hint) next[key] = value;
      }
      next[dimension] = code;
      return next;
    });
  };

  if (!trialId) return null;

  const visible = fields.filter((field) => field.disabled || field.options.length > 1);

  return (
    <View style={styles.block}>
      <View style={styles.headerRow}>
        <Text style={styles.header}>PROTOCOL ASSIGNMENT</Text>
        {loading && <ActivityIndicator size="small" color={colors.primary} />}
      </View>
      {!!readiness.message && (
        <Text style={[styles.note, !readiness.ready && styles.warning]}>
          {readiness.ready
            ? `Schedule assigned at enrollment: ${readiness.message}`
            : readiness.message}
        </Text>
      )}
      {schedules.length > 1 && schedules.map((item) => <Pressable
        key={item.schedule_version_id} accessibilityRole="button"
        onPress={() => {
          request.current += 1;
          setOptionsLoaded(false); setSelected({}); setFields([]); setAnchors([]);
          setScheduleVersionId(item.schedule_version_id);
          setReadiness({ ready: true, message: `${item.name} · Schedule v${item.version_number}` });
        }} style={styles.control}>
        <Text style={styles.controlText}>{item.schedule_version_id === scheduleVersionId ? "✓ " : ""}{item.name} · v{item.version_number}</Text>
      </Pressable>)}
      {visible.map((field) => (
        <View key={field.id} style={styles.field}>
          <Text style={styles.label}>
            {field.label}{field.disabled ? "" : " *"}
          </Text>
          <Pressable
            testID={`assignment-toggle-${field.id}`}
            accessibilityRole="button"
            accessibilityLabel={`Select ${field.label}`}
            disabled={field.disabled}
            onPress={() => setOpen((current) => current === field.id ? null : field.id)}
            style={[styles.control, field.disabled && styles.controlDisabled]}
          >
            <Text
              numberOfLines={1}
              style={[styles.controlText, !assignment.labels[field.id] && styles.placeholder]}
            >
              {assignment.labels[field.id] || (field.disabled ? "Not available yet" : "Select")}
            </Text>
            <ChevronRight
              size={16}
              color={colors.mutedFg}
              style={{ transform: [{ rotate: open === field.id ? "-90deg" : "90deg" }] }}
            />
          </Pressable>
          {!!field.hint && <Text style={styles.hint}>{field.hint}</Text>}
          {open === field.id && !field.disabled && (
            <View style={styles.dropdown}>
              {field.options.map((option) => (
                <Pressable
                  key={option.value}
                  testID={`assignment-opt-${field.id}-${option.value}`}
                  onPress={() => choose(field.id, option.value)}
                  style={styles.dropdownRow}
                >
                  <Text style={styles.dropdownText}>{option.label}</Text>
                </Pressable>
              ))}
            </View>
          )}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  block: { gap: 10, marginBottom: spacing.md },
  headerRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  header: {
    fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 0.6,
    color: colors.mutedFg,
  },
  note: { fontFamily: fonts.regular, fontSize: 12, color: colors.mutedFg, lineHeight: 17 },
  warning: { color: colors.warning },
  field: { gap: 5 },
  label: { fontFamily: fonts.semibold, fontSize: 12, color: colors.foreground },
  control: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.md,
    paddingHorizontal: 12, minHeight: 46, backgroundColor: colors.card,
  },
  controlDisabled: { opacity: 0.55 },
  controlText: { flex: 1, fontFamily: fonts.regular, fontSize: 14, color: colors.foreground },
  placeholder: { color: colors.mutedFg },
  hint: { fontFamily: fonts.regular, fontSize: 11, color: colors.mutedFg },
  dropdown: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radii.md,
    backgroundColor: colors.card, overflow: "hidden",
  },
  dropdownRow: {
    paddingHorizontal: 12, paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border,
  },
  dropdownText: { fontFamily: fonts.regular, fontSize: 14, color: colors.foreground },
});
