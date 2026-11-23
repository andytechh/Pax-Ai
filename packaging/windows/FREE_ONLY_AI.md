# PAX free-only AI mode

PAX's free-only model policy is enforced in application code, not merely in the
settings UI. Hosted model calls are rejected unless the provider ID and model
ID are on the explicit allowlist in `core/free_ai_policy.py`.

## Supported hosted free routes

- OpenCode Zen free-model catalog, through its OpenAI-compatible
  `https://opencode.ai/zen/v1` chat-completions endpoint.
- OpenRouter's `openrouter/free` router or model IDs ending in `:free`.

Free model availability is temporary and rate limited. The allowlist is
intentionally conservative and must be refreshed against provider catalogs.
Unknown model IDs are blocked rather than guessed to be free.

## API keys and setup

Add a provider API key in PAX's existing **Engine & Providers** settings.
Choose the corresponding free-only catalog entry, and choose a model present in
`core/free_ai_policy.py`. Never paste a paid provider key into a free-only
entry. A key itself does not make requests free; only the selected model's
provider pricing and account terms determine that.

## Gemini billing safety

Gemini API requests can be free on an eligible free-tier project, but the API
does not provide a reliable request flag that guarantees a request cannot be
billed if the Google project has billing enabled. Therefore PAX blocks Gemini
Live and Gemini side calls by default in free-only mode.

To use Gemini free quota, the owner must first ensure billing is disabled for
the Google AI Studio / Google Cloud project used by the API key, then explicitly
set `providers.gemini_free_tier_confirmed` to `true` in the existing
`%LOCALAPPDATA%\\PAX\\config\\api_keys.json`. If uncertain, leave it
false. The flag is an owner attestation, not an automated billing check.

When Gemini is not confirmed, configure a free-only LLM in Engine & Providers.
The voice pipeline additionally needs local Whisper STT and system/Piper TTS.
PAX will refuse unapproved providers rather than silently falling back to paid
models.

## Hard guarantees and limits

- Paid model IDs and unknown hosted model IDs are blocked.
- Gemini is blocked unless the owner attests the project has billing disabled.
- There is no automatic top-up, credit purchase, or paid fallback in this code.
- Provider accounts can still change terms or model availability; PAX cannot
  control charges made by the provider outside PAX.
- Existing credentials and user data are not rewritten by this feature.
