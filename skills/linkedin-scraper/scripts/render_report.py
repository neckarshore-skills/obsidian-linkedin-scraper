#!/usr/bin/env python3
"""Render a LinkedIn profile JSON envelope into Obsidian-friendly Markdown.

Output layout (per profile):
  <profile-folder>/
  ├── _<handle> overview.md                    # profile card + posts index (overwrites on re-render)
  ├── _<handle> overview.json                  # input JSON (kept here as raw)
  ├── <post-date> <title-slug>.md              # one file per post; polished content as ## Content
  └── ...

Two modes (only single-profile is used in first build session — batch lands once we have 2+):
  --input <overview.json>          # render one profile (overview + per-post files)
  --batch-dir <platform-folder>    # render every <handle>/_<*>overview.json; cross-profile summary
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _social_common.render_helpers import (
    fmt_int, fmt_int_yaml_str, yaml_quote, md_escape_pipe, url_encode_link,
    content_preview, sanitize_tag, slugify_for_filename, truncate_at_word,
)
from _social_common.timestamps import (
    fmt_property_ts, fmt_date_iso, derive_scrape_timestamp,
    read_existing_created, resolve_timestamps,
)
from _social_common.cleanup import cleanup_old_post_files
# ---------- tunables --------------------------------------------------------------------------

CONTENT_PREVIEW_CHARS = 60
TITLE_MAX_CHARS = 80
DESCRIPTION_MAX_CHARS = 125
SCRAPE_SOURCE = "apify/harvestapi/linkedin-profile-posts"
PROFILE_TAGS = ("LinkedIn", "Overview")
POST_TAGS_BASE = ("LinkedIn",)
SUMMARY_TAGS = ("LinkedIn", "Summary")
THEMES_TAGS = ("LinkedIn", "Themes")

# LinkedIn engagement-bait prefixes — strip when deriving a post title.
_BAIT_PREFIX_RE = re.compile(
    r"""^(
        repost\s+(this\s+)?(if|when|so)\s+
        | comment\s+["'“”‘’]?\w+["'“”‘’]?\s+(to|for|and|then|so)\s+
        | dm\s+["'“”‘’]?\w+["'“”‘’]?\s+(to|for|and)\s+
        | save\s+this\s+(post\s+)?(if|so|to|for)\s+
        | here['']s\s+(why|how|what)\s+
        | i\s+just\s+
    )""",
    re.IGNORECASE | re.VERBOSE,
)
_LEADING_EMOJI_RE = re.compile(r"^[☀-➿\U0001F300-\U0001FAFF✀-➿\s]+")
# Re-declared because used by derive_title (platform-specific). Same patterns as in
# _social_common.render_helpers; keep in sync.
_WS_COLLAPSE = re.compile(r"\s+")
# Auto-tag scrub for tables: `#tag`/`@mention` would render as Obsidian tag chips otherwise.


# ---------- argument parsing -----------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--input", type=Path, help="Path to a single profile overview.json")
    g.add_argument(
        "--batch-dir",
        type=Path,
        help="Platform folder containing one <handle>/ subfolder per profile",
    )
    return p.parse_args()


# ---------- low-level formatting helpers ------------------------------------------------------

# ---------- envelope/post field accessors -----------------------------------------------------

def get_posts(envelope: dict) -> list[dict]:
    posts = envelope.get("posts")
    return posts if isinstance(posts, list) else []


def get_handle(envelope: dict) -> str:
    """Handle is `_handle` (set by scrape_profile.py) or first post's author.publicIdentifier."""
    h = (envelope.get("_handle") or "").strip()
    if h:
        return h
    posts = get_posts(envelope)
    if posts:
        author = posts[0].get("author") or {}
        if isinstance(author, dict):
            v = author.get("publicIdentifier")
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
    return "unknown"


def get_profile_card(envelope: dict) -> dict:
    """Synthesize a profile-card dict from the FIRST post's embedded author. Returns {} if
    no posts. Most fields are LinkedIn-author-shape: name, publicIdentifier, info (=bio),
    website, websiteLabel, avatar (with .url), linkedinUrl."""
    posts = get_posts(envelope)
    if not posts:
        return {}
    author = posts[0].get("author") or {}
    if not isinstance(author, dict):
        return {}
    return author


def post_timestamp_iso(post: dict) -> str | None:
    pa = post.get("postedAt") or {}
    if isinstance(pa, dict):
        v = pa.get("date")
        if isinstance(v, str) and v.strip():
            return v.strip()
        ts = pa.get("timestamp")
        if isinstance(ts, (int, float)):
            ts_seconds = ts / 1000.0 if ts > 1e12 else ts
            try:
                return datetime.fromtimestamp(ts_seconds, tz=timezone.utc).isoformat(timespec="seconds")
            except (OSError, ValueError):
                return None
    return None


def post_engagement(post: dict) -> dict:
    e = post.get("engagement") or {}
    return e if isinstance(e, dict) else {}


def engagement_score(post: dict) -> int:
    """Likes + 2×comments + 3×shares — shares cost most for the author so weight them highest."""
    e = post_engagement(post)
    try:
        return int(e.get("likes") or 0) + 2 * int(e.get("comments") or 0) + 3 * int(e.get("shares") or 0)
    except (TypeError, ValueError):
        return 0


# ---------- timestamp preservation -----------------------------------------------------------



# ---------- caption/content dedup ------------------------------------------------------------

def dedupe_content_variants(posts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop the lower-engagement variants when the same post text appears multiple times.
    LinkedIn creators frequently re-publish identical posts weeks apart; this collapses them."""
    by_content: dict[str, list[dict]] = {}
    no_content: list[dict] = []
    for p in posts:
        c = (p.get("content") or "").strip()
        if not c:
            no_content.append(p)
            continue
        # Normalize whitespace for the dedupe key — a single trailing newline difference
        # shouldn't keep two otherwise-identical posts apart.
        key = " ".join(c.split())
        by_content.setdefault(key, []).append(p)

    kept: list[dict] = list(no_content)
    dropped: list[dict] = []
    for key, group in by_content.items():
        if len(group) == 1:
            kept.append(group[0])
            continue
        winner = max(
            group,
            key=lambda p: (
                engagement_score(p),
                post_timestamp_iso(p) or "",
            ),
        )
        kept.append(winner)
        for p in group:
            if p is not winner:
                dropped.append(p)
    return kept, dropped


# ---------- title / description derivation ---------------------------------------------------

def derive_title(content: str | None, post_date: str) -> str:
    """Title from post content: strip engagement-bait prefix + leading emojis, take first
    sentence, cap at TITLE_MAX_CHARS at a word boundary."""
    if not content:
        return f"{post_date} · Post"
    text = content.strip()
    text = _LEADING_EMOJI_RE.sub("", text).strip()
    text = _BAIT_PREFIX_RE.sub("", text).strip()
    # First sentence — split on . ! ? when followed by whitespace/end, OR on newline.
    text = re.split(r"[.!?](?=\s|$)|\n", text, maxsplit=1)[0].strip()
    text = _WS_COLLAPSE.sub(" ", text)
    if not text:
        return f"{post_date} · Post"
    text = text[0].upper() + text[1:]
    text = truncate_at_word(text, TITLE_MAX_CHARS)
    return text




def derive_description(post: dict) -> str:
    """Prefer the LLM-polished description (set by polish_post.py). Fall back to a raw
    truncation of the post content."""
    polished = (post.get("description_polished") or "").strip()
    if polished:
        return polished
    source = (post.get("content") or "").strip()
    if not source:
        return ""
    text = " ".join(source.split())
    if len(text) > DESCRIPTION_MAX_CHARS:
        text = text[:DESCRIPTION_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return text


# ---------- filename helpers -----------------------------------------------------------------

def extract_hhmm(timestamp_value) -> str | None:
    if not timestamp_value:
        return None
    s = str(timestamp_value)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.strftime("%H-%M")


def post_short_id(post: dict) -> str:
    """LinkedIn post IDs are long numerics; show the last 8 as a fallback filename suffix."""
    pid = post.get("id")
    if isinstance(pid, str) and pid:
        return pid[-8:]
    if isinstance(pid, int):
        return str(pid)[-8:]
    return "x"


def compute_post_stems(posts: list[dict], reserved: set[str]) -> list[str]:
    base_stems: list[str] = []
    for post in posts:
        ts_iso = post_timestamp_iso(post)
        post_date = fmt_date_iso(ts_iso)
        title = derive_title(post.get("content"), post_date)
        slug = slugify_for_filename(title)
        base = (
            f"{post_date} {slug}".strip()
            if slug
            else f"{post_date} {post_short_id(post)}"
        )
        base_stems.append(base)

    groups: dict[str, list[int]] = {}
    for i, base in enumerate(base_stems):
        groups.setdefault(base, []).append(i)

    suffix_for: dict[int, str] = {}
    for base, indices in groups.items():
        if len(indices) <= 1:
            continue
        hhmms = [extract_hhmm(post_timestamp_iso(posts[i])) for i in indices]
        if all(hhmms) and len(set(hhmms)) == len(hhmms):
            for i, hhmm in zip(indices, hhmms):
                suffix_for[i] = f"({hhmm})"
        else:
            for i in indices:
                suffix_for[i] = f"({post_short_id(posts[i])})"

    final: list[str] = []
    used = set(reserved)
    for i, base in enumerate(base_stems):
        suffix = suffix_for.get(i)
        stem = f"{base} {suffix}" if suffix else base
        if stem in used:
            stem = f"{stem} ({post_short_id(posts[i])})"
        used.add(stem)
        final.append(stem)
    return final


def overview_filename_stem(handle: str) -> str:
    return f"_{handle} overview"


def themes_filename_stem(handle: str) -> str:
    """Leading underscore so Obsidian sorts it next to the overview, after `_<handle> overview`
    in lexicographical order ("overview" < "themes")."""
    return f"_{handle} themes"


# ---------- frontmatter blocks ---------------------------------------------------------------

def frontmatter_overview(
    envelope: dict, profile: dict, scraped_at: datetime, created_str: str, modified_str: str
) -> str:
    handle = get_handle(envelope)
    bio = (profile.get("info") or "").strip()
    description_preview = " ".join(bio.split())[:DESCRIPTION_MAX_CHARS]
    full_name = (profile.get("name") or "").strip()
    scraped_str = fmt_property_ts(scraped_at)
    posts_loaded = len(get_posts(envelope))
    kind = (envelope.get("_kind") or "in").strip()

    lines = ["---"]
    lines.append(f"title: {yaml_quote(handle + ' overview')}")
    lines.append(f"description: {yaml_quote(description_preview)}")
    lines.append("status: draft")
    lines.append(f"handle: {yaml_quote(handle)}")
    if full_name:
        lines.append(f"full_name: {yaml_quote(full_name)}")
    lines.append(f"profile_kind: {yaml_quote(kind)}")  # 'in' (personal) or 'company'
    lines.append(f"posts_loaded: {fmt_int_yaml_str(posts_loaded)}")
    lines.append(f"scraped_at: {scraped_str}")
    lines.append(f"source: {SCRAPE_SOURCE}")
    lines.append(f"created: {created_str}")
    lines.append(f"modified: {modified_str}")
    lines.append("tags:")
    for tag in PROFILE_TAGS:
        lines.append(f"  - {tag}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def frontmatter_post(
    post: dict,
    envelope: dict,
    scraped_at: datetime,
    derived_title: str,
    created_str: str,
    modified_str: str,
) -> str:
    handle = get_handle(envelope)
    ts_iso = post_timestamp_iso(post)
    post_date = fmt_date_iso(ts_iso)
    pid = str(post.get("id") or "unknown")
    description_text = derive_description(post)
    scraped_str = fmt_property_ts(scraped_at)

    raw_content_tags = post.get("content_tags") or []
    sanitized = [sanitize_tag(t) for t in raw_content_tags if isinstance(t, str)]
    content_tags = [t for t in sanitized if t]
    seen: set[str] = set()
    tags: list[str] = []
    for t in list(POST_TAGS_BASE) + content_tags:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(t)

    lines = ["---"]
    lines.append(f"title: {yaml_quote(derived_title)}")
    lines.append(f"description: {yaml_quote(description_text)}")
    lines.append("status: draft")
    lines.append(f"handle: {yaml_quote(handle)}")
    lines.append(f"post_date: {post_date}")
    lines.append(f"scraped_at: {scraped_str}")
    lines.append(f"post_id: {yaml_quote(pid)}")
    lines.append(f"source: {SCRAPE_SOURCE}")
    lines.append(f"created: {created_str}")
    lines.append(f"modified: {modified_str}")
    lines.append("tags:")
    for tag in tags:
        lines.append(f"  - {tag}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def frontmatter_themes(
    envelope: dict,
    profile: dict,
    scraped_at: datetime,
    created_str: str,
    modified_str: str,
) -> str:
    handle = get_handle(envelope)
    full_name = (profile.get("name") or "").strip()
    model = (envelope.get("_themes_model") or "").strip()
    posts_used = envelope.get("_themes_posts_used") or len(get_posts(envelope))
    scraped_str = fmt_property_ts(scraped_at)

    lines = ["---"]
    lines.append(f"title: {yaml_quote(handle + ' — themes')}")
    lines.append(f"description: {yaml_quote('Strategy brief synthesized from ' + str(posts_used) + ' recent LinkedIn posts.')}")
    lines.append("status: draft")
    lines.append(f"handle: {yaml_quote(handle)}")
    if full_name:
        lines.append(f"full_name: {yaml_quote(full_name)}")
    lines.append(f"posts_used: {fmt_int_yaml_str(posts_used)}")
    if model:
        lines.append(f"themes_model: {yaml_quote(model)}")
    lines.append(f"scraped_at: {scraped_str}")
    lines.append(f"source: {SCRAPE_SOURCE}")
    lines.append(f"created: {created_str}")
    lines.append(f"modified: {modified_str}")
    lines.append("tags:")
    for tag in THEMES_TAGS:
        lines.append(f"  - {tag}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def frontmatter_summary(
    count: int, scraped_at: datetime, created_str: str, modified_str: str
) -> str:
    scraped_str = fmt_property_ts(scraped_at)
    lines = ["---"]
    lines.append(f"title: {yaml_quote(f'LinkedIn batch summary ({count} profiles)')}")
    lines.append(f"description: {yaml_quote(f'Comparative table for {count} LinkedIn profiles scraped on {scraped_str}')}")
    lines.append(f"created: {created_str}")
    lines.append(f"modified: {modified_str}")
    lines.append("status: draft")
    lines.append("tags:")
    for tag in SUMMARY_TAGS:
        lines.append(f"  - {tag}")
    lines.append(f"scraped_at: {scraped_str}")
    lines.append(f"source: {SCRAPE_SOURCE}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


# ---------- body renderers --------------------------------------------------------------------

# Reaction-type → glyph mapping for the byline. LinkedIn reaction types (harvestapi names):
# LIKE, PRAISE, APPRECIATION, EMPATHY, INTEREST, ENTERTAINMENT, MAYBE. The funny reaction
# comes back as ENTERTAINMENT (not FUNNY) from this actor. An unmapped type falls back to its
# capitalized name (never a bare separator) so the byline can't render a glyph-less "· · N".
REACTION_GLYPH = {
    "LIKE": "👍",
    "EMPATHY": "🫶",
    "APPRECIATION": "👏",
    "INTEREST": "💡",
    "PRAISE": "🎉",
    "ENTERTAINMENT": "😂",
    "FUNNY": "😂",
    "MAYBE": "🤔",
}


def reaction_breakdown_line(post: dict) -> str:
    """One-line reaction-type distribution: '👍 1.225 · 🫶 143 · 👏 41 · 💡 19 · 🎉 18'."""
    e = post_engagement(post)
    reactions = e.get("reactions") or []
    if not isinstance(reactions, list) or not reactions:
        return ""
    parts: list[str] = []
    for r in reactions:
        if not isinstance(r, dict):
            continue
        rtype = (r.get("type") or "").upper()
        count = r.get("count")
        if count is None:
            continue
        glyph = REACTION_GLYPH.get(rtype)
        if glyph:
            parts.append(f"{glyph} {fmt_int(count)}")
        elif rtype:
            # Unknown type: show its name rather than a bare separator (avoids "· · N").
            parts.append(f"{rtype.title()} {fmt_int(count)}")
    return " · ".join(parts)


def render_overview_body(
    envelope: dict,
    profile: dict,
    post_files: list[tuple[dict, Path]],
    dropped: list[dict] | None = None,
) -> str:
    handle = get_handle(envelope)
    full_name = (profile.get("name") or "").strip()
    bio = (profile.get("info") or "").strip()
    website = (profile.get("website") or "").strip()
    website_label = (profile.get("websiteLabel") or "").strip() or website
    avatar = profile.get("avatar") or {}
    avatar_url = avatar.get("url") if isinstance(avatar, dict) else ""
    linkedin_url = (profile.get("linkedinUrl") or "").split("?")[0]
    if not linkedin_url:
        kind = envelope.get("_kind") or "in"
        linkedin_url = f"https://www.linkedin.com/{kind}/{handle}/"

    lines: list[str] = []
    lines.append(f"# {handle}")
    if full_name:
        lines.append("")
        lines.append(f"**{full_name}**")
    lines.append("")
    nav_parts = [f"[Open profile]({linkedin_url})"]
    if avatar_url:
        nav_parts.append(f"[Profile picture]({avatar_url})")
    lines.append(" · ".join(nav_parts))
    lines.append("")

    if bio:
        lines.append("## Bio")
        lines.append("")
        lines.append("> " + bio.replace("\n", "\n> "))
        lines.append("")

    if website:
        lines.append(f"**External link:** [{website_label}]({website})")
        lines.append("")

    # Stats — without a profile-stats actor we don't have followers, so this is just
    # post-level counts. The full stats table comes once we wire in a chained call.
    lines.append("## Stats")
    lines.append("")
    lines.append("| # | Metric | Value |")
    lines.append("|---|---|---|")
    lines.append(f"| 1 | Posts loaded | {fmt_int(len(post_files))} |")
    if post_files:
        total_likes = sum(int((post_engagement(p).get("likes") or 0)) for p, _ in post_files)
        total_comments = sum(int((post_engagement(p).get("comments") or 0)) for p, _ in post_files)
        total_shares = sum(int((post_engagement(p).get("shares") or 0)) for p, _ in post_files)
        n = len(post_files)
        lines.append(f"| 2 | Avg. likes / post | {fmt_int(total_likes // n)} |")
        lines.append(f"| 3 | Avg. comments / post | {fmt_int(total_comments // n)} |")
        lines.append(f"| 4 | Avg. shares / post | {fmt_int(total_shares // n)} |")
    lines.append("")

    # Top 3 by engagement score
    ranked = sorted(post_files, key=lambda pf: engagement_score(pf[0]), reverse=True)
    top = ranked[:3]
    if top:
        lines.append("## Top 3 posts (likes + 2×comments + 3×shares)")
        lines.append("")
        lines.append("| # | Date | Likes | Comments | Shares | Score | Preview | Note |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for i, (post, path) in enumerate(top, start=1):
            e = post_engagement(post)
            lines.append(
                "| {idx} | {date} | {likes} | {comments} | {shares} | {score} | {prev} | [{name}]({link}) |".format(
                    idx=i,
                    date=fmt_date_iso(post_timestamp_iso(post)),
                    likes=fmt_int(e.get("likes")),
                    comments=fmt_int(e.get("comments")),
                    shares=fmt_int(e.get("shares")),
                    score=fmt_int(engagement_score(post)),
                    prev=md_escape_pipe(content_preview(post.get("content"))),
                    name=path.stem,
                    link=url_encode_link(path.name),
                )
            )
        lines.append("")

    # All posts
    lines.append(f"## All loaded posts ({len(post_files)})")
    lines.append("")
    lines.append("| # | Date | Likes | Comments | Shares | Preview | Note |")
    lines.append("|---|---|---|---|---|---|---|")
    chronological = sorted(
        post_files, key=lambda pf: post_timestamp_iso(pf[0]) or "", reverse=True
    )
    for i, (post, path) in enumerate(chronological, start=1):
        e = post_engagement(post)
        lines.append(
            "| {idx} | {date} | {likes} | {comments} | {shares} | {prev} | [{name}]({link}) |".format(
                idx=i,
                date=fmt_date_iso(post_timestamp_iso(post)),
                likes=fmt_int(e.get("likes")),
                comments=fmt_int(e.get("comments")),
                shares=fmt_int(e.get("shares")),
                prev=md_escape_pipe(content_preview(post.get("content"))),
                name=path.stem,
                link=url_encode_link(path.name),
            )
        )
    lines.append("")

    if dropped:
        kept_by_content: dict[str, tuple[dict, Path]] = {}
        for post, path in post_files:
            cap = " ".join((post.get("content") or "").split()).strip()
            if cap and cap not in kept_by_content:
                kept_by_content[cap] = (post, path)
        lines.append(f"## Removed duplicates ({len(dropped)})")
        lines.append("")
        lines.append(
            "_Posts whose content matched a higher-engagement variant. Highest "
            "(likes + 2×comments + 3×shares) wins; the originals stay in the JSON._"
        )
        lines.append("")
        lines.append("| # | Date | Likes | Comments | Shares | Score | Preview | Replaced by |")
        lines.append("|---|---|---|---|---|---|---|---|")
        dropped_chronological = sorted(
            dropped, key=lambda p: post_timestamp_iso(p) or "", reverse=True
        )
        for i, post in enumerate(dropped_chronological, start=1):
            cap = " ".join((post.get("content") or "").split()).strip()
            winner = kept_by_content.get(cap)
            if winner is not None:
                _, winner_path = winner
                replaced_by = f"[{winner_path.stem}]({url_encode_link(winner_path.name)})"
            else:
                replaced_by = "—"
            e = post_engagement(post)
            lines.append(
                "| {idx} | {date} | {likes} | {comments} | {shares} | {score} | {prev} | {replaced} |".format(
                    idx=i,
                    date=fmt_date_iso(post_timestamp_iso(post)),
                    likes=fmt_int(e.get("likes")),
                    comments=fmt_int(e.get("comments")),
                    shares=fmt_int(e.get("shares")),
                    score=fmt_int(engagement_score(post)),
                    prev=md_escape_pipe(content_preview(post.get("content"))),
                    replaced=replaced_by,
                )
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"_Scraped via Apify `{SCRAPE_SOURCE}`. "
        f"Rendered: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_"
    )
    lines.append("")
    return "\n".join(lines)


def render_post_body(post: dict, envelope: dict, overview_path: Path) -> str:
    handle = get_handle(envelope)
    ts_iso = post_timestamp_iso(post)
    post_date = fmt_date_iso(ts_iso)
    content = post.get("content") or ""
    e = post_engagement(post)
    likes = e.get("likes")
    comments = e.get("comments")
    shares = e.get("shares")

    # Resolve the canonical post URL — prefer linkedinUrl, fall back to socialContent.shareUrl.
    post_url = (post.get("linkedinUrl") or "").strip()
    if not post_url:
        sc = post.get("socialContent") or {}
        if isinstance(sc, dict):
            post_url = (sc.get("shareUrl") or "").strip()

    polished_content = (post.get("content_polished") or "").strip()
    polished_content_model = post.get("content_polished_model") or ""

    derived_title = derive_title(content, post_date)

    lines: list[str] = []
    lines.append(f"# {derived_title}")
    lines.append("")

    byline_parts = [handle, post_date]
    if likes is not None:
        byline_parts.append(f"{fmt_int(likes)} ❤")
    if comments is not None:
        byline_parts.append(f"{fmt_int(comments)} 💬")
    if shares is not None:
        byline_parts.append(f"{fmt_int(shares)} ↗")
    if post_url:
        byline_parts.append(f"[Open]({post_url})")
    byline_parts.append(f"[← Profile]({url_encode_link(overview_path.name)})")
    lines.append("_" + " · ".join(byline_parts) + "_")
    lines.append("")

    breakdown = reaction_breakdown_line(post)
    if breakdown:
        lines.append(f"_Reactions: {breakdown}_")
        lines.append("")

    # ## Content — polished briefing if available, else raw post text as a blockquote.
    lines.append("## Content")
    lines.append("")
    if polished_content:
        # Provider-neutral attribution: the polish step may run on Anthropic OR a local
        # Ollama model — the model id (`<provider>:<model>`) is the honest byline.
        lines.append(
            f"_Neutral briefing rewritten by `{polished_content_model}` "
            f"from the original post._"
        )
        lines.append("")
        lines.append(polished_content.rstrip())
        lines.append("")
    elif content.strip():
        lines.append(
            "_No LLM polish available — original post text. Set `ANTHROPIC_API_KEY` "
            "and run `polish_post.py` for a neutral briefing._"
        )
        lines.append("")
        for line in content.split("\n"):
            lines.append(f"> {line}" if line else ">")
        lines.append("")
    else:
        lines.append("_Content nicht erstellt — Post-Text fehlt._")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"_Scraped via Apify `{SCRAPE_SOURCE}`. "
        f"Rendered: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_"
    )
    lines.append("")
    return "\n".join(lines)


def render_summary_body(entries: list[tuple[dict, dict, Path]]) -> str:
    """entries: (envelope, profile_card, overview_path) per profile."""
    lines: list[str] = []
    lines.append("# LinkedIn batch summary")
    lines.append("")
    lines.append(
        f"_Scraped: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_ · "
        f"_{len(entries)} profile(s)_"
    )
    lines.append("")
    lines.append(
        "| # | Profile | Posts loaded | Avg. likes | Avg. comments | Avg. shares | Top score |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for i, (envelope, profile, overview_path) in enumerate(entries, start=1):
        handle = get_handle(envelope)
        posts = get_posts(envelope)
        likes_list = [int(post_engagement(p).get("likes") or 0) for p in posts]
        comments_list = [int(post_engagement(p).get("comments") or 0) for p in posts]
        shares_list = [int(post_engagement(p).get("shares") or 0) for p in posts]
        n = len(posts) or 1
        avg_likes = sum(likes_list) // n if posts else 0
        avg_comments = sum(comments_list) // n if posts else 0
        avg_shares = sum(shares_list) // n if posts else 0
        top_score = max((engagement_score(p) for p in posts), default=0)
        rel_link = f"{overview_path.parent.name}/{overview_path.name}"
        lines.append(
            "| {idx} | [{u}]({link}) | {pl} | {al} | {ac} | {as_} | {ts} |".format(
                idx=i,
                u=handle,
                link=url_encode_link(rel_link),
                pl=len(posts),
                al=fmt_int(avg_likes) if posts else "—",
                ac=fmt_int(avg_comments) if posts else "—",
                as_=fmt_int(avg_shares) if posts else "—",
                ts=fmt_int(top_score) if posts else "—",
            )
        )
    lines.append("")
    return "\n".join(lines)


# ---------- top-level rendering --------------------------------------------------------------

def render_themes_body(envelope: dict, profile: dict) -> str:
    """Build the body of `_<handle> themes.md`. Wraps the synthesized Markdown body in an H1
    and a one-line provenance subtitle."""
    handle = get_handle(envelope)
    full_name = (profile.get("name") or "").strip()
    body_md = (envelope.get("_themes_md") or "").strip()
    model = (envelope.get("_themes_model") or "").strip()
    themes_at = (envelope.get("_themes_at") or "").strip()
    posts_used = envelope.get("_themes_posts_used") or len(get_posts(envelope))

    lines: list[str] = []
    title_suffix = f"{full_name} — themes" if full_name else f"{handle} — themes"
    lines.append(f"# {title_suffix}")
    lines.append("")
    provenance_bits = [f"Synthesized from {posts_used} post(s)"]
    if model:
        provenance_bits.append(f"`{model}`")
    if themes_at:
        provenance_bits.append(themes_at[:19].replace("T", " "))
    lines.append("_" + " · ".join(provenance_bits) + "_")
    lines.append("")
    lines.append(body_md)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        f"_Scraped via Apify `{SCRAPE_SOURCE}`. "
        f"Rendered: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_"
    )
    lines.append("")
    return "\n".join(lines)


def render_profile(input_path: Path) -> tuple[Path, list[Path], list[Path]]:
    envelope = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict) or not isinstance(envelope.get("posts"), list):
        raise ValueError(f"Not a LinkedIn overview JSON: {input_path}")

    scraped_at = derive_scrape_timestamp(input_path, envelope)
    profile_dir = input_path.parent
    profile_dir.mkdir(parents=True, exist_ok=True)
    handle = get_handle(envelope)
    profile_card = get_profile_card(envelope)

    raw_posts = get_posts(envelope)
    posts, dropped = dedupe_content_variants(raw_posts)
    if dropped:
        sys.stderr.write(
            f"INFO: dropped {len(dropped)} duplicate-content post(s) with lower engagement\n"
        )

    overview_stem = overview_filename_stem(handle)
    overview_path = profile_dir / f"{overview_stem}.md"

    stems = compute_post_stems(posts, reserved={overview_stem})
    post_files: list[tuple[dict, Path]] = [
        (post, profile_dir / f"{stem}.md") for post, stem in zip(posts, stems)
    ]

    for post, path in post_files:
        ts_iso = post_timestamp_iso(post)
        post_date = fmt_date_iso(ts_iso)
        derived_title = derive_title(post.get("content"), post_date)
        body = render_post_body(post, envelope, overview_path)
        created_str, modified_str = resolve_timestamps(path)
        fm = frontmatter_post(post, envelope, scraped_at, derived_title, created_str, modified_str)
        path.write_text(fm + body, encoding="utf-8")

    overview_body = render_overview_body(envelope, profile_card, post_files, dropped=dropped)
    overview_created, overview_modified = resolve_timestamps(overview_path)
    overview_fm = frontmatter_overview(envelope, profile_card, scraped_at, overview_created, overview_modified)
    overview_path.write_text(overview_fm + overview_body, encoding="utf-8")

    # Themes file — only when themes_profile.py has populated `_themes_md` in the envelope.
    themes_paths: set[Path] = set()
    if (envelope.get("_themes_md") or "").strip():
        themes_path = profile_dir / f"{themes_filename_stem(handle)}.md"
        themes_body = render_themes_body(envelope, profile_card)
        themes_created, themes_modified = resolve_timestamps(themes_path)
        themes_fm = frontmatter_themes(envelope, profile_card, scraped_at, themes_created, themes_modified)
        themes_path.write_text(themes_fm + themes_body, encoding="utf-8")
        themes_paths.add(themes_path)

    keep_paths = {p for _, p in post_files}
    overview_paths = {overview_path} | themes_paths
    deleted = cleanup_old_post_files(profile_dir, keep_paths, overview_paths, SCRAPE_SOURCE)

    return overview_path, [p for _, p in post_files], deleted


def render_batch(platform_dir: Path) -> tuple[list[Path], Path | None, list[Path]]:
    if not platform_dir.is_dir():
        raise ValueError(f"Not a directory: {platform_dir}")

    entries: list[tuple[dict, dict, Path]] = []
    rendered_overviews: list[Path] = []
    all_deleted: list[Path] = []
    latest_scrape: datetime | None = None

    for profile_dir in sorted(platform_dir.iterdir()):
        if not profile_dir.is_dir():
            continue
        candidates = sorted(profile_dir.glob("_*overview.json"))
        if not candidates:
            candidates = sorted(profile_dir.glob("*overview.json"))
            if not candidates:
                continue
        overview_json = candidates[-1]
        try:
            envelope = json.loads(overview_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            sys.stderr.write(f"WARN: skipping unparseable JSON: {overview_json}\n")
            continue
        if not isinstance(envelope, dict) or not isinstance(envelope.get("posts"), list):
            continue

        overview_md, _, deleted = render_profile(overview_json)
        rendered_overviews.append(overview_md)
        all_deleted.extend(deleted)
        entries.append((envelope, get_profile_card(envelope), overview_md))

        ts = derive_scrape_timestamp(overview_json, envelope)
        if latest_scrape is None or ts > latest_scrape:
            latest_scrape = ts

    if len(entries) < 2:
        return rendered_overviews, None, all_deleted

    summary_ts = latest_scrape or datetime.now(timezone.utc)
    summary_path = platform_dir / f"{summary_ts.strftime('%Y-%m-%d')} linkedin batch summary.md"
    summary_body = render_summary_body(entries)
    summary_created, summary_modified = resolve_timestamps(summary_path)
    summary_fm = frontmatter_summary(len(entries), summary_ts, summary_created, summary_modified)
    summary_path.write_text(summary_fm + summary_body, encoding="utf-8")
    return rendered_overviews, summary_path, all_deleted


def main() -> int:
    args = parse_args()

    if args.input is not None:
        overview_path, post_paths, deleted = render_profile(args.input)
        print(
            json.dumps(
                {
                    "mode": "single",
                    "overview": str(overview_path),
                    "posts": [str(p) for p in post_paths],
                    "deleted_legacy": [str(p) for p in deleted],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    overviews, summary, deleted = render_batch(args.batch_dir)
    print(
        json.dumps(
            {
                "mode": "batch",
                "overviews": [str(p) for p in overviews],
                "summary": str(summary) if summary else None,
                "deleted_legacy": [str(p) for p in deleted],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
