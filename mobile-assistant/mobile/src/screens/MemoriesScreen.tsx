import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { deleteMemory, listMemories, type Memory } from "../db";

export default function MemoriesScreen() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [query, setQuery] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      setMemories(await listMemories(query));
    } finally {
      setRefreshing(false);
    }
  }, [query]);

  useEffect(() => {
    load();
  }, [load]);

  const confirmDelete = useCallback((m: Memory) => {
    Alert.alert("Delete memory?", m.content, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          await deleteMemory(m.id);
          setMemories((prev) => prev.filter((x) => x.id !== m.id));
        },
      },
    ]);
  }, []);

  return (
    <SafeAreaView style={styles.container} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>Memories</Text>
      </View>
      <TextInput
        style={styles.search}
        placeholder="Search"
        value={query}
        onChangeText={setQuery}
        onSubmitEditing={load}
        returnKeyType="search"
      />
      <FlatList
        data={memories}
        keyExtractor={(m) => String(m.id)}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={load} />}
        renderItem={({ item }) => (
          <Pressable
            style={styles.item}
            onLongPress={() => confirmDelete(item)}
          >
            <Text style={styles.itemText}>{item.content}</Text>
            {item.tags ? <Text style={styles.tags}>{item.tags}</Text> : null}
            <Text style={styles.date}>
              {new Date(item.created_at).toLocaleString()}
            </Text>
          </Pressable>
        )}
        ListEmptyComponent={
          !refreshing ? (
            <Text style={styles.empty}>
              Nothing saved yet. Tell the assistant to remember something.
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
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#ddd",
  },
  title: { fontSize: 20, fontWeight: "600" },
  search: {
    margin: 12,
    borderWidth: 1,
    borderColor: "#d1d5db",
    borderRadius: 20,
    paddingHorizontal: 14,
    paddingVertical: 8,
    fontSize: 15,
  },
  list: { paddingHorizontal: 12, paddingBottom: 24 },
  item: {
    padding: 12,
    borderRadius: 10,
    backgroundColor: "#f3f4f6",
    marginBottom: 8,
  },
  itemText: { fontSize: 15, color: "#111827" },
  tags: { fontSize: 12, color: "#2563eb", marginTop: 4 },
  date: { fontSize: 11, color: "#6b7280", marginTop: 4 },
  empty: {
    textAlign: "center",
    color: "#6b7280",
    marginTop: 40,
    paddingHorizontal: 24,
  },
});
