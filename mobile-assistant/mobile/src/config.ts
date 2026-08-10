import AsyncStorage from "@react-native-async-storage/async-storage";
import * as FileSystem from "expo-file-system";
import Constants from "expo-constants";

export type ModelChoice = {
  id: string;
  label: string;
  sizeMB: number;
  url: string;
  filename: string;
  contextSize: number;
  ramNeededMB: number;
};

// Both models are Apache-2.0 licensed and support tool calling via the
// Hermes / ChatML instruction style used in this app.
export const MODEL_CHOICES: ModelChoice[] = [
  {
    id: "qwen-1.5b",
    label: "Qwen 2.5 1.5B (fast, lower quality)",
    sizeMB: 1020,
    url: "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q5_k_m.gguf",
    filename: "qwen2.5-1.5b-instruct-q5_k_m.gguf",
    contextSize: 4096,
    ramNeededMB: 1500,
  },
  {
    id: "qwen-3b",
    label: "Qwen 2.5 3B (recommended)",
    sizeMB: 2100,
    url: "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
    filename: "qwen2.5-3b-instruct-q4_k_m.gguf",
    contextSize: 4096,
    ramNeededMB: 2800,
  },
];

const KEY_MODEL_ID = "config.modelId";
const KEY_SEARXNG_URL = "config.searxngUrl";
const KEY_TIMEZONE = "config.timezone";

export function modelsDirectory(): string {
  return `${FileSystem.documentDirectory}models/`;
}

export function modelPath(model: ModelChoice): string {
  return `${modelsDirectory()}${model.filename}`;
}

export async function getSavedModelId(): Promise<string | null> {
  return AsyncStorage.getItem(KEY_MODEL_ID);
}

export async function setSavedModelId(id: string): Promise<void> {
  await AsyncStorage.setItem(KEY_MODEL_ID, id);
}

export function getModelById(id: string | null): ModelChoice | null {
  if (!id) return null;
  return MODEL_CHOICES.find((m) => m.id === id) ?? null;
}

export async function getSearxngUrl(): Promise<string> {
  const stored = await AsyncStorage.getItem(KEY_SEARXNG_URL);
  if (stored) return stored;
  return (
    (Constants.expoConfig?.extra?.defaultSearxngUrl as string | undefined) ??
    "http://192.168.1.42:8888"
  );
}

export async function setSearxngUrl(url: string): Promise<void> {
  await AsyncStorage.setItem(KEY_SEARXNG_URL, url);
}

export async function getTimezone(): Promise<string> {
  const stored = await AsyncStorage.getItem(KEY_TIMEZONE);
  if (stored) return stored;
  return (
    Intl?.DateTimeFormat?.().resolvedOptions().timeZone ?? "UTC"
  );
}

export async function setTimezone(tz: string): Promise<void> {
  await AsyncStorage.setItem(KEY_TIMEZONE, tz);
}
