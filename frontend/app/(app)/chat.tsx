import React, { useEffect, useRef, useState } from "react";
import { View, ScrollView, TextInput, Pressable, StyleSheet, FlatList, KeyboardAvoidingView, Platform } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { ArrowLeft, Send } from "lucide-react-native";
import { colors, spacing, radii } from "@/src/theme/tokens";
import { Eyebrow, H1, Body, Small, Card } from "@/src/components/ui";
import { useAuth } from "@/src/auth/AuthContext";
import { api, tokenStore, wsUrl } from "@/src/api/client";

export default function Chat() {
  const router = useRouter();
  const { user } = useAuth();
  const [convs, setConvs] = useState<any[]>([]);
  const [users, setUsers] = useState<any[]>([]);
  const [active, setActive] = useState<any | null>(null);
  const [messages, setMessages] = useState<any[]>([]);
  const [text, setText] = useState("");
  const [typing, setTyping] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const listRef = useRef<FlatList>(null);

  // Load conversations + users + ws
  useEffect(() => {
    if (!user) return;
    (async () => {
      const [c, u] = await Promise.all([api.get("/conversations"), api.get("/users")]);
      setConvs(c.data); setUsers(u.data);
    })();
    (async () => {
      const t = await tokenStore.get("access_token"); if (!t) return;
      const ws = new WebSocket(wsUrl(t));
      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.type === "message") {
            setMessages(prev => [...prev, data]);
            setConvs(prev => prev.map(c => c.id === data.conversation_id ? { ...c, last_message: data.content } : c));
            if (data.conversation_id === active?.id) ws.send(JSON.stringify({ type: "read", conversation_id: data.conversation_id }));
          } else if (data.type === "typing" && data.conversation_id === active?.id) {
            setTyping(true); setTimeout(() => setTyping(false), 2500);
          }
        } catch {}
      };
      wsRef.current = ws;
    })();
    return () => wsRef.current?.close();
  }, [user?.id]);

  const openConv = async (c: any) => {
    setActive(c);
    const r = await api.get(`/conversations/${c.id}/messages`);
    setMessages(r.data);
    setTimeout(() => listRef.current?.scrollToEnd({ animated: false }), 100);
  };

  const startWith = async (otherId: string) => {
    const r = await api.post("/conversations", { participant_ids: [otherId] });
    const c = r.data;
    // refresh list
    const refresh = await api.get("/conversations");
    setConvs(refresh.data);
    const enriched = refresh.data.find((x: any) => x.id === c.id) || c;
    openConv(enriched);
  };

  const send = () => {
    if (!text.trim() || !active || !wsRef.current) return;
    wsRef.current.send(JSON.stringify({ type: "message", conversation_id: active.id, content: text.trim() }));
    setText("");
  };

  const onType = (v: string) => {
    setText(v);
    if (wsRef.current && active) wsRef.current.send(JSON.stringify({ type: "typing", conversation_id: active.id }));
  };

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
        </View>
        <ScrollView contentContainerStyle={{ padding: spacing.md, paddingBottom: spacing.xxl }}>
          {convs.length > 0 && <Eyebrow style={{ marginBottom: spacing.sm }}>Recent</Eyebrow>}
          {convs.map(c => {
            const other = c.other_participant;
            const name = c.title || other?.full_name || "Conversation";
            return (
              <Pressable key={c.id} testID={`conv-${c.id}`} onPress={() => openConv(c)}>
                <Card style={{ marginBottom: spacing.sm }}>
                  <View style={{ flexDirection: "row", alignItems: "center" }}>
                    <View style={s.avatar}><Body weight="700" color={colors.primary}>{other?.avatar_initials || "?"}</Body></View>
                    <View style={{ flex: 1, marginLeft: 12 }}>
                      <View style={{ flexDirection: "row", alignItems: "center" }}>
                        <Body weight="700" style={{ flex: 1 }}>{name}</Body>
                        {c.unread_count > 0 && <View style={s.badge}><Small color={colors.primaryFg} style={{ fontSize: 10, fontWeight: "700" as any }}>{c.unread_count}</Small></View>}
                      </View>
                      <Small numberOfLines={1} style={{ marginTop: 2 }}>{c.last_message || "Start chatting"}</Small>
                    </View>
                  </View>
                </Card>
              </Pressable>
            );
          })}
          <Eyebrow style={{ marginTop: spacing.lg, marginBottom: spacing.sm }}>Start a new chat</Eyebrow>
          {users.map(u => (
            <Pressable key={u.id} testID={`user-${u.id}`} onPress={() => startWith(u.id)}>
              <Card style={{ marginBottom: spacing.sm }}>
                <View style={{ flexDirection: "row", alignItems: "center" }}>
                  <View style={s.avatar}><Body weight="700" color={colors.primary}>{u.avatar_initials}</Body></View>
                  <View style={{ flex: 1, marginLeft: 12 }}>
                    <Body weight="700">{u.full_name}</Body>
                    <Small style={{ marginTop: 2 }}>{u.role.toUpperCase()} · {u.organization || u.email}</Small>
                  </View>
                  {u.is_online && <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: colors.success }} />}
                </View>
              </Card>
            </Pressable>
          ))}
        </ScrollView>
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
            <Small>{typing ? "typing…" : other?.is_online ? "online" : "offline"}</Small>
          </View>
        </View>

        <FlatList
          ref={listRef}
          data={messages}
          keyExtractor={(m) => m.id}
          contentContainerStyle={{ padding: spacing.md, gap: 8 }}
          onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: true })}
          renderItem={({ item }) => {
            const mine = item.sender_id === user?.id;
            return (
              <View style={[s.bubble, mine ? s.bubbleMine : s.bubbleOther]}>
                <Body color={mine ? colors.primaryFg : colors.foreground}>{item.content}</Body>
                <Small color={mine ? colors.overlay25 : colors.mutedFg} style={{ marginTop: 4, fontSize: 10 }}>
                  {new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </Small>
              </View>
            );
          }}
        />

        <View style={s.inputBar}>
          <TextInput testID="chat-input" placeholder="Type a message…" value={text} onChangeText={onType} style={s.textInput} multiline />
          <Pressable testID="chat-send" onPress={send} style={s.sendBtn}><Send size={20} color={colors.primaryFg} /></Pressable>
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
  inputBar: { flexDirection: "row", alignItems: "flex-end", gap: 8, paddingHorizontal: spacing.md, paddingVertical: 12, borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  textInput: { flex: 1, maxHeight: 100, backgroundColor: colors.background, borderWidth: 1, borderColor: colors.border, borderRadius: 20, paddingHorizontal: 14, paddingVertical: 10, color: colors.foreground, fontSize: 15 },
  sendBtn: { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.primary, alignItems: "center", justifyContent: "center" },
});
