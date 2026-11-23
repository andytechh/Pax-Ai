"""Hard allow-list for PAX's free-only AI mode.

A provider being OpenAI-compatible does not make it free. Free-only mode accepts
only explicit free model identifiers on the OpenCode Zen and OpenRouter
endpoints. Unknown providers and unknown model IDs fail closed.
"""
from __future__ import annotations

OPENCODE_FREE_MODELS = frozenset({
    "big-pickle",
    "mimo-v2.5-free",
    "ling-3.0-flash-fin-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "nemotron-3.5-super-vl-free",
    "deepseek-v4.1-flash-free",
    "flint-40b-free",
    "hy4-flash-free",
    "iceman-free",
    "tidepool-alpha-free",
    "jev-1.13-free",
})

FREE_PROVIDER_IDS = (
    "openai_compat@opencode_zen_free",
    "openai_compat@openrouter_free",
)

LOCAL_FREE_STT = frozenset({"stt_whisper"})
LOCAL_FREE_TTS = frozenset({"tts_piper", "tts_system"})


def is_free_model(provider_id: str, model: str) -> bool:
    pid = (provider_id or "").strip().lower()
    mid = (model or "").strip().lower()
    if pid == "openai_compat@opencode_zen_free":
        return mid in OPENCODE_FREE_MODELS
    if pid == "openai_compat@openrouter_free":
        return mid == "openrouter/free" or mid.endswith(":free")
    return False


def require_free_model(provider_id: str, model: str) -> None:
    if not is_free_model(provider_id, model):
        raise RuntimeError(
            f"Free-only mode blocked provider/model {provider_id!r}/{model!r}. "
            "Choose a verified free model; paid and unknown model IDs are disabled."
        )
