import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { appendChat, listReminders, loadChat, resetChat, type ChatMessage } from "../db";
import { runAgentTurn, type AgentEvent } from "../agent";
import { getTimezone } from "../config";
import { syncReminders } from "../notifications";

type DisplayMsg = {
  id: string;
  role: "user" | "assistant" | "trace";
  text: string;
};

export default function ChatScreen() {
  const [messages, setMessages] = useState<DisplayMsg[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [tzState, setTzState] = useState("UTC");
  const listRef = useRef<FlatList<DisplayMsg>>(null);

  useEffect(() => {
    (async () => {
      setTzState(await getTimezone());
      const history = await loadChat();
      setMessages(
        history
          .filter((m) => m.role === "user" || m.role === "assistant")
          .map((m) => ({
            id: `db-${m.id}`,
            role: m.role as "user" | "assistant",
            text: m.content,
          })),
      );
    })();
  }, []);

  const scrollToEnd = useCallback(() => {
    setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 50);
  }, []);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setSending(true);

    const userMsg: DisplayMsg = {
      id: `u-${Date.now()}`,
      role: "user",
      text,
    };
    setMessages((prev) => [...prev, userMsg]);
    scrollToEnd();

    try {
      await appendChat("user", text);

      // Load db history for the model (user+assistant only — the agent injects
      // its own scratch turns for tools).
      const history: ChatMessage[] = (await loadChat()).filter(
        (m) => m.role === "user" || m.role === "assistant",
      );

      const onEvent = (e: AgentEvent) => {
        if (e.type === "tool_call") {
          setMessages((prev) => [
            ...prev,
            {
              id: `t-${Date.now()}-${Math.random()}`,
              role: "trace",
              text: `→ ${e.name}(${JSON.stringify(e.args)})`,
            },
          ]);
          scrollToEnd();
        }
      };

      const { assistantText } = await runAgentTurn(history, text, tzState, onEvent);

      await appendChat("assistant", assistantText);
      setMessages((prev) => [
        ...prev,
        {
          id: `a-${Date.now()}`,
          role: "assistant",
          text: assistantText,
        },
      ]);
      scrollToEnd();

      // Reminders may have changed; refresh on-device notifications.
      try {
        const reminders = await listReminders(false);
        await syncReminders(reminders);
      } catch {
        /* silent */
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: `e-${Date.now()}`,
          role: "assistant",
          text: `⚠ Error: ${(err as Error).message}`,
        },
      ]);
      scrollToEnd();
    } finally {
      setSending(false);
    }
  }, [input, sending, scrollToEnd, tzState]);

  const clear = useCallback(async () => {
    await resetChat();
    setMessages([]);
  }, []);

  return (
    <SafeAreaView style={styles.container} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>Assistant</Text>
        <Pressable onPress={clear} hitSlop={12}>
          <Text style={styles.headerAction}>New</Text>
        </Pressable>
      </View>

      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        keyboardVerticalOffset={80}
      >
        <FlatList
          ref={listRef}
          data={messages}
          keyExtractor={(m) => m.id}
          contentContainerStyle={styles.listContent}
          renderItem={({ item }) => {
            if (item.role === "trace") {
              return (
                <Text style={styles.trace}>{item.text}</Text>
              );
            }
            return (
              <View
                style={[
                  styles.bubble,
                  item.role === "user" ? styles.userBubble : styles.assistantBubble,
                ]}
              >
                <Text
                  style={item.role === "user" ? styles.userText : styles.assistantText}
                >
                  {item.text}
                </Text>
              </View>
            );
          }}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Text style={styles.emptyTitle}>Hi 👋</Text>
              <Text style={styles.emptyBody}>
                Ask me anything. I can remember things, set reminders, and search.
              </Text>
            </View>
          }
        />

        {sending && (
          <View style={styles.thinking}>
            <ActivityIndicator size="small" />
            <Text style={styles.thinkingText}>Thinking on-device…</Text>
          </View>
        )}

        <View style={styles.inputRow}>
          <TextInput
            style={styles.input}
            placeholder="Message"
            value={input}
            onChangeText={setInput}
            editable={!sending}
            multiline
          />
          <Pressable
            style={[styles.sendBtn, (!input.trim() || sending) && styles.sendBtnDisabled]}
            onPress={send}
            disabled={!input.trim() || sending}
          >
            <Text style={styles.sendBtnText}>Send</Text>
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  flex: { flex: 1 },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#ddd",
  },
  title: { fontSize: 20, fontWeight: "600" },
  headerAction: { color: "#2563eb", fontSize: 16 },
  listContent: { padding: 12, gap: 8 },
  bubble: {
    maxWidth: "85%",
    padding: 10,
    borderRadius: 12,
    marginVertical: 2,
  },
  userBubble: {
    alignSelf: "flex-end",
    backgroundColor: "#2563eb",
  },
  assistantBubble: {
    alignSelf: "flex-start",
    backgroundColor: "#e5e7eb",
  },
  userText: { color: "#fff", fontSize: 16 },
  assistantText: { color: "#111827", fontSize: 16 },
  trace: {
    color: "#6b7280",
    fontSize: 12,
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: "monospace" }),
    marginHorizontal: 8,
  },
  empty: {
    padding: 24,
    alignItems: "center",
    gap: 8,
    marginTop: 60,
  },
  emptyTitle: { fontSize: 24, fontWeight: "600" },
  emptyBody: {
    color: "#6b7280",
    textAlign: "center",
    fontSize: 15,
  },
  thinking: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 16,
    paddingVertical: 4,
  },
  thinkingText: { color: "#6b7280" },
  inputRow: {
    flexDirection: "row",
    padding: 8,
    gap: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#ddd",
    alignItems: "flex-end",
  },
  input: {
    flex: 1,
    minHeight: 40,
    maxHeight: 120,
    borderWidth: 1,
    borderColor: "#d1d5db",
    borderRadius: 20,
    paddingHorizontal: 14,
    paddingVertical: 8,
    fontSize: 16,
  },
  sendBtn: {
    backgroundColor: "#2563eb",
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 20,
  },
  sendBtnDisabled: { backgroundColor: "#93c5fd" },
  sendBtnText: { color: "#fff", fontWeight: "600" },
});
