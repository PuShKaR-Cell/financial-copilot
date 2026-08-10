# Mobile AI Assistant

A React Native personal assistant that runs on your phone via Expo Go.

- **Chat** with Claude (Opus 5) about anything
- **Search the web** (uses Anthropic's hosted web_search tool)
- **Remembers things** you tell it (persisted server-side)
- **Sets reminders** that fire as local push notifications on your device
- Bottom-tab UI: Chat / Memories / Reminders

## Architecture

```
Phone (Expo Go / React Native)
      │
      │ HTTP
      ▼
FastAPI backend (this repo, mobile-assistant/backend)
      │
      │ Claude Opus 5 + tool runner
      │  ├── save_memory / list_memories / delete_memory  (SQLite)
      │  ├── create_reminder / list_reminders / complete_reminder  (SQLite)
      │  ├── current_time
      │  └── web_search (Anthropic-hosted server tool)
      ▼
   SQLite (assistant.db)
```

Reminders are stored server-side; the mobile app fetches them and schedules
matching **local** notifications via `expo-notifications`, so they fire even
when the phone is offline.

## Backend setup

```sh
cd mobile-assistant/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # paste your ANTHROPIC_API_KEY
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The `--host 0.0.0.0` bind is important so your phone can reach it over your
local network.

## Mobile setup

Requires Node 18+ and the Expo CLI.

```sh
cd mobile-assistant/mobile
npm install
```

Set the API URL to your dev machine's LAN IP (not `localhost` — that's the
phone itself). Edit `mobile-assistant/mobile/app.json`:

```json
"extra": {
  "apiUrl": "http://192.168.1.42:8000"
}
```

Then:

```sh
npx expo start
```

Install **Expo Go** on your phone (iOS App Store / Android Play Store), scan
the QR code from the terminal, and the app loads. Grant notification
permissions when prompted.

## Usage examples

- *"Remember I'm allergic to peanuts."* → assistant calls `save_memory`
- *"What am I allergic to?"* → assistant calls `list_memories`
- *"Remind me to call Mom tomorrow at 6pm"* → assistant calls
  `current_time` then `create_reminder`, and your phone schedules a local
  push notification
- *"What's the weather in Tokyo right now?"* → assistant calls `web_search`

## Notes

- Single-user by default (`user_id="default"`). To make it multi-user, thread
  a per-device ID through `chat()` / the tool functions.
- The backend is stateful (conversation history in SQLite), so multiple
  devices sharing a URL share a conversation. Fix by scoping `user_id` per
  device.
- To build a standalone binary (no Expo Go), use `eas build` — see
  https://docs.expo.dev/build/setup/.
