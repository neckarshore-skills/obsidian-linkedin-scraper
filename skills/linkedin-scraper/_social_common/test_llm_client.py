#!/usr/bin/env python3
"""Tests for `_social_common.llm_client` — the provider-agnostic LLM completion seam.

Run: `python3 test_llm_client.py` (from this directory or anywhere).
Exit code: 0 on success, 1 on any failed assertion (with the failure printed).

Same conventions as test_smoke.py: stdlib only, no pytest. Network transports are NOT
exercised here — only provider resolution, payload building, and response parsing
(pure logic). The live paths are covered by skill UAT runs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the package importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _social_common.llm_client import (  # noqa: E402
    LLMConfigError,
    build_ollama_payload,
    complete,
    describe_target,
    parse_ollama_response,
    resolve_model,
    resolve_provider,
)


_failures = 0


def _check(label: str, actual, expected) -> None:
    global _failures
    if actual != expected:
        sys.stderr.write(
            f"FAIL [{label}]:\n  expected: {expected!r}\n  actual:   {actual!r}\n"
        )
        _failures += 1


class _env:
    """Temporarily set/unset env vars; restores prior state on exit."""

    def __init__(self, **kv: str | None):
        self.kv = kv
        self.saved: dict[str, str | None] = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


# --- resolve_provider ---------------------------------------------------------------

with _env(SOCIAL_LLM_PROVIDER=None):
    _check("provider: default is anthropic", resolve_provider(), "anthropic")

with _env(SOCIAL_LLM_PROVIDER="ollama"):
    _check("provider: env ollama", resolve_provider(), "ollama")

with _env(SOCIAL_LLM_PROVIDER="  OLLAMA "):
    _check("provider: case/space normalized", resolve_provider(), "ollama")

with _env(SOCIAL_LLM_PROVIDER="openai"):
    try:
        resolve_provider()
        _check("provider: invalid raises LLMConfigError", "no-raise", "LLMConfigError")
    except LLMConfigError:
        pass

# --- resolve_model ------------------------------------------------------------------

with _env(SOCIAL_LLM_MODEL=None):
    _check(
        "model: caller default passes through",
        resolve_model("claude-haiku-4-5-20251001"),
        "claude-haiku-4-5-20251001",
    )

with _env(SOCIAL_LLM_MODEL="qwen2.5-coder:7b"):
    _check(
        "model: env override wins",
        resolve_model("claude-haiku-4-5-20251001"),
        "qwen2.5-coder:7b",
    )

# --- build_ollama_payload -----------------------------------------------------------

payload = build_ollama_payload(
    system="SYS", user="USR", model="m1", json_mode=True, max_tokens=200
)
_check("ollama payload: model", payload["model"], "m1")
_check("ollama payload: stream off", payload["stream"], False)
_check("ollama payload: json format", payload.get("format"), "json")
_check("ollama payload: temperature 0", payload["options"]["temperature"], 0)
_check("ollama payload: num_predict from max_tokens", payload["options"]["num_predict"], 200)
_check(
    "ollama payload: messages shape",
    payload["messages"],
    [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USR"}],
)

payload_plain = build_ollama_payload(
    system="SYS", user="USR", model="m1", json_mode=False, max_tokens=100
)
_check("ollama payload: no format key without json_mode", "format" in payload_plain, False)

# --- parse_ollama_response ----------------------------------------------------------

_check(
    "ollama parse: happy path",
    parse_ollama_response({"message": {"content": "  hello  "}}),
    "hello",
)
_check("ollama parse: missing message", parse_ollama_response({}), "")
_check("ollama parse: content None", parse_ollama_response({"message": {"content": None}}), "")

# --- complete: graceful unconfigured paths (no network) ------------------------------

with _env(SOCIAL_LLM_PROVIDER=None, ANTHROPIC_API_KEY=None):
    _check(
        "complete: anthropic without key returns None (graceful no-op)",
        complete("SYS", "USR", default_model="claude-haiku-4-5-20251001", max_tokens=50),
        None,
    )

with _env(SOCIAL_LLM_PROVIDER="ollama", SOCIAL_LLM_MODEL=None):
    _check(
        "complete: ollama without SOCIAL_LLM_MODEL returns None (graceful no-op)",
        complete("SYS", "USR", default_model="claude-haiku-4-5-20251001", max_tokens=50),
        None,
    )

# --- describe_target ----------------------------------------------------------------

with _env(SOCIAL_LLM_PROVIDER=None, SOCIAL_LLM_MODEL=None):
    _check(
        "describe: anthropic default",
        describe_target("claude-haiku-4-5-20251001"),
        "anthropic:claude-haiku-4-5-20251001",
    )

with _env(SOCIAL_LLM_PROVIDER="ollama", SOCIAL_LLM_MODEL="qwen2.5-coder:7b"):
    _check(
        "describe: ollama with model override",
        describe_target("claude-haiku-4-5-20251001"),
        "ollama:qwen2.5-coder:7b",
    )

# --------------------------------------------------------------------------------------

if _failures:
    sys.stderr.write(f"\n{_failures} assertion(s) failed.\n")
    sys.exit(1)
print("test_llm_client: all assertions passed")
