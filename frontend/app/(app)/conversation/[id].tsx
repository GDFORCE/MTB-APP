import React, { useEffect, useState } from "react";
import { View, ScrollView, Pressable, TextInput, Alert, Share, ActivityIndicator, StyleSheet } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import {
  X, Pencil, Check, UserPlus, Link2, Search, Users, Bell, BellOff, Clock,
  ShieldCheck, Copy, Trash2, LogOut, Flag, ChevronRight, AlertTriangle, RefreshCcw,
} from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card } from "@/src/components/ui";
import { useAuth } from "@/src/auth/AuthContext";
import { api } from "@/src/api/client";
import { AddMemberSheet } from "@/src/components/AddMemberSheet";

const AUTO_DELETE_OPTIONS: { label: string; days: number | null }[] = [
  { label: "Off", days: null },
  { label: "1 day", days: 1 },
  { label: "7 days", days: 7 },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
];

export default function ConversationInfo() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { user } = useAuth();
  const [conv, setConv] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  const [addMemberOpen, setAddMemberOpen] = useState(false);
  const [addMemberAutoFocus, setAddMemberAutoFocus] = useState(false);
  const [autoDeleteOpen, setAutoDeleteOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportReason, setReportReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setLoading(true); setError("");
    try {
      const r = await api.get(`/conversations/${id}`);
      setConv(r.data);
    } catch {
      setError("Couldn't load this channel. Check your connection and retry.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [id]);

  const startEdit = () => {
    setEditTitle(conv.title || "");
    setEditDescription(conv.description || "");
    setEditing(true);
  };

  const saveEdit = async () => {
    setSavingEdit(true);
    try {
      await api.patch(`/conversations/${id}/settings`, { title: editTitle.trim(), description: editDescription.trim() });
      setConv((prev: any) => ({ ...prev, title: editTitle.trim(), description: editDescription.trim() }));
      setEditing(false);
    } catch {
      Alert.alert("Couldn't save", "Try again in a moment.");
    } finally {
      setSavingEdit(false);
    }
  };

  const toggleNotifications = async () => {
    setBusy(true);
    try {
      const r = await api.post(`/conversations/${id}/flags`, { muted: !conv.muted });
      setConv((prev: any) => ({ ...prev, muted: r.data.muted }));
    } catch {
      Alert.alert("Couldn't update notifications", "Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  const setAutoDelete = async (days: number | null) => {
    setAutoDeleteOpen(false);
    setBusy(true);
    try {
      await api.patch(`/conversations/${id}/settings`, { auto_delete_days: days });
      setConv((prev: any) => ({ ...prev, auto_delete_days: days }));
    } catch {
      Alert.alert("Couldn't update auto-delete", "Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  const shareInviteLink = async () => {
    setBusy(true);
    try {
      const r = await api.get(`/conversations/${id}/invite-link`);
      await Share.share({ message: `Join "${conv.title}" on My Trial Board: mytrialboard://conversations/join/${r.data.token}` });
    } catch {
      Alert.alert("Couldn't create invite link", "Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  const setupSimilarChannel = async () => {
    setBusy(true);
    try {
      const memberIds = (conv.participants || []).map((p: any) => p.id).filter((pid: string) => pid !== user?.id);
      const r = await api.post("/conversations", {
        participant_ids: memberIds, is_group: true,
        title: `${conv.title} (copy)`, description: conv.description, trial_id: conv.trial_id,
      });
      router.replace({ pathname: "/(app)/conversation/[id]", params: { id: r.data.id } });
    } catch {
      Alert.alert("Couldn't set up a similar channel", "Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  const clearMessages = () => {
    Alert.alert("Clear messages", "This clears your view of this channel's history. Other members keep theirs.", [
      { text: "Cancel", style: "cancel" },
      { text: "Clear", style: "destructive", onPress: async () => {
        try { await api.post(`/conversations/${id}/clear`); Alert.alert("Cleared", "Your message history for this channel has been cleared."); }
        catch { Alert.alert("Couldn't clear messages", "Try again in a moment."); }
      } },
    ]);
  };

  const leaveGroup = () => {
    Alert.alert("Leave group", "You'll stop receiving messages from this channel.", [
      { text: "Cancel", style: "cancel" },
      { text: "Leave", style: "destructive", onPress: async () => {
        try { await api.delete(`/conversations/${id}/members/${user?.id}`); router.replace("/(app)/chat"); }
        catch { Alert.alert("Couldn't leave", "Try again in a moment."); }
      } },
    ]);
  };

  const submitReport = async () => {
    setBusy(true);
    try {
      const r = await api.post(`/conversations/${id}/report`, { reason: reportReason.trim() || undefined });
      setReportOpen(false); setReportReason("");
      Alert.alert("Reported", `Support ticket ${r.data.ticket_id} was opened for review.`);
    } catch {
      Alert.alert("Couldn't report this channel", "Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  const removeMember = (memberId: string, memberName: string) => {
    Alert.alert(`Remove ${memberName}?`, "They will lose access to this channel.", [
      { text: "Cancel", style: "cancel" },
      { text: "Remove", style: "destructive", onPress: async () => {
        try {
          await api.delete(`/conversations/${id}/members/${memberId}`);
          setConv((prev: any) => ({ ...prev, participants: (prev.participants || []).filter((p: any) => p.id !== memberId) }));
        } catch { Alert.alert("Couldn't remove member", "Try again in a moment."); }
      } },
    ]);
  };

  if (loading) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.background, alignItems: "center", justifyContent: "center" }}>
        <ActivityIndicator color={colors.primary} />
      </SafeAreaView>
    );
  }
  if (error || !conv) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.background, padding: spacing.md }}>
        <Card>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
            <AlertTriangle size={18} color={colors.destructive} />
            <Body weight="600" style={{ flex: 1 }}>{error || "Channel not found."}</Body>
          </View>
          <Pressable testID="conv-info-retry" onPress={load} style={s.retryBtn}>
            <RefreshCcw size={14} color={colors.primary} />
            <Small weight="700" color={colors.primary}>Retry</Small>
          </Pressable>
        </Card>
      </SafeAreaView>
    );
  }

  const participants: any[] = conv.participants || [];
  const onlineCount = participants.filter((p) => p.is_online).length;
  const autoDeleteLabel = AUTO_DELETE_OPTIONS.find((o) => o.days === (conv.auto_delete_days ?? null))?.label || "Off";
  const complianceStandard = !!conv.trial_id;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <View style={s.header}>
        <Pressable testID="conv-info-close" onPress={() => router.back()} hitSlop={12}><X size={22} color={colors.foreground} /></Pressable>
        <Eyebrow style={{ flex: 1, textAlign: "center" }}>Team channel</Eyebrow>
        {conv.is_group && conv.is_admin ? (
          editing ? (
            <Pressable testID="conv-info-save" onPress={saveEdit} disabled={savingEdit} hitSlop={12}>
              {savingEdit ? <ActivityIndicator size="small" color={colors.primary} /> : <Check size={20} color={colors.primary} />}
            </Pressable>
          ) : (
            <Pressable testID="conv-info-edit" onPress={startEdit} hitSlop={12}><Pencil size={19} color={colors.foreground} /></Pressable>
          )
        ) : <View style={{ width: 22 }} />}
      </View>

      <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl, gap: spacing.sm }}>
        <View style={{ alignItems: "center", paddingVertical: spacing.md }}>
          <View style={s.bigAvatar}><Users size={30} color={colors.primaryFg} /></View>
          {editing ? (
            <>
              <TextInput testID="conv-info-title-input" value={editTitle} onChangeText={setEditTitle} style={s.titleInput} placeholder="Channel name" placeholderTextColor={colors.mutedFg} />
              <TextInput testID="conv-info-desc-input" value={editDescription} onChangeText={setEditDescription} style={s.descInput} placeholder="Description" placeholderTextColor={colors.mutedFg} multiline />
            </>
          ) : (
            <>
              <H1 style={{ marginTop: 10, textAlign: "center" }}>{conv.title || "Conversation"}</H1>
              {conv.protocol_id ? (
                <View style={s.protocolTag}><Small weight="700" color={colors.primary}>{conv.protocol_id} · {conv.trial_title || "Site coordination"}</Small></View>
              ) : null}
              {conv.description ? <Small style={{ marginTop: 8, textAlign: "center", paddingHorizontal: spacing.lg }}>{conv.description}</Small> : null}
            </>
          )}
          <Small style={{ marginTop: 10 }}>{participants.length} members · {onlineCount} online</Small>
        </View>

        {conv.is_group && (
          <View style={{ flexDirection: "row", gap: 10 }}>
            <Pressable testID="conv-info-add-member" style={s.actionBtn} onPress={() => { setAddMemberAutoFocus(false); setAddMemberOpen(true); }}>
              <UserPlus size={18} color={colors.primary} />
              <Small weight="700" color={colors.primary}>Add member</Small>
            </Pressable>
            <Pressable testID="conv-info-invite-link" style={s.actionBtn} onPress={shareInviteLink} disabled={busy}>
              <Link2 size={18} color={colors.primary} />
              <Small weight="700" color={colors.primary}>Invite link</Small>
            </Pressable>
            <Pressable testID="conv-info-find-member" style={s.actionBtn} onPress={() => { setAddMemberAutoFocus(true); setAddMemberOpen(true); }}>
              <Search size={18} color={colors.primary} />
              <Small weight="700" color={colors.primary}>Find member</Small>
            </Pressable>
          </View>
        )}

        <Eyebrow style={{ marginTop: spacing.md }}>Who&apos;s in this channel</Eyebrow>
        <Card padded={false}>
          {participants.map((p, i) => (
            <Pressable
              key={p.id}
              testID={`conv-info-member-${p.id}`}
              onLongPress={() => (conv.is_admin && p.id !== user?.id ? removeMember(p.id, p.full_name) : undefined)}
              style={[s.memberRow, i > 0 && { borderTopWidth: 1, borderTopColor: colors.border }]}
            >
              <View style={s.avatar}><Small weight="700" color={colors.primary}>{p.avatar_initials || "?"}</Small></View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Body weight="600" numberOfLines={1}>{p.full_name}{p.id === user?.id ? " (you)" : ""}{p.admin ? "  ·  Admin" : ""}</Body>
                <Small numberOfLines={1}>{String(p.role || "").toUpperCase()}{p.organization ? ` · ${p.organization}` : ""}</Small>
              </View>
              {p.is_online && <View style={s.onlineDot} />}
            </Pressable>
          ))}
        </Card>

        <Eyebrow style={{ marginTop: spacing.md }}>Shared files & media</Eyebrow>
        <Pressable testID="conv-info-all-files" onPress={() => router.push({ pathname: "/(app)/conversation/[id]/files", params: { id: String(id) } })}>
          <Card style={{ flexDirection: "row", alignItems: "center" }}>
            <Small style={{ flex: 1 }}>{conv.media_count || 0} item{conv.media_count === 1 ? "" : "s"}</Small>
            <Small weight="700" color={colors.primary}>All {conv.media_count || 0}</Small>
            <ChevronRight size={16} color={colors.primary} />
          </Card>
        </Pressable>

        <Eyebrow style={{ marginTop: spacing.md }}>Channel controls</Eyebrow>
        <Card padded={false}>
          <Pressable testID="conv-info-notifications" onPress={toggleNotifications} disabled={busy} style={[s.controlRow]}>
            {conv.muted ? <BellOff size={18} color={colors.mutedFg} /> : <Bell size={18} color={colors.primary} />}
            <View style={{ flex: 1, marginLeft: 10 }}>
              <Body weight="600">Notifications</Body>
              <Small>{conv.muted ? "Off" : "Every message"}</Small>
            </View>
            <View style={[s.toggleTrack, !conv.muted && s.toggleTrackOn]}>
              <View style={[s.toggleThumb, !conv.muted && s.toggleThumbOn]} />
            </View>
          </Pressable>
          <Pressable testID="conv-info-auto-delete" onPress={() => setAutoDeleteOpen(true)} style={[s.controlRow, { borderTopWidth: 1, borderTopColor: colors.border }]}>
            <Clock size={18} color={colors.primary} />
            <View style={{ flex: 1, marginLeft: 10 }}>
              <Body weight="600">Auto-delete timer</Body>
              <Small>Messages stay until deleted</Small>
            </View>
            <View style={s.pill}><Small weight="700" color={colors.primary}>{autoDeleteLabel}</Small></View>
          </Pressable>
          <View style={[s.controlRow, { borderTopWidth: 1, borderTopColor: colors.border }]}>
            <ShieldCheck size={18} color={colors.success} />
            <View style={{ flex: 1, marginLeft: 10 }}>
              <Body weight="600">Compliance & data controls</Body>
              <Small>Retention and audit follow trial policy</Small>
            </View>
            <View style={s.pill}><Small weight="700" color={colors.primary}>{complianceStandard ? "Standard" : "General"}</Small></View>
          </View>
        </Card>

        {autoDeleteOpen && (
          <Card>
            {AUTO_DELETE_OPTIONS.map((opt) => (
              <Pressable key={opt.label} testID={`auto-delete-${opt.days ?? "off"}`} onPress={() => setAutoDelete(opt.days)} style={{ paddingVertical: 10, flexDirection: "row", alignItems: "center" }}>
                <Body weight={opt.days === (conv.auto_delete_days ?? null) ? "700" : "400"} style={{ flex: 1 }}>{opt.label}</Body>
                {opt.days === (conv.auto_delete_days ?? null) && <Check size={16} color={colors.primary} />}
              </Pressable>
            ))}
          </Card>
        )}

        <Card style={{ flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.secondary, borderColor: colors.secondary }}>
          <ShieldCheck size={18} color={colors.primary} />
          <Small style={{ flex: 1 }} color={colors.secondaryFg}>
            Encrypted in transit and at rest. Every message here is covered by MTB&apos;s data-protection policy.{" "}
            <Small color={colors.primary} weight="700" onPress={() => router.push("/(app)/data-policy")}>View policy</Small>
          </Small>
        </Card>

        {conv.is_group && (
          <Pressable testID="conv-info-duplicate" onPress={setupSimilarChannel} disabled={busy}>
            <Card style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
              <Copy size={18} color={colors.primary} />
              <View style={{ flex: 1 }}>
                <Body weight="700" color={colors.primary}>Set up a similar channel</Body>
                <Small>Start a new channel with these {participants.length} members, ready to adjust</Small>
              </View>
            </Card>
          </Pressable>
        )}

        {conv.is_group && (
          <Card padded={false} style={{ marginTop: spacing.sm }}>
            <Pressable testID="conv-info-clear" onPress={clearMessages} style={s.dangerRow}>
              <Trash2 size={18} color={colors.destructive} />
              <Small weight="700" color={colors.destructive}>Clear messages</Small>
            </Pressable>
            <Pressable testID="conv-info-leave" onPress={leaveGroup} style={[s.dangerRow, { borderTopWidth: 1, borderTopColor: colors.border }]}>
              <LogOut size={18} color={colors.destructive} />
              <Small weight="700" color={colors.destructive}>Leave group</Small>
            </Pressable>
            <Pressable testID="conv-info-report" onPress={() => setReportOpen(true)} style={[s.dangerRow, { borderTopWidth: 1, borderTopColor: colors.border }]}>
              <Flag size={18} color={colors.destructive} />
              <Small weight="700" color={colors.destructive}>Report group</Small>
            </Pressable>
          </Card>
        )}
      </ScrollView>

      <AddMemberSheet
        visible={addMemberOpen}
        onClose={() => setAddMemberOpen(false)}
        conversationId={String(id)}
        existingIds={participants.map((p) => p.id)}
        autoFocusSearch={addMemberAutoFocus}
        onMemberAdded={(updated) => setConv((prev: any) => ({ ...prev, participant_ids: updated.participant_ids }))}
      />

      {reportOpen && (
        <View style={s.reportOverlay}>
          <Card style={{ width: "100%" }}>
            <Body weight="700" style={{ marginBottom: 8 }}>Report this channel</Body>
            <TextInput
              testID="conv-info-report-reason"
              value={reportReason}
              onChangeText={setReportReason}
              placeholder="What's wrong? (optional)"
              placeholderTextColor={colors.mutedFg}
              style={s.reportInput}
              multiline
            />
            <View style={{ flexDirection: "row", gap: 10, marginTop: 12 }}>
              <Pressable testID="conv-info-report-cancel" onPress={() => setReportOpen(false)} style={[s.actionBtn, { flex: 1 }]}>
                <Small weight="700" color={colors.foreground}>Cancel</Small>
              </Pressable>
              <Pressable testID="conv-info-report-submit" onPress={submitReport} disabled={busy} style={[s.actionBtn, { flex: 1, backgroundColor: colors.destructive, borderColor: colors.destructive }]}>
                {busy ? <ActivityIndicator size="small" color={colors.destructiveFg} /> : <Small weight="700" color={colors.destructiveFg}>Submit</Small>}
              </Pressable>
            </View>
          </Card>
        </View>
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.md, paddingVertical: 12, borderBottomWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  bigAvatar: { width: 84, height: 84, borderRadius: 42, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
  protocolTag: { marginTop: 8, paddingHorizontal: 12, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.secondary },
  titleInput: { marginTop: 10, width: "100%", textAlign: "center", fontFamily: "BricolageGrotesque-Bold", fontSize: 22, color: colors.foreground, borderBottomWidth: 1, borderColor: colors.border, paddingVertical: 4 },
  descInput: { marginTop: 8, width: "100%", textAlign: "center", fontSize: 14, color: colors.foreground, paddingHorizontal: spacing.lg, minHeight: 40 },
  actionBtn: { flex: 1, alignItems: "center", gap: 6, paddingVertical: 12, borderRadius: radii.lg, borderWidth: 1, borderColor: colors.primary + "44", backgroundColor: colors.primary + "0D" },
  avatar: { width: 38, height: 38, borderRadius: 19, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  memberRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 10, paddingHorizontal: spacing.md },
  onlineDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.success },
  controlRow: { flexDirection: "row", alignItems: "center", paddingVertical: 12, paddingHorizontal: spacing.md },
  toggleTrack: { width: 44, height: 26, borderRadius: 13, backgroundColor: colors.border, padding: 2, justifyContent: "center" },
  toggleTrackOn: { backgroundColor: colors.success },
  toggleThumb: { width: 22, height: 22, borderRadius: 11, backgroundColor: colors.card },
  toggleThumbOn: { alignSelf: "flex-end" },
  pill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: colors.secondary },
  dangerRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 12, paddingHorizontal: spacing.md },
  reportOverlay: { position: "absolute", left: 0, right: 0, top: 0, bottom: 0, backgroundColor: "rgba(46,27,51,0.45)", alignItems: "center", justifyContent: "center", padding: spacing.md },
  reportInput: { minHeight: 80, borderWidth: 1, borderColor: colors.border, borderRadius: radii.md, padding: 10, color: colors.foreground, textAlignVertical: "top" },
  retryBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 10, borderRadius: 999, borderWidth: 1, borderColor: colors.primary + "44", backgroundColor: colors.primary + "0D" },
});
