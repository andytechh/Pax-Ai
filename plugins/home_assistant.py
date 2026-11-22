"""
plugins/home_assistant.py — smart lights and switches: "turn my desk light blue".

Two ways to connect, use either or both:
  • Tuya / Smart Life bulbs directly over your Wi-Fi (no cloud, no hub) —
    needs each bulb's device ID, local key and IP (see the tinytuya wizard).
  • A Home Assistant server — paste its address and a long-lived access token,
    and every light and switch it knows is available by name.

Settings → HOME / SMART LIGHTS.
  Tuya devices: one line, entries separated by ';', fields by ',':
      desk light,bf1234abcd,192.168.1.40,LOCALKEY,3.4 ; lamp,bf5678,192.168.1.41,KEY2,3.3
"""
from __future__ import annotations

import colorsys
import re

NS = "home_assistant"

PLUGIN = {
    "name": "home_assistant",
    "tier": "extra",
    "description": (
        "Control smart lights and switches by name: on, off, brightness, colour. Use for 'turn my "
        "desk light blue', 'lights off', 'dim the lamp to 30', 'turn on the fan'. device is the name "
        "the user gives; omit it for 'all lights'. Do NOT use for the computer's own screen "
        "brightness (computer_settings)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "on | off | toggle | brightness | color | list"},
            "device": {"type": "STRING", "description": "Name of the light or switch; empty = all lights"},
            "brightness": {"type": "INTEGER", "description": "0-100 (action brightness, or with on)"},
            "color": {"type": "STRING", "description": "A colour name (blue, warm white, orange…) or #RRGGBB"},
        },
        "required": ["action"],
    },
}

_COLORS = {
    "red": (255, 0, 0), "orange": (255, 120, 0), "yellow": (255, 220, 0), "green": (0, 220, 40),
    "cyan": (0, 220, 220), "blue": (0, 60, 255), "purple": (140, 0, 255), "violet": (140, 0, 255),
    "pink": (255, 60, 150), "magenta": (255, 0, 200), "white": (255, 255, 255),
    "lime": (140, 255, 0), "teal": (0, 160, 140), "gold": (255, 180, 0), "red-orange": (255, 70, 0),
}
_WARM = ("warm white", "warm", "soft white", "candle")
_COOL = ("cool white", "daylight", "cool")


def _cfg() -> dict:
    from memory.config_manager import get_plugin_config
    c = get_plugin_config(NS)
    tuya = []
    for entry in str(c.get("tuya_devices") or "").split(";"):
        parts = [p.strip() for p in entry.split(",")]
        if len(parts) >= 4 and all(parts[:4]):
            tuya.append({"name": parts[0], "id": parts[1], "ip": parts[2], "key": parts[3],
                         "version": float(parts[4]) if len(parts) > 4 and re.fullmatch(r"[\d.]+", parts[4]) else 3.3})
    url = str(c.get("ha_url") or "").strip().rstrip("/")
    if url and not re.match(r"^https?://", url):
        url = "http://" + url
    return {"tuya": tuya, "ha_url": url, "ha_token": str(c.get("ha_token") or "").strip()}


def _rgb(text: str):
    t = str(text or "").strip().lower()
    m = re.fullmatch(r"#?([0-9a-f]{6})", t)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return _COLORS.get(t)


def _match(name: str, candidates: list[str]) -> list[str]:
    n = str(name or "").strip().lower()
    if not n:
        return list(candidates)
    exact = [c for c in candidates if c.lower() == n]
    if exact:
        return exact
    words = [w for w in re.split(r"\W+", n) if w and w not in ("the", "my", "light", "lights", "lamp")]
    return [c for c in candidates if all(w in c.lower() for w in words)] if words else \
        [c for c in candidates if n in c.lower()]


# ── Tuya, local ──────────────────────────────────────────────────────────────

def _tuya(dev: dict):
    import tinytuya
    d = tinytuya.BulbDevice(dev["id"], dev["ip"], dev["key"])
    d.set_version(dev["version"])
    d.set_socketPersistent(False)
    d.set_socketTimeout(4)
    return d


def _tuya_do(dev: dict, a: dict) -> str:
    d = _tuya(dev)
    action, bri, color = a["action"], a.get("brightness"), str(a.get("color") or "").lower()
    if action == "off":
        r = d.turn_off()
    elif action == "toggle":
        r = d.turn_off() if (d.state().get("dps") or {}).get("20") else d.turn_on()
    elif action in ("on", "brightness", "color"):
        r = d.turn_on() if action == "on" else {}
        if color:
            if color in _WARM:
                r = d.set_white_percentage(brightness=int(bri if bri is not None else 80), colourtemp=0)
            elif color in _COOL:
                r = d.set_white_percentage(brightness=int(bri if bri is not None else 80), colourtemp=100)
            else:
                rgb = _rgb(color)
                if rgb is None:
                    return f"I don't know the colour '{color}'."
                r = d.set_colour(*rgb)
                if bri is not None:
                    h, s, _ = colorsys.rgb_to_hsv(*(x / 255 for x in rgb))
                    r = d.set_hsv(h, s, max(bri, 1) / 100)
        elif bri is not None:
            r = d.set_brightness_percentage(max(1, min(100, int(bri))))
    else:
        return f"{dev['name']}: can't {action}."
    if isinstance(r, dict) and r.get("Error"):
        return f"{dev['name']}: {r.get('Error')}"
    return f"{dev['name']} done."


# ── Home Assistant ───────────────────────────────────────────────────────────

def _ha(cfg: dict, method: str, path: str, **kw):
    import requests
    try:
        r = requests.request(method, cfg["ha_url"] + path, timeout=10,
                             headers={"Authorization": f"Bearer {cfg['ha_token']}"}, **kw)
    except requests.RequestException as e:
        raise RuntimeError(f"can't reach Home Assistant ({type(e).__name__})") from None
    if r.status_code == 401:
        raise RuntimeError("Home Assistant refused the access token")
    if r.status_code >= 400:
        raise RuntimeError(f"Home Assistant answered {r.status_code}")
    return r.json() if r.content else {}


def _ha_entities(cfg: dict) -> dict[str, str]:
    out = {}
    for s in _ha(cfg, "GET", "/api/states"):
        eid = s.get("entity_id", "")
        if eid.split(".")[0] in ("light", "switch", "fan") and s.get("state") != "unavailable":
            out[(s.get("attributes") or {}).get("friendly_name") or eid] = eid
    return out


def _ha_do(cfg: dict, eid: str, label: str, a: dict) -> str:
    domain = eid.split(".")[0]
    action, bri, color = a["action"], a.get("brightness"), str(a.get("color") or "").lower()
    if action == "off":
        _ha(cfg, "POST", f"/api/services/{domain}/turn_off", json={"entity_id": eid})
    elif action == "toggle":
        _ha(cfg, "POST", f"/api/services/{domain}/toggle", json={"entity_id": eid})
    else:
        data: dict = {"entity_id": eid}
        if domain == "light":
            if bri is not None:
                data["brightness_pct"] = max(1, min(100, int(bri)))
            if color in _WARM:
                data["color_temp_kelvin"] = 2700
            elif color in _COOL:
                data["color_temp_kelvin"] = 6500
            elif color:
                rgb = _rgb(color)
                if rgb is None:
                    return f"I don't know the colour '{color}'."
                data["rgb_color"] = list(rgb)
        _ha(cfg, "POST", f"/api/services/{domain}/turn_on", json=data)
    return f"{label} done."


def test_connection(values: dict):
    """The TEST button in plugin settings."""
    msgs, ok = [], True
    url, token = str(values.get("ha_url") or "").strip().rstrip("/"), str(values.get("ha_token") or "").strip()
    if url and token:
        try:
            if not re.match(r"^https?://", url):
                url = "http://" + url
            n = len(_ha_entities({"ha_url": url, "ha_token": token}))
            msgs.append(f"Home Assistant OK — {n} light/switch entities.")
        except Exception as e:
            ok = False
            msgs.append(str(e))
    dev = _cfg_from_values(values)
    if dev:
        try:
            _tuya(dev[0]).status()
            msgs.append(f"Tuya '{dev[0]['name']}' answered.")
        except Exception as e:
            ok = False
            msgs.append(f"Tuya '{dev[0]['name']}': {type(e).__name__}")
    return ok and bool(msgs), " ".join(msgs) or "Enter Tuya devices or a Home Assistant address and token."


def _cfg_from_values(values: dict) -> list[dict]:
    out = []
    for entry in str(values.get("tuya_devices") or "").split(";"):
        p = [x.strip() for x in entry.split(",")]
        if len(p) >= 4 and all(p[:4]):
            out.append({"name": p[0], "id": p[1], "ip": p[2], "key": p[3],
                        "version": float(p[4]) if len(p) > 4 and re.fullmatch(r"[\d.]+", p[4]) else 3.3})
    return out


PLUGIN_SETTINGS = {
    "namespace": NS,
    "title": "HOME / SMART LIGHTS",
    "fields": [
        {"key": "tuya_devices", "label": "Tuya bulbs: name,id,ip,localkey,version ; …", "type": "password"},
        {"key": "ha_url", "label": "Home Assistant address", "type": "text", "placeholder": "http://homeassistant.local:8123"},
        {"key": "ha_token", "label": "Home Assistant long-lived token", "type": "password"},
    ],
    "action": {"label": "TEST", "run": test_connection},
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    a = {k: parameters.get(k) for k in ("action", "device", "brightness", "color")}
    a["action"] = str(a["action"] or "list").strip().lower()
    cfg = _cfg()
    if not cfg["tuya"] and not (cfg["ha_url"] and cfg["ha_token"]):
        return ("No smart-home devices are set up. Open plugin settings → HOME / SMART LIGHTS and add "
                "your Tuya bulbs or your Home Assistant address and token.")
    try:
        ha = _ha_entities(cfg) if cfg["ha_url"] and cfg["ha_token"] else {}
        if a["action"] == "list":
            names = [d["name"] for d in cfg["tuya"]] + list(ha)
            return "I can control: " + ", ".join(names[:25]) + "." if names else "I found no devices."
        if a["action"] == "brightness" and a["brightness"] is None:
            return "What brightness, from 0 to 100?"
        if a["action"] == "color" and not a["color"]:
            return "Which colour?"
        if a["action"] not in ("on", "off", "toggle", "brightness", "color"):
            return f"Unknown action '{a['action']}'."
        wanted = str(a["device"] or "").strip()
        t_hits = [d for d in cfg["tuya"] if d["name"] in _match(wanted, [x["name"] for x in cfg["tuya"]])]
        h_hits = {n: ha[n] for n in _match(wanted, list(ha))}
        if not wanted:                      # "turn off the lights" = lights only, never switches/fans
            h_hits = {n: e for n, e in h_hits.items() if e.startswith("light.")}
        if not t_hits and not h_hits:
            have = ", ".join([d["name"] for d in cfg["tuya"]] + list(ha)[:10])
            return f"I don't know a device called '{wanted}'. I know: {have}."
        results = []
        for d in t_hits:
            try:
                results.append(_tuya_do(d, a))
            except Exception as e:
                results.append(f"{d['name']}: couldn't reach it ({type(e).__name__})")
        for n, eid in h_hits.items():
            try:
                results.append(_ha_do(cfg, eid, n, a))
            except Exception as e:
                results.append(f"{n}: {e}")
        return " ".join(results)
    except Exception as e:
        return f"Smart home: {e}"
