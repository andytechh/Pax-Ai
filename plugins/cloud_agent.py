"""
plugins/cloud_agent.py — pair this computer with your own PAX server.

WHAT YOU GET
    Telegram keeps answering while this computer is OFF, with the same memory;
    long research and reminders keep running; the server wakes the PC if you set
    up wake-on-LAN; and when the computer is on it takes Telegram back, so your
    phone talks to the full assistant with the screen and the files.

WHAT IT NEEDS
    A small Linux server of your own (any $4–6/month VPS with a public IP).
    Nothing of PAX is hosted by anyone else. On YOUR computer:

        python -m core.cloud deploy root@YOUR.SERVER.IP

    That copies the cloud body over SSH, installs it as a service, and prints a
    pairing code (it starts with jarvis-cloud://). Paste the code into
    PAX → plugin settings → CLOUD AGENT → TEST & SYNC.

WHAT LEAVES THIS COMPUTER (only when you connect, only to your server)
    Your assistant name, your Gemini key, the Telegram bot token and paired chats,
    and your memory file — over TLS pinned to the certificate fingerprint inside
    the pairing code. The server can never reach back into this computer: it can
    only QUEUE a request, and the computer runs it only if "accept requests from
    the cloud" is on (off by default).
"""
from __future__ import annotations

import atexit
import base64
import platform
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    from plugins import _telegram_ops as ops
except Exception:
    import importlib.util as _ilu
    import sys as _sys
    _p = Path(__file__).with_name("_telegram_ops.py")
    _spec = _ilu.spec_from_file_location("plugins._telegram_ops", _p)
    ops = _ilu.module_from_spec(_spec)
    _sys.modules["plugins._telegram_ops"] = ops
    _spec.loader.exec_module(ops)

NS = "cloud_agent"
POLL_WAIT = 25
RESYNC_SECONDS = 900

_state: dict = {"connected": False, "client": None, "event_seq": 0, "memory_seq": 0,
                "error": "", "holds": False, "last_sync": 0.0, "server": ""}
_stop = threading.Event()
_lock = threading.RLock()
_thread: threading.Thread | None = None
_player = None
_capture = {"active": False, "lines": [], "last": 0.0}
_cap_lock = threading.Lock()


# ── config ───────────────────────────────────────────────────────────────────

def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def _cfg() -> dict:
    from memory.config_manager import get_plugin_config
    c = get_plugin_config(NS)
    return {
        "pairing_code": str(c.get("pairing_code") or "").strip(),
        "server_pem": str(c.get("server_pem") or ""),
        "accept_commands": _truthy(c.get("accept_commands", False)),
        "hold_telegram": _truthy(c.get("hold_telegram", True)),
        "auto_connect": _truthy(c.get("auto_connect", True)),
        "wake_mac": str(c.get("wake_mac") or ""), "wake_host": str(c.get("wake_host") or ""),
        "wake_port": str(c.get("wake_port") or ""), "wake_url": str(c.get("wake_url") or ""),
    }


def _save(**values) -> None:
    from memory.config_manager import save_plugin_config
    save_plugin_config(NS, values)


def _log(text: str) -> None:
    try:
        if _player is not None:
            _player.write_log(text)
        else:
            print(text)
    except Exception:
        pass


def _name() -> str:
    try:
        from memory.config_manager import get_assistant_name
        return get_assistant_name()
    except Exception:
        return "PAX"


# ── the link ─────────────────────────────────────────────────────────────────

def _make_client(cfg: dict):
    from core import cloud
    if not cfg["pairing_code"]:
        raise RuntimeError("no pairing code yet — run `python -m core.cloud deploy root@YOUR.SERVER` "
                           "and paste the code it prints into CLOUD AGENT settings")
    try:
        return cloud.Client.from_code(cfg["pairing_code"], cfg["server_pem"])
    except ValueError as e:
        raise RuntimeError(str(e)) from None


def _request(route: str, body: dict | None = None, timeout: float = 20.0) -> dict:
    client = _state["client"]
    if client is None:
        raise RuntimeError("not connected to a server")
    data = client.request("POST", route, body or {}, timeout=timeout)
    if client.pem and client.pem != _cfg()["server_pem"]:
        _save(server_pem=client.pem)                 # pinned: later runs reject any other certificate
    return data


def _tz_minutes() -> int:
    off = datetime.now().astimezone().utcoffset()
    return int(off.total_seconds() // 60) if off else 0


def _info() -> dict:
    tg = ops.telegram_module()
    return {"accept_commands": _cfg()["accept_commands"],
            "holds_telegram": bool(_state["holds"] and tg and tg._state.get("running")),
            "os": platform.system(), "host": platform.node(), "name": _name()}


def _sync_body() -> dict:
    from core import gemini
    from memory.config_manager import get_plugin_config, get_user_name
    from memory.memory_manager import load_memory
    cfg = _cfg()
    tgc = get_plugin_config("telegram_remote")
    settings = {"assistant_name": _name(), "user_name": get_user_name() or "",
                "gemini_api_key": gemini.api_key() or "", "tz_offset_min": _tz_minutes(),
                "wake": {k: cfg[k] for k in ("wake_mac", "wake_host", "wake_port", "wake_url")}}
    if str(tgc.get("bot_token") or "").strip():
        settings["telegram"] = {k: tgc.get(k) for k in
                                ("bot_token", "relay_replies", "voice_replies",
                                 "allowed_chat_ids", "pairing_code") if tgc.get(k) not in (None, "")}
    return {"settings": settings, "memory": load_memory(), "memory_seq": _state["memory_seq"]}


def _sync() -> dict:
    out = _request("sync", _sync_body(), timeout=60)
    _state["memory_seq"] = int(out.get("memory_seq") or _state["memory_seq"])
    _state["last_sync"] = time.time()
    ids = str((out.get("telegram") or {}).get("allowed_chat_ids") or "")
    if ids:
        from memory.config_manager import save_plugin_config
        save_plugin_config("telegram_remote", {"allowed_chat_ids": ids})
    return out


# ── Telegram hand-over ───────────────────────────────────────────────────────

def _take_telegram() -> str:
    tg = ops.telegram_module()
    if tg is None:
        return "telegram_remote is not loaded"
    if not tg._cfg().get("bot_token"):
        return "no bot token set"
    out = _request("telegram", {"hold": True}, timeout=60)
    if not out.get("released"):
        return "the server did not let go of Telegram in time; it keeps answering"
    _state["holds"] = True
    return tg.run({"action": "start"}, player=_player)


def _give_back_telegram() -> None:
    tg = ops.telegram_module()
    if tg is not None and tg._state.get("running"):
        tg.run({"action": "stop"}, player=_player)
        for _ in range(8):                        # a long poll takes a moment to end
            if not tg._state.get("running"):
                break
            time.sleep(0.5)
    _state["holds"] = False
    try:
        _request("telegram", {"hold": False}, timeout=20)
    except Exception:
        pass


# ── requests the server queues for this computer ─────────────────────────────

def _install_capture(player) -> None:
    if getattr(player, "_cloud_wrapped", False):
        return
    orig = player.write_log

    def wrapped(text, _orig=orig):
        _orig(text)
        try:
            if _capture["active"]:
                import re
                m = re.match(rf"^{re.escape(_name())}\s*:\s*(.+)$", str(text).strip(), re.I | re.S)
                if m:
                    with _cap_lock:
                        _capture["lines"].append(m.group(1).strip())
                        _capture["last"] = time.time()
        except Exception:
            pass
    player.write_log = wrapped
    player._cloud_wrapped = True


def _output(cmd_id: str, text: str = "", done: bool = True, photo: bytes | None = None) -> None:
    body = {"cmd_id": cmd_id, "text": text, "done": done}
    if photo:
        body["photo_b64"] = base64.b64encode(photo).decode("ascii")
    try:
        _request("output", body, timeout=60)
    except Exception as e:
        _log(f"SYS: Cloud — could not return a result ({e})")


def _run_command(cmd: dict) -> None:
    cid, kind = str(cmd.get("id") or ""), str(cmd.get("kind") or "")
    cfg = _cfg()
    if not cfg["accept_commands"]:
        return _output(cid, "The computer is on, but it is set not to accept requests from the cloud "
                            "(CLOUD AGENT settings on the computer).")
    if kind == "system":
        tg = ops.telegram_module()
        return _output(cid, tg._status_text() if tg else f"{_name()} is running.")
    if kind == "screenshot":
        try:
            tg = ops.telegram_module()
            return _output(cid, "", photo=tg.capture_screen_jpeg())
        except Exception as e:
            return _output(cid, f"I couldn't capture the screen ({e}).")
    if kind == "command":
        handler = getattr(_player, "on_text_command", None)
        text = str(cmd.get("text") or "").strip()
        if not handler or not text:
            return _output(cid, "I'm not ready to take that yet.")
        with _cap_lock:
            _capture.update(active=True, lines=[], last=0.0)
        _log(f"SYS: Cloud → {text[:120]}")
        handler(f"[Request relayed from the user's own cloud server — it came from their phone. "
                f"Keep the answer short] {text}")
        started = time.time()
        while time.time() - started < 120:
            time.sleep(1.0)
            with _cap_lock:
                if _capture["lines"] and time.time() - _capture["last"] > 4:
                    break
        with _cap_lock:
            reply = " ".join(_capture["lines"]).strip()
            _capture.update(active=False, lines=[])
        return _output(cid, reply or "Done — nothing to report back.")
    _output(cid, f"I don't know how to do '{kind}'.")


def _apply_events(events: list) -> None:
    for e in events or []:
        text = str(e.get("text") or e.get("summary") or e.get("message") or "").strip()
        kind = str(e.get("kind") or "event")
        if text:
            _log(f"SYS: Cloud · {kind} — {text[:300]}")
            if len(text) > 300 and _player is not None:
                try:
                    _player.show_content(f"FROM YOUR SERVER · {kind}"[:48], text)
                except Exception:
                    pass


def _apply_deltas(deltas: list) -> None:
    if not deltas:
        return
    try:
        from memory.memory_manager import remember
        for d in deltas:
            if d.get("op") == "set" and d.get("key") and d.get("value"):
                remember(str(d["key"]), str(d["value"]), str(d.get("category") or "notes"))
    except Exception as e:
        _log(f"SYS: Cloud — could not merge memory ({e})")


# ── the connection loop ──────────────────────────────────────────────────────

def _poll_loop() -> None:
    backoff, was_down = 3.0, False
    while not _stop.is_set():
        try:
            out = _request("poll", {"info": _info(), "event_seq": _state["event_seq"],
                                    "memory_seq": _state["memory_seq"], "wait": POLL_WAIT},
                           timeout=POLL_WAIT + 25)
            if was_down:
                _log("SYS: Cloud — reconnected.")
                was_down = False
                try:
                    _sync()
                except Exception:
                    pass
            backoff = 3.0
            _state["event_seq"] = int(out.get("event_seq") or _state["event_seq"])
            _state["memory_seq"] = int(out.get("memory_seq") or _state["memory_seq"])
            _apply_events(out.get("events"))
            _apply_deltas(out.get("deltas"))
            for cmd in out.get("commands") or []:
                threading.Thread(target=_run_command, args=(cmd,), daemon=True).start()
            if time.time() - _state["last_sync"] > RESYNC_SECONDS:
                _sync()
        except Exception as e:
            status = getattr(e, "status", 0)
            if status == 401:
                _state.update(connected=False, error="the server rejected the pairing code")
                _log("SYS: Cloud — the server rejected the pairing code. Disconnected.")
                return
            if not was_down:
                _log(f"SYS: Cloud — connection lost ({e}). Retrying.")
                was_down = True
            _stop.wait(backoff)
            backoff = min(backoff * 2, 90)
    _state["connected"] = False


def _connect(player) -> str:
    global _thread, _player
    with _lock:
        if _state["connected"]:
            return "already connected"
        cfg = _cfg()
        _player = player or _player
        _state["client"] = _make_client(cfg)
        hello = _request("hello", timeout=20)
        _state["server"] = str(hello.get("host") or "")
        _sync()
        if _player is not None:
            _install_capture(_player)
        _state.update(connected=True, error="")
        _stop.clear()
    note = ""
    if cfg["hold_telegram"]:
        try:
            note = f" Telegram: {_take_telegram()}."
        except Exception as e:
            note = f" Telegram hand-over failed ({e})."
    _thread = threading.Thread(target=_poll_loop, daemon=True, name="cloud-poll")
    _thread.start()
    return (f"connected to your server '{_state['server']}'. Server Telegram: "
            f"{'on' if hello.get('telegram_running') else 'off'}, "
            f"{hello.get('telegram_phones', 0)} phone(s) paired.{note}")


def _disconnect() -> str:
    with _lock:
        if not _state["connected"]:
            return "not connected"
        _stop.set()
    _give_back_telegram()
    try:
        _request("bye", timeout=10)
    except Exception:
        pass
    _state["connected"] = False
    return "disconnected — the server keeps answering Telegram on its own"


def test_connection(values: dict):
    """The CONNECT button in plugin settings."""
    try:
        if _state["connected"]:
            return True, f"Already connected to '{_state['server']}'."
        return True, _connect(_player or ops.find_player(timeout=3.0, need_text=False))
    except Exception as e:
        return False, str(e)


# ── tool ─────────────────────────────────────────────────────────────────────

PLUGIN = {
    "name": "cloud_agent",
    "tier": "extra",
    "description": (
        "The link to the user's own always-on PAX server (their VPS). Use for: 'connect to my "
        "server', 'is my server online', 'disconnect the cloud', 'run this research on my "
        "server', 'what are my server jobs', 'sync my memory to the cloud'. delegate hands a "
        "long job to the server so it keeps running if the PC is switched off. NOT for ordinary "
        "web research the user wants now (use web_search)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "connect | disconnect | status | sync | delegate | jobs"},
            "task": {"type": "STRING", "description": "delegate: the job, in the user's own words"},
        },
        "required": ["action"],
    },
}

PLUGIN_SETTINGS = {
    "namespace": NS,
    "title": "CLOUD AGENT (optional 24/7 server)",
    "fields": [
        {"key": "pairing_code", "label": "Server pairing code (jarvis-cloud://…)", "type": "password"},
        {"key": "auto_connect", "label": "Connect when PAX starts", "type": "toggle", "default": True},
        {"key": "hold_telegram", "label": "Answer Telegram myself while the PC is on", "type": "toggle", "default": True},
        {"key": "accept_commands", "label": "Accept requests from the cloud", "type": "toggle", "default": False},
        {"key": "wake_mac", "label": "Wake-on-LAN: this PC's MAC address", "type": "text"},
        {"key": "wake_host", "label": "Wake-on-LAN: public address / DDNS name", "type": "text"},
        {"key": "wake_port", "label": "Wake-on-LAN: UDP port", "type": "text", "placeholder": "9"},
        {"key": "wake_url", "label": "…or a URL that wakes the PC", "type": "password"},
    ],
    "action": {"label": "TEST & SYNC", "run": test_connection},
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    global _player
    if player is not None:
        _player = player
    action = str(parameters.get("action") or "status").strip().lower()
    try:
        if action == "connect":
            return _connect(player)
        if action == "disconnect":
            return _disconnect()
        if action == "sync":
            if not _state["connected"]:
                return "Not connected to a server."
            _sync()
            return "Memory and settings synced to your server."
        if action == "status":
            if not _state["connected"]:
                return "Not connected." + (f" {_state['error']}." if _state["error"] else "")
            hello = _request("hello")
            return (f"Connected to '{hello.get('host')}'. Server Telegram "
                    f"{'running' if hello.get('telegram_running') else 'stopped'}, "
                    f"{hello.get('telegram_phones', 0)} phone(s) paired.")
        if action == "delegate":
            task = str(parameters.get("task") or "").strip()
            if not task:
                return "What should the server do?"
            if not _state["connected"]:
                return "Not connected to a server — say 'connect to my server' first."
            job = _request("delegate", {"task": task, "notify": "desk"}).get("job") or {}
            return f"Started on your server (job {job.get('id', '?')}). The result will come back here and to your phone."
        if action == "jobs":
            if not _state["connected"]:
                return "Not connected to a server."
            jobs = _request("jobs").get("jobs") or []
            return "\n".join(f"{j.get('id')} [{j.get('status')}] {str(j.get('task'))[:80]}"
                             for j in jobs[:8]) or "No jobs on the server yet."
        return f"Unknown action '{action}'."
    except Exception as e:
        return f"Cloud agent: {e}"


# ── start with the app, and hand Telegram back on the way out ────────────────

def _auto() -> None:
    try:
        cfg = _cfg()
        if not (cfg["auto_connect"] and cfg["pairing_code"]):
            return
        player = ops.find_player()
        if player is None:
            return
        try:
            print(f"[Cloud] auto-connect — {_connect(player)}")
        except Exception as e:
            _log(f"SYS: Cloud — could not connect ({e}). Starting Telegram on this PC instead.")
            tg = ops.telegram_module()
            if tg is not None and tg._cfg().get("start_on_launch") and not tg._state.get("running"):
                tg.run({"action": "start"}, player=player)
    except Exception as e:
        print(f"[Cloud] auto-connect failed ({type(e).__name__})")


def _at_exit() -> None:
    try:
        if _state["connected"]:
            _stop.set()
            _give_back_telegram()
            _request("bye", timeout=8)
    except Exception:
        pass


atexit.register(_at_exit)
try:
    threading.Thread(target=_auto, daemon=True, name="cloud-auto").start()
except Exception:
    pass
