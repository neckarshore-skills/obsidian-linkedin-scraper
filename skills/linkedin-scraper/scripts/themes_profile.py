#!/usr/bin/env python3
"""Synthesize a one-page strategy brief from a creator's polished LinkedIn posts.

Reads the envelope JSON written by scrape_profile.py + polish_post.py, sends bio + all
polished post briefings through one Sonnet call, and stores the returned Markdown body in
the envelope under `_themes_md`. The render step writes that body to a `_<handle> themes.md`
file in the profile folder.

The brief sits NEXT to the per-post notes — the reader scans it before deciding which
individual posts to dive into. Sections: Top themes, Recurring hooks, Tonality, Audience
cues, Posting strategy.

Cost ~$0.04–0.07 per profile (Sonnet 4.6, system prompt cached). One call per profile.
Idempotent: skips if `_themes_md` is already set unless `--regenerate` is passed.

No-ops gracefully if the configured LLM provider is unconfigured. Default provider: Anthropic
(ANTHROPIC_API_KEY). Fully local: SOCIAL_LLM_PROVIDER=ollama + SOCIAL_LLM_MODEL=<pulled model>
— see _social_common/llm_client.py for the full env contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _social_common.llm_client import complete, describe_target
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 2500
PER_POST_CONTENT_CHARS = 600  # truncate each polished content this far for the input bundle
MIN_POSTS_FOR_THEMES = 5      # below this the synthesis becomes anecdotal, not pattern-finding

SYSTEM_PROMPT = """\
You analyze a creator's recent LinkedIn posts and produce a one-page strategy brief for a
content-research vault. The brief sits next to the per-post notes; the reader scans your
brief BEFORE reading individual posts to decide which to dive into.

Given the creator's bio plus recent polished post briefings, return a Markdown body with
these sections, in this order:

## Top themes

A numbered list of the 3–5 themes the creator returns to most often. For each:
1. **Theme name** — one-sentence what-and-angle. Posts: N/total
2. ...

Pick themes that recur — a one-off post is not a theme. The "Posts: N/total" count tells
the reader how dominant each theme is.

## Recurring hooks

A bullet list of 3–5 sentence patterns the creator opens posts with most often. Format:
- "{exact hook from a post, verbatim}" — what kind of post it sets up

If the creator doesn't have a signature hook style (e.g. they vary openings widely), say
that explicitly in one sentence and skip the bullet list.

## Tonality

1–2 paragraphs naming the voice. Cover:
- Register (formal/casual, jargon-heavy/accessible)
- Rhetorical moves (anecdotes, frameworks, contrarian takes, second-person addresses)
- Any signature stylistic tells (em-dash usage, repetition, parenthetical asides, etc.)

## Audience cues

1 paragraph: who this content seems designed to attract (job title, career stage, mindset).
What does the engagement pattern signal — which themes/hooks land hardest?

## Posting strategy

2–4 bullets:
- Cadence (daily/several-per-week/sporadic) inferred from the dates
- Format mix (long-form essays / lists / one-liners / personal anecdotes)
- Notable absences — what kinds of posts are NOT in the feed that you might expect

Rules:
- Match the language of the bio. German bio → German brief. English bio → English brief.
  Mixed/ambiguous → English.
- Cite specific posts when claiming a pattern. "Posts: 5/25" for theme dominance; quote
  hooks VERBATIM (no paraphrasing). Never invent a quote.
- No marketing language ("unique value", "powerful insights", "thought-provoking", "must-read").
- No quality judgments ("his best post", "the strongest theme") — this is descriptive,
  not evaluative. The reader judges; you describe.
- Don't pad. If a section has nothing substantive, write one sentence that explicitly says
  so (e.g. "No recurring hook style — openings vary by post."). Don't fill with platitudes.
- Avoid `###` and deeper headings — sections stop at `##`. Use bold lead-ins for emphasis.
- Output ONLY the Markdown body. Start with `## Top themes`. No preamble. No frontmatter.
  No trailing horizontal rule.
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--input", type=Path, required=True, help="Path to overview.json from scrape_profile.py")
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=(
            f"Model ID for the default anthropic provider (default: {DEFAULT_MODEL}). "
            "Any provider's model can also be set via SOCIAL_LLM_MODEL; "
            "switch providers via SOCIAL_LLM_PROVIDER (anthropic | ollama)."
        ),
    )
    p.add_argument(
        "--regenerate",
        action="store_true",
        help="Re-generate themes brief even if `_themes_md` is already set.",
    )
    return p.parse_args()



def post_engagement_line(post: dict) -> str:
    e = post.get("engagement") or {}
    if not isinstance(e, dict):
        return ""
    parts = []
    for label, key in (("likes", "likes"), ("comments", "comments"), ("shares", "shares")):
        v = e.get(key)
        if v is not None:
            parts.append(f"{v} {label}")
    return " · ".join(parts)


def post_date_short(post: dict) -> str:
    pa = post.get("postedAt") or {}
    if isinstance(pa, dict):
        d = pa.get("date")
        if isinstance(d, str) and len(d) >= 10:
            return d[:10]
    return "?"


def build_user_message(envelope: dict) -> tuple[str, int]:
    """Pack handle + bio + per-post bundle. Returns (message, post_count_used)."""
    handle = (envelope.get("_handle") or "").strip()
    kind = (envelope.get("_kind") or "in").strip()
    posts = envelope.get("posts") or []
    author = (posts[0].get("author") if posts and isinstance(posts[0], dict) else None) or {}
    if not isinstance(author, dict):
        author = {}

    full_name = (author.get("name") or "").strip()
    bio = (author.get("info") or "").strip()
    profile_kind_label = "Personal profile" if kind == "in" else (
        "Company page" if kind == "company" else f"Profile ({kind})"
    )

    parts = [f"Handle: {handle or '(unknown)'}"]
    if full_name:
        parts.append(f"Name: {full_name}")
    parts.append(f"Profile kind: {profile_kind_label}")
    parts.append(f"Bio: {bio or '(empty)'}")
    parts.append(f"\nRecent polished posts ({len(posts)} total):")

    used = 0
    for i, p in enumerate(posts, start=1):
        if not isinstance(p, dict):
            continue
        # Source order: prefer polished briefing; fall back to raw content if polish missing.
        polished_desc = (p.get("description_polished") or "").strip()
        polished_content = (p.get("content_polished") or "").strip()
        raw_content = (p.get("content") or "").strip()
        body = polished_content or raw_content
        if not body:
            continue

        date_short = post_date_short(p)
        engagement = post_engagement_line(p)
        truncated = body[:PER_POST_CONTENT_CHARS]
        if len(body) > PER_POST_CONTENT_CHARS:
            truncated += " […]"

        block = [f"\n[{i}] {date_short} · {engagement}"]
        if polished_desc:
            block.append(f"Description: {polished_desc}")
        block.append(f"Content: {truncated}")
        parts.append("\n".join(block))
        used += 1

    return ("\n".join(parts), used)


def main() -> int:
    args = parse_args()

    if not args.input.exists():
        sys.stderr.write(f"ERROR: input file not found: {args.input}\n")
        return 2

    envelope = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict) or not isinstance(envelope.get("posts"), list):
        sys.stderr.write("ERROR: not a LinkedIn overview envelope (expected dict with posts[])\n")
        return 2

    if not args.regenerate and (envelope.get("_themes_md") or "").strip():
        sys.stderr.write("INFO: themes already set; skipping\n")
        print(json.dumps({"themes": "already_set", "regenerated": False}, ensure_ascii=False))
        return 0

    posts = envelope.get("posts") or []
    if len(posts) < MIN_POSTS_FOR_THEMES:
        sys.stderr.write(
            f"INFO: only {len(posts)} post(s) loaded — themes synthesis needs ≥ "
            f"{MIN_POSTS_FOR_THEMES} to find patterns; skipping\n"
        )
        print(json.dumps({"themes": None, "reason": "too_few_posts", "loaded": len(posts)}))
        return 0

    user_message, used_count = build_user_message(envelope)
    if used_count == 0:
        sys.stderr.write("WARN: no posts had renderable content — nothing to synthesize\n")
        print(json.dumps({"themes": None, "reason": "no_renderable_content"}))
        return 0

    target = describe_target(args.model)
    sys.stderr.write(f"INFO: synthesizing themes via {target} (using {used_count} post(s))\n")
    try:
        raw = complete(
            SYSTEM_PROMPT,
            user_message,
            default_model=args.model,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
    except Exception as exc:
        sys.stderr.write(f"ERROR: themes call failed: {exc}\n")
        print(json.dumps({"themes": None, "reason": "api_error", "detail": str(exc)[:200]}))
        return 0

    if raw is None:
        sys.stderr.write("INFO: LLM provider unconfigured — skipping themes synthesis.\n")
        print(json.dumps({"themes": None, "reason": "llm_unconfigured"}))
        return 0
    raw = raw.strip()

    # The model returns Markdown directly. Defensively strip an opening code-fence or any
    # leading commentary before the first `## ` heading.
    body = raw
    if body.startswith("```"):
        # Strip ```markdown … ``` wrapper if present
        end_fence = body.rfind("```")
        if end_fence > 3:
            inner = body[body.find("\n") + 1 : end_fence].strip()
            if inner:
                body = inner
    # If the model added preamble, jump to the first `## ` heading
    first_heading = body.find("## ")
    if first_heading > 0:
        body = body[first_heading:].strip()

    if not body or "## " not in body:
        sys.stderr.write(f"WARN: model output had no `## ` headings; raw head: {raw[:200]!r}\n")
        print(json.dumps({"themes": None, "reason": "no_headings", "raw_head": raw[:200]}))
        return 0

    envelope["_themes_md"] = body
    envelope["_themes_model"] = target
    envelope["_themes_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    envelope["_themes_posts_used"] = used_count

    args.input.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")

    sys.stderr.write(f"INFO: themes brief written ({len(body)} chars)\n")
    print(
        json.dumps(
            {
                "themes": "ok",
                "regenerated": True,
                "posts_used": used_count,
                "model": target,
                "body_chars": len(body),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
