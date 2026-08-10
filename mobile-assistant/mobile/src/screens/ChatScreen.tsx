import React, { useCallback, useRef, useState } from "react";
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

import { chat, listReminders, resetChat } from "../api";
import { syncReminders } from "../notifications";

type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
};

const timezone =
  Intl?.DateTimeFormat?.().resolvedOptions().timeZone ?? "UTC";

export default function ChatScreen() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const listRef = useRef<FlatList<Message>>(null);

  const scrollToEnd = useCallback(() => {
    setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 50);
  }, []);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setSending(true);
    const userMsg: Message = {
      id: `u-${Date.now()}`,
      role: "user",
      text,
    };
    setMessages((prev) => [...prev, userMsg]);
    scrollToEnd();

    try {
      const { reply } = await chat(text, timezone);
      const assistantMsg: Message = {
        id: `a-${Date.now()}`,
        role: "assistant",
        text: reply,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      scrollToEnd();

      // The assistant may have created reminders; refresh local notifications.
      try {
        const reminders = await listReminders();
        await syncReminders(reminders);
      } catch {
        /* silent — notifications will resync next open */
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
  }, [input, sending, scrollToEnd]);

  const clear = useCallback(async () => {
    try {
      await resetChat();
      setMessages([]);
    } catch (err) {
      /* leave UI untouched on failure */
    }
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
          renderItem={({ item }) => (
            <View
              style={[
                styles.bubble,
                item.role === "user" ? styles.userBubble : styles.assistantBubble,
              ]}
            >
              <Text
                style={
                  item.role === "user" ? styles.userText : styles.assistantText
                }
              >
                {item.text}
              </Text>
            </View>
          )}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Text style={styles.emptyTitle}>Hi 👋</Text>
              <Text style={styles.emptyBody}>
                Ask me anything, or tell me to remember something or set a reminder.
              </Text>
            </View>
          }
        />

        {sending && (
          <View style={styles.thinking}>
            <ActivityIndicator size="small" />
            <Text style={styles.thinkingText}>Thinking…</Text>
          </View>
        )}

        <View style={styles.inputRow}>
          <TextInput
            style={styles.input}
            placeholder="Message"
            value={input}
            onChangeText={setInput}
            onSubmitEditing={send}
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
