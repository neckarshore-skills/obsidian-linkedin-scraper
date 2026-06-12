"""Provider-agnostic LLM completion seam for the social scrapers.

The scrapers use an LLM for two narrow jobs — profile "essence" one-liners and
transcript "polish" briefings. Both share one call shape: a cacheable system prompt
plus a single user message, returning raw text that the caller parses defensively
(see `llm_helpers.extract_json_object`). This module owns that call shape so the
provider is a configuration choice, not a code dependency (philosophy: AI-agnostic,
no vendor lock-in).

Providers:
  anthropic (default)  Anthropic Messages API via the `anthropic` package.
  ollama               Local Ollama server via HTTP (`requests`, already pinned).

Env contract:
  SOCIAL_LLM_PROVIDER  "anthropic" (default) | "ollama"
  SOCIAL_LLM_MODEL     Overrides the caller-supplied default model for any provider.
                       REQUIRED for ollama (we cannot know which models are pulled).
  OLLAMA_HOST          Ollama base URL. Default: "http://localhost:11434".
  ANTHROPIC_API_KEY    Anthropic provider only.

Graceful no-op rule (matches the existing scraper shape): when the selected provider
is not fully configured (missing key / missing ollama model), `complete()` prints a
setup hint to stderr and returns None — the caller skips its LLM-driven step instead
of failing the scrape. An INVALID provider value, by contrast, raises LLMConfigError:
misconfiguration should fail loudly, absence should degrade gracefully.
"""

from __future__ import annotations

import os
import sys


VALID_PROVIDERS = ("anthropic", "ollama")
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
OLLAMA_TIMEOUT_SECONDS = 300  # local 7B models can take ~20-60s per call on Apple Silicon

OLLAMA_SETUP_HINT = """\
INFO: SOCIAL_LLM_PROVIDER=ollama but SOCIAL_LLM_MODEL is not set — skipping LLM-driven step.
Setup:
  1. Pick a pulled model:  ollama list
  2. Add to ~/.zshrc:  export SOCIAL_LLM_MODEL='qwen2.5:7b'   # or any pulled model
  3. Reload your shell:  source ~/.zshrc
(Optional: export OLLAMA_HOST if the server is not on http://localhost:11434)
"""


class LLMConfigError(ValueError):
    """Raised when SOCIAL_LLM_PROVIDER holds a value we do not support."""


def resolve_provider() -> str:
    """Read SOCIAL_LLM_PROVIDER (default: anthropic). Raises LLMConfigError on junk."""
    raw = os.environ.get("SOCIAL_LLM_PROVIDER", "anthropic").strip().lower()
    if raw not in VALID_PROVIDERS:
        raise LLMConfigError(
            f"SOCIAL_LLM_PROVIDER={raw!r} is not supported. "
            f"Valid values: {', '.join(VALID_PROVIDERS)}."
        )
    return raw


def resolve_model(default_model: str) -> str:
    """SOCIAL_LLM_MODEL wins over the caller-supplied default."""
    return os.environ.get("SOCIAL_LLM_MODEL", "").strip() or default_model


def describe_target(default_model: str) -> str:
    """Provenance string '<provider>:<model>' for logs and JSON metadata fields."""
    provider = resolve_provider()
    if provider == "ollama":
        model = os.environ.get("SOCIAL_LLM_MODEL", "").strip() or "(unset)"
    else:
        model = resolve_model(default_model)
    return f"{provider}:{model}"


def build_ollama_payload(
    *, system: str, user: str, model: str, json_mode: bool, max_tokens: int
) -> dict:
    """Build the /api/chat request body. Pure — unit-tested without network.

    temperature=0 pins determinism (same input → same essence/polish across re-runs,
    matching the determinism convention used elsewhere in the skill family).
    """
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0, "num_predict": max_tokens},
    }
    if json_mode:
        payload["format"] = "json"
    return payload


def parse_ollama_response(resp: dict) -> str:
    """Extract the assistant text from an /api/chat response. Defensive: '' on any gap."""
    message = resp.get("message") or {}
    content = message.get("content") or ""
    return str(content).strip()


def _complete_anthropic(system: str, user: str, *, model: str, max_tokens: int) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        sys.stderr.write(
            "INFO: ANTHROPIC_API_KEY not set — skipping LLM-driven step.\n"
            "Setup:\n"
            "  1. Get a key at https://console.anthropic.com/settings/keys\n"
            "  2. Add to ~/.zshrc:  export ANTHROPIC_API_KEY='sk-ant-...'\n"
            "  3. Reload your shell:  source ~/.zshrc\n"
            "(Or run fully local: export SOCIAL_LLM_PROVIDER=ollama — see README.)\n"
        )
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        sys.stderr.write("ERROR: 'anthropic' package not installed. Run pip install -r requirements.txt\n")
        return None
    client = Anthropic(api_key=key)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    chunks = [block.text for block in msg.content if getattr(block, "type", None) == "text"]
    return " ".join(chunks).strip()


def _complete_ollama(
    system: str, user: str, *, max_tokens: int, json_mode: bool
) -> str | None:
    model = os.environ.get("SOCIAL_LLM_MODEL", "").strip()
    if not model:
        sys.stderr.write(OLLAMA_SETUP_HINT)
        return None
    import requests  # already pinned in requirements.txt

    host = os.environ.get("OLLAMA_HOST", "").strip() or DEFAULT_OLLAMA_HOST
    payload = build_ollama_payload(
        system=system, user=user, model=model, json_mode=json_mode, max_tokens=max_tokens
    )
    resp = requests.post(
        f"{host.rstrip('/')}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT_SECONDS
    )
    resp.raise_for_status()
    return parse_ollama_response(resp.json())


def complete(
    system: str,
    user: str,
    *,
    default_model: str,
    max_tokens: int,
    json_mode: bool = False,
) -> str | None:
    """One LLM completion via the configured provider.

    Returns the raw assistant text, or None when the provider is unconfigured
    (setup hint printed; caller no-ops). Transport/API errors propagate as
    exceptions — callers already wrap their LLM calls in try/except and degrade
    per-item, which this preserves.

    `json_mode` is enforced natively on ollama (format=json); on anthropic the
    system prompts already demand a single JSON object and callers parse
    defensively via extract_json_object — unchanged behavior.
    """
    provider = resolve_provider()
    if provider == "ollama":
        return _complete_ollama(system, user, max_tokens=max_tokens, json_mode=json_mode)
    return _complete_anthropic(
        system, user, model=resolve_model(default_model), max_tokens=max_tokens
    )
