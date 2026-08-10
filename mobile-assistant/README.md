# Mobile AI Assistant — fully independent

A React Native personal assistant that runs **entirely on your phone**. The
LLM runs on-device via `llama.rn` (llama.cpp). No Anthropic, no OpenAI, no
cloud AI. The only optional external piece is a self-hosted **SearXNG**
instance you (or someone you know) run for web search.

- **Chat** with a small local LLM (Qwen 2.5 1.5B or 3B, quantized GGUF)
- **Remembers things** you tell it — SQLite on-device
- **Sets reminders** with on-device local notifications, fires even offline
- **Web search** via your own SearXNG (leave the URL blank to disable)
- Bottom-tab UI: Chat / Memories / Reminders

Built for the **no-local-dev-toolchain** case: you don't need Xcode, Android
Studio, or a beefy Mac. Expo's cloud build service (EAS Build) compiles the
app for you and gives you an install link on your phone.

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
                              SearXNG (self-hosted, optional)
```

Everything except web search runs offline.

## Building the app with EAS Build

You need to trigger the build once. After that, the app runs on your phone
with zero further dependency on a computer.

### 0. What you need

- A free **Expo account** (`https://expo.dev/signup`)
- **Node 18+** — install once via https://nodejs.org (any machine, or use an
  online IDE like GitHub Codespaces / Gitpod / StackBlitz — Node is enough)
- **iOS only**: an Apple Developer account ($99/yr). EAS Build can create
  the provisioning profile for you but Apple requires the paid membership
  to install a real IPA on a physical device. **Android has no such fee**
  — if you're on Android, you can build and install completely free.

### 1. One-time setup

```sh
cd mobile-assistant/mobile
npm install
npx eas-cli login          # your Expo account
npx eas-cli init            # creates a project on Expo, writes projectId to app.json
```

The `eas init` step replaces the `REPLACE_WITH_YOUR_PROJECT_ID` placeholder
in `app.json` with your real project ID. Commit that change.

### 2. Build

**Android (recommended — free, easiest):**

```sh
npm run eas:build:android
```

EAS builds the APK in the cloud (~15 min). When it finishes it prints a URL
and a QR code. Open the URL on your phone, download the APK, install it.
(You may need to enable "Install unknown apps" for your browser once.)

**iOS:**

```sh
npm run eas:build:ios
```

EAS will prompt you to log in to your Apple Developer account and register
your device. It handles certificates and provisioning profiles for you.
When done, open the install link on your phone in Safari and tap Install.

Either way, no local compile toolchain needed.

### 3. First-launch on the phone

The app opens on a Setup screen:

1. Pick a model (1.5B fast, 3B recommended)
2. Enter your SearXNG URL, or leave blank to disable web search
3. Tap **Start** — downloads the GGUF from HuggingFace and loads it into memory

Downloading a 2 GB model over Wi-Fi takes ~2–5 minutes. It's cached after
that — subsequent launches load in seconds.

## Optional: SearXNG for web search

If you want web search, you need SearXNG running somewhere the phone can
reach. Two easy options:

**A. Run it on any machine you already have** — a home PC, a Raspberry Pi,
a cheap VPS:

```sh
cd mobile-assistant/searxng
# Edit settings.yml → change 'secret_key' to a long random string
docker compose up -d
```

SearXNG listens on port 8888. Point the app at `http://<that-host>:8888`.

**B. Use a public SearXNG instance** — https://searx.space lists community
instances that expose JSON search. Point the app at one of those URLs.
Trade-off: someone else sees your queries.

**C. Skip web search entirely.** Leave the SearXNG URL blank in Setup. The
assistant loses `web_search` as a tool; everything else still works.

## Usage examples

- *"Remember I'm allergic to peanuts."* → `save_memory`
- *"What am I allergic to?"* → `list_memories`
- *"Remind me to call Mom tomorrow at 6pm"* → `current_time` → `create_reminder`,
  phone schedules a local notification
- *"Latest news about X"* → `web_search` (via your SearXNG)

Each tool call shows as a small trace line in the chat so you can see what
the model decided to do.

## Updating the app later

You have three routes, in increasing complexity:

1. **Never update it.** The app is self-contained. You're done.
2. **Push a JS-only update over the air.** `npx eas-cli update` sends new
   JavaScript to the installed app without a rebuild. Doesn't work for
   native changes (like updating `llama.rn`).
3. **Rebuild** with `npm run eas:build:android` / `ios` when you change
   native modules.

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
  cloud model.
- **Battery + heat.** Sustained inference will warm the phone. Fine for
  short interactive turns; not for hour-long conversations.
- **iOS memory ceiling.** iOS aggressively kills apps that use lots of
  RAM. 3B at Q4_K_M is usually fine on iPhone 12+; on older phones drop
  to the 1.5B.
- **First-run download is 1–2 GB.** Do it on Wi-Fi.
- **iOS Developer account.** $99/yr from Apple. No way around it if you
  want to install a real app on a physical iPhone without a jailbreak.
  Android has no such fee.

## Extending

- **Add a tool** → edit `src/tools.ts`, add a `ToolDef`. Model sees the
  schema on the next turn.
- **Different model** → add to `MODEL_CHOICES` in `src/config.ts`. Any
  Qwen-2.5-style GGUF (ChatML template + JSON tool-call training) works.
- **Push notifications from tools** → use `expo-notifications` from inside
  a tool's `run` — same API you'd use anywhere else.
