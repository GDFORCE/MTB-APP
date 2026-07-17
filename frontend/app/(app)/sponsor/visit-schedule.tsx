import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  TextInput,
  View,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import {
  AlertTriangle,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ClipboardList,
  Download,
  Edit3,
  MessageSquareText,
  Plus,
  Sparkles,
  Stethoscope,
  Trash2,
  X,
} from "lucide-react-native";
import * as DocumentPicker from "expo-document-picker";
import { api } from "@/src/api/client";
import { ScreenContainer, ScreenHeader } from "@/src/components/ScreenHeader";
import { Body, Button, Card, Eyebrow, Small } from "@/src/components/ui";
import { colors, fonts, radii, shadows, spacing } from "@/src/theme/tokens";

type Row = {
  id?: string;
  visit_number?: number;
  name: string;
  day_offset: string;
  window_days: string;
  activities: string;
  clinical_tasks: string;
  admin_tasks: string;
  comments: string;
  extraction_warning: boolean;
  review_status: "pending" | "ok";
  extracted_from_protocol: boolean;
};

type ExtractedVisit = {
  name: string;
  day_offset: number;
  window_days: number;
  activities: string[];
  clinical_tasks?: string[];
  admin_tasks?: string[];
  comments?: string;
  extraction_warning?: boolean;
  review_status?: "pending" | "ok";
};

const blankRow = (name = "New Visit"): Row => ({
  name,
  day_offset: "0",
  window_days: "3",
  activities: "",
  clinical_tasks: "",
  admin_tasks: "",
  comments: "",
  extraction_warning: false,
  review_status: "ok",
  extracted_from_protocol: false,
});

const templateToRow = (template: any): Row => ({
  id: template.id,
  visit_number: template.visit_number,
  name: template.name ?? "",
  day_offset: String(template.day_offset ?? 0),
  window_days: String(template.window_days ?? 3),
  activities: (template.activities ?? []).join(", "),
  clinical_tasks: (template.clinical_tasks ?? []).join(", "),
  admin_tasks: (template.admin_tasks ?? []).join(", "),
  comments: template.comments ?? "",
  extraction_warning: Boolean(template.extraction_warning),
  review_status: template.review_status === "pending" ? "pending" : "ok",
  extracted_from_protocol: Boolean(template.extracted_from_protocol),
});

const sameRow = (left: Row, right: Row) =>
  left.name.trim() === right.name.trim()
  && parseInt(left.day_offset || "0", 10) === parseInt(right.day_offset || "0", 10)
  && parseInt(left.window_days || "3", 10) === parseInt(right.window_days || "3", 10)
  && left.activities.trim() === right.activities.trim()
  && left.clinical_tasks.trim() === right.clinical_tasks.trim()
  && left.admin_tasks.trim() === right.admin_tasks.trim()
  && left.comments.trim() === right.comments.trim()
  && left.extraction_warning === right.extraction_warning
  && left.review_status === right.review_status
  && left.extracted_from_protocol === right.extracted_from_protocol;

const dayLabel = (value: string) => {
  const day = parseInt(value || "0", 10);
  return day > 0 ? `+${day}` : String(day);
};

const csvCell = (value: string | number | boolean) => `"${String(value).replace(/"/g, "\"\"")}"`;

export default function VisitScheduleEditor() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [rows, setRows] = useState<Row[]>([]);
  const [original, setOriginal] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [notice, setNotice] = useState("");
  const [extracting, setExtracting] = useState(false);
  const [extractErr, setExtractErr] = useState("");
  const [editing, setEditing] = useState(false);
  const [filter, setFilter] = useState<"all" | "pending" | "ok">("all");
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [confirmSave, setConfirmSave] = useState(false);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadErr("");
    try {
      const response = await api.get(`/trials/${id}/visits`);
      const templates: any[] = response.data ?? [];
      const loaded = templates.map(templateToRow);
      setRows(loaded);
      setOriginal(loaded);
      setEditing(loaded.length === 0);
    } catch (error: any) {
      setLoadErr(error?.response?.data?.detail || "Couldn't load the existing schedule.");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const updateRow = <K extends keyof Row>(index: number, key: K, value: Row[K]) => {
    setRows((current) => current.map((row, rowIndex) => (
      rowIndex === index ? { ...row, [key]: value } : row
    )));
  };

  const add = () => {
    setRows((current) => {
      const next = [...current, blankRow(`Visit ${current.length + 1}`)];
      setSelectedIndex(next.length - 1);
      return next;
    });
    setEditing(true);
  };

  const remove = (index: number) => {
    setRows((current) => current.filter((_, rowIndex) => rowIndex !== index));
    setSelectedIndex(null);
  };

  const move = (from: number, to: number) => {
    setRows((current) => {
      if (to < 0 || to >= current.length) return current;
      const next = [...current];
      const [item] = next.splice(from, 1);
      next.splice(to, 0, item);
      return next;
    });
  };

  const acknowledge = (index: number) => {
    setRows((current) => current.map((row, rowIndex) => (
      rowIndex === index
        ? { ...row, extraction_warning: false, review_status: "ok" }
        : row
    )));
  };

  const autofill = async () => {
    setExtractErr("");
    let asset: DocumentPicker.DocumentPickerAsset;
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: "application/pdf",
        copyToCacheDirectory: true,
        multiple: false,
      });
      if (result.canceled || !result.assets?.length) return;
      asset = result.assets[0];
    } catch {
      setExtractErr("Couldn't open the file picker.");
      return;
    }

    const form = new FormData();
    if (Platform.OS === "web") {
      const file = (asset as any).file as File | undefined;
      if (!file) {
        setExtractErr("Couldn't read the selected file.");
        return;
      }
      form.append("file", file, asset.name || "protocol.pdf");
    } else {
      form.append("file", {
        uri: asset.uri,
        name: asset.name || "protocol.pdf",
        type: asset.mimeType || "application/pdf",
      } as any);
    }

    setExtracting(true);
    try {
      const response = await api.post(`/trials/${id}/extract-schedule`, form, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 120000,
      });
      const visits: ExtractedVisit[] = response.data?.visits ?? [];
      if (!visits.length) {
        setExtractErr("No visit schedule was found in that PDF. You can still build it manually.");
        return;
      }
      setRows(visits.map((visit) => ({
        name: visit.name ?? "",
        day_offset: String(visit.day_offset ?? 0),
        window_days: String(visit.window_days ?? 3),
        activities: (visit.activities ?? []).join(", "),
        clinical_tasks: (visit.clinical_tasks ?? visit.activities ?? []).join(", "),
        admin_tasks: (visit.admin_tasks ?? []).join(", "),
        comments: visit.comments ?? "",
        extraction_warning: Boolean(visit.extraction_warning),
        review_status: visit.review_status === "pending" ? "pending" : "ok",
        extracted_from_protocol: true,
      })));
      setSelectedIndex(null);
      setEditing(false);
      setNotice("Draft extracted. Review every flagged row before saving.");
    } catch (error: any) {
      const status = error?.response?.status;
      setExtractErr(error?.response?.data?.detail
        || (status === 503
          ? "AI extraction isn't configured on the server yet."
          : "Couldn't extract the schedule. Try again, or build it manually."));
    } finally {
      setExtracting(false);
    }
  };

  const validate = () => {
    if (!rows.length) return "Add at least one visit before saving.";
    const invalidIndex = rows.findIndex((row) => (
      !row.name.trim()
      || !Number.isFinite(Number(row.day_offset))
      || !Number.isFinite(Number(row.window_days))
      || Number(row.window_days) < 0
    ));
    if (invalidIndex >= 0) return `Check the name, day and window for Visit ${invalidIndex + 1}.`;
    return "";
  };

  const save = async () => {
    setConfirmSave(false);
    setSaving(true);
    setErr("");
    try {
      const keptIds = new Set(rows.filter((row) => row.id).map((row) => row.id));
      for (const previous of original) {
        if (previous.id && !keptIds.has(previous.id)) {
          await api.delete(`/visits/${previous.id}`);
        }
      }
      for (let index = 0; index < rows.length; index += 1) {
        const row = rows[index];
        const visitNumber = index + 1;
        const payload = {
          name: row.name.trim(),
          visit_number: visitNumber,
          day_offset: parseInt(row.day_offset || "0", 10),
          window_days: parseInt(row.window_days || "3", 10),
          activities: row.activities.split(",").map((value) => value.trim()).filter(Boolean),
          clinical_tasks: row.clinical_tasks.split(",").map((value) => value.trim()).filter(Boolean),
          admin_tasks: row.admin_tasks.split(",").map((value) => value.trim()).filter(Boolean),
          comments: row.comments.trim(),
          extraction_warning: row.extraction_warning,
          review_status: row.review_status,
          extracted_from_protocol: row.extracted_from_protocol,
        };
        if (row.id) {
          const previous = original.find((item) => item.id === row.id);
          const numberChanged = !previous || previous.visit_number !== visitNumber;
          if (!previous || !sameRow(previous, row) || numberChanged) {
            await api.put(`/visits/${row.id}`, payload);
          }
        } else {
          await api.post("/visits", { trial_id: id, ...payload });
        }
      }
      setSaved(true);
    } catch (error: any) {
      setErr(error?.response?.data?.detail || "The schedule couldn't be saved. Please try again.");
    } finally {
      setSaving(false);
    }
  };

  const requestSave = () => {
    const message = validate();
    if (message) {
      setErr(message);
      return;
    }
    setErr("");
    if (pendingCount > 0) {
      setConfirmSave(true);
      return;
    }
    save();
  };

  const download = async () => {
    if (!rows.length) {
      setErr("Add at least one visit before downloading.");
      return;
    }
    const header = [
      "Visit number", "Visit name", "Day offset", "Window days", "Activities",
      "Clinical tasks", "Administrative tasks", "Comments", "Review status",
    ];
    const lines = rows.map((row, index) => [
      index + 1,
      row.name,
      row.day_offset,
      row.window_days,
      row.activities,
      row.clinical_tasks,
      row.admin_tasks,
      row.comments,
      row.extraction_warning || row.review_status === "pending" ? "Needs review" : "OK",
    ].map(csvCell).join(","));
    const csv = `\uFEFF${header.map(csvCell).join(",")}\r\n${lines.join("\r\n")}`;
    const filename = `visit-schedule-${id || "trial"}.csv`;
    try {
      if (Platform.OS === "web") {
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 4000);
      } else {
        const uri = `data:text/csv;charset=utf-8,${encodeURIComponent(csv)}`;
        await Share.share({
          title: filename,
          message: csv,
          url: uri,
        });
      }
      setNotice(`Schedule exported as ${filename}`);
      setErr("");
    } catch (error: any) {
      setErr(error?.message || "Couldn't export the schedule.");
    }
  };

  const goToTrial = () => {
    setSaved(false);
    router.replace({ pathname: "/(app)/sponsor/trial-detail", params: { id } });
  };

  const pendingCount = rows.filter((row) => (
    row.extraction_warning || row.review_status === "pending"
  )).length;
  const visibleRows = rows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => (
      filter === "all"
      || (filter === "pending"
        ? row.extraction_warning || row.review_status === "pending"
        : !row.extraction_warning && row.review_status === "ok")
    ));
  const selectedRow = selectedIndex === null ? null : rows[selectedIndex];
  const isProtocolExtract = rows.some((row) => row.extracted_from_protocol);

  if (loading) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Build schedule" title="Visit Schedule" />
        <View style={styles.center}><ActivityIndicator size="large" color={colors.primary} /></View>
      </ScreenContainer>
    );
  }

  if (loadErr) {
    return (
      <ScreenContainer>
        <ScreenHeader eyebrow="Build schedule" title="Visit Schedule" />
        <View style={styles.center}>
          <Small color={colors.destructive} style={styles.centerText}>{loadErr}</Small>
          <Button testID="retry-load" variant="secondary" onPress={load}>Try again</Button>
        </View>
      </ScreenContainer>
    );
  }

  return (
    <ScreenContainer>
      <ScreenHeader
        eyebrow="Build schedule"
        title="Visit Schedule"
        right={(
          <View style={styles.headerActions}>
            <Pressable
              accessibilityLabel="Download visit schedule"
              testID="download-visit-schedule"
              onPress={download}
              style={styles.headerIcon}
            >
              <Download size={17} color={colors.primaryFg} />
            </Pressable>
            <Pressable
              accessibilityLabel={editing ? "Finish editing schedule" : "Edit schedule"}
              testID="toggle-schedule-edit"
              onPress={() => setEditing((value) => !value)}
              style={[styles.headerIcon, editing && styles.headerIconActive]}
            >
              {editing
                ? <Check size={17} color={colors.primaryFg} />
                : <Edit3 size={17} color={colors.primaryFg} />}
            </Pressable>
          </View>
        )}
      />

      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={styles.flex}
      >
        <ScrollView
          contentContainerStyle={styles.content}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          <Card style={styles.extractCard}>
            <View style={styles.extractHeading}>
              <View style={styles.sparkIcon}><Sparkles size={17} color={colors.primary} /></View>
              <View style={styles.flex}>
                <Eyebrow>Protocol assistant</Eyebrow>
                <Small style={styles.extractCopy}>
                  Upload a protocol PDF to draft its Schedule of Assessments.
                </Small>
              </View>
            </View>
            <Pressable
              testID="extract-protocol"
              onPress={autofill}
              disabled={extracting}
              style={[styles.extractButton, extracting && styles.disabled]}
            >
              {extracting
                ? <ActivityIndicator size="small" color={colors.primary} />
                : <Sparkles size={16} color={colors.primary} />}
              <Small color={colors.primary} weight="700">
                {extracting ? "Reading protocol..." : "Auto-fill from protocol PDF"}
              </Small>
            </Pressable>
            {!!extractErr && <Small color={colors.destructive} style={styles.inlineMessage}>{extractErr}</Small>}
          </Card>

          <View style={styles.summary}>
            <View style={styles.summaryLeft}>
              <View style={styles.sourcePill}>
                <Sparkles size={12} color={colors.primary} />
                <Small color={colors.primary} weight="700">
                  {isProtocolExtract ? "AI Extracted" : "Visit Template"}
                </Small>
              </View>
              <Small>{rows.length} visits</Small>
            </View>
            {pendingCount > 0 && (
              <View style={styles.pendingPill}>
                <AlertTriangle size={13} color={colors.warning} />
                <Small color={colors.warning} weight="700">{pendingCount} need review</Small>
              </View>
            )}
          </View>

          {editing && (
            <View style={styles.editHint}>
              <Edit3 size={14} color={colors.primary} />
              <Small color={colors.primary} weight="700">
                Editing: tap a visit for details, use arrows to reorder, or remove it.
              </Small>
            </View>
          )}

          <View style={styles.filters}>
            {(["all", "pending", "ok"] as const).map((value) => {
              const count = value === "all"
                ? rows.length
                : value === "pending" ? pendingCount : rows.length - pendingCount;
              const active = filter === value;
              return (
                <Pressable
                  key={value}
                  testID={`schedule-filter-${value}`}
                  onPress={() => setFilter(value)}
                  style={[styles.filterChip, active && styles.filterChipActive]}
                >
                  {active && <Check size={12} color={colors.primaryFg} />}
                  <Small color={active ? colors.primaryFg : colors.mutedFg} weight="700">
                    {value === "all" ? "All" : value === "pending" ? "Pending" : "OK"}
                  </Small>
                  <View style={[styles.filterCount, active && styles.filterCountActive]}>
                    <Small color={active ? colors.primaryFg : colors.mutedFg} weight="700">{count}</Small>
                  </View>
                </Pressable>
              );
            })}
          </View>

          <View style={styles.table}>
            <View style={[styles.tableGrid, styles.tableHeader, editing && styles.tableGridEditing]}>
              {editing && <View style={styles.orderCell} />}
              <Small weight="700" style={styles.numberCell}>#</Small>
              <Small weight="700" style={styles.nameCell}>Visit name</Small>
              <Small weight="700" style={styles.dayCell}>Day</Small>
              <Small weight="700" style={styles.windowCell}>Window</Small>
              {editing && <View style={styles.deleteCell} />}
            </View>

            {visibleRows.map(({ row, index }) => {
              const warning = row.extraction_warning || row.review_status === "pending";
              return (
                <Pressable
                  key={row.id ?? `new-${index}`}
                  testID={`visit-row-${index}`}
                  onPress={() => setSelectedIndex(index)}
                  style={[
                    styles.tableGrid,
                    styles.tableRow,
                    editing && styles.tableGridEditing,
                    warning && styles.warningRow,
                  ]}
                >
                  {editing && (
                    <View style={styles.orderCell}>
                      <Pressable
                        disabled={index === 0}
                        onPress={(event) => {
                          event.stopPropagation();
                          move(index, index - 1);
                        }}
                        style={styles.orderButton}
                      >
                        <ChevronUp size={13} color={index === 0 ? colors.border : colors.mutedFg} />
                      </Pressable>
                      <Pressable
                        disabled={index === rows.length - 1}
                        onPress={(event) => {
                          event.stopPropagation();
                          move(index, index + 1);
                        }}
                        style={styles.orderButton}
                      >
                        <ChevronDown size={13} color={index === rows.length - 1 ? colors.border : colors.mutedFg} />
                      </Pressable>
                    </View>
                  )}
                  <View style={styles.numberCell}>
                    {warning
                      ? <AlertTriangle size={15} color={colors.warning} />
                      : <Small weight="700">{index + 1}</Small>}
                  </View>
                  <View style={styles.nameCell}>
                    <Body weight="700" numberOfLines={1}>{row.name || "Untitled visit"}</Body>
                    {!!row.comments && <MessageSquareText size={11} color={colors.mutedFg} />}
                  </View>
                  <Small style={styles.dayCell}>{dayLabel(row.day_offset)}</Small>
                  <Small style={styles.windowCell}>±{row.window_days || 0}</Small>
                  {editing && (
                    <View style={styles.deleteCell}>
                      <Pressable
                        testID={`remove-visit-${index}`}
                        onPress={(event) => {
                          event.stopPropagation();
                          remove(index);
                        }}
                        style={styles.deleteButton}
                      >
                        <Trash2 size={15} color={colors.destructive} />
                      </Pressable>
                    </View>
                  )}
                </Pressable>
              );
            })}

            {visibleRows.length === 0 && (
              <View style={styles.emptyRows}>
                <Small color={colors.mutedFg}>
                  {rows.length ? "No visits in this filter." : "No visits yet. Add one or extract from a protocol."}
                </Small>
              </View>
            )}
          </View>

          <Pressable testID="add-visit-row" onPress={add} style={styles.addButton}>
            <Plus size={17} color={colors.primary} />
            <Small color={colors.primary} weight="700">Add Visit</Small>
          </Pressable>

          {!!notice && (
            <Pressable onPress={() => setNotice("")} style={styles.notice}>
              <CheckCircle2 size={15} color={colors.success} />
              <Small color={colors.success} weight="700" style={styles.flex}>{notice}</Small>
              <X size={13} color={colors.success} />
            </Pressable>
          )}
          {!!err && <Small color={colors.destructive} style={styles.inlineMessage}>{err}</Small>}
        </ScrollView>

        <View style={styles.saveBar}>
          <Button testID="save-schedule" onPress={requestSave} loading={saving}>
            <View style={styles.buttonContent}>
              <Check size={14} color={colors.primaryFg} />
              <Small color={colors.primaryFg} weight="700">Save Template</Small>
            </View>
          </Button>
        </View>
      </KeyboardAvoidingView>

      <VisitDetailSheet
        row={selectedRow}
        index={selectedIndex}
        editing={editing}
        onClose={() => setSelectedIndex(null)}
        onChange={updateRow}
        onAcknowledge={acknowledge}
        onDelete={remove}
      />

      <Modal
        transparent
        visible={confirmSave}
        animationType="fade"
        onRequestClose={() => setConfirmSave(false)}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.confirmCard}>
            <View style={styles.warningOrb}><AlertTriangle size={24} color={colors.warning} /></View>
            <Body weight="700" style={styles.confirmTitle}>Save with pending review?</Body>
            <Small style={styles.confirmCopy}>
              {pendingCount} {pendingCount === 1 ? "visit still needs" : "visits still need"} manual review.
              You can save the draft now, but it should be acknowledged before sharing.
            </Small>
            <View style={styles.confirmActions}>
              <Pressable onPress={() => setConfirmSave(false)} style={styles.secondaryAction}>
                <Small color={colors.primary} weight="700">Review visits</Small>
              </Pressable>
              <Pressable testID="confirm-save-schedule" onPress={save} style={styles.primaryAction}>
                <Small color={colors.primaryFg} weight="700">Save draft</Small>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      <Modal transparent visible={saved} animationType="fade" onRequestClose={goToTrial}>
        <View style={styles.modalBackdrop}>
          <View style={styles.successCard}>
            <View style={styles.successOrb}><CheckCircle2 size={31} color={colors.success} /></View>
            <Body weight="700" style={styles.confirmTitle}>Visit template saved</Body>
            <Small style={styles.confirmCopy}>
              {rows.length} {rows.length === 1 ? "visit is" : "visits are"} now attached to this trial.
              The schedule can be reviewed or shared from the trial workspace.
            </Small>
            <Pressable testID="return-to-trial" onPress={goToTrial} style={styles.successAction}>
              <Small color={colors.primaryFg} weight="700">Return to trial</Small>
            </Pressable>
          </View>
        </View>
      </Modal>
    </ScreenContainer>
  );
}

function VisitDetailSheet({
  row,
  index,
  editing,
  onClose,
  onChange,
  onAcknowledge,
  onDelete,
}: {
  row: Row | null;
  index: number | null;
  editing: boolean;
  onClose: () => void;
  onChange: <K extends keyof Row>(index: number, key: K, value: Row[K]) => void;
  onAcknowledge: (index: number) => void;
  onDelete: (index: number) => void;
}) {
  if (!row || index === null) return null;
  const warning = row.extraction_warning || row.review_status === "pending";
  return (
    <Modal transparent visible animationType="slide" onRequestClose={onClose}>
      <View style={styles.sheetBackdrop}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.sheetKeyboard}
        >
          <View style={styles.sheet}>
            <View style={styles.sheetHandle} />
            <View style={styles.sheetHeader}>
              <View style={[styles.visitBadge, warning && styles.visitBadgeWarning]}>
                {warning
                  ? <AlertTriangle size={18} color={colors.warning} />
                  : <Body color={colors.primary} weight="700">{index + 1}</Body>}
              </View>
              <View style={styles.flex}>
                {editing ? (
                  <TextInput
                    testID={`vname-${index}`}
                    value={row.name}
                    onChangeText={(value) => onChange(index, "name", value)}
                    placeholder="Visit name"
                    placeholderTextColor={colors.mutedFg}
                    style={[styles.input, styles.nameInput]}
                  />
                ) : (
                  <>
                    <Body weight="700">Visit {index + 1} · {row.name}</Body>
                    <View style={styles.metaLine}>
                      <CalendarDays size={12} color={colors.mutedFg} />
                      <Small>Day {dayLabel(row.day_offset)}</Small>
                      <Small>·</Small>
                      <Small>Window ±{row.window_days || 0} days</Small>
                    </View>
                  </>
                )}
              </View>
              <Pressable accessibilityLabel="Close visit details" onPress={onClose} style={styles.closeButton}>
                <X size={17} color={colors.mutedFg} />
              </Pressable>
            </View>

            <ScrollView
              contentContainerStyle={styles.sheetContent}
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator={false}
            >
              {warning && (
                <View style={styles.warningBanner}>
                  <AlertTriangle size={15} color={colors.warning} />
                  <Small color={colors.warning} style={styles.flex}>
                    This protocol-extracted visit needs manual review.
                  </Small>
                </View>
              )}

              {editing && (
                <View style={styles.twoColumns}>
                  <Field label="Day">
                    <TextInput
                      testID={`vday-${index}`}
                      value={row.day_offset}
                      onChangeText={(value) => onChange(index, "day_offset", value)}
                      keyboardType="numbers-and-punctuation"
                      style={styles.input}
                    />
                  </Field>
                  <Field label="Window (± days)">
                    <TextInput
                      testID={`vwin-${index}`}
                      value={row.window_days}
                      onChangeText={(value) => onChange(index, "window_days", value)}
                      keyboardType="number-pad"
                      style={styles.input}
                    />
                  </Field>
                </View>
              )}

              <TaskField
                icon={<Stethoscope size={16} color={colors.primary} />}
                title="Clinical Tasks"
                value={row.clinical_tasks}
                fallback={row.activities}
                editing={editing}
                placeholder="Vitals, ECG, blood draw"
                onChange={(value) => onChange(index, "clinical_tasks", value)}
              />
              <TaskField
                icon={<ClipboardList size={16} color={colors.success} />}
                title="Administrative Tasks"
                value={row.admin_tasks}
                editing={editing}
                placeholder="eCRF, consent, drug accountability"
                onChange={(value) => onChange(index, "admin_tasks", value)}
              />
              <TaskField
                icon={<CheckCircle2 size={16} color={colors.info} />}
                title="Other Activities"
                value={row.activities}
                editing={editing}
                placeholder="Additional protocol activities"
                onChange={(value) => onChange(index, "activities", value)}
              />

              <View>
                <View style={styles.sectionTitle}>
                  <MessageSquareText size={16} color={colors.mutedFg} />
                  <Body weight="700">Comments</Body>
                </View>
                {editing ? (
                  <TextInput
                    value={row.comments}
                    onChangeText={(value) => onChange(index, "comments", value)}
                    multiline
                    textAlignVertical="top"
                    placeholder="Add visit-specific comments..."
                    placeholderTextColor={colors.mutedFg}
                    style={[styles.input, styles.commentInput]}
                  />
                ) : (
                  <Small style={styles.readComment}>
                    {row.comments.trim() || "No comments added."}
                  </Small>
                )}
              </View>

              {warning && (
                <Pressable
                  testID={`ack-warning-${index}`}
                  onPress={() => onAcknowledge(index)}
                  style={styles.acknowledgeButton}
                >
                  <Check size={15} color={colors.success} />
                  <Small color={colors.success} weight="700">Acknowledge warning</Small>
                </Pressable>
              )}
            </ScrollView>

            <View style={styles.sheetFooter}>
              {editing && (
                <Pressable
                  testID={`sheet-delete-visit-${index}`}
                  onPress={() => onDelete(index)}
                  style={styles.sheetDelete}
                >
                  <Trash2 size={16} color={colors.destructive} />
                  <Small color={colors.destructive} weight="700">Delete</Small>
                </Pressable>
              )}
              <Pressable onPress={onClose} style={styles.sheetDone}>
                <Small color={colors.primaryFg} weight="700">{editing ? "Done" : "Close"}</Small>
              </Pressable>
            </View>
          </View>
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={styles.field}>
      <Small color={colors.foreground} weight="700" style={styles.fieldLabel}>{label}</Small>
      {children}
    </View>
  );
}

function TaskField({
  icon,
  title,
  value,
  fallback = "",
  editing,
  placeholder,
  onChange,
}: {
  icon: React.ReactNode;
  title: string;
  value: string;
  fallback?: string;
  editing: boolean;
  placeholder: string;
  onChange: (value: string) => void;
}) {
  const tasks = (value || fallback).split(",").map((item) => item.trim()).filter(Boolean);
  return (
    <View>
      <View style={styles.sectionTitle}>
        {icon}
        <Body weight="700">{title}</Body>
      </View>
      {editing ? (
        <TextInput
          value={value}
          onChangeText={onChange}
          placeholder={placeholder}
          placeholderTextColor={colors.mutedFg}
          multiline
          textAlignVertical="top"
          style={[styles.input, styles.taskInput]}
        />
      ) : tasks.length ? (
        <View style={styles.taskList}>
          {tasks.map((task, taskIndex) => (
            <View key={`${task}-${taskIndex}`} style={styles.taskRow}>
              <View style={styles.taskDot} />
              <Small style={styles.flex}>{task}</Small>
            </View>
          ))}
        </View>
      ) : (
        <Small color={colors.mutedFg} style={styles.emptyTask}>No tasks specified.</Small>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md },
  centerText: { textAlign: "center" },
  content: { padding: spacing.md, paddingBottom: spacing.xl },
  headerActions: { flexDirection: "row", alignItems: "center", gap: 5 },
  headerIcon: { width: 34, height: 34, borderRadius: 11, alignItems: "center", justifyContent: "center" },
  headerIconActive: { backgroundColor: "rgba(255,255,255,0.16)" },
  extractCard: { padding: 14 },
  extractHeading: { flexDirection: "row", alignItems: "center", gap: 10 },
  sparkIcon: { width: 34, height: 34, borderRadius: 12, alignItems: "center", justifyContent: "center", backgroundColor: colors.primary + "12" },
  extractCopy: { marginTop: 3, lineHeight: 17 },
  extractButton: { marginTop: 12, minHeight: 42, borderRadius: radii.md, borderWidth: 1, borderColor: colors.primary + "45", backgroundColor: colors.primary + "0D", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 7 },
  disabled: { opacity: 0.65 },
  summary: { marginTop: 13, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  summaryLeft: { flexDirection: "row", alignItems: "center", gap: 8 },
  sourcePill: { paddingHorizontal: 9, paddingVertical: 5, borderRadius: radii.pill, flexDirection: "row", alignItems: "center", gap: 5, backgroundColor: colors.primary + "12" },
  pendingPill: { paddingHorizontal: 9, paddingVertical: 5, borderRadius: radii.pill, flexDirection: "row", alignItems: "center", gap: 5, backgroundColor: colors.warning + "14" },
  editHint: { marginTop: 10, paddingHorizontal: 10, paddingVertical: 9, borderRadius: radii.md, borderWidth: 1, borderColor: colors.primary + "25", backgroundColor: colors.primary + "0A", flexDirection: "row", alignItems: "center", gap: 7 },
  filters: { marginTop: 12, flexDirection: "row", gap: 7 },
  filterChip: { minHeight: 34, paddingHorizontal: 10, borderRadius: radii.pill, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, flexDirection: "row", alignItems: "center", gap: 5 },
  filterChipActive: { borderColor: colors.primary, backgroundColor: colors.primary },
  filterCount: { minWidth: 19, height: 19, borderRadius: 10, paddingHorizontal: 4, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface },
  filterCountActive: { backgroundColor: "rgba(255,255,255,0.16)" },
  table: { marginTop: 12, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.card, overflow: "hidden", ...shadows.sm },
  tableGrid: { minHeight: 50, paddingHorizontal: 10, display: "flex", flexDirection: "row", alignItems: "center", gap: 5 },
  tableGridEditing: {},
  tableHeader: { minHeight: 37, backgroundColor: colors.surface, borderBottomWidth: 1, borderBottomColor: colors.border },
  tableRow: { borderBottomWidth: 1, borderBottomColor: colors.border },
  warningRow: { backgroundColor: colors.warning + "09" },
  orderCell: { width: 27, alignItems: "center", justifyContent: "center", gap: 1 },
  orderButton: { width: 24, height: 19, alignItems: "center", justifyContent: "center" },
  numberCell: { width: 26, alignItems: "center", justifyContent: "center", textAlign: "center" },
  nameCell: { flex: 1, minWidth: 0, flexDirection: "row", alignItems: "center", gap: 5 },
  dayCell: { width: 39, textAlign: "center" },
  windowCell: { width: 50, textAlign: "center" },
  deleteCell: { width: 29, alignItems: "center", justifyContent: "center" },
  deleteButton: { width: 29, height: 29, borderRadius: 10, alignItems: "center", justifyContent: "center", backgroundColor: colors.destructive + "0C" },
  emptyRows: { paddingHorizontal: 16, paddingVertical: 30, alignItems: "center" },
  addButton: { marginTop: 11, minHeight: 46, borderRadius: radii.lg, borderWidth: 1.5, borderStyle: "dashed", borderColor: colors.primary + "55", backgroundColor: colors.primary + "08", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 },
  notice: { marginTop: 12, minHeight: 42, paddingHorizontal: 11, borderRadius: radii.md, borderWidth: 1, borderColor: colors.success + "40", backgroundColor: colors.success + "10", flexDirection: "row", alignItems: "center", gap: 7 },
  inlineMessage: { marginTop: 9 },
  saveBar: { paddingHorizontal: spacing.md, paddingVertical: 12, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.card },
  buttonContent: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 },
  modalBackdrop: { flex: 1, padding: 24, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(46,27,51,0.52)" },
  confirmCard: { width: "100%", maxWidth: 380, padding: 20, borderRadius: radii.xl, alignItems: "center", backgroundColor: colors.card, ...shadows.md },
  successCard: { width: "100%", maxWidth: 380, padding: 24, borderRadius: radii.xl, alignItems: "center", backgroundColor: colors.card, ...shadows.md },
  warningOrb: { width: 52, height: 52, borderRadius: 26, alignItems: "center", justifyContent: "center", backgroundColor: colors.warning + "16" },
  successOrb: { width: 64, height: 64, borderRadius: 32, alignItems: "center", justifyContent: "center", backgroundColor: colors.success + "16" },
  confirmTitle: { marginTop: 13, textAlign: "center" },
  confirmCopy: { marginTop: 7, textAlign: "center", lineHeight: 18 },
  confirmActions: { alignSelf: "stretch", marginTop: 18, flexDirection: "row", gap: 9 },
  secondaryAction: { flex: 1, minHeight: 44, borderRadius: radii.md, borderWidth: 1, borderColor: colors.primary + "45", alignItems: "center", justifyContent: "center" },
  primaryAction: { flex: 1, minHeight: 44, borderRadius: radii.md, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
  successAction: { alignSelf: "stretch", marginTop: 20, minHeight: 46, borderRadius: radii.md, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
  sheetBackdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(46,27,51,0.48)" },
  sheetKeyboard: { maxHeight: "89%" },
  sheet: { maxHeight: "100%", borderTopLeftRadius: 26, borderTopRightRadius: 26, backgroundColor: colors.card, overflow: "hidden", ...shadows.md },
  sheetHandle: { alignSelf: "center", width: 40, height: 5, marginTop: 9, borderRadius: 3, backgroundColor: colors.border },
  sheetHeader: { paddingHorizontal: 17, paddingTop: 10, paddingBottom: 13, borderBottomWidth: 1, borderBottomColor: colors.border, flexDirection: "row", alignItems: "center", gap: 10 },
  visitBadge: { width: 42, height: 42, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.primary + "12" },
  visitBadgeWarning: { backgroundColor: colors.warning + "16" },
  nameInput: { fontFamily: fonts.semibold, fontSize: 15 },
  metaLine: { marginTop: 4, flexDirection: "row", alignItems: "center", gap: 5 },
  closeButton: { width: 32, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface },
  sheetContent: { padding: 17, paddingBottom: 22, gap: 18 },
  warningBanner: { padding: 10, borderRadius: radii.md, borderWidth: 1, borderColor: colors.warning + "35", backgroundColor: colors.warning + "10", flexDirection: "row", alignItems: "flex-start", gap: 7 },
  twoColumns: { flexDirection: "row", gap: 10 },
  field: { flex: 1 },
  fieldLabel: { marginBottom: 5 },
  input: { minHeight: 42, paddingHorizontal: 11, paddingVertical: 9, borderRadius: radii.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.background, color: colors.foreground, fontFamily: fonts.regular, fontSize: 13 },
  sectionTitle: { marginBottom: 8, flexDirection: "row", alignItems: "center", gap: 7 },
  taskInput: { minHeight: 67 },
  taskList: { gap: 7 },
  taskRow: { flexDirection: "row", alignItems: "flex-start", gap: 8 },
  taskDot: { width: 6, height: 6, marginTop: 6, borderRadius: 3, backgroundColor: colors.primary },
  emptyTask: { fontStyle: "italic" },
  commentInput: { minHeight: 72 },
  readComment: { padding: 11, borderRadius: radii.md, backgroundColor: colors.surface, lineHeight: 18 },
  acknowledgeButton: { minHeight: 42, borderRadius: radii.md, borderWidth: 1, borderColor: colors.success + "45", backgroundColor: colors.success + "10", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 },
  sheetFooter: { paddingHorizontal: 17, paddingVertical: 12, borderTopWidth: 1, borderTopColor: colors.border, flexDirection: "row", gap: 10 },
  sheetDelete: { flex: 1, minHeight: 44, borderRadius: radii.md, borderWidth: 1, borderColor: colors.destructive + "35", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6 },
  sheetDone: { flex: 1, minHeight: 44, borderRadius: radii.md, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
});
