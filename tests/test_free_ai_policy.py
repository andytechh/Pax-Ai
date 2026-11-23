from core.free_ai_policy import is_free_model, require_free_model
import pytest


@pytest.mark.parametrize("provider,model", [
    ("openai_compat@opencode_zen_free", "big-pickle"),
    ("openai_compat@opencode_zen_free", "mimo-v2.5-free"),
    ("openai_compat@openrouter_free", "openrouter/free"),
    ("openai_compat@openrouter_free", "meta-llama/llama-3.3-70b-instruct:free"),
])
def test_known_free_models_are_allowed(provider, model):
    assert is_free_model(provider, model)
    require_free_model(provider, model)


@pytest.mark.parametrize("provider,model", [
    ("openai_compat@opencode_zen_free", "claude-sonnet-4-5"),
    ("openai_compat@opencode_zen_free", "glm-5.3"),
    ("openai_compat@openrouter_free", "openai/gpt-5.5"),
    ("openai_compat@openrouter_free", "unknown-model"),
    ("openai_compat@openrouter", "meta-llama/llama:free"),
])
def test_paid_or_unverified_models_fail_closed(provider, model):
    assert not is_free_model(provider, model)
    with pytest.raises(RuntimeError):
        require_free_model(provider, model)
