"""Always-on Windows wake listener for PAX.

Runs separately from the GUI. Detection is local; only after the exact Hey PAX
model fires does it start the PAX desktop executable. The custom ONNX model is
user-provisioned at %LOCALAPPDATA%\\PAX\\models\\hey_pax.onnx.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import threading
from pathlib import Path

import sounddevice as sd

from core.wake_word import WakeWordDetector, model_path, is_ready, SAMPLE_RATE


def app_executable() -> Path:
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().with_name("PAX.exe")
        if candidate.exists():
            return candidate
    root = Path(__file__).resolve().parents[1]
    packaged = root / "dist" / "PAX" / "PAX.exe"
    return packaged if packaged.exists() else root / "pax_entry.py"


def install_startup() -> None:
    """Register this listener at user logon using the Windows Task Scheduler."""
    import subprocess
    exe = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        command = f'"{exe}" --pax-wake-listener'
    else:
        entry = Path(__file__).resolve().parents[1] / "pax_entry.py"
        command = f'"{exe}" "{entry}" --pax-wake-listener'
    task = "PAX Hey PAX Wake Listener"
    subprocess.run([
        "schtasks", "/Create", "/TN", task, "/SC", "ONLOGON",
        "/TR", command, "/F"
    ], check=True)


def remove_startup() -> None:
    subprocess.run(["schtasks", "/Delete", "/TN", "PAX Hey PAX Wake Listener", "/F"],
                   check=False, capture_output=True)


def run_listener() -> int:
    if os.name != "nt":
        print("The always-on listener is currently supported on Windows only.")
        return 2
    if not is_ready():
        print(f"Hey PAX model missing or invalid: {model_path()}")
        print("Install a trained hey_pax.onnx model before enabling startup listening.")
        return 3

    target = app_executable()
    if not target.exists():
        print(f"PAX executable not found: {target}")
        return 4

    launched = threading.Event()
    child = {"process": None}

    def detected() -> None:
        if launched.is_set():
            return
        try:
            if getattr(sys, "frozen", False):
                proc = subprocess.Popen([str(target)], cwd=str(target.parent))
            elif target.suffix.lower() == ".py":
                proc = subprocess.Popen([sys.executable, str(target)], cwd=str(target.parent))
            else:
                proc = subprocess.Popen([str(target)], cwd=str(target.parent))
            child["process"] = proc
            launched.set()
        except Exception as exc:
            print(f"Could not launch PAX: {exc}")

    detector = WakeWordDetector(detected, logger=print)
    if not detector.start():
        return 5
    print("PAX wake listener active. Listening locally for Hey PAX.")
    try:
        def audio_callback(indata, frames, time_info, status):
            if status:
                print(f"Microphone: {status}")
            detector.feed(indata)
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                            blocksize=1280, callback=audio_callback):
            while detector.ready and not launched.is_set():
                time.sleep(0.25)
    except KeyboardInterrupt:
        detector.stop()
        return 0
    except Exception as exc:
        print(f"Wake listener stopped: {exc}")
        return 6
    finally:
        detector.stop()

    # Release the microphone before PAX starts using it. When the GUI closes,
    # restart this listener so the wake phrase can launch PAX again.
    proc = child.get("process")
    if proc is not None:
        try:
            proc.wait()
        except Exception:
            pass
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable, "--pax-wake-listener"],
                             cwd=str(Path(sys.executable).resolve().parent))
        else:
            entry = Path(__file__).resolve().parent / "pax_entry.py"
            subprocess.Popen([sys.executable, str(entry), "--pax-wake-listener"],
                             cwd=str(entry.parent))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-startup", action="store_true")
    parser.add_argument("--remove-startup", action="store_true")
    args = parser.parse_args()
    if args.install_startup:
        install_startup()
        print("PAX wake listener registered at Windows logon.")
        return 0
    if args.remove_startup:
        remove_startup()
        print("PAX wake listener startup task removed.")
        return 0
    return run_listener()


if __name__ == "__main__":
    raise SystemExit(main())
