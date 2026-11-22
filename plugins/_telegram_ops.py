"""
plugins/_telegram_ops.py — small helpers shared by telegram_remote and cloud_agent.

Leading underscore: the plugin loader skips this file, so it is never offered to
the model as a tool. It is also in core/cloud.py's MANIFEST, so it is copied to
the server along with telegram_remote.py.

Standard library only at the top level — it must import on a bare server.
"""
from __future__ import annotations

import os
import re
import secrets
import sys
import time
from pathlib import Path

# No 0/O/1/I/L: a code read off a screen and typed on a phone must survive.
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def new_pairing_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalise_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())


def parse_ids(raw) -> list[int]:
    out: list[int] = []
    for part in str(raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit() and int(part) not in out:
            out.append(int(part))
    return out


def split_text(text: str, limit: int = 3800) -> list[str]:
    """Telegram refuses messages over 4096 characters. Break on paragraph, then
    line, then word, so nothing is cut in the middle of a sentence."""
    text = str(text or "").strip()
    chunks: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind("\n\n", 0, limit), text.rfind("\n", 0, limit), text.rfind(" ", 0, limit))
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks


def safe_filename(name: str, fallback: str = "file") -> str:
    name = os.path.basename(str(name or "")).strip().replace("\x00", "")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return (name or fallback)[:120]


def unique_path(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / safe_filename(name)
    stem, suffix, n = target.stem, target.suffix, 1
    while target.exists():
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    return target


def confine(base: Path, candidate) -> Path | None:
    """`candidate` resolved, only if it is inside `base` — symlinks and `..`
    included. This is the wall between 'send me my report' and 'send me every
    file the account can read'."""
    try:
        base_r = Path(base).expanduser().resolve()
        cand = Path(candidate).expanduser()
        if not cand.is_absolute():
            cand = base_r / cand
        cand_r = cand.resolve()
        if os.path.commonpath([str(base_r), str(cand_r)]) != str(base_r):
            return None
        return cand_r
    except (OSError, ValueError):
        return None


def find_file(base: Path, query: str, max_files: int = 6000, max_depth: int = 4) -> Path | None:
    """Best filename match for `query` under `base`. Exact name, then startswith,
    then every word contained; newest wins a tie."""
    base = Path(base).expanduser()
    q = str(query or "").strip().lower()
    if not q or not base.is_dir():
        return None
    words = [w for w in re.split(r"\W+", q) if w]
    best: tuple[int, float, Path] | None = None
    seen = 0
    base_depth = len(base.parts)
    for root, dirs, files in os.walk(base):
        if len(Path(root).parts) - base_depth >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in ("node_modules", "__pycache__")]
        for f in files:
            seen += 1
            if seen > max_files:
                return best[2] if best else None
            low = f.lower()
            if low == q:
                score = 3
            elif low.startswith(q):
                score = 2
            elif words and all(w in low for w in words):
                score = 1
            else:
                continue
            p = Path(root) / f
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if best is None or (score, mtime) > (best[0], best[1]):
                best = (score, mtime, p)
    return best[2] if best else None


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ── finding the running app from a plugin ────────────────────────────────────
# A plugin is only handed `player` when the model calls it. Things that start on
# their own (the bot after launch, the cloud link) have no call to wait for, so
# they find the window the way the app itself built it: one JarvisUI instance
# created in main(), alive for the whole process.

def find_player(timeout: float = 180.0, need_text: bool = True):
    import gc

    deadline = time.time() + timeout
    player = None
    while time.time() < deadline and player is None:
        cls = getattr(sys.modules.get("ui"), "JarvisUI", None)
        if cls is not None:
            try:
                for obj in gc.get_objects():
                    if type(obj) is cls:
                        player = obj
                        break
            except Exception:
                player = None
        if player is None:
            time.sleep(2.0)
    while player is not None and need_text and time.time() < deadline:
        if getattr(player, "on_text_command", None):
            return player
        time.sleep(1.0)
    return player


def telegram_module():
    """The telegram_remote module the app already loaded — whatever name the
    loader gave it — or None. Never imports a second copy: two copies would be
    two bots polling one token."""
    for name, mod in list(sys.modules.items()):
        f = str(getattr(mod, "__file__", "") or "")
        if f.endswith("telegram_remote.py") and hasattr(mod, "_state"):
            return mod
    # Not loaded yet: import it under the name the plugin loader uses, so the
    # loader and this caller end up sharing one copy.
    try:
        import importlib
        mod = importlib.import_module("plugins.telegram_remote")
        return mod if hasattr(mod, "_state") else None
    except Exception:
        return None
