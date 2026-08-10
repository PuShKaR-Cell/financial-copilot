import * as FileSystem from "expo-file-system";

import { modelPath, modelsDirectory, type ModelChoice } from "./config";

export async function ensureDir(): Promise<void> {
  const dir = modelsDirectory();
  const info = await FileSystem.getInfoAsync(dir);
  if (!info.exists) {
    await FileSystem.makeDirectoryAsync(dir, { intermediates: true });
  }
}

export async function isModelDownloaded(model: ModelChoice): Promise<boolean> {
  const info = await FileSystem.getInfoAsync(modelPath(model));
  return info.exists && (info.size ?? 0) > 100 * 1024 * 1024;
}

export type DownloadProgress = { downloaded: number; total: number };

export async function downloadModel(
  model: ModelChoice,
  onProgress: (p: DownloadProgress) => void,
): Promise<void> {
  await ensureDir();
  const dest = modelPath(model);
  const resumable = FileSystem.createDownloadResumable(
    model.url,
    dest,
    {},
    (p) => {
      onProgress({
        downloaded: p.totalBytesWritten,
        total: p.totalBytesExpectedToWrite || model.sizeMB * 1024 * 1024,
      });
    },
  );
  const result = await resumable.downloadAsync();
  if (!result) throw new Error("Download failed.");
}

export async function deleteModel(model: ModelChoice): Promise<void> {
  const p = modelPath(model);
  const info = await FileSystem.getInfoAsync(p);
  if (info.exists) {
    await FileSystem.deleteAsync(p);
  }
}
