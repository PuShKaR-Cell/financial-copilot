import { completion } from "./llm";
import { TOOLS, TOOLS_BY_NAME } from "./tools";
import type { ChatMessage } from "./db";

const MAX_TOOL_ROUNDS = 6;

function toolsSpec(): string {
  return JSON.stringify(
    TOOLS.map((t) => ({
      type: "function",
      function: {
        name: t.name,
        description: t.description,
        parameters: {
          type: "object",
          properties: t.parameters,
          required: t.required,
        },
      },
    })),
    null,
    2,
  );
}

function systemPrompt(timezone: string): string {
  return `You are the user's personal AI assistant, running privately on their phone.

You help with day-to-day life: answering questions, searching the web, remembering
things they tell you, setting reminders, and making useful suggestions.

Style:
- Be concise. Phone screens are small.
- Use tools when they help; answer directly when they don't.
- When the user tells you to remember something, call save_memory.
- Before answering a question that depends on prior context, call list_memories.
- Before creating a reminder, call current_time and use its output to compute the
  absolute ISO-8601 timestamp for create_reminder.
- Use web_search for current information (news, prices, weather, events).
- The user's timezone is ${timezone}.

# Tool use format
When you want to call a tool, emit a single line of the form:
<tool_call>{"name": "<tool_name>", "arguments": {<json arguments>}}</tool_call>

The tool result will be returned as:
<tool_response>...</tool_response>

You may call multiple tools sequentially. When you have enough information,
respond to the user as plain text (no tool_call tag). Do not put explanations
before or after a tool_call on the same turn — call the tool alone, then react
to the response.

# Tools
${toolsSpec()}`;
}

function renderPrompt(
  system: string,
  history: { role: string; content: string }[],
): string {
  // ChatML format used by Qwen 2.5.
  let s = `<|im_start|>system\n${system}<|im_end|>\n`;
  for (const m of history) {
    s += `<|im_start|>${m.role}\n${m.content}<|im_end|>\n`;
  }
  s += `<|im_start|>assistant\n`;
  return s;
}

type ParsedToolCall = { name: string; arguments: Record<string, unknown> } | null;

function extractToolCall(text: string): ParsedToolCall {
  // Robust to whitespace around/inside the tag.
  const match = text.match(/<tool_call>\s*([\s\S]*?)\s*<\/tool_call>/);
  if (!match) return null;
  const raw = match[1].trim();
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed?.name === "string") {
      return {
        name: parsed.name,
        arguments: typeof parsed.arguments === "object" && parsed.arguments !== null
          ? parsed.arguments as Record<string, unknown>
          : {},
      };
    }
    return null;
  } catch {
    return null;
  }
}

function stripToolCall(text: string): string {
  return text.replace(/<tool_call>[\s\S]*?<\/tool_call>/g, "").trim();
}

export type AgentEvent =
  | { type: "text"; delta: string }
  | { type: "tool_call"; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; name: string; result: string }
  | { type: "done"; finalText: string };

export async function runAgentTurn(
  history: ChatMessage[],
  userMessage: string,
  timezone: string,
  onEvent?: (e: AgentEvent) => void,
): Promise<{ assistantText: string; scratch: { role: string; content: string }[] }> {
  const system = systemPrompt(timezone);

  // Working history mirrors what the model sees (roles: user | assistant | tool).
  const working: { role: string; content: string }[] = history.map((m) => ({
    role: m.role,
    content: m.content,
  }));
  working.push({ role: "user", content: userMessage });

  let finalText = "";

  for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
    const prompt = renderPrompt(system, working);
    const raw = await completion(prompt, {
      stop: ["<|im_end|>", "<|endoftext|>"],
      maxTokens: 512,
      temperature: 0.6,
      onToken: (tok) => onEvent?.({ type: "text", delta: tok }),
    });

    const trimmed = raw.trim();
    const toolCall = extractToolCall(trimmed);

    if (!toolCall) {
      finalText = trimmed;
      working.push({ role: "assistant", content: trimmed });
      break;
    }

    // Assistant emitted a tool call — record it in the working history verbatim
    // so subsequent rounds see it.
    working.push({ role: "assistant", content: trimmed });
    onEvent?.({ type: "tool_call", name: toolCall.name, args: toolCall.arguments });

    const tool = TOOLS_BY_NAME[toolCall.name];
    let result: string;
    if (!tool) {
      result = `Unknown tool ${toolCall.name}.`;
    } else {
      try {
        result = await tool.run(toolCall.arguments);
      } catch (err) {
        result = `Tool error: ${(err as Error).message}`;
      }
    }

    onEvent?.({ type: "tool_result", name: toolCall.name, result });
    working.push({
      role: "tool",
      content: `<tool_response>${result}</tool_response>`,
    });
  }

  if (!finalText) {
    finalText =
      "I hit the tool-use limit without producing an answer. Try rephrasing?";
    working.push({ role: "assistant", content: finalText });
  }

  // stripToolCall in case the model leaked tags into the visible reply.
  const cleaned = stripToolCall(finalText) || finalText;
  onEvent?.({ type: "done", finalText: cleaned });
  return { assistantText: cleaned, scratch: working };
}
