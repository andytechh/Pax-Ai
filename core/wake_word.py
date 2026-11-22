"""
Local wake-word detection for PAX ("Hey PAX").

Design goals:
  • ZERO cost when the feature is off — openwakeword is imported ONLY inside
    start()/install helpers, never at module load. If the user never enables
    wake word, none of this touches the app.
  • ZERO latency on the audio path — the microphone callback only ever does a
    cheap, non-blocking queue push (feed()); the actual model inference runs in
    this module's own background thread, so the real-time audio thread and the
    Gemini stream are never slowed.
  • Fully local & offline — audio fed here never leaves the machine; there is no
    network call except the one-time model download the user triggers from the UI.

openwakeword ships small ONNX models (a few MB each) and runs comfortably on a
CPU. The custom wake phrase is "Hey PAX"; its ONNX model is provisioned separately.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

# Custom openWakeWord model trained for the exact phrase "Hey PAX".
WAKE_MODEL = "hey_pax"

def model_path() -> Path:
    """Per-user custom model location; never silently substitute another phrase."""
    import os
    root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return root / "PAX" / "models" / f"{WAKE_MODEL}.onnx"
# Score in [0,1]; above this counts as a detection. Tunable per environment.
DEFAULT_THRESHOLD = 0.5
# Mic frames arrive at 16 kHz int16; this is just the detector's input rate.
SAMPLE_RATE = 16000


def is_installed() -> bool:
    """True if the openwakeword package is importable (no model check)."""
    try:
        import importlib.util
        return importlib.util.find_spec("openwakeword") is not None
    except Exception:
        return False


def is_ready() -> bool:
    """True if openwakeword is installed AND its model files are present on disk.

    This is a cheap, DETERMINISTIC file-existence check. It deliberately does NOT
    construct a Model to probe readiness — doing that is slow and, worse, can clash
    with the detector's own Model when it's already running, which intermittently
    returned False and made the UI flicker to 'not downloaded'. Never raises.
    """
    if not is_installed() or not model_path().is_file():
        return False
    try:
        import openwakeword
        models_dir = Path(openwakeword.__file__).resolve().parent / "resources" / "models"
        has_mel = any(models_dir.glob("melspectrogram*.onnx"))
        has_emb = any(models_dir.glob("embedding_model*.onnx"))
        return bool(has_mel and has_emb)
    except Exception:
        return False


def install_and_download(logger: Callable[[str], None] = print,
                         notify: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """Check runtime readiness; never download a different wake phrase as fallback."""
    _tell = notify or (lambda _msg: None)
    try:
        if not model_path().is_file():
            return False, ("The Hey PAX model has not been trained and installed yet. "
                           "Place the evaluated hey_pax.onnx file in %LOCALAPPDATA%\\PAX\\models.")
        if is_ready():
            logger("Wake word: Hey PAX model is ready.")
            return True, "Hey PAX wake-word model is ready."
        if not is_installed():
            if getattr(sys, "frozen", False):
                return False, ("Wake-word runtime is missing from this PAX build. "
                               "Install the latest PAX build.")
            _tell("Wake word: installing local detector runtime…")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "openwakeword"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if result.returncode != 0:
                tail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or [""]
                return False, f"openWakeWord install failed: {tail[0][:160]}"
        if not is_ready():
            return False, "The Hey PAX model or its local feature models are missing."
        logger("Wake word: Hey PAX ready.")
        return True, "Hey PAX wake-word model is ready."
    except Exception as exc:
        return False, f"wake-word setup error: {exc}"


class WakeWordDetector:
    """
    Runs the wake model in a dedicated thread. The mic thread calls feed() with
    raw int16 frames; detections invoke on_detect() (called from this thread —
    the callback must marshal to whatever loop/UI it needs).
    """

    def __init__(self, on_detect: Callable[[], None],
                 threshold: float = DEFAULT_THRESHOLD,
                 logger: Callable[[str], None] = print,
                 notify: Callable[[str], None] | None = None):
        self._on_detect = on_detect
        self._threshold = threshold
        self._logger    = logger
        # See PluginRegistry: `logger` is the console and gets everything,
        # `notify` is the activity log and gets only what the user must act on.
        self._notify    = notify or (lambda _msg: None)
        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._thread: threading.Thread | None = None
        self._running = False
        self._model = None
        self._ready = False
        self._reset_pending = False

    def start(self) -> bool:
        """Load the model and spawn the inference thread. Returns True on success.
        Safe to call again — a no-op if already running. Never raises."""
        if self._running:
            return True
        try:
            from openwakeword.model import Model
            custom = model_path()
            if not custom.is_file():
                raise FileNotFoundError(f"Hey PAX model is missing: {custom}")
            self._model = Model(wakeword_models=[str(custom)], inference_framework="onnx")
        except Exception as e:
            self._logger(f"Wake word: could not load model — {e}")
            self._notify("Wake word unavailable — use the WAKE NOW button.")
            self._model = None
            return False
        self._running = True
        self._ready = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="WakeWordThread")
        self._thread.start()
        self._logger("Wake word: listening for 'Hey PAX'.")
        return True

    def stop(self) -> None:
        self._running = False
        # unblock the thread if it's waiting on the queue
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        self._model = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def reset(self) -> None:
        """Forget what was heard so far — call it when the assistant goes to sleep.
        Done on the detector's own thread, which is the only one that touches
        the model."""
        self._reset_pending = True

    def _reset_model(self) -> None:
        # The model keeps its recent audio features and scores. Left alone, one
        # "Hey PAX" fired a dozen times in a row (measured: 12), and audio
        # from before the assistant woke was still in there when it went back
        # to sleep — so it could wake again on words said minutes earlier.
        try:
            self._model.reset()
        except Exception:
            pass

    def feed(self, frame_int16) -> None:
        """Called from the mic callback (real-time thread). Must stay cheap and
        never block — the frame is copied and dropped if the queue is backed up."""
        if not self._running:
            return
        try:
            # frame_int16 is a numpy int16 array (possibly 2-D mono) — flatten to 1-D
            data = frame_int16[:, 0].copy() if getattr(frame_int16, "ndim", 1) > 1 else frame_int16.copy()
            self._queue.put_nowait(data)
        except queue.Full:
            pass
        except Exception:
            pass

    def _loop(self) -> None:
        import numpy as np
        while self._running:
            try:
                frame = self._queue.get()
                if frame is None or not self._running:
                    break
                if self._reset_pending:
                    self._reset_pending = False
                    self._reset_model()
                scores = self._model.predict(np.asarray(frame, dtype=np.int16))
                score = 0.0
                if isinstance(scores, dict):
                    for k, v in scores.items():
                        if "hey_pax" in k.lower() or "hey-pax" in k.lower():
                            score = max(score, float(v))
                    if score == 0.0 and len(scores) == 1:
                        score = max(float(v) for v in scores.values())
                if score >= self._threshold:
                    # drain any backlog and clear the model, so one utterance is one wake
                    self._drain()
                    self._reset_model()
                    try:
                        self._on_detect()
                    except Exception as e:
                        self._logger(f"Wake word: on_detect error — {e}")
            except Exception as e:
                self._logger(f"Wake word: inference error — {e}")

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except Exception:
            pass
