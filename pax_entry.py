"""PAX Windows GUI bootstrap: establish writable data/log paths before startup."""
from __future__ import annotations
import os
import runpy
import sys
import traceback
from pathlib import Path

def main() -> None:
    if getattr(sys, "frozen", False):
        root = Path(sys._MEIPASS)
        local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        data = Path(os.environ.get("PAX_DATA_DIR") or (local / "PAX"))
        os.environ.setdefault("PAX_DATA_DIR", str(data))
        (data / "logs").mkdir(parents=True, exist_ok=True)
        log = (data / "logs" / "pax.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = log
        sys.stderr = log
        sys.path.insert(0, str(root))
        try:
            runpy.run_path(str(root / "main.py"), run_name="__main__")
        except BaseException:
            traceback.print_exc()
            raise
    else:
        runpy.run_path(str(Path(__file__).resolve().with_name("main.py")), run_name="__main__")

if __name__ == "__main__":
    main()
