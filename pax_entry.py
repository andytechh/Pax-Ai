"""PAX Windows GUI bootstrap: establish writable data/log paths before startup."""
from __future__ import annotations
import os
import runpy
import sys
import traceback
from pathlib import Path

def main() -> None:
    if "--pax-wake-listener" in sys.argv:
        from pax_wake_listener import run_listener
        raise SystemExit(run_listener())
    if "--pax-install-wake-startup" in sys.argv:
        from pax_wake_listener import install_startup
        install_startup()
        print("PAX wake listener registered at Windows logon.")
        return
    if "--pax-remove-wake-startup" in sys.argv:
        from pax_wake_listener import remove_startup
        remove_startup()
        print("PAX wake listener startup task removed.")
        return
    if getattr(sys, "frozen", False):
        root = Path(sys._MEIPASS)
        if "--pax-smoke-test" in sys.argv:
            required = [root / "main.py", root / "core" / "prompt.txt", root / "actions", root / "plugins", root / "dashboard" / "static"]
            missing = [str(p) for p in required if not p.exists()]
            if missing:
                print("PAX package smoke test failed; missing: " + ", ".join(missing))
                raise SystemExit(2)
            import importlib.util
            for module in ("PyQt6", "google.genai", "sounddevice", "numpy", "win32crypt", "pywinauto", "openwakeword", "pax_wake_listener"):
                if importlib.util.find_spec(module) is None:
                    print("PAX package smoke test failed; missing module: " + module)
                    raise SystemExit(3)
            print("PAX packaged resource and dependency smoke test passed.")
            raise SystemExit(0)
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
