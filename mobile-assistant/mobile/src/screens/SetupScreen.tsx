import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import {
  MODEL_CHOICES,
  getModelById,
  getSavedModelId,
  getSearxngUrl,
  modelPath,
  setSavedModelId,
  setSearxngUrl,
  type ModelChoice,
} from "../config";
import { downloadModel, isModelDownloaded } from "../modelDownload";
import { loadModel, isLoaded } from "../llm";

type Props = { onReady: () => void };

export default function SetupScreen({ onReady }: Props) {
  const [selectedId, setSelectedId] = useState<string>(MODEL_CHOICES[1].id);
  const [searxng, setSearxng] = useState<string>("");
  const [status, setStatus] = useState<string>("Checking existing model...");
  const [busy, setBusy] = useState<boolean>(false);
  const [progressPct, setProgressPct] = useState<number>(0);

  useEffect(() => {
    (async () => {
      const savedId = await getSavedModelId();
      if (savedId) setSelectedId(savedId);
      setSearxng(await getSearxngUrl());

      const model = getModelById(savedId) ?? MODEL_CHOICES[1];
      const downloaded = await isModelDownloaded(model);
      if (downloaded && isLoaded()) {
        onReady();
        return;
      }
      if (downloaded) {
        setStatus(`${model.label} is downloaded. Tap Start to load.`);
      } else {
        setStatus("No model yet. Pick one and download.");
      }
    })();
  }, [onReady]);

  const selected = getModelById(selectedId) ?? MODEL_CHOICES[1];

  const start = async () => {
    setBusy(true);
    try {
      await setSearxngUrl(searxng.trim());
      await setSavedModelId(selected.id);

      const downloaded = await isModelDownloaded(selected);
      if (!downloaded) {
        setStatus(`Downloading ${selected.label}...`);
        await downloadModel(selected, (p) => {
          const pct = p.total > 0 ? Math.floor((p.downloaded / p.total) * 100) : 0;
          setProgressPct(pct);
          setStatus(
            `Downloading ${selected.label}... ${pct}% (${(p.downloaded / 1024 / 1024).toFixed(0)} / ${(p.total / 1024 / 1024).toFixed(0)} MB)`,
          );
        });
      }
      setStatus("Loading model into memory...");
      await loadModel(selected, modelPath(selected));
      setStatus("Ready.");
      onReady();
    } catch (err) {
      Alert.alert("Setup failed", (err as Error).message);
      setStatus(`Error: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.title}>Personal Assistant</Text>
        <Text style={styles.subtitle}>
          Runs fully on your device. No accounts, no cloud AI.
        </Text>

        <Text style={styles.section}>Model</Text>
        {MODEL_CHOICES.map((m) => (
          <Pressable
            key={m.id}
            style={[styles.card, selectedId === m.id && styles.cardSelected]}
            onPress={() => setSelectedId(m.id)}
            disabled={busy}
          >
            <Text style={styles.cardTitle}>{m.label}</Text>
            <Text style={styles.cardMeta}>
              {m.sizeMB} MB · ~{m.ramNeededMB} MB RAM at runtime
            </Text>
          </Pressable>
        ))}

        <Text style={styles.section}>SearXNG URL (for web search)</Text>
        <TextInput
          style={styles.input}
          value={searxng}
          onChangeText={setSearxng}
          placeholder="http://192.168.1.42:8888"
          autoCapitalize="none"
          autoCorrect={false}
          editable={!busy}
        />
        <Text style={styles.hint}>
          Point at your self-hosted SearXNG. See the repo README for docker-compose.
          Leave blank to disable web search.
        </Text>

        <View style={styles.statusRow}>
          {busy && <ActivityIndicator />}
          <Text style={styles.status}>{status}</Text>
        </View>

        {busy && progressPct > 0 && (
          <View style={styles.progressOuter}>
            <View style={[styles.progressInner, { width: `${progressPct}%` }]} />
          </View>
        )}

        <Pressable
          style={[styles.startBtn, busy && styles.startBtnDisabled]}
          onPress={start}
          disabled={busy}
        >
          <Text style={styles.startBtnText}>
            {busy ? "Working..." : "Start"}
          </Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  content: { padding: 20, gap: 12 },
  title: { fontSize: 28, fontWeight: "700", marginTop: 20 },
  subtitle: { color: "#6b7280", fontSize: 15, marginBottom: 8 },
  section: { fontWeight: "600", marginTop: 12, fontSize: 15 },
  card: {
    padding: 14,
    borderRadius: 12,
    borderWidth: 2,
    borderColor: "#e5e7eb",
    backgroundColor: "#f9fafb",
  },
  cardSelected: {
    borderColor: "#2563eb",
    backgroundColor: "#eff6ff",
  },
  cardTitle: { fontWeight: "600", fontSize: 15 },
  cardMeta: { color: "#6b7280", marginTop: 4, fontSize: 13 },
  input: {
    borderWidth: 1,
    borderColor: "#d1d5db",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
  },
  hint: { color: "#6b7280", fontSize: 12 },
  statusRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: 12,
  },
  status: { flex: 1, color: "#374151" },
  progressOuter: {
    height: 6,
    backgroundColor: "#e5e7eb",
    borderRadius: 3,
    overflow: "hidden",
  },
  progressInner: {
    height: "100%",
    backgroundColor: "#2563eb",
  },
  startBtn: {
    backgroundColor: "#2563eb",
    padding: 16,
    borderRadius: 12,
    alignItems: "center",
    marginTop: 20,
  },
  startBtnDisabled: { backgroundColor: "#93c5fd" },
  startBtnText: { color: "#fff", fontWeight: "600", fontSize: 16 },
});
