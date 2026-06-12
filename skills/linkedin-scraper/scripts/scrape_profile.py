#!/usr/bin/env python3
"""Scrape LinkedIn profiles + their latest posts via Apify actor `harvestapi/linkedin-profile-posts`.

Reads APIFY_API_TOKEN from the environment, accepts one or more handles/URLs, calls the Apify
actor synchronously, and writes one raw.json per profile.

Stdout: a JSON summary with succeeded/failed lists for downstream consumers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _social_common.tokens import get_apify_token
from _social_common.folder_rename import rename_folder_with_essence, FOLDER_ESSENCE_SEPARATOR
from _social_common.timestamps import fmt_batch_log_ts
try:
    import requests
except ImportError:
    sys.stderr.write(
        "ERROR: 'requests' is not installed. Run:\n"
        "  pip install -r " + str(Path(__file__).parent.parent / "requirements.txt") + "\n"
    )
    sys.exit(2)

APIFY_ENDPOINT = (
    "https://api.apify.com/v2/acts/harvestapi~linkedin-profile-posts/run-sync-get-dataset-items"
)
DEFAULT_POSTS_LIMIT = 25
DEFAULT_TIMEOUT_SECONDS = 600  # LinkedIn scrape can be slower than IG; harvestapi runs longer
HANDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-_.]{2,100}$")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--usernames",
        help="Comma-separated list of handles or LinkedIn URLs (e.g. 'example-creator,@another-example').",
    )
    src.add_argument(
        "--input-file",
        type=Path,
        help="Path to a text file with one handle/URL per line.",
    )
    p.add_argument(
        "--posts-limit",
        type=int,
        default=DEFAULT_POSTS_LIMIT,
        help=f"Max posts per profile (default: {DEFAULT_POSTS_LIMIT}; 0 = all available).",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Output base directory. Default: $OBSIDIAN_VAULT_PATH/LinkedIn Scraper "
            "(fails fast with exit 3 if neither --out-dir nor OBSIDIAN_VAULT_PATH is set)."
        ),
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    p.add_argument(
        "--polish",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "After scraping, run polish_post.py on each profile's JSON to add neutral "
            "Markdown briefings (description_polished, content_polished, content_tags). "
            "Default ON; turns into a no-op if ANTHROPIC_API_KEY is missing."
        ),
    )
    p.add_argument(
        "--themes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "After polish, run themes_profile.py to synthesize a one-page strategy brief "
            "(top themes, hooks, tonality, audience cues, posting strategy) via Sonnet 4.6. "
            "Default ON; no-op if ANTHROPIC_API_KEY missing."
        ),
    )
    p.add_argument(
        "--essence",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "After scraping, run essence_profile.py and rename the folder to "
            "`<handle> — <essence>`. Default ON; no-op if ANTHROPIC_API_KEY missing."
        ),
    )
    return p.parse_args()


def normalize_handle(raw: str) -> tuple[str, str] | None:
    """Accept '@user', 'user', 'https://linkedin.com/in/user/', '.../company/foo/', etc.

    Returns (kind, slug) where kind is 'in' (personal) or 'company', or None if unparseable.
    Slug is lowercased; LinkedIn slugs are case-insensitive.
    """
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None

    kind = "in"
    m = re.match(
        r"^(?:https?://)?(?:[a-z]+\.)?linkedin\.com/(in|company|school|pub|showcase)/([^/?#]+)/?",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        url_kind = m.group(1).lower()
        # `pub` is a legacy redirect target for `in` → personal. `company` and `showcase`
        # (a company's product sub-page) keep their own path segment; everything else is personal.
        kind = url_kind if url_kind in ("company", "showcase") else "in"
        s = m.group(2)
    else:
        s = s.lstrip("@").strip("/")

    s = s.lower()
    if not HANDLE_RE.match(s):
        return None
    return (kind, s)


def collect_handles(args: argparse.Namespace) -> tuple[list[tuple[str, str]], list[str]]:
    """Returns (valid [(kind, slug), ...], rejected_raw)."""
    raw_items: list[str] = []
    if args.usernames:
        raw_items = [x for x in re.split(r"[,;\s\n]+", args.usernames) if x.strip()]
    else:
        text = args.input_file.read_text(encoding="utf-8")
        raw_items = [x for x in re.split(r"[,;\s\n]+", text) if x.strip()]

    seen: set[tuple[str, str]] = set()
    valid: list[tuple[str, str]] = []
    rejected: list[str] = []
    for raw in raw_items:
        norm = normalize_handle(raw)
        if norm is None:
            rejected.append(raw)
            continue
        if norm in seen:
            continue
        seen.add(norm)
        valid.append(norm)
    return valid, rejected



def build_target_url(kind: str, slug: str) -> str:
    return f"https://www.linkedin.com/{kind}/{slug}/"


def call_apify(handles: list[tuple[str, str]], posts_limit: int, token: str, timeout: int) -> list[dict]:
    """Call the Apify harvestapi/linkedin-profile-posts actor and return dataset items.

    The actor returns one item per post. Each item carries author/profile metadata so we can
    group items back to their profile after the call.
    """
    payload = {
        "targetUrls": [build_target_url(k, s) for (k, s) in handles],
        "maxPosts": posts_limit,
        # Cost-control defaults: reactions/comments bodies stay off — the per-post counts come
        # back regardless. Opt-in flags will land in a follow-up session.
        "scrapeReactions": False,
        "scrapeComments": False,
    }
    resp = requests.post(
        APIFY_ENDPOINT,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code == 401:
        sys.stderr.write("ERROR: Apify rejected the token (401). Verify APIFY_API_TOKEN.\n")
        sys.exit(3)
    if resp.status_code == 402:
        sys.stderr.write(
            "ERROR: Apify returned 402 (payment required). "
            "Check your credit balance at https://console.apify.com/billing.\n"
        )
        sys.exit(3)
    if resp.status_code == 403:
        body = resp.text[:600] if resp.text else "(empty body)"
        sys.stderr.write(
            "ERROR: Apify returned 403 Forbidden — token authenticated but lacks permissions.\n"
            "Most common cause: the token was created without 'Allow this token to access "
            "default run storages' enabled, OR the Actor-specific permission for "
            "harvestapi/linkedin-profile-posts is missing.\n"
            f"Apify response body: {body}\n"
        )
        sys.exit(3)
    if not resp.ok:
        body = resp.text[:600] if resp.text else "(empty body)"
        sys.stderr.write(
            f"ERROR: Apify returned HTTP {resp.status_code}.\n"
            f"Response body: {body}\n"
        )
        sys.exit(3)
    data = resp.json()
    if not isinstance(data, list):
        sys.stderr.write(f"ERROR: Unexpected Apify response shape: {type(data).__name__}\n")
        sys.exit(3)
    return data


def slug_from_item(item: dict) -> str | None:
    """Best-effort: derive the profile slug for an Apify item.

    harvestapi items include the source URL via `inputUrl` / `targetUrl` / `profileUrl` /
    `author.linkedinUrl` etc. We try a few common shapes; whichever wins, we strip the
    `linkedin.com/{kind}/<slug>` part and return the slug. Returns None if no shape matches.
    """
    candidates: list[str] = []
    for key in ("inputUrl", "targetUrl", "profileUrl", "url"):
        val = item.get(key)
        if isinstance(val, str):
            candidates.append(val)
    author = item.get("author") or {}
    if isinstance(author, dict):
        for key in ("linkedinUrl", "url", "publicIdentifier", "username"):
            val = author.get(key)
            if isinstance(val, str):
                candidates.append(val)
    for cand in candidates:
        m = re.search(
            r"linkedin\.com/(?:in|company|school|pub|showcase)/([^/?#]+)/?",
            cand,
            flags=re.IGNORECASE,
        )
        if m:
            return m.group(1).lower()
        # Bare slug? (publicIdentifier sometimes is)
        if HANDLE_RE.match(cand) and "/" not in cand:
            return cand.lower()
    return None


def group_items_by_handle(
    items: list[dict],
    requested: list[tuple[str, str]],
) -> dict[str, list[dict]]:
    """Group Apify items back to their requested handle.

    Returns dict mapping slug -> list of items. Items that we cannot map go into a synthetic
    `__unmatched__` bucket and are surfaced in the run summary.
    """
    by_slug: dict[str, list[dict]] = {slug: [] for (_, slug) in requested}
    by_slug["__unmatched__"] = []
    for item in items:
        slug = slug_from_item(item)
        if slug is None or slug not in by_slug:
            by_slug["__unmatched__"].append(item)
        else:
            by_slug[slug].append(item)
    return by_slug




def run_chained_script(script_name: str, raw_path: Path) -> dict:
    """Run a sibling script with --input <raw_path>; return {ok, exit_code, stdout, stderr_tail}."""
    script_path = Path(__file__).parent / script_name
    try:
        proc = subprocess.run(
            [sys.executable, str(script_path), "--input", str(raw_path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        sys.stderr.write(f"WARN: could not run {script_name}: {exc}\n")
        return {"ok": False, "exit_code": -1, "reason": str(exc)}
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    parsed: dict = {}
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = {"raw_stdout": proc.stdout[:500]}
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode, **parsed}


BATCH_LOG_NAME = "LinkedIn scraper batch.md"
BATCH_LOG_HEADER = """\
---
title: "LinkedIn scraper batch log"
description: "Run-by-run history of every LinkedIn scrape (newest entry at the bottom)."
status: active
tags:
  - LinkedIn
  - Inbox
  - Log
source: harvestapi/linkedin-profile-posts
---

# LinkedIn scraper batch log

Each scrape appends a section below. Append-only — most recent run lives at the bottom.

"""


def append_run_to_batch_log(
    out_dir: Path,
    *,
    scraped_at: datetime,
    requested: list[tuple[str, str]],
    succeeded: list[tuple[str, str, int]],  # (kind, slug, post_count)
    failed: list[dict],
    posts_limit: int,
    unmatched_count: int,
) -> None:
    """Append one run section to <out-dir>/LinkedIn scraper batch.md."""
    log_path = out_dir / BATCH_LOG_NAME
    if not log_path.exists():
        log_path.write_text(BATCH_LOG_HEADER, encoding="utf-8")

    ts = fmt_batch_log_ts(scraped_at)
    succeeded_lines: list[str] = []
    for kind, slug, count in succeeded:
        target = f"{slug}/_{slug} overview.json"
        encoded = target.replace(" ", "%20")
        marker = "" if kind == "in" else f" ({kind})"
        succeeded_lines.append(f"[{slug}{marker}]({encoded}) — {count} post(s)")

    section_lines: list[str] = []
    section_lines.append(f"## {ts} — {len(succeeded)} profile(s) scraped")
    section_lines.append("")
    requested_str = ", ".join(
        f"{slug}" + ("" if k == "in" else f" ({k})") for (k, slug) in requested
    )
    section_lines.append(f"- **Requested:** {requested_str or '—'}")
    if succeeded_lines:
        section_lines.append(f"- **Succeeded:** {', '.join(succeeded_lines)}")
    if failed:
        formatted = ", ".join(
            f"{f.get('slug','?')} ({f.get('reason','?')})" for f in failed
        )
        section_lines.append(f"- **Failed:** {formatted}")
    section_lines.append(f"- **Posts-limit (requested):** {posts_limit}")
    if unmatched_count:
        section_lines.append(
            f"- **Unmatched items:** {unmatched_count} (see `__unmatched__.json`)"
        )
    section_lines.append("")

    with log_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(section_lines))
        f.write("\n")


def main() -> int:
    args = parse_args()
    handles, rejected = collect_handles(args)

    if rejected:
        sys.stderr.write(f"WARN: ignoring {len(rejected)} unparseable input(s): {rejected}\n")
    if not handles:
        sys.stderr.write("ERROR: no valid handles to scrape.\n")
        return 2

    token = get_apify_token()

    out_dir = args.out_dir
    if out_dir is None:
        vault = os.environ.get("OBSIDIAN_VAULT_PATH")
        if not vault:
            sys.stderr.write(
                "ERROR: --out-dir not provided and OBSIDIAN_VAULT_PATH is not set.\n"
                "Either pass --out-dir <path> or export OBSIDIAN_VAULT_PATH=/path/to/vault.\n"
            )
            return 3
        out_dir = Path(vault) / "LinkedIn Scraper"
    out_dir.mkdir(parents=True, exist_ok=True)

    sys.stderr.write(
        f"INFO: scraping {len(handles)} profile(s) with up to {args.posts_limit} posts each. "
        f"Out: {out_dir}\n"
    )

    items = call_apify(handles, args.posts_limit, token, args.timeout)
    sys.stderr.write(f"INFO: Apify returned {len(items)} item(s) total.\n")
    by_slug = group_items_by_handle(items, handles)

    succeeded: list[tuple[str, str, int]] = []
    failed: list[dict] = []
    written: list[str] = []
    polish_results: list[dict] = []
    themes_results: list[dict] = []
    essence_results: list[dict] = []
    scraped_at = datetime.now(timezone.utc)

    for kind, slug in handles:
        slug_items = by_slug.get(slug, [])
        if not slug_items:
            failed.append({"slug": slug, "kind": kind, "reason": "no_data_returned"})
            continue

        profile_dir = out_dir / slug
        profile_dir.mkdir(parents=True, exist_ok=True)
        raw_path = profile_dir / f"_{slug} overview.json"

        # Wrap items in a profile-level envelope so the JSON has run metadata at top level
        # and the post list is addressable as a single field. The exact Apify item shape is
        # preserved verbatim under `posts` — we don't transform it.
        envelope = {
            "_scraped_at": scraped_at.isoformat(timespec="seconds"),
            "_source": "apify/harvestapi/linkedin-profile-posts",
            "_handle": slug,
            "_kind": kind,
            "_target_url": build_target_url(kind, slug),
            "_posts_limit_requested": args.posts_limit,
            "posts_count": len(slug_items),
            "posts": slug_items,
        }
        raw_path.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Chained polish (per-post Haiku call). No-op if ANTHROPIC_API_KEY missing.
        if args.polish:
            sys.stderr.write(f"INFO: polishing posts for {slug} ...\n")
            polish_results.append({"slug": slug, **run_chained_script("polish_post.py", raw_path)})

        # Chained themes synthesis (one Sonnet call per profile). Runs AFTER polish so it has
        # the polished briefings as input — better signal than raw post text.
        if args.themes:
            sys.stderr.write(f"INFO: synthesizing themes for {slug} ...\n")
            themes_results.append({"slug": slug, **run_chained_script("themes_profile.py", raw_path)})

        # Chained essence (one Haiku call per profile, locks `_essence` in JSON).
        if args.essence:
            sys.stderr.write(f"INFO: generating essence for {slug} ...\n")
            essence_results.append({"slug": slug, **run_chained_script("essence_profile.py", raw_path)})

        # Folder-rename to `<handle> — <essence>` once essence is set.
        if args.essence:
            raw_path = rename_folder_with_essence(profile_dir, raw_path, slug)

        succeeded.append((kind, slug, len(slug_items)))
        written.append(str(raw_path))

    unmatched = by_slug.get("__unmatched__", [])
    if unmatched:
        unmatched_path = out_dir / "__unmatched__.json"
        unmatched_path.write_text(
            json.dumps(
                {
                    "_scraped_at": scraped_at.isoformat(timespec="seconds"),
                    "count": len(unmatched),
                    "items": unmatched,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        other_slugs = sorted({s for s in (slug_from_item(it) for it in unmatched) if s})
        origin = f" (from: {', '.join(other_slugs)})" if other_slugs else ""
        sys.stderr.write(
            f"WARN: {len(unmatched)} item(s) could not be mapped to a requested handle"
            f"{origin} — these belong to a different LinkedIn entity (e.g. a company's "
            f"/showcase/ sub-page). Scrape that handle directly to capture them. "
            f"Saved to {unmatched_path} for inspection.\n"
        )

    append_run_to_batch_log(
        out_dir,
        scraped_at=scraped_at,
        requested=handles,
        succeeded=succeeded,
        failed=failed,
        posts_limit=args.posts_limit,
        unmatched_count=len(unmatched),
    )

    summary = {
        "scraped_at": scraped_at.isoformat(timespec="seconds"),
        "out_dir": str(out_dir),
        "requested": [{"kind": k, "slug": s} for (k, s) in handles],
        "rejected_input": rejected,
        "succeeded": [
            {"kind": k, "slug": s, "posts": c} for (k, s, c) in succeeded
        ],
        "failed": failed,
        "files": written,
        "posts_limit": args.posts_limit,
        "unmatched_count": len(unmatched),
        "polish_results": polish_results if args.polish else None,
        "themes_results": themes_results if args.themes else None,
        "essence_results": essence_results if args.essence else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
