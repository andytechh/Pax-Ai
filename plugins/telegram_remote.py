"""
plugins/telegram_remote.py — your own private Telegram bot for PAX.

Message it, or send a voice note, from anywhere. On the computer it answers
while PAX is running; on a cloud server (core/cloud.py, optional) the SAME file
keeps answering while the computer is off, with the same memory.

SET UP (two minutes)
    1. In Telegram, talk to @BotFather → /newbot → copy the token.
    2. PAX → plugin settings → TELEGRAM REMOTE → paste the token → START.
       Or just say: "start Telegram".
    3. Open your new bot in Telegram and send it the PAIRING CODE shown in the
       settings (and in the activity log). That phone is now yours — the code
       changes the moment it is used.

THE WALLS (each one exists because the bot can drive your computer)
    1. Only chats on the allow-list are answered; everyone else gets silence
       (or, with a pairing code in hand, one chance to join).
    2. Pairing codes are 8 characters, compared in constant time, rotate after
       one use, and a chat that guesses wrong 5 times is ignored for 15 minutes.
    3. Private chats only. A bot added to a group is not an owner.
    4. Messages older than 10 minutes are dropped — a command that sat in
       Telegram's queue while the computer was off is not run hours later.
    5. Files are sent ONLY from the share folder, resolved through symlinks and
       "..", and never more than 50 MB.
    6. Files received land in the inbox folder under a sanitised name, never
       overwriting anything, and are never executed.
    7. Screenshots and the camera are separate switches, off by default.
    8. The token is never printed, logged or echoed in an error.
    9. Anything the model sends to the phone from a tool call goes through the
       same share-folder wall, and is written to the activity log.
"""
from __future__ import annotations

import atexit
import io
import re
import secrets
import threading
import time
from collections import deque
from pathlib import Path

try:
    from plugins import _telegram_ops as ops
except Exception:                                     # loaded from a bare path
    import importlib.util as _ilu
    import sys as _sys
    _p = Path(__file__).with_name("_telegram_ops.py")
    _spec = _ilu.spec_from_file_location("plugins._telegram_ops", _p)
    ops = _ilu.module_from_spec(_spec)
    _sys.modules["plugins._telegram_ops"] = ops
    _spec.loader.exec_module(ops)

NS = "telegram_remote"
API = "https://api.telegram.org"
STALE_SECONDS = 600
MAX_SEND_BYTES = 50 * 1024 * 1024
MAX_RECV_BYTES = 20 * 1024 * 1024          # the Bot API's own download limit
POLL_SECONDS = 20

_DEFAULTS = {
    "bot_token": "", "allowed_chat_ids": "", "pairing_code": "",
    "relay_replies": True, "voice_replies": False,
    "allow_screenshot": False, "allow_camera": False,
    "start_on_launch": False, "away_alerts": False,
    "share_folder": "", "inbox_folder": "",
}

# The names the cloud runtime reads (core/cloud.py TelegramFront).
_state: dict = {"running": False, "stopping": False, "turn_chat": None,
                "bot": None, "seen": 0, "skew": None, "error": ""}
_lock = threading.RLock()
_stop = threading.Event()
_out: "deque[dict]" = deque()
_out_cv = threading.Condition()
_turn = {"until": 0.0, "answered": False}
_buf = {"lines": [], "timer": None}
_buf_lock = threading.Lock()
_player = None
_orig_log = None
_fails: dict = {}                    # chat -> [count, blocked_until]
_threads: list = []


# ── config ───────────────────────────────────────────────────────────────────

def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def _cfg() -> dict:
    from memory.config_manager import get_plugin_config
    stored = get_plugin_config(NS)
    cfg = dict(_DEFAULTS)
    cfg.update({k: v for k, v in stored.items() if v is not None})
    for k in ("relay_replies", "voice_replies", "allow_screenshot", "allow_camera",
              "start_on_launch", "away_alerts"):
        cfg[k] = _truthy(cfg[k]) if k in stored else _DEFAULTS[k]
    if not str(cfg.get("share_folder") or "").strip():
        cfg["share_folder"] = str(Path.home() / "Desktop")
    if not str(cfg.get("inbox_folder") or "").strip():
        cfg["inbox_folder"] = str(Path.home() / "PAX Inbox")
    return cfg


def _save(**values) -> None:
    from memory.config_manager import save_plugin_config
    save_plugin_config(NS, values)


def _parse_ids(raw):
    return ops.parse_ids(raw)


def _token(cfg: dict | None = None) -> str:
    return "".join(str((cfg or _cfg()).get("bot_token") or "").split())


def _name() -> str:
    try:
        from memory.config_manager import get_assistant_name
        return get_assistant_name()
    except Exception:
        return "PAX"


def _log(text: str) -> None:
    p = _player
    try:
        if p is not None:
            p.write_log(text)
        else:
            print(text)
    except Exception:
        pass


# ── Telegram API ─────────────────────────────────────────────────────────────

class _Conflict(RuntimeError):
    pass


class _AuthError(RuntimeError):
    pass


def _api(token: str, method: str, data: dict | None = None, files: dict | None = None,
         timeout: float = 30.0):
    import requests

    url = f"{API}/bot{token}/{method}"
    try:
        if files:
            r = requests.post(url, data=data or {}, files=files, timeout=timeout)
        else:
            r = requests.post(url, json=data or {}, timeout=timeout)
        body = r.json()
    except requests.RequestException as e:
        # The URL carries the token; report the kind of failure, never the URL.
        raise RuntimeError(f"network error ({type(e).__name__})") from None
    except ValueError:
        raise RuntimeError(f"Telegram answered with something that is not JSON ({r.status_code})") from None
    if body.get("ok"):
        return body.get("result")
    code = int(body.get("error_code") or r.status_code)
    desc = str(body.get("description") or "error")
    if code == 409:
        raise _Conflict(desc)
    if code in (401, 404):
        raise _AuthError("Telegram rejected the bot token")
    if code == 429:
        time.sleep(min(int((body.get("parameters") or {}).get("retry_after") or 3), 30))
        raise RuntimeError("rate limited, retrying")
    raise RuntimeError(f"{code}: {desc}")


def _download(token: str, file_id: str) -> bytes:
    import requests

    info = _api(token, "getFile", {"file_id": file_id})
    size = int(info.get("file_size") or 0)
    if size > MAX_RECV_BYTES:
        raise RuntimeError(f"that file is {ops.human_size(size)}; Telegram bots can only fetch 20 MB")
    try:
        r = requests.get(f"{API}/file/bot{token}/{info['file_path']}", timeout=120)
        r.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"download failed ({type(e).__name__})") from None
    return r.content


# ── outgoing queue (the names core/cloud.py calls) ───────────────────────────

def _put(item: dict) -> None:
    with _out_cv:
        _out.append(item)
        _out_cv.notify()


def _enqueue(chat, text: str) -> None:
    _put({"kind": "text", "chat": chat, "text": text})


def _keyboard(cfg: dict) -> dict:
    row1 = [{"text": "📊 Status"}]
    if cfg.get("allow_screenshot"):
        row1.append({"text": "🖥 Screenshot"})
    rows = [row1]
    row2 = []
    if cfg.get("allow_camera"):
        row2.append({"text": "📷 Camera"})
    row2.append({"text": "❓ Help"})
    rows.append(row2)
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}


def _send_item(token: str, item: dict) -> None:
    cid, kind = item.get("chat"), item.get("kind", "text")
    if not cid:
        return
    if kind == "text":
        chunks = ops.split_text(item.get("text", ""))
        for i, chunk in enumerate(chunks):
            payload = {"chat_id": cid, "text": chunk, "disable_web_page_preview": True}
            if i == len(chunks) - 1:
                payload["reply_markup"] = item.get("markup") or _keyboard(_cfg())
            _api(token, "sendMessage", payload)
    elif kind in ("photo", "document"):
        field, method = ("photo", "sendPhoto") if kind == "photo" else ("document", "sendDocument")
        _api(token, method, {"chat_id": cid, "caption": str(item.get("caption") or "")[:1024]},
             files={field: (item.get("name") or "file", item["data"], item.get("mime") or "application/octet-stream")},
             timeout=180)
    elif kind == "action":
        _api(token, "sendChatAction", {"chat_id": cid, "action": item.get("action", "typing")})


def _sender(token: str) -> None:
    while not _stop.is_set():
        with _out_cv:
            if not _out:
                _out_cv.wait(timeout=1.0)
            item = _out.popleft() if _out else None
        if item is None:
            continue
        for attempt in range(3):
            try:
                _send_item(token, item)
                break
            except _AuthError:
                _state["error"] = "the bot token was rejected"
                _stop.set()
                return
            except Exception as e:
                if attempt == 2:
                    print(f"[Telegram] could not send ({e})")
                else:
                    time.sleep(1.5 * (attempt + 1))


# ── a turn: the stretch of time in which the assistant's words go to a phone ──

def _turn_active() -> bool:
    return bool(_state.get("turn_chat")) and time.time() < _turn["until"]


def _open_turn(chat, seconds: float = 150.0) -> None:
    _state["turn_chat"] = chat
    _turn["until"] = time.time() + seconds
    _turn["answered"] = False
    _put({"kind": "action", "chat": chat, "action": "typing"})

    def nudge(c=chat):
        if _state.get("turn_chat") == c and not _turn["answered"] and _turn_active():
            _enqueue(c, "⏳ Still working on it…")
    threading.Timer(45.0, nudge).start()


def _relay(line: str) -> None:
    if not _turn_active() or not _cfg()["relay_replies"]:
        return
    text = str(line or "").strip()
    m = re.match(rf"^{re.escape(_name())}\s*:\s*(.+)$", text, re.I | re.S)
    if m:
        body = m.group(1).strip()
    elif re.match(r"^SYS:\s*I'm asleep", text, re.I):
        body = "I'm asleep right now. Say the wake word at the computer, or tap WAKE NOW."
    else:
        return
    if not body:
        return
    _turn["answered"] = True
    # Once the answer is in, the window closes in seconds — long enough to catch the
    # rest of a chunked reply, too short for something said at the PC afterwards
    # (or by someone else in the room) to be sent to the phone.
    _turn["until"] = time.time() + 6
    with _buf_lock:
        _buf["lines"].append(body)
        if _buf["timer"]:
            _buf["timer"].cancel()
        _buf["timer"] = threading.Timer(2.5, _flush)
        _buf["timer"].start()


def _flush() -> None:
    with _buf_lock:
        text = " ".join(_buf["lines"]).strip()
        _buf["lines"].clear()
        _buf["timer"] = None
    chat = _state.get("turn_chat")
    if text and chat:
        _enqueue(chat, text)


def _wrap_player(player) -> None:
    global _orig_log
    if getattr(player, "_tg_wrapped", False):
        return
    orig = player.write_log
    _orig_log = orig

    def wrapped(text, _orig=orig):
        _orig(text)
        try:
            _relay(text)
        except Exception:
            pass
    player.write_log = wrapped
    player._tg_wrapped = True


def _unwrap_player(player) -> None:
    if player is not None and getattr(player, "_tg_wrapped", False):
        try:
            if _orig_log is not None:
                player.write_log = _orig_log
            else:
                del player.write_log
        except Exception:
            pass
        player._tg_wrapped = False


# ── pairing and authorisation ────────────────────────────────────────────────

def _ensure_pairing(cfg: dict) -> str:
    code = str(cfg.get("pairing_code") or "").strip()
    if not code:
        code = ops.new_pairing_code()
        _save(pairing_code=code)
    return code


def _blocked(chat) -> bool:
    rec = _fails.get(chat)
    return bool(rec and rec[1] > time.time())


def _fail(chat) -> None:
    rec = _fails.setdefault(chat, [0, 0.0])
    rec[0] += 1
    if rec[0] >= 5:
        rec[0], rec[1] = 0, time.time() + 900


def _try_pair(chat, who: str, text: str) -> bool:
    cfg = _cfg()
    code = ops.normalise_code(cfg.get("pairing_code"))
    sent = ops.normalise_code(re.sub(r"^/start\s*", "", text.strip(), flags=re.I))
    if not code or len(sent) != len(code) or not secrets.compare_digest(sent, code):
        _fail(chat)
        return False
    ids = _parse_ids(cfg.get("allowed_chat_ids"))
    if chat not in ids:
        ids.append(chat)
    _save(allowed_chat_ids=",".join(map(str, ids)), pairing_code=ops.new_pairing_code())
    _log(f"SYS: Telegram — paired {who}. A new pairing code was generated.")
    _enqueue(chat, f"✅ Paired. I'm {_name()}. Talk to me like you would at the computer, "
                   f"or send a voice note. /help shows what I can do from here.")
    return True


# ── incoming ─────────────────────────────────────────────────────────────────

def _safe_handle(token: str, update: dict) -> None:
    try:
        _handle(token, update)
    except _AuthError:
        _stop.set()
    except Exception as e:
        print(f"[Telegram] handler failed ({type(e).__name__}: {e})")


def _handle(token: str, update: dict) -> None:
    cb = update.get("callback_query")
    msg = update.get("message")
    if cb:
        try:
            _api(token, "answerCallbackQuery", {"callback_query_id": cb.get("id")})
        except Exception:
            pass
        msg = dict(cb.get("message") or {})
        msg["text"] = cb.get("data") or ""
        msg["from"] = cb.get("from") or {}
    if not msg:
        return
    chat_obj = msg.get("chat") or {}
    if chat_obj.get("type") != "private":                          # wall 3
        return
    chat = chat_obj.get("id")
    date = int(msg.get("date") or 0)
    if not cb and date:
        _state["skew"] = int(time.time() - date)
        if time.time() - date > STALE_SECONDS:                      # wall 4
            return
    who = (msg.get("from") or {}).get("first_name") or "a phone"
    text = str(msg.get("text") or msg.get("caption") or "").strip()
    cfg = _cfg()
    allowed = _parse_ids(cfg.get("allowed_chat_ids"))

    if chat not in allowed:                                         # wall 1
        if _blocked(chat) or not text:
            return
        if _try_pair(chat, who, text):
            return
        if text.lower().startswith("/start"):
            _enqueue(chat, "This is a private assistant. Send the pairing code shown on the "
                           "computer to connect this phone.")
        return

    _state["seen"] = int(_state.get("seen") or 0) + 1
    low = text.lower()
    cmd = low.split()[0].split("@")[0] if low.startswith("/") else ""
    arg = text.split(None, 1)[1].strip() if cmd and len(text.split(None, 1)) > 1 else ""

    if msg.get("voice") or msg.get("audio"):
        media = msg.get("voice") or msg.get("audio")
        text = _transcribe_message(token, chat, media)
        if not text:
            return
    elif msg.get("photo") or msg.get("document") or msg.get("video"):
        return _receive_file(token, chat, msg, cfg, text)
    elif cmd in ("/help", "/start") or "help" in low and low.endswith("help") and len(low) < 9:
        return _enqueue(chat, _help(cfg))
    elif cmd == "/status" or low.endswith("status") and len(low) < 12:
        return _enqueue(chat, _status_text())
    elif cmd == "/screenshot" or low.endswith("screenshot") and len(low) < 16:
        return _send_screenshot(chat, cfg)
    elif cmd == "/camera" or low.endswith("camera") and len(low) < 12:
        return _send_camera(chat, cfg)
    elif cmd in ("/send", "/file"):
        return _send_named_file(chat, cfg, arg)
    elif cmd == "/unpair":
        ids = [i for i in allowed if i != chat]
        _save(allowed_chat_ids=",".join(map(str, ids)))
        _log("SYS: Telegram — a phone was unpaired from the phone itself.")
        return _enqueue(chat, "This phone is unpaired.")
    elif cmd.startswith("/"):
        return _enqueue(chat, "I don't know that command. /help lists them — or just say what you want.")

    if not text:
        return
    _handoff(chat, text)


def _handoff(chat, text: str) -> None:
    p = _player
    handler = getattr(p, "on_text_command", None) if p is not None else None
    if not handler:
        return _enqueue(chat, "I'm still starting up — try again in a few seconds.")
    _open_turn(chat)
    _log(f"SYS: Telegram → {text[:120]}")
    handler(f"[Message from the user's phone via Telegram — keep the answer short] {text}")


def _transcribe_message(token: str, chat, media: dict) -> str:
    try:
        from google.genai import types

        from core import gemini
        data = _download(token, media["file_id"])
        mime = str(media.get("mime_type") or "audio/ogg").split(";")[0]
        part = types.Part.from_bytes(data=data, mime_type=mime)
        heard = gemini.text(["Transcribe this voice message exactly, in the language it was spoken. "
                             "Output only the transcript, nothing else.", part],
                            tier=gemini.FAST, timeout_ms=60_000)
        heard = str(heard or "").strip()
    except Exception as e:
        _enqueue(chat, f"I couldn't understand that voice note ({e}).")
        return ""
    if heard:
        _enqueue(chat, f"🎙 {heard}")
    else:
        _enqueue(chat, "I couldn't hear anything in that voice note.")
    return heard


def _receive_file(token: str, chat, msg: dict, cfg: dict, caption: str) -> None:
    if msg.get("photo"):
        best = max(msg["photo"], key=lambda p: int(p.get("file_size") or 0))
        file_id, name = best["file_id"], f"photo_{int(time.time())}.jpg"
    else:
        doc = msg.get("document") or msg.get("video") or {}
        file_id, name = doc.get("file_id"), doc.get("file_name") or f"file_{int(time.time())}"
    if not file_id:
        return
    try:
        data = _download(token, file_id)
        dest = ops.unique_path(Path(cfg["inbox_folder"]).expanduser(), name)    # wall 6
        dest.write_bytes(data)
    except Exception as e:
        return _enqueue(chat, f"I couldn't save that file ({e}).")
    _log(f"FILE: Telegram — saved {dest.name} ({ops.human_size(len(data))}) to {dest.parent}")
    note = f" Their note: {caption}" if caption else ""
    if _player is not None and getattr(_player, "on_text_command", None):
        _open_turn(chat)
        _player.on_text_command(
            f"[Message from the user's phone via Telegram] I just sent you a file. It is saved at "
            f"{dest}.{note} Tell me you have it and ask what to do with it, unless I already said.")
    else:
        _enqueue(chat, f"Saved as {dest.name}.")


def _help(cfg: dict) -> str:
    lines = [f"I'm {_name()}. Just write or send a voice note — anything you'd say at the computer.",
             "", "Shortcuts:", "/status — how the PC is doing"]
    if cfg.get("allow_screenshot"):
        lines.append("/screenshot — what's on the screen")
    if cfg.get("allow_camera"):
        lines.append("/camera — a photo from the webcam")
    lines += ["/send <name> — a file from the shared folder",
              "Send me a file or photo and I'll save it to the inbox.",
              "/unpair — disconnect this phone"]
    return "\n".join(lines)


def _status_text() -> str:
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.4)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage(str(Path.home().anchor or "/"))
        up = int(time.time() - psutil.boot_time())
        lines = [f"🟢 {_name()} is running on this PC.",
                 f"CPU {cpu:.0f}% · RAM {ram.percent:.0f}% · Disk {disk.percent:.0f}% used",
                 f"PC up {up // 3600}h {up % 3600 // 60}m"]
        bat = psutil.sensors_battery()
        if bat:
            lines.append(f"Battery {bat.percent:.0f}%{' (charging)' if bat.power_plugged else ''}")
        return "\n".join(lines)
    except Exception as e:
        return f"🟢 {_name()} is running. (Could not read the system: {e})"


def _send_screenshot(chat, cfg: dict) -> None:
    if not cfg.get("allow_screenshot"):                             # wall 7
        return _enqueue(chat, "Screenshots are switched off. Enable them in TELEGRAM REMOTE settings on the computer.")
    try:
        data = capture_screen_jpeg()
    except Exception as e:
        return _enqueue(chat, f"I couldn't capture the screen ({e}).")
    _put({"kind": "photo", "chat": chat, "data": data, "name": "screen.jpg", "mime": "image/jpeg",
          "caption": time.strftime("Screen at %H:%M")})
    _log("SYS: Telegram — screenshot sent to the phone.")


def capture_screen_jpeg(max_width: int = 1600) -> bytes:
    import mss
    from PIL import Image
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[0])
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    if img.width > max_width:
        img = img.resize((max_width, int(img.height * max_width / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return buf.getvalue()


def _send_camera(chat, cfg: dict) -> None:
    if not cfg.get("allow_camera"):
        return _enqueue(chat, "The camera is switched off. Enable it in TELEGRAM REMOTE settings on the computer.")
    try:
        import cv2
        cam = cv2.VideoCapture(0)
        try:
            ok, frame = False, None
            for _ in range(5):                       # the first frames are dark
                ok, frame = cam.read()
                time.sleep(0.15)
        finally:
            cam.release()
        if not ok:
            raise RuntimeError("no frame from the webcam")
        ok, enc = cv2.imencode(".jpg", frame)
        data = enc.tobytes()
    except Exception as e:
        return _enqueue(chat, f"I couldn't use the camera ({e}).")
    _put({"kind": "photo", "chat": chat, "data": data, "name": "camera.jpg", "mime": "image/jpeg"})
    _log("SYS: Telegram — camera photo sent to the phone.")


def _send_named_file(chat, cfg: dict, query: str) -> None:
    if not query:
        return _enqueue(chat, "Which file? For example: /send report")
    base = Path(cfg["share_folder"]).expanduser()
    found = ops.find_file(base, query)
    ok, why = _send_path(chat, found, base) if found else (False, f"I found nothing like '{query}' in {base}.")
    if not ok:
        _enqueue(chat, why)


def _send_path(chat, path, base: Path) -> tuple[bool, str]:
    safe = ops.confine(base, path)                                  # wall 5
    if safe is None or not safe.is_file():
        return False, f"That file is outside the shared folder ({base}), so I won't send it."
    size = safe.stat().st_size
    if size > MAX_SEND_BYTES:
        return False, f"{safe.name} is {ops.human_size(size)} — Telegram allows 50 MB."
    _put({"kind": "document", "chat": chat, "data": safe.read_bytes(), "name": safe.name,
          "mime": "application/octet-stream", "caption": safe.name})
    _log(f"FILE: Telegram — sent {safe.name} ({ops.human_size(size)}) to the phone.")
    return True, f"Sent {safe.name}."


# ── polling ──────────────────────────────────────────────────────────────────

def _poller(token: str) -> None:
    offset = None
    backoff = 2.0
    try:
        while not _stop.is_set():
            try:
                updates = _api(token, "getUpdates",
                               {"timeout": POLL_SECONDS, "offset": offset,
                                "allowed_updates": ["message", "callback_query"]},
                               timeout=POLL_SECONDS + 10)
            except _Conflict:
                _state["error"] = "another program is already polling this bot (409)"
                print("[Telegram] 409: another process is using this bot — stopping.")
                break
            except _AuthError:
                _state["error"] = "the bot token was rejected"
                break
            except Exception as e:
                print(f"[Telegram] poll error ({e})")
                _stop.wait(backoff)
                backoff = min(backoff * 2, 60)
                continue
            backoff = 2.0
            for u in updates or []:
                offset = int(u["update_id"]) + 1
                t = threading.Thread(target=_safe_handle, args=(token, u), daemon=True)
                t.start()
    finally:
        with _lock:
            _state["running"] = False
            _state["stopping"] = False
            _unwrap_player(_player)
        _stop.set()
        with _out_cv:
            _out_cv.notify_all()


# ── tool entry points ────────────────────────────────────────────────────────

def _start(player) -> str:
    global _player
    with _lock:
        if _state["running"]:
            return "already listening"
        cfg = _cfg()
        token = _token(cfg)
        if not token:
            return "no bot token yet — paste it in the TELEGRAM REMOTE plugin settings"
        try:
            me = _api(token, "getMe", timeout=15)
        except _AuthError:
            return "Telegram rejected that token — check it with @BotFather"
        except Exception as e:
            return f"could not reach Telegram ({e})"
        code = _ensure_pairing(cfg)
        _player = player
        if player is not None:
            _wrap_player(player)
        _state.update(running=True, stopping=False, bot=me.get("username"), error="", seen=0)
        _stop.clear()
        with _out_cv:
            _out.clear()
        _threads[:] = [threading.Thread(target=_poller, args=(token,), daemon=True, name="tg-poll"),
                       threading.Thread(target=_sender, args=(token,), daemon=True, name="tg-send")]
        for t in _threads:
            t.start()
    paired = len(_parse_ids(cfg.get("allowed_chat_ids")))
    if paired:
        return f"listening as @{me.get('username')} ({paired} paired phone{'s' if paired != 1 else ''})"
    _log(f"SYS: Telegram — open @{me.get('username')} and send it this pairing code: {code}")
    return f"listening as @{me.get('username')}; send it the pairing code {code} to connect your phone"


def _stop_bot() -> str:
    with _lock:
        if not _state["running"]:
            return "not running"
        _state["stopping"] = True
    _stop.set()
    with _out_cv:
        _out_cv.notify_all()
    return "stopping"


def test_connection(values: dict):
    """The TEST button in plugin settings."""
    token = "".join(str(values.get("bot_token") or "").split())
    if not token:
        return False, "Paste the token from @BotFather first."
    try:
        me = _api(token, "getMe", timeout=15)
    except Exception as e:
        return False, str(e)
    return True, f"Connected to @{me.get('username')}."


def _status_report() -> str:
    cfg = _cfg()
    ids = _parse_ids(cfg.get("allowed_chat_ids"))
    if _state["running"]:
        s = f"Telegram is on as @{_state.get('bot')}, {len(ids)} paired phone(s)."
    else:
        s = "Telegram is off." + (f" Last problem: {_state['error']}." if _state.get("error") else "")
    if not ids:
        s += f" No phone is paired yet; the pairing code is {cfg.get('pairing_code') or '(created on start)'}."
    return s


PLUGIN = {
    "name": "telegram_remote",
    "tier": "extra",
    "description": (
        "The user's private Telegram bot — it lets them message or voice-note the assistant from "
        "their phone. Use for: 'start/stop Telegram', 'is the phone bot on', 'what's my pairing "
        "code', 'send me <file> on Telegram / my phone', 'message my phone'. Files can only be "
        "sent from the user's shared folder. Do NOT use for WhatsApp or other messengers "
        "(send_message handles those)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "description": "start | stop | status | send_file | send_message"},
            "path": {"type": "STRING",
                     "description": "send_file: a file name or path inside the shared folder"},
            "text": {"type": "STRING", "description": "send_message: the words to send to the phone"},
        },
        "required": ["action"],
    },
}

PLUGIN_SETTINGS = {
    "namespace": NS,
    "title": "TELEGRAM REMOTE",
    "fields": [
        {"key": "bot_token", "label": "Bot token (from @BotFather)", "type": "password"},
        {"key": "allowed_chat_ids", "label": "Paired chat IDs (filled in by pairing)", "type": "text",
         "placeholder": "added automatically"},
        {"key": "pairing_code", "label": "Pairing code — send this to your bot", "type": "text"},
        {"key": "relay_replies", "label": "Send answers to the phone", "type": "toggle", "default": True},
        {"key": "allow_screenshot", "label": "Allow /screenshot", "type": "toggle", "default": False},
        {"key": "allow_camera", "label": "Allow /camera", "type": "toggle", "default": False},
        {"key": "start_on_launch", "label": "Start with PAX", "type": "toggle", "default": False},
        {"key": "share_folder", "label": "Folder the phone may take files from", "type": "text",
         "placeholder": "default: Desktop"},
        {"key": "inbox_folder", "label": "Where files from the phone are saved", "type": "text",
         "placeholder": "default: ~/PAX Inbox"},
    ],
    "action": {"label": "TEST BOT", "run": test_connection},
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "status").strip().lower()
    try:
        if action == "start":
            return _start(player)
        if action == "stop":
            return _stop_bot()
        if action == "status":
            return _status_report()
        if action in ("send_file", "send_message"):
            if not _state["running"]:
                return "Telegram is off — start it first."
            cfg = _cfg()
            targets = _parse_ids(cfg.get("allowed_chat_ids"))
            if not targets:
                return "No phone is paired yet."
            if action == "send_message":
                text = str(parameters.get("text") or "").strip()
                if not text:
                    return "What should I send?"
                for cid in targets:
                    _enqueue(cid, text)
                return "Sent to your phone."
            query = str(parameters.get("path") or "").strip()
            if not query:
                return "Which file should I send?"
            base = Path(cfg["share_folder"]).expanduser()
            candidate = ops.confine(base, query) if (Path(query).is_absolute() or "/" in query or "\\" in query) \
                else ops.find_file(base, query)
            if candidate is None or not Path(candidate).is_file():
                return (f"I couldn't find '{query}' inside the shared folder ({base}). "
                        f"Change the shared folder in TELEGRAM REMOTE settings to widen it.")
            ok, why = True, ""
            for cid in targets:
                ok, why = _send_path(cid, candidate, base)
            return why
        return f"Unknown action '{action}'. Use start, stop, status, send_file or send_message."
    except Exception as e:
        return f"Telegram failed: {e}"


# ── start with the app, and hand the bot back on exit ────────────────────────

def _autostart() -> None:
    try:
        cfg = _cfg()
        if not (cfg["start_on_launch"] and _token(cfg)):
            return
        # When a cloud server is paired and the computer is asked to hold the bot,
        # cloud_agent starts Telegram itself after the server lets go of it.
        from memory.config_manager import get_plugin_config
        if str(get_plugin_config("cloud_agent").get("pairing_code") or "").strip():
            return
        player = ops.find_player()
        if player is not None and not _state["running"]:
            print(f"[Telegram] auto-start — {_start(player)}")
    except Exception as e:
        print(f"[Telegram] auto-start failed ({type(e).__name__})")


def _at_exit() -> None:
    try:
        if _state["running"]:
            _stop.set()
    except Exception:
        pass


atexit.register(_at_exit)
try:
    threading.Thread(target=_autostart, daemon=True, name="tg-autostart").start()
except Exception:
    pass
