import unittest

from core.free_ai_policy import is_free_model, require_free_model


class FreeAIModelPolicyTests(unittest.TestCase):
    def test_known_free_models_are_allowed(self):
        cases = [
            ("openai_compat@opencode_zen_free", "big-pickle"),
            ("openai_compat@opencode_zen_free", "mimo-v2.5-free"),
            ("openai_compat@openrouter_free", "openrouter/free"),
            ("openai_compat@openrouter_free", "meta-llama/llama-3.3-70b-instruct:free"),
        ]
        for provider, model in cases:
            with self.subTest(provider=provider, model=model):
                self.assertTrue(is_free_model(provider, model))
                require_free_model(provider, model)

    def test_paid_or_unknown_models_fail_closed(self):
        cases = [
            ("openai_compat@opencode_zen_free", "claude-sonnet-4-5"),
            ("openai_compat@opencode_zen_free", "glm-5.3"),
            ("openai_compat@openrouter_free", "openai/gpt-5.5"),
            ("openai_compat@openrouter_free", "unknown-model"),
            ("openai_compat@openrouter", "meta-llama/llama:free"),
        ]
        for provider, model in cases:
            with self.subTest(provider=provider, model=model):
                self.assertFalse(is_free_model(provider, model))
                with self.assertRaises(RuntimeError):
                    require_free_model(provider, model)


if __name__ == "__main__":
    unittest.main()
