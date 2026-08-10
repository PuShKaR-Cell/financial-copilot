import { initLlama, LlamaContext } from "llama.rn";

import type { ModelChoice } from "./config";

let ctx: LlamaContext | null = null;
let loadedModelId: string | null = null;

export async function loadModel(
  model: ModelChoice,
  modelFilePath: string,
): Promise<void> {
  if (ctx && loadedModelId === model.id) return;
  if (ctx) {
    await ctx.release();
    ctx = null;
  }
  ctx = await initLlama({
    model: modelFilePath,
    n_ctx: model.contextSize,
    n_gpu_layers: 0,
    n_threads: 4,
    use_mlock: false,
  });
  loadedModelId = model.id;
}

export function isLoaded(): boolean {
  return ctx !== null;
}

export async function completion(
  prompt: string,
  opts: {
    stop?: string[];
    maxTokens?: number;
    temperature?: number;
    onToken?: (t: string) => void;
  } = {},
): Promise<string> {
  if (!ctx) throw new Error("Model not loaded.");
  const result = await ctx.completion(
    {
      prompt,
      n_predict: opts.maxTokens ?? 512,
      temperature: opts.temperature ?? 0.6,
      top_p: 0.9,
      stop: opts.stop ?? [],
    },
    (data: { token: string }) => {
      opts.onToken?.(data.token);
    },
  );
  return result.text;
}

export async function releaseModel(): Promise<void> {
  if (ctx) {
    await ctx.release();
    ctx = null;
    loadedModelId = null;
  }
}
