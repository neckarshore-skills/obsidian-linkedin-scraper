#!/usr/bin/env python3
"""Polish LinkedIn post text via the Anthropic Claude API.

For every post in a scraped overview.json (envelope shape from `scrape_profile.py`), make ONE
Haiku call that returns a JSON object with three fields:

  - `description` — third-person, neutral, factual, ≤120 chars. Used in YAML Properties.
  - `content`     — neutral Markdown briefing of the substance. Strips LinkedIn-isms (the
                    one-line-per-paragraph hooks, the "Here's why →" cliffhangers, the
                    "Repost this if it resonated" CTAs) and keeps only the actual claims.
  - `tags`        — 2–3 Obsidian-safe content tags.

Idempotent: if `description_polished` AND `content_polished` are both already set, skip.

Pricing reference: Haiku 4.5 input ~$1/MTok, output ~$5/MTok. With prompt caching on the
system instruction, per-post ≈ $0.002 ($0.05 for a 25-post profile).

Setup:
  ANTHROPIC_API_KEY in env. Get one at https://console.anthropic.com/settings/keys.
  pip install anthropic  (already pinned in requirements.txt)

Usage:
  polish_post.py --input <overview.json>                       # default: claude-haiku-4-5-20251001
  polish_post.py --input <overview.json> --no-skip-existing    # re-polish even if fields exist
  polish_post.py --input <overview.json> --model claude-sonnet-4-6   # use a different model

The script no-ops gracefully if `ANTHROPIC_API_KEY` isn't set — prints a short setup hint and
exits 0 so it can be safely chained from `scrape_profile.py`.
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
MAX_DESCRIPTION_CHARS = 120
MIN_CONTENT_CHARS = 80  # below this, the post is too short to merit a polish (likely a one-liner reshare)

SYSTEM_PROMPT = """\
You rewrite LinkedIn posts as neutral, well-structured briefings for a content-research vault.
Goal: a reader should grasp the substance faster from your rewrite than from the original
post — even (and especially) when the original is padded with engagement-bait scaffolding.

Given a post's text, return ONE JSON object with these fields:

{
  "description": "<one factual sentence describing the post's topic>",
  "content":     "<a neutral, structured Markdown briefing of the substance>",
  "tags":        ["Tag1", "Tag2", "Tag3"]
}

Rules for `description`:
- Third person, neutral, factual. No "I", "we", "you", "let's", "here's".
- Describe WHAT the post is about (the topic / claim), not what the author is doing.
- Maximum 120 characters total. Strict.
- No marketing language ("amazing", "must-read", "incredible", "game-changer").
- No filler prefix ("This post covers", "The author shares", "In this LinkedIn post").
- One sentence. End with a period.

Rules for `content`:
- Neutral third-person voice. Do NOT mimic the author's voice or first-person framing.
  Translate the substance into a neutral briefing.
- Strip the LinkedIn performative scaffolding aggressively. Common patterns to remove:
    * Engagement hooks ("Here's something nobody talks about:", "After 10 years in X, here's
      what I learned:", "Most founders get this wrong:", "Read this twice.")
    * One-sentence-per-line stagger formatting — collapse to natural paragraphs.
    * Ellipses-as-cliffhangers ("And then…", "But there's a catch…").
    * Calls to action ("Repost ♻️ if you agree", "Follow me for more", "Comment X to get my
      free guide", "DM me for the deck", "Click the link in my bio/featured").
    * Self-promo blocks ("PS — I help founders do X. Book a call:", "If this resonated,
      check out my newsletter:").
    * Closing summary lines that just restate the title ("That's the playbook.", "It's
      really that simple.", "End of thread.").
- Keep EVERY substantive claim, number, name, framework, and step. The polished version
  replaces the post for skim-reading; nothing of substance should be lost.
- Use Markdown structure aggressively for readability:
    * Short intro paragraph (1–2 sentences) naming the topic.
    * Bullet lists when the post enumerates features, steps, options, pros/cons.
    * `**Bold**` for product/tool/company names and key claims.
    * For multi-aspect posts, use INLINE bold lead-ins as section markers, not headings:
      `**The setup —** content here.` followed by `**The catch —** content here.` Each
      lead-in is its own paragraph. Do NOT use `###` or any other Markdown headings — the
      rendered file already has `## Content` as the section heading and skipping levels
      breaks lint.
    * Skip lead-ins entirely if the post is single-topic — just write paragraphs.
- Brand attribution is hard fact, not interpretation. Do NOT cross-attribute features, tools,
  or capabilities across ecosystems (OpenAI ≠ Anthropic ≠ Google ≠ Meta).
- When in doubt about ANY proper noun (product, company, person), prefer omission or generic
  wording ("the model", "the tool", "the company") over a guess. A factual gap is better than
  a wrong attribution.
- Keep it concise. If the author repeats themselves (LinkedIn rewards repetition for
  engagement), state the point once.
- Do NOT add information that isn't in the post. Do NOT speculate or fill gaps.
- If the post is a personal anecdote or motivation piece with no concrete claims/numbers/
  steps, write a 1–2 sentence summary of the message and stop. Don't pad.

Rules for `tags` (used as Obsidian content tags):
- Exactly 2 to 3 tags. No more, no less.
- Specific to THIS post's substance: product/tool names, models, companies, technical
  topics, frameworks, industries. Examples: `Solopreneur`, `Newsletter-Growth`,
  `Sales-Funnel`, `B2B-SaaS`, `Personal-Branding`, `Content-Strategy`, `Cold-Email`.
- Format: PascalCase or kebab-case. ASCII letters/digits/hyphens/underscores ONLY. No
  dots (Obsidian truncates tags at `.`), no spaces, no slashes, no emoji. So write
  `B2B-SaaS` not `B2B SaaS`, `Series-A` not `Series A`.
- Avoid generic structural tags ("LinkedIn", "Post", "Insight", "Tip", "Howto",
  "Business", "Career") — those add no filtering value.
- Avoid hashtag-style marketing words ("Trending", "MustRead", "Inspiration").
- For thought-leadership pieces about a niche topic, the niche IS the tag (e.g.
  `LinkedIn-Algorithm`, `Founder-Sales`, `Pricing-Strategy`).

The `content` value is a Markdown string — embed real `\\n` line breaks (JSON-escaped) for
paragraph breaks, blank lines around lists, etc. Do NOT wrap output in code fences.

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
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip posts that already have BOTH description_polished and content_polished. Default ON.",
    )
    return p.parse_args()


def truncate_description(text: str) -> str:
    text = text.strip().strip("\"'“”‘’ ")
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[: MAX_DESCRIPTION_CHARS - 1].rsplit(" ", 1)[0] + "…"
    return text


def short_id(post: dict, fallback_idx: int) -> str:
    """Best-effort short identifier for log lines. LinkedIn IDs are long — show the last 8."""
    pid = post.get("id")
    if isinstance(pid, str) and len(pid) >= 8:
        return f"…{pid[-8:]}"
    if isinstance(pid, (int,)):
        return f"…{str(pid)[-8:]}"
    return f"#{fallback_idx}"


def main() -> int:
    args = parse_args()

    api_key = get_anthropic_key()
    if not api_key:
        sys.stderr.write(
            "INFO: ANTHROPIC_API_KEY not set — skipping post-polish step.\n"
            "Setup:\n"
            "  1. Get a key at https://console.anthropic.com/settings/keys\n"
            "  2. Add to ~/.zshrc:  export ANTHROPIC_API_KEY='sk-ant-...'\n"
            "  3. Reload your shell:  source ~/.zshrc\n"
        )
        print(json.dumps({"polished": 0, "skipped": 0, "reason": "no_api_key"}))
        return 0

    try:
        from anthropic import Anthropic
    except ImportError:
        sys.stderr.write(
            "ERROR: 'anthropic' package not installed. Run:\n"
            "  pip install -r " + str(Path(__file__).parent.parent / "requirements.txt") + "\n"
        )
        return 2

    if not args.input.exists():
        sys.stderr.write(f"ERROR: input file not found: {args.input}\n")
        return 2

    envelope = json.loads(args.input.read_text(encoding="utf-8"))
    posts = envelope.get("posts") or []
    if not isinstance(posts, list):
        sys.stderr.write("ERROR: posts is not a list — is this a LinkedIn overview.json from scrape_profile.py?\n")
        return 2

    candidates: list[tuple[int, dict]] = []
    skipped_short = 0
    for i, p in enumerate(posts):
        content = (p.get("content") or "").strip()
        if not content:
            continue
        if len(content) < MIN_CONTENT_CHARS:
            skipped_short += 1
            continue
        if args.skip_existing and p.get("description_polished") and p.get("content_polished"):
            continue
        candidates.append((i, p))

    if not candidates:
        sys.stderr.write(f"INFO: no posts need polishing in {args.input.name}\n")
        print(json.dumps({"polished": 0, "skipped": len(posts), "reason": "nothing_to_do"}))
        return 0

    sys.stderr.write(
        f"INFO: polishing {len(candidates)} post(s) via {args.model}"
        + (f" (skipped {skipped_short} too-short post(s))" if skipped_short else "")
        + "\n"
    )

    client = Anthropic(api_key=api_key)

    polished_count = 0
    failed: list[dict] = []
    polished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for n, (idx, post) in enumerate(candidates, start=1):
        sid = short_id(post, idx)
        content = (post.get("content") or "").strip()
        # Trim very long posts (rare — LinkedIn caps at ~3000 chars) to keep input tokens bounded
        prompt_input = content[:8000]

        sys.stderr.write(f"  [{n}/{len(candidates)}] {sid}: polishing ...\n")
        try:
            msg = client.messages.create(
                model=args.model,
                max_tokens=2000,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {"role": "user", "content": f"LinkedIn post:\n\n{prompt_input}"}
                ],
            )
        except Exception as exc:
            sys.stderr.write(f"    ERROR: {exc}\n")
            failed.append({"id": sid, "reason": str(exc)[:200]})
            continue

        text_chunks = [block.text for block in msg.content if getattr(block, "type", None) == "text"]
        raw = " ".join(text_chunks).strip()
        parsed = extract_json_object(raw)

        if not isinstance(parsed, dict):
            failed.append({"id": sid, "reason": "non_json_response", "raw_head": raw[:200]})
            continue

        description = (parsed.get("description") or "").strip()
        polished_content = (parsed.get("content") or "").strip()
        raw_tags = parsed.get("tags") or []
        if isinstance(raw_tags, list):
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]
        else:
            tags = []

        if not description or not polished_content:
            failed.append({"id": sid, "reason": "missing_field", "got": list(parsed.keys())})
            continue

        post["description_polished"] = truncate_description(description)
        post["description_polished_model"] = args.model
        post["description_polished_at"] = polished_at
        post["content_polished"] = polished_content
        post["content_polished_model"] = args.model
        post["content_polished_at"] = polished_at
        post["content_tags"] = tags
        polished_count += 1

    args.input.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "input": str(args.input),
                "polished": polished_count,
                "skipped_too_short": skipped_short,
                "skipped_existing_or_empty": len(posts) - len(candidates) - skipped_short,
                "failed": failed,
                "model": args.model,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
