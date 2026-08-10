# Mobile AI Assistant — fully independent

A React Native personal assistant that runs **entirely on your phone**. The
LLM runs on-device via `llama.rn` (llama.cpp). No Anthropic, no OpenAI, no
cloud AI. The only optional external piece is a self-hosted **SearXNG**
instance you run on your own machine for web search.

- **Chat** with a small local LLM (Qwen 2.5 1.5B or 3B, quantized GGUF)
- **Remembers things** you tell it — SQLite on-device
- **Sets reminders** with on-device local notifications, fires even offline
- **Web search** via *your* SearXNG (drop the URL to disable)
- Bottom-tab UI: Chat / Memories / Reminders

## Architecture

```
Phone (React Native, on-device)
├── llama.rn  →  Qwen 2.5 3B  (GGUF, ~2 GB, cached in app storage)
├── agent.ts  →  ChatML + <tool_call> parsing loop
├── tools.ts  →  save_memory | list_memories | delete_memory
│               create_reminder | list_reminders | complete_reminder
│               current_time | web_search
├── db.ts     →  expo-sqlite (memories, reminders, chat history)
└── expo-notifications  →  local push for reminders
                                        │
                                        │ (only external call)
                                        ▼
                              SearXNG on your PC/Mac
                              (docker-compose provided)
```

Everything except web search runs offline. Web search calls a URL you
configure — nobody else's server.

## Requirements

- **Node 18+** (for building the app)
- **Xcode** (macOS, for iOS) *or* **Android Studio** (Windows/macOS/Linux, for Android)
- A phone with ~4 GB free storage and 3+ GB RAM (for the 3B model)
- **Docker** (only if you want web search)

> `llama.rn` includes native code, so **Expo Go doesn't work**. You'll build
> a small custom dev client onto your phone once (see below); after that,
> reloading the JS is instant.

## Setup

### 1. Optional: SearXNG for web search

If you want the assistant to search the web, run SearXNG on your PC/Mac:

```sh
cd mobile-assistant/searxng
# Edit settings.yml → change 'secret_key' to a long random string
docker compose up -d
```

SearXNG will listen on `http://<your-lan-ip>:8888`. Test it:

```sh
curl 'http://localhost:8888/search?q=hello&format=json' | head
```

### 2. Build the app onto your phone

```sh
cd mobile-assistant/mobile
npm install
```

**iOS (from macOS)**: connect your phone via USB, trust the computer, then:

```sh
npx expo run:ios --device
```

**Android**: enable USB debugging on the phone, connect it, then:

```sh
npx expo run:android --device
```

The first build takes ~10 minutes as native modules compile. After that,
`npm start` runs the Metro bundler and any JS change hot-reloads on the
phone.

### 3. First-launch setup on the phone

The app opens on a Setup screen:

1. Pick a model (1.5B fast, 3B recommended)
2. Enter your SearXNG URL (`http://<your-lan-ip>:8888`), or leave blank to
   disable search
3. Tap **Start** — downloads the GGUF from HuggingFace and loads it into memory

Downloading a 2 GB model over Wi-Fi takes ~2–5 minutes. It's cached after
that — subsequent launches load in seconds.

## Usage examples

- *"Remember I'm allergic to peanuts."* → `save_memory`
- *"What am I allergic to?"* → `list_memories`
- *"Remind me to call Mom tomorrow at 6pm"* → `current_time` → `create_reminder`,
  phone schedules a local notification
- *"Latest news about X"* → `web_search` (via your SearXNG)

Watch the chat: each tool call shows as a small trace line so you can see
what the model decided to do.

## Tool-call protocol

The agent uses ChatML with a `<tool_call>{...}</tool_call>` convention that
Qwen 2.5 was trained on. Adding a new tool means one entry in
`src/tools.ts` — the model sees the schema in its system prompt
automatically.

## What's persisted where

| Data | Where |
|---|---|
| Model weights | `<app-docs>/models/*.gguf` |
| Memories, reminders, chat | `<app-docs>/SQLite/assistant.db` (expo-sqlite) |
| Selected model, SearXNG URL | `AsyncStorage` |

All of this lives inside the app sandbox — no cloud sync.

## Limits and trade-offs

- **On-device models are smaller.** The 3B is strong at everyday chat and
  simple tool use, weaker at complex reasoning than any Opus/Sonnet-class
  cloud model. Expect the trade — you got privacy and independence.
- **Battery + heat.** Sustained inference will warm the phone. Fine for
  short interactive turns; don't hold it in your palm during long
  multi-round tool loops.
- **iOS memory ceiling.** iOS aggressively kills apps that use lots of
  RAM. The 3B at Q4_K_M is usually fine on iPhone 12 and later; on older
  phones drop to the 1.5B.
- **First-run download is 1–2 GB.** Do it on Wi-Fi.

## Extending

- **Add a tool** → edit `src/tools.ts`, add a `ToolDef`. It's exposed to the
  model on the next turn.
- **Different model** → add to `MODEL_CHOICES` in `src/config.ts`. Anything
  Qwen-2.5-style (ChatML template + JSON tool-call training) works.
- **Push notifications from tools** → use `expo-notifications` from inside a
  tool's `run` — same API you'd use anywhere else.

## Standalone builds

To ship a version you can install without the dev toolchain, use EAS Build:
https://docs.expo.dev/build/setup/
