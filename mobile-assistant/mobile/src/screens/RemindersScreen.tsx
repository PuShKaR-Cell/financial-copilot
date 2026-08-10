import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Switch,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { completeReminder, listReminders, type Reminder } from "../api";
import { syncReminders } from "../notifications";

export default function RemindersScreen() {
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [includeDone, setIncludeDone] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      const data = await listReminders(includeDone);
      setReminders(data);
      // Keep on-device notifications in sync with the server as source of truth.
      const active = includeDone ? await listReminders(false) : data;
      await syncReminders(active);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRefreshing(false);
    }
  }, [includeDone]);

  useEffect(() => {
    load();
  }, [load]);

  const done = useCallback((r: Reminder) => {
    Alert.alert("Mark done?", r.text, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Done",
        onPress: async () => {
          try {
            await completeReminder(r.id);
            await load();
          } catch (err) {
            Alert.alert("Failed", (err as Error).message);
          }
        },
      },
    ]);
  }, [load]);

  return (
    <SafeAreaView style={styles.container} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>Reminders</Text>
        <View style={styles.toggleRow}>
          <Text style={styles.toggleLabel}>Show done</Text>
          <Switch value={includeDone} onValueChange={setIncludeDone} />
        </View>
      </View>
      {error && <Text style={styles.error}>{error}</Text>}
      <FlatList
        data={reminders}
        keyExtractor={(r) => String(r.id)}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={load} />}
        renderItem={({ item }) => {
          const when = new Date(item.remind_at);
          const past = when.getTime() < Date.now();
          return (
            <Pressable
              style={[
                styles.item,
                item.done && styles.itemDone,
              ]}
              onPress={() => !item.done && done(item)}
            >
              <Text
                style={[
                  styles.itemText,
                  item.done && styles.itemTextDone,
                ]}
              >
                {item.text}
              </Text>
              <Text style={[styles.date, past && !item.done && styles.datePast]}>
                {when.toLocaleString()}
              </Text>
            </Pressable>
          );
        }}
        ListEmptyComponent={
          !refreshing ? (
            <Text style={styles.empty}>
              No reminders. Ask the assistant to set one.
            </Text>
          ) : null
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
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
  toggleRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  toggleLabel: { color: "#6b7280" },
  list: { paddingHorizontal: 12, paddingTop: 12, paddingBottom: 24 },
  item: {
    padding: 12,
    borderRadius: 10,
    backgroundColor: "#f3f4f6",
    marginBottom: 8,
  },
  itemDone: { opacity: 0.5 },
  itemText: { fontSize: 15, color: "#111827" },
  itemTextDone: { textDecorationLine: "line-through" },
  date: { fontSize: 12, color: "#6b7280", marginTop: 4 },
  datePast: { color: "#b91c1c" },
  empty: {
    textAlign: "center",
    color: "#6b7280",
    marginTop: 40,
    paddingHorizontal: 24,
  },
  error: {
    color: "#b91c1c",
    textAlign: "center",
    marginTop: 8,
  },
});
