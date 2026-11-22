"""
plugins/disk_map.py — "what is eating my disk?"

Walks a folder (or a whole drive) within a time budget and ranks the biggest
folders and files, drawn as bars on the HUD panel. Read-only: it never deletes
anything — ask file_controller for that, which is undoable.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

PLUGIN = {
    "name": "disk_map",
    "tier": "extra",
    "description": (
        "Show where disk space is going: the biggest folders and files under a path (default: the "
        "user's home folder; a drive letter like C:\\ also works). Use for 'what's using my disk', "
        "'why is my C drive full', 'find the biggest files'. Read-only. To free space the user "
        "must then ask for specific files to be deleted (file_controller)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "path": {"type": "STRING", "description": "Folder or drive to map; default the home folder"},
            "mode": {"type": "STRING", "description": "folders (default) | files"},
            "top": {"type": "INTEGER", "description": "How many entries to list (default 12)"},
            "seconds": {"type": "INTEGER", "description": "Time budget in seconds (default 30, max 120)"},
        },
        "required": [],
    },
}


def _fmt(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def _scan(root: Path, deadline: float):
    """(per-child-folder totals, biggest files, total bytes, finished?)"""
    totals: dict[str, int] = {}
    big: list[tuple[int, str]] = []
    grand, finished = 0, True
    try:
        children = list(os.scandir(root))
    except OSError as e:
        raise RuntimeError(f"can't read {root}: {e}") from None
    stack = []
    for c in children:
        try:
            if c.is_symlink():
                continue
            if c.is_dir(follow_symlinks=False):
                stack.append((c.path, c.path))
            elif c.is_file(follow_symlinks=False):
                s = c.stat().st_size
                totals["(files directly here)"] = totals.get("(files directly here)", 0) + s
                big.append((s, c.path))
                grand += s
        except OSError:
            continue
    while stack:
        if time.time() > deadline:
            finished = False
            break
        top, path = stack.pop()
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append((top, e.path))
                        elif e.is_file(follow_symlinks=False):
                            s = e.stat().st_size
                            totals[top] = totals.get(top, 0) + s
                            grand += s
                            if len(big) < 4000 or s > big[-1][0]:
                                big.append((s, e.path))
                    except OSError:
                        continue
        except OSError:
            continue
        if len(big) > 8000:
            big.sort(reverse=True)
            del big[2000:]
    big.sort(reverse=True)
    return totals, big[:200], grand, finished


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        root = Path(str(parameters.get("path") or "").strip() or Path.home()).expanduser()
        if not root.exists():
            return f"I can't find {root}."
        top = max(3, min(int(parameters.get("top") or 12), 30))
        budget = max(5, min(int(parameters.get("seconds") or 30), 120))
        mode = str(parameters.get("mode") or "folders").lower()
        totals, big, grand, finished = _scan(root, time.time() + budget)
        if mode == "files":
            rows = [(s, p) for s, p in big[:top]]
        else:
            rows = sorted(((s, p) for p, s in totals.items()), reverse=True)[:top]
        if not rows:
            return f"{root} looks empty."
        peak = rows[0][0] or 1
        lines = [f"{root}  —  {_fmt(grand)} scanned{'' if finished else ' (time ran out — partial)'}", ""]
        for s, p in rows:
            label = p if p.startswith("(") else (os.path.relpath(p, root) if mode == "files" else Path(p).name)
            bar = "█" * max(1, int(18 * s / peak))
            lines.append(f"{bar:<18} {_fmt(s):>9}  {label}")
        if player:
            try:
                player.show_content("DISK MAP", "\n".join(lines))
            except Exception:
                pass
        lead = rows[0]
        name = lead[1] if lead[1].startswith("(") else Path(lead[1]).name
        tail = "" if finished else " The scan ran out of time, so these are the biggest found so far."
        return (f"Scanned {_fmt(grand)} in {root}. The biggest {'file' if mode == 'files' else 'folder'} "
                f"is {name} at {_fmt(lead[0])}.{tail} The full ranking is on screen.")
    except Exception as e:
        return f"Disk map failed: {e}"
