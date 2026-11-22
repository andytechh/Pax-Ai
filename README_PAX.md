# PAX — personal AI assistant for your PC

PAX hears you, sees your screen and controls your Windows PC: opens and closes
apps, manages files, drives the browser, clicks and types, sets volume and
brightness, runs reminders and routines, does background research, and now also
talks to your phone, your spreadsheets, your lights and your 3D printer.

## Attribution (required by the license)

PAX is a **modified version of "Mark LVII" by FatihMakes**
(https://github.com/FatihMakes/Mark-LVII), licensed under
**Creative Commons Attribution-NonCommercial 4.0 (CC BY-NC 4.0)**.

Changes: assistant renamed JARVIS → PAX; nine plugins added (see below).
Internal identifiers (e.g. `JarvisUI`) are unchanged so existing code keeps working.
**Commercial use is not permitted.** See `LICENSE`.

## Install (Windows 10/11)

1. Install Python 3.11, 3.12 or 3.13 (tick "Add to PATH").
2. In a terminal in this folder:

       python setup.py
       python main.py

3. Paste a free Gemini API key on first launch (https://aistudio.google.com/apikey).
4. Talk to PAX. Hold **Ctrl+Space** for push-to-talk.

The name is PAX by default; change it any time in the customization panel.
The wake word is still the pretrained "Hey Jarvis" (a "Hey Pax" word needs a custom
openwakeword model) — use Ctrl+Space or WAKE NOW meanwhile.

## What was added (all in `plugins/`, all configurable under ⚙ → PLUGIN SETTINGS)

| Plugin | What it does | Needs |
|---|---|---|
| **telegram_remote** | Private Telegram bot: text or voice-note PAX from your phone; send/receive files; /status, /screenshot, /camera | A bot token from @BotFather |
| **cloud_agent** | Optional 24/7 server: Telegram answers while the PC is off, jobs and reminders keep running, memory synced | A VPS of your own |
| **excel_writer** | Create/extend real .xlsx files (formulas, totals, styled headers) by voice | — |
| **shopping_list** | A list on the HUD: add / bought / remove / clear | — |
| **disk_map** | "What's eating my disk?" — biggest folders/files, drawn as bars. Read-only | — |
| **live_panels** | Self-refreshing HUD panels: system load, top processes, network, battery | — |
| **printer_control** | 3D printer: status, progress, files, pause/resume, preheat. Start/cancel need your CONFIRM click | Klipper (Moonraker) or OctoPrint |
| **build_guide** | A full step-by-step build guide (parts, steps with checks, mistakes, safety) saved as Markdown | — |
| **home_assistant** | "Turn my desk light blue": Tuya/Smart Life bulbs directly, or any Home Assistant server | Bulb keys, or HA address + token |

### Telegram (2 minutes)
1. In Telegram: @BotFather → /newbot → copy the token.
2. PAX → plugin settings → TELEGRAM REMOTE → paste token → TEST BOT.
3. Say "start Telegram". Open your bot and send it the **pairing code** shown in
   the settings. The code changes the moment it is used.
4. `/screenshot` and `/camera` are OFF until you switch them on. Files can only be
   sent from the **shared folder** (default: Desktop) — widen it in settings if you want more.

### 24/7 server (optional)
On your own computer, with a fresh Ubuntu/Debian VPS:

    python -m core.cloud deploy root@YOUR.SERVER.IP

Paste the printed `jarvis-cloud://…` code into CLOUD AGENT → TEST & SYNC.
Open TCP port 7443 in the VPS provider's firewall. While the PC is on it answers
Telegram itself (full screen/file powers); when it is off, the server answers.
"Accept requests from the cloud" is OFF by default.
This sends your Gemini key, Telegram token and memory to *your* server (TLS pinned
to the code's fingerprint) — only do it with a server you control.

## Safety model
- Irreversible actions (shutdown, restart, WiFi off, starting/cancelling a print) wait
  for **you** to press CONFIRM on the HUD; the model cannot confirm for itself.
- Telegram: allow-listed private chats only, rotating pairing code, 5-guess lockout,
  stale messages dropped, file sending confined to one folder, incoming files never executed.
- Reversible actions (file moves, volume, etc.) just happen — say "undo".
- API keys live in `config/api_keys.json`. Never share or commit that file.

## Not included
- Bambu Lab printers (MQTT) — only Moonraker and OctoPrint are supported.
- Gmail/Calendar plugins (the requirements list the Google libraries, but no plugin ships).
- Anything that makes it a hosted service: there is no PAX cloud; the server is yours.
