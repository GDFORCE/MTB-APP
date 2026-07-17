// Real-time messaging: conversation list + thread view over the live
// /conversations HTTP contracts and the authenticated WebSocket.
//
// Reliability model (approved Messages states):
//   • initial load has real loading / error+retry / empty states;
//   • the socket reports connecting / online / offline, reconnects with
//     exponential backoff, and resyncs conversations + the open thread after
//     every reconnect (messages missed while offline are recovered);
//   • sends are optimistic — a pending bubble appears immediately, failures
//     stay in the thread as "Not sent" with tap-to-retry / long-press-discard;
//   • send stays disabled without text; HTTP sending works even while the
//     socket is down, so the offline banner never blocks output silently.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, ScrollView, TextInput, Pressable, StyleSheet, FlatList, KeyboardAvoidingView, Platform, ActivityIndicator, Modal } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { ArrowLeft, Send, WifiOff, RefreshCcw, AlertTriangle, Pin, BellOff, SquarePen, Users, X, Search } from "lucide-react-native";
import { colors, spacing } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card } from "@/src/components/ui";
import { useAuth } from "@/src/auth/AuthContext";
import { api, tokenStore, wsUrl } from "@/src/api/client";
import { animateNextLayout } from "@/src/lib/motion";

type Connection = "connecting" | "online" | "offline";

export default function Chat() {
  const router = useRouter();
  const params = useLocalSearchParams<{ conversationId?: string; participantId?: string }>();
  const { user } = useAuth();
  const [convs, setConvs] = useState<any[]>([]);
  const [users, setUsers] = useState<any[]>([]);
  const [active, setActive] = useState<any | null>(null);
  const [messages, setMessages] = useState<any[]>([]);
  const [text, setText] = useState("");
  const [typing, setTyping] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [threadLoading, setThreadLoading] = useState(false);
  const [threadError, setThreadError] = useState("");
  const [starting, setStarting] = useState<string | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [directoryLoaded, setDirectoryLoaded] = useState(false);
  const [filter, setFilter] = useState<"all" | "unread" | "groups">("all");
  const [details, setDetails] = useState<any | null>(null);
  const [flagBusy, setFlagBusy] = useState(false);
  const [composeOpen, setComposeOpen] = useState(false);
  const [composeQuery, setComposeQuery] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttempts = useRef(0);
  const mountedRef = useRef(true);
  const autoOpenedRef = useRef(false);
  const activeIdRef = useRef<string | undefined>(undefined);
  const listRef = useRef<FlatList>(null);
  const userId = user?.id;

  useEffect(() => {
    activeIdRef.current = active?.id;
  }, [active?.id]);

  const loadDirectory = useCallback(async (silent = false) => {
    if (!silent) { setLoading(true); setLoadError(""); }
    try {
      const [c, u] = await Promise.all([api.get("/conversations"), api.get("/messaging/recipients")]);
      setConvs(c.data); setUsers(u.data);
      setLoadError("");
    } catch {
      if (!silent) setLoadError("Couldn't load your messages. Check your connection and retry.");
    } finally {
      setLoading(false);
      setDirectoryLoaded(true);
    }
  }, []);

  // Re-sync the open thread (used after a reconnect so nothing is missed).
  const resyncActive = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) return;
    try {
      const r = await api.get(`/conversations/${id}/messages`);
      setMessages(prev => {
        const pendingLocal = prev.filter(m => m.pending || m.failed);
        return [...r.data, ...pendingLocal];
      });
    } catch { /* thread keeps its current contents; user can pull the thread again */ }
  }, []);

  // ── WebSocket with exponential-backoff reconnect ──
  useEffect(() => {
    if (!userId) return;
    mountedRef.current = true;
    loadDirectory();

    const connect = async () => {
      const t = await tokenStore.get("access_token");
      if (!t || !mountedRef.current) return;
      setConnection(prev => (prev === "online" ? prev : "connecting"));
      const ws = new WebSocket(wsUrl(t));
      wsRef.current = ws;
      ws.onopen = () => {
        if (!mountedRef.current) return;
        const wasRetry = reconnectAttempts.current > 0;
        reconnectAttempts.current = 0;
        setConnection("online");
        if (wasRetry) { loadDirectory(true); resyncActive(); }
      };
      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.type === "message") {
            if (data.conversation_id === activeIdRef.current) {
              setMessages(prev => prev.some(m => m.id === data.id) ? prev : [...prev, data]);
              ws.send(JSON.stringify({ type: "read", conversation_id: data.conversation_id }));
            }
            setConvs(prev => prev.map(c => c.id === data.conversation_id
              ? { ...c, last_message: data.content, unread_count: data.conversation_id === activeIdRef.current ? 0 : (c.unread_count || 0) + (data.sender_id === userId ? 0 : 1) }
              : c));
          } else if (data.type === "typing" && data.conversation_id === activeIdRef.current) {
            setTyping(true); setTimeout(() => setTyping(false), 2500);
          } else if (data.type === "read" && data.conversation_id === activeIdRef.current) {
            // live read receipt: mark my messages as read by that member
            setMessages(prev => prev.map(m => m.sender_id === userId
              ? { ...m, read_by: { ...(m.read_by || {}), [data.user_id]: data.read_at } }
              : m));
          }
        } catch {}
      };
      const scheduleReconnect = () => {
        if (!mountedRef.current) return;
        setConnection("offline");
        const delay = Math.min(30000, 2000 * 2 ** reconnectAttempts.current);
        reconnectAttempts.current += 1;
        if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
        reconnectTimer.current = setTimeout(connect, delay);
      };
      ws.onerror = () => { try { ws.close(); } catch {} };
      ws.onclose = scheduleReconnect;
    };
    connect();

    return () => {
      mountedRef.current = false;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      const ws = wsRef.current;
      if (ws) { ws.onclose = null; ws.close(); }
    };
  }, [userId, loadDirectory, resyncActive]);

  const openConv = useCallback(async (c: any) => {
    setError(""); setThreadError("");
    setActive(c);
    setThreadLoading(true);
    setConvs(prev => prev.map(x => x.id === c.id ? { ...x, unread_count: 0 } : x));
    try {
      const r = await api.get(`/conversations/${c.id}/messages`);
      setMessages(r.data);
      setTimeout(() => listRef.current?.scrollToEnd({ animated: false }), 100);
    } catch {
      setMessages([]);
      setThreadError("Couldn't load this conversation.");
    } finally {
      setThreadLoading(false);
    }
  }, []);

  const startWith = useCallback(async (otherId: string) => {
    setStarting(otherId);
    try {
      const r = await api.post("/conversations", { participant_ids: [otherId] });
      const c = r.data;
      const refresh = await api.get("/conversations");
      setConvs(refresh.data);
      const enriched = refresh.data.find((x: any) => x.id === c.id) || c;
      await openConv(enriched);
    } finally {
      setStarting(null);
    }
  }, [openConv]);

  useEffect(() => {
    if (!directoryLoaded || autoOpenedRef.current) return;
    const requestedConversation = params.conversationId
      ? convs.find((conversation) => conversation.id === params.conversationId)
      : null;
    const requestedParticipant = String(params.participantId || "").trim();
    const existingDirect = requestedParticipant
      ? convs.find((conversation) => (
          conversation.other_participant?.id === requestedParticipant
          || conversation.participant_ids?.includes(requestedParticipant)
        ))
      : null;
    if (!requestedConversation && !requestedParticipant) return;
    autoOpenedRef.current = true;
    if (requestedConversation || existingDirect) {
      openConv(requestedConversation || existingDirect);
      return;
    }
    startWith(requestedParticipant).catch((e: any) => {
      setError(e?.response?.data?.detail || "Could not open the requested conversation.");
    });
  }, [convs, directoryLoaded, openConv, params.conversationId, params.participantId, startWith]);

  // ── Optimistic send with persistent failed-message retry ──
  const sendContent = useCallback(async (content: string, reuseLocalId?: string) => {
    if (!active) return;
    const localId = reuseLocalId || `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const optimistic = {
      id: localId, content, sender_id: userId,
      created_at: new Date().toISOString(), pending: true, failed: false,
    };
    setMessages(prev => {
      const without = prev.filter(m => m.id !== localId);
      return [...without, optimistic];
    });
    setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 50);
    setSending(true);
    try {
      const response = await api.post(`/conversations/${active.id}/messages`, { content });
      setMessages(prev => {
        const replaced = prev.map(m => (m.id === localId ? response.data : m));
        return replaced.filter((m, i, arr) => arr.findIndex(x => x.id === m.id) === i);
      });
      setConvs(prev => prev.map(c => c.id === active.id ? { ...c, last_message: response.data.content } : c));
    } catch {
      setMessages(prev => prev.map(m => m.id === localId ? { ...m, pending: false, failed: true } : m));
    } finally {
      setSending(false);
    }
  }, [active, userId]);

  const send = async () => {
    const content = text.trim();
    if (!content || !active || sending) return;
    setError("");
    setText("");
    await sendContent(content);
  };

  const onType = (v: string) => {
    setText(v);
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN && active) {
      ws.send(JSON.stringify({ type: "typing", conversation_id: active.id }));
    }
  };

  // Per-user pin/mute via POST /conversations/{id}/flags (member-gated).
  const setFlags = async (c: any, flags: { pinned?: boolean; muted?: boolean }) => {
    setFlagBusy(true);
    try {
      const r = await api.post(`/conversations/${c.id}/flags`, flags);
      animateNextLayout();
      setConvs(prev => prev.map(x => x.id === c.id ? { ...x, pinned: r.data.pinned, muted: r.data.muted } : x));
      setDetails((prev: any) => prev && prev.id === c.id ? { ...prev, pinned: r.data.pinned, muted: r.data.muted } : prev);
    } catch {
      setError("Couldn't update this conversation. Try again.");
    } finally { setFlagBusy(false); }
  };

  const unreadTotal = convs.filter(c => (c.unread_count || 0) > 0).length;
  const groupTotal = convs.filter(c => c.is_group).length;
  const visibleConvs = convs
    .filter(c => filter === "all" ? true : filter === "unread" ? (c.unread_count || 0) > 0 : !!c.is_group)
    .slice()
    .sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned));

  const connectionBanner = connection === "offline" ? (
    <View testID="chat-connection-banner" style={s.offlineBanner} accessibilityLiveRegion="polite">
      <WifiOff size={13} color={colors.warning} />
      <Small color={colors.warning} style={{ flex: 1, fontSize: 12 }}>
        You&apos;re offline — reconnecting… New messages will sync automatically.
      </Small>
    </View>
  ) : null;

  // ── Conversation list view ──
  if (!active) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
        <View style={s.header}>
          <Pressable onPress={() => router.back()} hitSlop={12}><ArrowLeft size={22} color={colors.foreground} /></Pressable>
          <View style={{ flex: 1, marginLeft: 12 }}>
            <Eyebrow color={colors.accent}>Conversations</Eyebrow>
            <H1>Messages</H1>
          </View>
          <Pressable testID="chat-compose" onPress={() => { setComposeQuery(""); setComposeOpen(true); }} hitSlop={10} style={s.composeBtn} accessibilityLabel="New message">
            <SquarePen size={19} color={colors.primary} />
          </Pressable>
        </View>
        {connectionBanner}
        {!loading && !loadError && (
          <View style={s.filterRow}>
            {([
              { key: "all", label: `All · ${convs.length}` },
              { key: "unread", label: `Unread · ${unreadTotal}` },
              { key: "groups", label: `Groups · ${groupTotal}` },
            ] as const).map(f => {
              const on = filter === f.key;
              return (
                <Pressable key={f.key} testID={`chat-filter-${f.key}`} onPress={() => { animateNextLayout(); setFilter(f.key); }} style={[s.filterChip, on && s.filterChipOn]}>
                  <Small weight="700" color={on ? colors.primaryFg : colors.mutedFg}>{f.label}</Small>
                </Pressable>
              );
            })}
          </View>
        )}
        {loading ? (
          <View style={{ flex: 1, alignItems: "center", justifyContent: "center", gap: 10 }}>
            <ActivityIndicator color={colors.primary} />
            <Small color={colors.mutedFg}>Loading your messages…</Small>
          </View>
        ) : loadError ? (
          <View style={{ padding: spacing.md }}>
            <Card>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                <AlertTriangle size={18} color={colors.destructive} />
                <Body weight="600" style={{ flex: 1 }}>{loadError}</Body>
              </View>
              <Pressable testID="chat-retry" onPress={() => loadDirectory()} style={s.retryBtn}>
                <RefreshCcw size={14} color={colors.primary} />
                <Small weight="700" color={colors.primary}>Retry</Small>
              </Pressable>
            </Card>
          </View>
        ) : (
        <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }}>
          {error ? <Small color={colors.destructive} style={{ marginBottom: spacing.sm }}>{error}</Small> : null}
          {visibleConvs.length > 0 && <Eyebrow style={{ marginBottom: spacing.sm }}>{filter === "all" ? "Recent" : filter === "unread" ? "Unread" : "Groups"}</Eyebrow>}
          {convs.length > 0 && visibleConvs.length === 0 && (
            <Card style={{ alignItems: "center", paddingVertical: spacing.lg, marginBottom: spacing.sm }}>
              <Body weight="600">{filter === "unread" ? "You're all caught up" : "No group conversations yet"}</Body>
              <Small style={{ marginTop: 4 }}>{filter === "unread" ? "No unread conversations." : "Group chats appear here once you're added to one."}</Small>
            </Card>
          )}
          {visibleConvs.map(c => {
            const other = c.other_participant;
            const name = c.title || other?.full_name || "Conversation";
            return (
              <Pressable key={c.id} testID={`conv-${c.id}`} onPress={() => openConv(c)} onLongPress={() => setDetails(c)}>
                <Card style={{ marginBottom: spacing.sm }}>
                  <View style={{ flexDirection: "row", alignItems: "center" }}>
                    <View style={s.avatar}>
                      {c.is_group
                        ? <Users size={18} color={colors.primary} />
                        : <Body weight="700" color={colors.primary}>{other?.avatar_initials || "?"}</Body>}
                    </View>
                    <View style={{ flex: 1, marginLeft: 12 }}>
                      <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                        <Body weight="700" style={{ flexShrink: 1 }} numberOfLines={1}>{name}</Body>
                        {c.pinned ? <Pin size={12} color={colors.accent} /> : null}
                        {c.muted ? <BellOff size={12} color={colors.mutedFg} /> : null}
                        <View style={{ flex: 1 }} />
                        {c.unread_count > 0 && (
                          <View style={[s.badge, c.muted && { backgroundColor: colors.mutedFg }]}>
                            <Small color={colors.primaryFg} style={{ fontSize: 10, fontWeight: "700" as any }}>{c.unread_count}</Small>
                          </View>
                        )}
                      </View>
                      <Small numberOfLines={1} style={{ marginTop: 2 }}>{c.last_message || "Start chatting"}</Small>
                    </View>
                  </View>
                </Card>
              </Pressable>
            );
          })}
          {convs.length === 0 && users.length === 0 && (
            <Card style={{ alignItems: "center", paddingVertical: spacing.lg }}>
              <Body weight="600">No conversations available</Body>
              <Small style={{ marginTop: 4, textAlign: "center" }}>Your care-team contacts appear here once they are linked to your account.</Small>
            </Card>
          )}
          {users.length > 0 && <Eyebrow style={{ marginTop: spacing.lg, marginBottom: spacing.sm }}>Start a new chat</Eyebrow>}
          {users.map(u => (
            <Pressable
              key={u.id}
              testID={`user-${u.id}`}
              disabled={!!starting}
              onPress={() => startWith(u.id).catch((e: any) => setError(e?.response?.data?.detail || "Couldn't start this conversation."))}
            >
              <Card style={{ marginBottom: spacing.sm, opacity: starting && starting !== u.id ? 0.6 : 1 }}>
                <View style={{ flexDirection: "row", alignItems: "center" }}>
                  <View style={s.avatar}><Body weight="700" color={colors.primary}>{u.avatar_initials}</Body></View>
                  <View style={{ flex: 1, marginLeft: 12 }}>
                    <Body weight="700">{u.full_name}</Body>
                    <Small style={{ marginTop: 2 }}>{u.role.toUpperCase()} · {u.organization || u.email}</Small>
                  </View>
                  {starting === u.id
                    ? <ActivityIndicator size="small" color={colors.primary} />
                    : u.is_online && <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: colors.success }} />}
                </View>
              </Card>
            </Pressable>
          ))}
        </ScrollView>
        )}

        {/* Conversation / group details + pin/mute (long-press a conversation) */}
        <Modal visible={!!details} transparent animationType="slide" onRequestClose={() => setDetails(null)}>
          <View style={s.modalOverlay}>
            <Pressable style={{ flex: 1 }} onPress={() => setDetails(null)} />
            {details && (
              <View style={s.modalSheet}>
                <View style={{ flexDirection: "row", alignItems: "center", marginBottom: spacing.sm }}>
                  <Body weight="700" style={{ flex: 1 }}>{details.title || details.other_participant?.full_name || "Conversation"}</Body>
                  <Pressable onPress={() => setDetails(null)} hitSlop={10}><X size={18} color={colors.mutedFg} /></Pressable>
                </View>
                {details.is_group ? (
                  <>
                    <Eyebrow style={{ marginBottom: 6 }}>{(details.participants || []).length} members</Eyebrow>
                    <ScrollView style={{ maxHeight: 260 }}>
                      {(details.participants || []).map((p: any) => (
                        <View key={p.id} style={{ flexDirection: "row", alignItems: "center", paddingVertical: 8, gap: 10 }}>
                          <View style={[s.avatar, { width: 34, height: 34, borderRadius: 17 }]}><Small weight="700" color={colors.primary}>{p.avatar_initials || "?"}</Small></View>
                          <View style={{ flex: 1, minWidth: 0 }}>
                            <Body weight="600" numberOfLines={1}>{p.full_name}{p.id === userId ? " (you)" : ""}</Body>
                            <Small numberOfLines={1}>{(p.role || "").toUpperCase()}{p.organization ? ` · ${p.organization}` : ""}</Small>
                          </View>
                          {p.is_online && <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.success }} />}
                        </View>
                      ))}
                    </ScrollView>
                  </>
                ) : details.other_participant ? (
                  <Small style={{ marginBottom: 4 }}>
                    {(details.other_participant.role || "").toUpperCase()}
                    {details.other_participant.organization ? ` · ${details.other_participant.organization}` : ""}
                  </Small>
                ) : null}
                <View style={{ flexDirection: "row", gap: 10, marginTop: spacing.md }}>
                  <Pressable
                    testID="conv-pin-toggle"
                    disabled={flagBusy}
                    onPress={() => setFlags(details, { pinned: !details.pinned })}
                    style={[s.flagBtn, details.pinned && s.flagBtnOn]}
                  >
                    <Pin size={14} color={details.pinned ? colors.primaryFg : colors.primary} />
                    <Small weight="700" color={details.pinned ? colors.primaryFg : colors.primary}>{details.pinned ? "Unpin" : "Pin"}</Small>
                  </Pressable>
                  <Pressable
                    testID="conv-mute-toggle"
                    disabled={flagBusy}
                    onPress={() => setFlags(details, { muted: !details.muted })}
                    style={[s.flagBtn, details.muted && s.flagBtnOn]}
                  >
                    <BellOff size={14} color={details.muted ? colors.primaryFg : colors.primary} />
                    <Small weight="700" color={details.muted ? colors.primaryFg : colors.primary}>{details.muted ? "Unmute" : "Mute"}</Small>
                  </Pressable>
                </View>
                <Pressable onPress={() => { const c = details; setDetails(null); openConv(c); }} style={[s.flagBtn, { marginTop: 10, borderColor: colors.border }]}>
                  <Small weight="700" color={colors.foreground}>Open conversation</Small>
                </Pressable>
              </View>
            )}
          </View>
        </Modal>

        {/* Compose: searchable authorized-recipient picker */}
        <Modal visible={composeOpen} transparent animationType="slide" onRequestClose={() => setComposeOpen(false)}>
          <View style={s.modalOverlay}>
            <Pressable style={{ flex: 1 }} onPress={() => setComposeOpen(false)} />
            <View style={s.modalSheet}>
              <View style={{ flexDirection: "row", alignItems: "center", marginBottom: spacing.sm }}>
                <Body weight="700" style={{ flex: 1 }}>New message</Body>
                <Pressable onPress={() => setComposeOpen(false)} hitSlop={10}><X size={18} color={colors.mutedFg} /></Pressable>
              </View>
              <View style={s.composeSearch}>
                <Search size={15} color={colors.mutedFg} />
                <TextInput
                  testID="compose-search"
                  value={composeQuery}
                  onChangeText={setComposeQuery}
                  placeholder="Search your care team & contacts"
                  placeholderTextColor={colors.mutedFg + "99"}
                  style={{ flex: 1, paddingVertical: 8, color: colors.foreground, fontSize: 14 }}
                />
              </View>
              <ScrollView style={{ maxHeight: 320, marginTop: spacing.sm }} keyboardShouldPersistTaps="handled">
                {users
                  .filter(u => {
                    const q = composeQuery.trim().toLowerCase();
                    if (!q) return true;
                    return [u.full_name, u.role, u.organization, u.email].some(v => String(v || "").toLowerCase().includes(q));
                  })
                  .map(u => (
                    <Pressable
                      key={u.id}
                      testID={`compose-user-${u.id}`}
                      disabled={!!starting}
                      onPress={() => {
                        setComposeOpen(false);
                        startWith(u.id).catch((e: any) => setError(e?.response?.data?.detail || "Couldn't start this conversation."));
                      }}
                      style={{ flexDirection: "row", alignItems: "center", paddingVertical: 10, gap: 10 }}
                    >
                      <View style={[s.avatar, { width: 36, height: 36, borderRadius: 18 }]}><Small weight="700" color={colors.primary}>{u.avatar_initials}</Small></View>
                      <View style={{ flex: 1, minWidth: 0 }}>
                        <Body weight="600" numberOfLines={1}>{u.full_name}</Body>
                        <Small numberOfLines={1}>{u.role.toUpperCase()} · {u.organization || u.email}</Small>
                      </View>
                      {u.is_online && <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.success }} />}
                    </Pressable>
                  ))}
                {users.length === 0 && (
                  <Small color={colors.mutedFg} style={{ paddingVertical: spacing.md, textAlign: "center" }}>
                    No authorized contacts are available for your account.
                  </Small>
                )}
              </ScrollView>
            </View>
          </View>
        </Modal>
      </SafeAreaView>
    );
  }

  // ── Active conversation view ──
  const other = active.other_participant;
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={["top", "bottom"]}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : "height"} style={{ flex: 1 }}>
        <View style={s.header}>
          <Pressable onPress={() => setActive(null)} hitSlop={12}><ArrowLeft size={22} color={colors.foreground} /></Pressable>
          <View style={[s.avatar, { marginLeft: 12 }]}><Body weight="700" color={colors.primary}>{other?.avatar_initials || "?"}</Body></View>
          <View style={{ flex: 1, marginLeft: 12 }}>
            <Body weight="700">{other?.full_name || active.title}</Body>
            <Small>{connection === "offline" ? "reconnecting…" : typing ? "typing…" : other?.is_online ? "online" : "offline"}</Small>
          </View>
        </View>
        {connectionBanner}

        {threadLoading ? (
          <View style={{ flex: 1, alignItems: "center", justifyContent: "center", gap: 10 }}>
            <ActivityIndicator color={colors.primary} />
            <Small color={colors.mutedFg}>Loading conversation…</Small>
          </View>
        ) : threadError ? (
          <View style={{ flex: 1, padding: spacing.md }}>
            <Card>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                <AlertTriangle size={18} color={colors.destructive} />
                <Body weight="600" style={{ flex: 1 }}>{threadError}</Body>
              </View>
              <Pressable testID="thread-retry" onPress={() => openConv(active)} style={s.retryBtn}>
                <RefreshCcw size={14} color={colors.primary} />
                <Small weight="700" color={colors.primary}>Retry</Small>
              </Pressable>
            </Card>
          </View>
        ) : (
        <FlatList
          ref={listRef}
          data={messages}
          keyExtractor={(m) => m.id}
          contentContainerStyle={{ padding: spacing.md, gap: 8 }}
          onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: true })}
          ListEmptyComponent={
            <View style={{ alignItems: "center", paddingTop: spacing.xl }}>
              <Small color={colors.mutedFg}>No messages yet — say hello.</Small>
            </View>
          }
          renderItem={({ item }) => {
            const mine = item.sender_id === user?.id;
            const bubble = (
              <View style={[s.bubble, mine ? s.bubbleMine : s.bubbleOther, item.failed && s.bubbleFailed]}>
                <Body color={mine ? colors.primaryFg : colors.foreground}>{item.content}</Body>
                <Small color={mine ? colors.overlay25 : colors.mutedFg} style={{ marginTop: 4, fontSize: 10 }}>
                  {item.pending
                    ? "Sending…"
                    : item.failed
                      ? "Not sent"
                      : `${new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}${
                          mine && item.read_by && Object.keys(item.read_by).some(id => id !== userId) ? " · Read" : ""}`}
                </Small>
              </View>
            );
            if (!item.failed) return bubble;
            return (
              <Pressable
                testID={`retry-msg-${item.id}`}
                onPress={() => sendContent(item.content, item.id)}
                onLongPress={() => setMessages(prev => prev.filter(m => m.id !== item.id))}
              >
                {bubble}
                <Small color={colors.destructive} style={{ alignSelf: "flex-end", marginTop: 2, fontSize: 10 }}>
                  Tap to retry · long-press to discard
                </Small>
              </Pressable>
            );
          }}
        />
        )}

        <View style={s.inputBar}>
          {error ? <Small color={colors.destructive} style={s.inputError}>{error}</Small> : null}
          <TextInput testID="chat-input" placeholder="Type a message…" value={text} onChangeText={onType} style={s.textInput} multiline />
          <Pressable testID="chat-send" onPress={send} disabled={sending || !text.trim()} style={[s.sendBtn, (sending || !text.trim()) && { opacity: 0.5 }]}>
            {sending ? <ActivityIndicator size="small" color={colors.primaryFg} /> : <Send size={20} color={colors.primaryFg} />}
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.md, paddingVertical: 12, borderBottomWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  badge: { minWidth: 22, height: 22, paddingHorizontal: 6, borderRadius: 11, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
  bubble: { maxWidth: "78%", paddingHorizontal: 12, paddingVertical: 8, borderRadius: 16 },
  bubbleMine: { alignSelf: "flex-end", backgroundColor: colors.primary, borderBottomRightRadius: 4 },
  bubbleOther: { alignSelf: "flex-start", backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderBottomLeftRadius: 4 },
  bubbleFailed: { opacity: 0.85, borderWidth: 1, borderColor: colors.destructive },
  offlineBanner: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: spacing.md, paddingVertical: 8, backgroundColor: "rgba(216,154,60,0.12)", borderBottomWidth: 1, borderBottomColor: "rgba(216,154,60,0.25)" },
  composeBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.secondary, alignItems: "center", justifyContent: "center" },
  filterRow: { flexDirection: "row", gap: 8, paddingHorizontal: spacing.md, paddingTop: spacing.sm },
  filterChip: { paddingHorizontal: 12, height: 30, borderRadius: 999, alignItems: "center", justifyContent: "center", backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border },
  filterChipOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  modalOverlay: { flex: 1, backgroundColor: "rgba(46,27,51,0.45)", justifyContent: "flex-end" },
  modalSheet: { backgroundColor: colors.card, borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: spacing.md, paddingBottom: spacing.xl },
  flagBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 11, borderRadius: 999, borderWidth: 1, borderColor: colors.primary + "44", backgroundColor: colors.primary + "0D" },
  flagBtnOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  composeSearch: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 14, paddingHorizontal: 12 },
  retryBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, paddingVertical: 10, borderRadius: 999, borderWidth: 1, borderColor: colors.primary + "44", backgroundColor: colors.primary + "0D" },
  inputBar: { flexDirection: "row", flexWrap: "wrap", alignItems: "flex-end", gap: 8, paddingHorizontal: spacing.md, paddingVertical: 12, borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  inputError: { width: "100%" },
  textInput: { flex: 1, maxHeight: 100, backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 20, paddingHorizontal: 14, paddingVertical: 10, color: colors.foreground, fontSize: 15 },
  sendBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
});
