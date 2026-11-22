"""
plugins/printer_control.py — your 3D printer, by voice.

Works with Klipper printers (Moonraker) and OctoPrint — the two ways nearly every
hobby printer is networked. Status, temperatures, progress, files, start, pause,
resume, cancel, preheat, cool down and home.

Starting and cancelling a print are the two things you cannot take back (hours of
plastic), so both wait for YOU to press CONFIRM on the HUD. The model cannot
confirm for you.

Set it up: PAX → plugin settings → 3D PRINTER → printer type, address
(e.g. http://192.168.1.50:7125 for Moonraker, http://octopi.local for OctoPrint)
and, for OctoPrint, its API key.
"""
from __future__ import annotations

import re

NS = "printer_control"

PLUGIN = {
    "name": "printer_control",
    "tier": "extra",
    "description": (
        "Control the user's networked 3D printer (Klipper/Moonraker or OctoPrint). Actions: status "
        "(temperatures + print progress), files, start (needs a file name), pause, resume, cancel, "
        "preheat (hotend/bed °C), cooldown, home. Use for 'how's my print going', 'is the printer "
        "done', 'preheat for PETG', 'pause the print'. start and cancel always ask the user to "
        "confirm on screen first."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "status | files | start | pause | resume | cancel | preheat | cooldown | home"},
            "file": {"type": "STRING", "description": "start: the G-code file name (partial is fine)"},
            "hotend": {"type": "INTEGER", "description": "preheat: hotend °C"},
            "bed": {"type": "INTEGER", "description": "preheat: bed °C"},
            "material": {"type": "STRING", "description": "preheat: PLA, PETG, ABS, ASA or TPU instead of numbers"},
        },
        "required": ["action"],
    },
}

_PRESETS = {"pla": (200, 60), "petg": (240, 80), "abs": (250, 100), "asa": (255, 100), "tpu": (225, 50)}


def _cfg() -> dict:
    from memory.config_manager import get_plugin_config
    c = get_plugin_config(NS)
    kind = "octoprint" if "octo" in str(c.get("printer_type") or "").lower() else "moonraker"
    addr = str(c.get("address") or "").strip().rstrip("/")
    if addr and not re.match(r"^https?://", addr):
        addr = "http://" + addr
    return {"kind": kind, "address": addr, "api_key": str(c.get("api_key") or "").strip()}


def _http(cfg: dict, method: str, path: str, **kw):
    import requests
    headers = {"X-Api-Key": cfg["api_key"]} if cfg["api_key"] else {}
    try:
        r = requests.request(method, cfg["address"] + path, headers=headers, timeout=10, **kw)
    except requests.RequestException as e:
        raise RuntimeError(f"can't reach the printer at {cfg['address']} ({type(e).__name__})") from None
    if r.status_code in (401, 403):
        raise RuntimeError("the printer refused the API key")
    if r.status_code >= 400:
        raise RuntimeError(f"the printer answered {r.status_code}")
    try:
        return r.json()
    except ValueError:
        return {}


def _eta(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return ""
    return f"{s // 3600}h {s % 3600 // 60}m" if s >= 3600 else f"{s // 60}m"


def _status(cfg: dict) -> str:
    if cfg["kind"] == "moonraker":
        q = _http(cfg, "GET", "/printer/objects/query?extruder&heater_bed&print_stats&virtual_sdcard")
        st = q["result"]["status"]
        ex, bed, ps, sd = st["extruder"], st["heater_bed"], st["print_stats"], st["virtual_sdcard"]
        state = ps.get("state", "unknown")
        s = (f"The printer is {state}. Hotend {ex['temperature']:.0f}°C (target {ex['target']:.0f}), "
             f"bed {bed['temperature']:.0f}°C (target {bed['target']:.0f}).")
        if state in ("printing", "paused"):
            pct = sd.get("progress", 0) * 100
            dur, name = ps.get("print_duration", 0), ps.get("filename", "")
            left = (dur / sd["progress"] - dur) if sd.get("progress", 0) > 0.01 else None
            s += f" {name} is {pct:.0f}% done" + (f", about {_eta(left)} left." if left else ".")
        return s
    printer, job = None, _http(cfg, "GET", "/api/job")
    try:
        printer = _http(cfg, "GET", "/api/printer")
    except RuntimeError:
        pass                                   # 409 while disconnected from the printer
    state = job.get("state", "unknown")
    s = f"The printer is {state.lower()}."
    if printer:
        t0 = (printer.get("temperature") or {}).get("tool0", {})
        bd = (printer.get("temperature") or {}).get("bed", {})
        s += (f" Hotend {t0.get('actual', 0):.0f}°C (target {t0.get('target') or 0:.0f}), "
              f"bed {bd.get('actual', 0):.0f}°C (target {bd.get('target') or 0:.0f}).")
    prog = job.get("progress") or {}
    if prog.get("completion") is not None and state.lower() in ("printing", "paused", "pausing"):
        name = ((job.get("job") or {}).get("file") or {}).get("name", "the job")
        s += f" {name} is {prog['completion']:.0f}% done"
        s += f", about {_eta(prog.get('printTimeLeft'))} left." if prog.get("printTimeLeft") else "."
    return s


def _files(cfg: dict) -> list[str]:
    if cfg["kind"] == "moonraker":
        items = _http(cfg, "GET", "/server/files/list").get("result", [])
        items.sort(key=lambda f: f.get("modified", 0), reverse=True)
        return [f["path"] for f in items]
    items = _http(cfg, "GET", "/api/files?recursive=true").get("files", [])
    items = [f for f in items if f.get("type") == "machinecode"]
    items.sort(key=lambda f: f.get("date", 0), reverse=True)
    return [f.get("path") or f.get("name") for f in items]


def _gcode(cfg: dict, line: str) -> None:
    if cfg["kind"] == "moonraker":
        _http(cfg, "POST", "/printer/gcode/script", params={"script": line})
    else:
        _http(cfg, "POST", "/api/printer/command", json={"commands": [line]})


def _do(cfg: dict, action: str, **kw) -> str:
    if action == "start":
        if cfg["kind"] == "moonraker":
            _http(cfg, "POST", "/printer/print/start", params={"filename": kw["file"]})
        else:
            _http(cfg, "POST", f"/api/files/local/{kw['file']}", json={"command": "select", "print": True})
        return f"Started printing {kw['file']}."
    if action == "pause":
        if cfg["kind"] == "moonraker":
            _http(cfg, "POST", "/printer/print/pause")
        else:
            _http(cfg, "POST", "/api/job", json={"command": "pause", "action": "pause"})
        return "Print paused."
    if action == "resume":
        if cfg["kind"] == "moonraker":
            _http(cfg, "POST", "/printer/print/resume")
        else:
            _http(cfg, "POST", "/api/job", json={"command": "pause", "action": "resume"})
        return "Print resumed."
    if action == "cancel":
        if cfg["kind"] == "moonraker":
            _http(cfg, "POST", "/printer/print/cancel")
        else:
            _http(cfg, "POST", "/api/job", json={"command": "cancel"})
        return "Print cancelled."
    raise RuntimeError(f"no such action {action}")


def test_connection(values: dict):
    """The TEST button in plugin settings."""
    kind = "octoprint" if "octo" in str(values.get("printer_type") or "").lower() else "moonraker"
    addr = str(values.get("address") or "").strip().rstrip("/")
    if not addr:
        return False, "Enter the printer's address first."
    if not re.match(r"^https?://", addr):
        addr = "http://" + addr
    cfg = {"kind": kind, "address": addr, "api_key": str(values.get("api_key") or "").strip()}
    try:
        return True, _status(cfg)
    except Exception as e:
        return False, str(e)


PLUGIN_SETTINGS = {
    "namespace": NS,
    "title": "3D PRINTER",
    "fields": [
        {"key": "printer_type", "label": "Printer software", "type": "choice",
         "options": ["Moonraker (Klipper)", "OctoPrint"], "default": "Moonraker (Klipper)"},
        {"key": "address", "label": "Address", "type": "text",
         "placeholder": "http://192.168.1.50:7125  or  http://octopi.local"},
        {"key": "api_key", "label": "API key (OctoPrint requires one)", "type": "password"},
    ],
    "action": {"label": "TEST PRINTER", "run": test_connection},
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    cfg = _cfg()
    if not cfg["address"]:
        return "No printer is set up yet. Open plugin settings → 3D PRINTER and enter its address."
    action = str(parameters.get("action") or "status").strip().lower()
    try:
        if action == "status":
            return _status(cfg)
        if action == "files":
            names = _files(cfg)
            return ("Newest files: " + ", ".join(names[:8]) + ".") if names else "The printer has no G-code files."
        if action == "start":
            want = str(parameters.get("file") or "").strip().lower()
            if not want:
                return "Which file should I print?"
            names = _files(cfg)
            hits = [n for n in names if want in n.lower()]
            if not hits:
                return f"No file matches '{want}'. Newest are: {', '.join(names[:5])}."
            if len(hits) > 1 and want not in [h.lower() for h in hits]:
                return f"More than one matches: {', '.join(hits[:5])}. Which one?"
            target = next((h for h in hits if h.lower() == want), hits[0])
            from core import confirm
            return confirm.request(key="printer_start", title=f"START PRINT: {target}",
                                   detail="The printer will begin this job now.",
                                   run=lambda c=cfg, t=target: _do(c, "start", file=t))
        if action in ("pause", "resume"):
            return _do(cfg, action)
        if action == "cancel":
            from core import confirm
            return confirm.request(key="printer_cancel", title="CANCEL THE PRINT",
                                   detail="The current job is stopped and cannot be resumed.",
                                   run=lambda c=cfg: _do(c, "cancel"))
        if action == "preheat":
            hotend, bed = parameters.get("hotend"), parameters.get("bed")
            mat = str(parameters.get("material") or "").strip().lower()
            if (hotend is None or bed is None) and mat in _PRESETS:
                hotend, bed = _PRESETS[mat]
            if hotend is None and bed is None:
                return "What temperatures? For example hotend 210 and bed 60, or a material like PETG."
            hotend, bed = int(hotend or 0), int(bed or 0)
            if hotend > 300 or bed > 130:
                return "That's above what I'm willing to set (300°C hotend, 130°C bed)."
            if hotend:
                _gcode(cfg, f"M104 S{hotend}")
            if bed:
                _gcode(cfg, f"M140 S{bed}")
            return f"Heating: hotend {hotend}°C, bed {bed}°C."
        if action == "cooldown":
            _gcode(cfg, "M104 S0")
            _gcode(cfg, "M140 S0")
            return "Heaters off."
        if action == "home":
            _gcode(cfg, "G28")
            return "Homing all axes."
        return f"Unknown action '{action}'."
    except Exception as e:
        return f"Printer: {e}"
