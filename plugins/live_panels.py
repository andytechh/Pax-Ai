"""
plugins/live_panels.py — live panels on the HUD.

"Show my PC's load on a panel", "show the top processes", "network panel for two
minutes". The panel under the avatar refreshes every couple of seconds until the
time is up or you say "close the panel".
"""
from __future__ import annotations

import threading
import time

PLUGIN = {
    "name": "live_panels",
    "tier": "extra",
    "description": (
        "Put a LIVE, self-refreshing panel on the HUD: system (CPU/RAM/disk/GPU/temperature), "
        "processes (top apps by CPU and memory), network (up/down speed), battery. Use for 'show my "
        "PC's load on a panel', 'live CPU graph', 'what's using my CPU' (processes), 'close the "
        "panel'. For a one-off spoken number ('how hot is my CPU') use system_monitor instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "show (default) | stop"},
            "panel": {"type": "STRING", "description": "system | processes | network | battery"},
            "seconds": {"type": "INTEGER", "description": "How long to keep it live (default 120, max 900)"},
        },
        "required": [],
    },
}

_live = {"thread": None, "stop": threading.Event()}
_hist: dict[str, list] = {"cpu": [], "ram": [], "down": [], "up": []}
_SPARK = "▁▂▃▄▅▆▇█"


def _bar(pct: float, width: int = 20) -> str:
    pct = max(0.0, min(100.0, pct))
    n = int(round(width * pct / 100))
    return "█" * n + "░" * (width - n)


def _spark(values: list, hi: float | None = None) -> str:
    if not values:
        return ""
    hi = hi or max(values) or 1
    return "".join(_SPARK[min(7, int(7 * v / hi))] for v in values[-30:])


def _push(key: str, v: float) -> None:
    _hist[key].append(v)
    del _hist[key][:-60]


def _fmt_rate(b: float) -> str:
    for u in ("B/s", "KB/s", "MB/s", "GB/s"):
        if b < 1024 or u == "GB/s":
            return f"{b:.0f} {u}" if u == "B/s" else f"{b:.1f} {u}"
        b /= 1024


def _system(ps) -> str:
    cpu = ps.cpu_percent(interval=None)
    ram = ps.virtual_memory()
    _push("cpu", cpu)
    _push("ram", ram.percent)
    lines = [f"CPU  {_bar(cpu)} {cpu:5.1f}%", f"     {_spark(_hist['cpu'], 100)}",
             f"RAM  {_bar(ram.percent)} {ram.percent:5.1f}%  ({ram.used / 2**30:.1f} / {ram.total / 2**30:.1f} GB)",
             f"     {_spark(_hist['ram'], 100)}"]
    try:
        from actions.system_monitor import _get_gpu_usage
        gpu = float(_get_gpu_usage())
        if gpu >= 0:
            lines.append(f"GPU  {_bar(gpu)} {gpu:5.1f}%")
    except Exception:
        pass
    try:
        import pathlib
        d = ps.disk_usage(str(pathlib.Path.home().anchor or "/"))
        lines.append(f"DISK {_bar(d.percent)} {d.percent:5.1f}%  ({d.free / 2**30:.0f} GB free)")
    except Exception:
        pass
    try:
        temps = ps.sensors_temperatures() if hasattr(ps, "sensors_temperatures") else {}
        hot = max((t.current for ts in temps.values() for t in ts), default=None)
        if hot:
            lines.append(f"TEMP {hot:.0f} °C")
    except Exception:
        pass
    return "\n".join(lines)


def _processes(ps) -> str:
    rows = []
    for p in ps.process_iter(["name", "cpu_percent", "memory_percent"]):
        try:
            rows.append((p.info["cpu_percent"] or 0.0, p.info["memory_percent"] or 0.0, p.info["name"] or "?"))
        except Exception:
            continue
    merged: dict[str, list] = {}
    for c, m, n in rows:
        a = merged.setdefault(n, [0.0, 0.0])
        a[0] += c
        a[1] += m
    top = sorted(merged.items(), key=lambda kv: (kv[1][0], kv[1][1]), reverse=True)[:10]
    out = [f"{'APP':<26}{'CPU':>7}{'RAM':>8}"]
    out += [f"{n[:25]:<26}{c / (ps.cpu_count() or 1):>6.1f}%{m:>7.1f}%" for n, (c, m) in top]
    return "\n".join(out)


_last_net = {"t": 0.0, "rx": 0, "tx": 0}


def _network(ps) -> str:
    io = ps.net_io_counters()
    now = time.time()
    if _last_net["t"]:
        dt = max(now - _last_net["t"], 0.001)
        down, up = (io.bytes_recv - _last_net["rx"]) / dt, (io.bytes_sent - _last_net["tx"]) / dt
    else:
        down = up = 0.0
    _last_net.update(t=now, rx=io.bytes_recv, tx=io.bytes_sent)
    _push("down", down)
    _push("up", up)
    return (f"DOWN {_fmt_rate(down):>10}  {_spark(_hist['down'])}\n"
            f"UP   {_fmt_rate(up):>10}  {_spark(_hist['up'])}\n"
            f"Total received {io.bytes_recv / 2**30:.2f} GB · sent {io.bytes_sent / 2**30:.2f} GB")


def _battery(ps) -> str:
    b = ps.sensors_battery()
    if not b:
        return "No battery — this looks like a desktop."
    left = f"{int(b.secsleft // 3600)}h {int(b.secsleft % 3600 // 60)}m left" if b.secsleft and b.secsleft > 0 else ""
    return f"BAT  {_bar(b.percent)} {b.percent:.0f}%  {'charging' if b.power_plugged else 'on battery'}  {left}"


_PANELS = {"system": ("SYSTEM LOAD", _system), "processes": ("TOP PROCESSES", _processes),
           "network": ("NETWORK", _network), "battery": ("BATTERY", _battery)}


def _loop(panel: str, seconds: int, player) -> None:
    import psutil
    title, fn = _PANELS[panel]
    psutil.cpu_percent(interval=None)
    end = time.time() + seconds
    while time.time() < end and not _live["stop"].is_set():
        try:
            player.show_content(title + " · LIVE", fn(psutil))
        except Exception:
            return
        _live["stop"].wait(2.0)
    if not _live["stop"].is_set():
        try:
            player.show_content(title, "Panel closed (time's up). Ask again to reopen it.")
        except Exception:
            pass


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "show").strip().lower()
    if action in ("stop", "close", "hide"):
        _live["stop"].set()
        if player:
            try:
                player.show_content("PANEL", "Closed.")
            except Exception:
                pass
        return "Panel closed."
    if player is None:
        return "There's no screen to put a panel on."
    try:
        import psutil  # noqa: F401
    except ImportError:
        return "Live panels need psutil — run: pip install psutil"
    panel = str(parameters.get("panel") or "system").strip().lower()
    panel = {"cpu": "system", "load": "system", "pc": "system", "apps": "processes", "top": "processes",
             "internet": "network", "wifi": "network"}.get(panel, panel)
    if panel not in _PANELS:
        return f"I have these panels: {', '.join(_PANELS)}."
    seconds = max(10, min(int(parameters.get("seconds") or 120), 900))
    _live["stop"].set()
    old = _live["thread"]
    if old is not None and old.is_alive():
        old.join(timeout=3)
    _live["stop"] = threading.Event()
    t = threading.Thread(target=_loop, args=(panel, seconds, player), daemon=True, name="live-panel")
    _live["thread"] = t
    t.start()
    span = f"{seconds // 60} minute(s)" if seconds >= 60 else f"{seconds} seconds"
    return f"The {_PANELS[panel][0].lower()} panel is live for {span}. Say 'close the panel' to stop it."
