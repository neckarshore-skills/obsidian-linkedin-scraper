#!/usr/bin/env python3
"""Generate a one-sentence "essence" of what a LinkedIn account stands for.

Used for the folder name: `<handle> — <essence>` so the Obsidian sidebar shows at a glance
what each scraped account is about. The essence is profile-level (not per-post), generated
from the embedded author bio plus a small content sample, and stored in the JSON envelope's
top-level `_essence` field so re-renders and re-scrapes are idempotent.

One Haiku 4.5 call per profile. Cost ≈ $0.001/profile. Skips if `_essence` is already set
unless `--regenerate` is passed.

Usage:
  essence_profile.py --input <overview.json>                  # default model claude-haiku-4-5
  essence_profile.py --input <overview.json> --regenerate     # force a fresh essence
  essence_profile.py --input <overview.json> --model <id>     # override model

No-ops gracefully if `ANTHROPIC_API_KEY` isn't set — same shape as polish_post.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _social_common.tokens import get_anthropic_key, print_anthropic_setup_hint
from _social_common.llm_helpers import extract_json_object
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_ESSENCE_CHARS = 60
CONTENT_SAMPLE_COUNT = 3
CONTENT_SAMPLE_CHARS = 400  # per post, hook + first paragraph is enough

SYSTEM_PROMPT = """\
You write one-sentence essences of LinkedIn accounts. The essence appears in a folder name in
the user's research vault, so the reader can tell at a glance what the account is about.

Given the account's bio (LinkedIn headline / `info`), full name, profile kind (personal vs.
company), website-label hint (if any), and a small sample of recent posts, return ONE JSON
object:

{ "essence": "<one short sentence>" }

Rules:
- Maximum 60 characters total. Strict. Folder names get long fast.
- One sentence. End WITHOUT a period (folder names look cleaner without trailing punctuation).
- WHAT the account is about (their angle, niche, value-prop), not a job title verbatim.
  Example good (personal, English bio): "Solopreneur economics & creator-business tactics"
  Example good (company, German bio):   "Entwickler-Tooling für KI-first-Engineering-Teams"
  Example bad:                          "Posts about business on LinkedIn weekly"
- For PERSONAL profiles, capture the niche/perspective the person teaches or builds in. Their
  job title alone is too broad — extract the angle from bio + recent posts.
- For COMPANY profiles, capture the product/category and the audience.
- Match the language of the bio. German bio → German essence. English bio → English essence.
  Mixed/ambiguous → English. Do NOT copy the language of the examples above — they only
  illustrate style; the bio language is the only signal that counts.
- No marketing language ("amazing", "leading", "the best", "innovative", "cutting-edge").
- No emojis. No hashtags. No quotation marks.
- No generic categories ("Marketing agency", "Content creator") unless that IS the niche —
  prefer something more specific from the posts.
- ASCII letters/digits/spaces/hyphens preferred; umlauts (ä ö ü ß) and é/è are OK; avoid
  special chars that confuse filesystems (/ \\ : * ? < > | ").

Output ONLY the JSON object. No preamble. No commentary.
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--input", type=Path, required=True, help="Path to overview.json from scrape_profile.py")
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model ID (default: {DEFAULT_MODEL}).",
    )
    p.add_argument(
        "--regenerate",
        action="store_true",
        help="Re-generate essence even if `_essence` is already set.",
    )
    return p.parse_args()


_FS_REPLACE = re.compile(r"[/\\:*?<>|\"]+")
_WS_COLLAPSE = re.compile(r"\s+")


def sanitize_essence(text: str) -> str:
    text = text.strip().strip("\"'“”‘’ ")
    text = _FS_REPLACE.sub(" ", text)
    text = _WS_COLLAPSE.sub(" ", text)
    text = text.rstrip(".!?,;:— -")
    if len(text) > MAX_ESSENCE_CHARS:
        text = text[: MAX_ESSENCE_CHARS - 1].rsplit(" ", 1)[0]
    return text.strip()


def build_user_message(envelope: dict) -> str:
    """Pack the profile signals the model needs into one user-message payload. Source for
    profile metadata is the embedded author of the first post (harvestapi shape)."""
    handle = (envelope.get("_handle") or "").strip()
    kind = (envelope.get("_kind") or "in").strip()
    posts = envelope.get("posts") or []
    author = (posts[0].get("author") if posts and isinstance(posts[0], dict) else None) or {}
    if not isinstance(author, dict):
        author = {}

    full_name = (author.get("name") or "").strip()
    bio = (author.get("info") or "").strip()
    website_label = (author.get("websiteLabel") or "").strip()
    profile_kind_label = "Personal profile" if kind == "in" else (
        "Company page" if kind == "company" else f"Profile ({kind})"
    )

    samples: list[str] = []
    for p in posts[:CONTENT_SAMPLE_COUNT]:
        if not isinstance(p, dict):
            continue
        # Prefer the polished briefing over the raw post — it's already neutral and
        # densified, less noise per token. Fall back to raw `content` when polish missing.
        c = (p.get("content_polished") or p.get("content") or "").strip()
        if c:
            samples.append(c[:CONTENT_SAMPLE_CHARS])

    parts = [f"Handle: {handle or '(unknown)'}"]
    if full_name:
        parts.append(f"Full name: {full_name}")
    parts.append(f"Profile kind: {profile_kind_label}")
    parts.append(f"Bio / headline: {bio or '(empty)'}")
    if website_label:
        parts.append(f"Website label: {website_label}")
    if samples:
        parts.append("\nRecent posts:")
        for i, c in enumerate(samples, 1):
            parts.append(f"\n[{i}] {c}")
    return "\n".join(parts)


def main() -> int:
    args = parse_args()

    if not args.input.exists():
        sys.stderr.write(f"ERROR: input file not found: {args.input}\n")
        return 2

    envelope = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict) or not isinstance(envelope.get("posts"), list):
        sys.stderr.write("ERROR: not a LinkedIn overview envelope (expected dict with posts[])\n")
        return 2

    if not args.regenerate and (envelope.get("_essence") or "").strip():
        sys.stderr.write(f"INFO: essence already set ({envelope['_essence']!r}); skipping\n")
        print(json.dumps({"essence": envelope["_essence"], "regenerated": False}, ensure_ascii=False))
        return 0

    api_key = get_anthropic_key()
    if not api_key:
        sys.stderr.write(
            "INFO: ANTHROPIC_API_KEY not set — skipping essence generation.\n"
            "Setup:\n"
            "  1. Get a key at https://console.anthropic.com/settings/keys\n"
            "  2. Add to ~/.zshrc:  export ANTHROPIC_API_KEY='sk-ant-...'\n"
            "  3. Reload your shell:  source ~/.zshrc\n"
        )
        print(json.dumps({"essence": None, "reason": "no_api_key"}))
        return 0

    try:
        from anthropic import Anthropic
    except ImportError:
        sys.stderr.write("ERROR: 'anthropic' package not installed. Run pip install -r requirements.txt\n")
        return 2

    user_message = build_user_message(envelope)

    client = Anthropic(api_key=api_key)
    sys.stderr.write(f"INFO: generating essence via {args.model}\n")
    try:
        msg = client.messages.create(
            model=args.model,
            max_tokens=200,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        sys.stderr.write(f"ERROR: essence call failed: {exc}\n")
        print(json.dumps({"essence": None, "reason": "api_error", "detail": str(exc)[:200]}))
        return 0

    text_chunks = [block.text for block in msg.content if getattr(block, "type", None) == "text"]
    raw = " ".join(text_chunks).strip()
    parsed = extract_json_object(raw)

    if not isinstance(parsed, dict) or not (parsed.get("essence") or "").strip():
        sys.stderr.write(f"WARN: model did not return a valid essence; raw head: {raw[:200]!r}\n")
        print(json.dumps({"essence": None, "reason": "non_json_or_empty", "raw_head": raw[:200]}))
        return 0

    essence = sanitize_essence(parsed["essence"])
    if not essence:
        print(json.dumps({"essence": None, "reason": "empty_after_sanitize"}))
        return 0

    envelope["_essence"] = essence
    envelope["_essence_model"] = args.model
    envelope["_essence_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    args.input.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")

    sys.stderr.write(f"INFO: essence: {essence!r}\n")
    print(json.dumps({"essence": essence, "regenerated": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
