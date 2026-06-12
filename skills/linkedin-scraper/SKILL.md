---
name: linkedin-scraper
description: >
  Scrape LinkedIn profiles and their latest posts via the Apify
  `harvestapi/linkedin-profile-posts` actor, polish each post into a neutral Markdown briefing
  via Anthropic Haiku, and render everything as Obsidian-friendly notes. Use this skill
  whenever the user asks to scrape, fetch, pull, ziehen, analyze, audit, monitor, or research
  one or more LinkedIn accounts — including post text, engagement counts (reactions, comments,
  reposts), reaction-type breakdowns, or competitive/creator research. Strong triggers:
  "scrape LinkedIn", "LinkedIn-Profil ziehen", "analyse handle on LinkedIn", "letzte Posts
  von …", "LinkedIn report for …", "compare these LinkedIn accounts", "screen these creators",
  or any message containing one or more LinkedIn URLs (`linkedin.com/in/...`,
  `linkedin.com/company/...`) where data extraction or content analysis is implied.
  Auto-detects single vs. batch input. Returns one Obsidian-friendly subfolder per account
  (`<handle> — <essence>/`) with an overview note, a one-page strategy brief
  (`_<handle> themes.md` — top themes / hooks / tonality / audience cues), and one file per
  post (each carrying a Haiku-polished neutral briefing instead of the raw LinkedIn
  engagement-bait formatting). Costs ~$0.15 per profile end-to-end (~$0.05 Apify + ~$0.05
  Haiku polish + ~$0.05 Sonnet themes synthesis + $0.001 essence on 25 posts).
---

# LinkedIn scraper

Wraps the Apify `harvestapi/linkedin-profile-posts` actor with Anthropic Haiku (per-post
polish + per-profile essence) and Anthropic Sonnet (per-profile themes synthesis):

| File | Purpose |
|---|---|
| `scripts/scrape_profile.py` | Calls Apify, writes one `<handle>/_<handle> overview.json` per profile. Auto-chains polish + themes + essence + folder-rename by default. |
| `scripts/polish_post.py` | For every post with substantive content: one Haiku call returns `{description, content, tags}` — neutral third-person briefing + Obsidian-safe content tags. Strips LinkedIn engagement-bait scaffolding (one-line-per-paragraph hooks, "Repost ♻️ if…" CTAs, self-promo blocks). Idempotent. |
| `scripts/themes_profile.py` | One Sonnet 4.6 call per profile produces a one-page strategy brief: top themes (with post-count distribution), recurring hooks, tonality, audience cues, posting strategy. Stored in JSON `_themes_md`; rendered as `_<handle> themes.md`. Idempotent. |
| `scripts/essence_profile.py` | One Haiku call per profile returns a one-sentence "essence" (≤60 chars) of what the account stands for. Stored in JSON `_essence`; used for the folder name `<handle> — <essence>`. Idempotent; locked at first scrape. |
| `scripts/render_report.py` | Renders the JSON envelope into Obsidian-friendly Markdown: overview note + themes brief + one per-post note per profile (Reaction-type breakdown inline). Cross-profile batch summary when ≥2 profiles. Dedupes content variants (highest engagement wins). |

## Setup (one-time)

### Apify

```bash
# Add to ~/.zshrc, then `source ~/.zshrc`
export APIFY_API_TOKEN='apify_api_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
```

Token at <https://console.apify.com/account/integrations>. **Resource-specific Actor
permissions must use the 17-char hex ID** (`A3cAPGpwBEG8RJwse` for
`harvestapi/linkedin-profile-posts`), not the slug — see `references/apify-actor.md` for the
exact token config. The same token works for the Instagram scraper if you already have it set;
just add this LinkedIn actor's hex to the existing token's resource permissions.

### Anthropic polish + themes + essence (auto-chained after scrape)

```bash
# Add to ~/.zshrc, then `source ~/.zshrc`
export ANTHROPIC_API_KEY='sk-ant-...'
```

Token at <https://console.anthropic.com/settings/keys>. If the key is missing, all three
LLM-driven steps (polish, themes, essence) no-op with a setup hint and the pipeline continues
— posts then render with the raw post text in `## Content`, no `_<handle> themes.md` file is
written, and the folder stays on `<handle>/` instead of `<handle> — <essence>/`. Default
models: `claude-haiku-4-5-20251001` for polish + essence; `claude-sonnet-4-6` for themes
(Sonnet handles the cross-post synthesis better).

### Skill venv

```bash
SKILL_DIR="${CLAUDE_PLUGIN_ROOT}/skills/linkedin-scraper"
PY="$SKILL_DIR/.venv/bin/python"
```

If `$PY` does not exist on first invocation, the user has not run the **First-Run Setup** yet — see the plugin's README for the one-time `python3 -m venv` + `pip install -r requirements.txt` bootstrap.

## When to invoke this skill

1. The user names one or more LinkedIn handles or URLs and asks for any data about them.
2. The user wants to monitor or refresh prior LinkedIn data ("re-scrape these LinkedIn
   profiles").
3. The user asks for a LinkedIn-focused competitive analysis or creator screen.

Do **not** invoke for: comments-only scraping, hashtag/feed searches, anything requiring login,
or post discovery by keyword. For those, use a different actor (e.g.
`harvestapi/linkedin-post-search`) — extending this skill is a scoped follow-up task.

## Workflow

### 1. Parse the user's input into a list of handles

The script normalizes: full URLs (`https://linkedin.com/in/<slug>/`,
`https://linkedin.com/company/<slug>/`), bare handles (`example-creator`), and `@`-prefixed handles
(`@example-creator`). It supports both **personal** profiles (`/in/`) and **company** pages
(`/company/`). Mixed delimiters (commas, semicolons, whitespace) work in `--usernames`.

### 2. Decide on `--posts-limit`

Default 25. Apify default is 10; the skill overrides to 25 because LinkedIn posts carry more
content density per item than Instagram, and the cost is still ~$0.05 per profile. Pass
`--posts-limit 100` (or `0` for "all available") for deep-dives.

### 3. Run the scrape

**`--out-dir` resolves from `$OBSIDIAN_VAULT_PATH`** — the env var pointing to the user's
Obsidian vault root. Always pass `--out-dir` explicitly:

```bash
"$PY" "$SKILL_DIR/scripts/scrape_profile.py" \
  --usernames "<comma-separated handles or URLs>" \
  --posts-limit 25 \
  --out-dir "${OBSIDIAN_VAULT_PATH:?Set OBSIDIAN_VAULT_PATH to your vault root before running this skill}/LinkedIn Scraper"
```

If the user's vault has a dedicated inbox folder (e.g. `Inbox/Social Scrapers/LinkedIn Scraper`),
append that path segment. Otherwise the default `<vault>/LinkedIn Scraper` is fine.

The script creates `<out-dir>/<handle>/` subfolders, writes one `_<handle> overview.json` per
profile (overwritten on re-scrape), then auto-chains:
1. `polish_post.py` per profile (per-post Haiku briefing → JSON mutation).
2. `themes_profile.py` per profile (one Sonnet call → JSON `_themes_md` mutation).
3. `essence_profile.py` per profile (one Haiku call → JSON `_essence` mutation).
4. Folder rename: `<handle>/` → `<handle> — <essence>/` (idempotent).

Stdout is a JSON summary with `succeeded`/`failed`/`polish_results`/`themes_results`/
`essence_results` lists. Each step can be disabled with `--no-polish`, `--no-themes`,
`--no-essence`.

### 4. Render

```bash
"$PY" "$SKILL_DIR/scripts/render_report.py" --batch-dir "<platform-folder>"
```

The renderer walks every `<handle>/` (or `<handle> — <essence>/`) subfolder, picks up
`_<handle> overview.json`, and writes:

- `<handle>/_<handle> overview.md` — profile card + posts index (sorts to top via `_` prefix)
- `<handle>/_<handle> themes.md` — strategy brief (top themes + hooks + tonality + audience + posting strategy). Only written when `_themes_md` is present in the JSON.
- `<handle>/<post-date> <title-slug>.md` — one note per post; polished briefing inline

Content-dedup runs every render — duplicate posts (same text re-published days/weeks apart)
collapse to the highest-engagement variant; dropped posts surface in `## Removed duplicates (N)`.

For ≥ 2 profiles it also writes `<DATE> linkedin batch summary.md` at the platform-folder
root, with a cross-profile comparison table.

To re-render just one profile: `--input <path-to-overview.json>`.

### 5. Tell the user what landed where

Show file paths and a 3-bullet TL;DR. Don't paste the full Markdown unless asked.

## Output shape

Per-account subfolder (`Social Scrapers/LinkedIn Scraper/<handle> — <essence>/`):

```
example-creator — Short essence describing the creator's focus area/
├── _example-creator overview.json                   # raw envelope (hidden in Obsidian)
├── _example-creator overview.md                     # profile card + posts index (sorts to top)
├── _example-creator themes.md                       # strategy brief (themes/hooks/tonality/audience)
├── 2026-04-25 First post title.md                   # one note per post
├── 2026-04-22 Second post title.md
└── ...
```

The folder name carries the LLM-generated essence after the first scrape:
`<handle> — <essence>/` (em-dash with spaces). The essence is locked at first scrape and
never auto-updates — folder renames break Obsidian links, so stability wins. Force a fresh
one with `essence_profile.py --regenerate` followed by a manual `mv` of the folder.

The overview file uses a leading underscore so Obsidian sorts it to the top.

## Body shape (per-post)

```markdown
# <derived title from first sentence>

_handle · 2026-04-25 · 1.446 ❤ · 461 💬 · 35 ↗ · [Open](...) · [← Profile](...)_

_Reactions: 👍 1.225 · 🫶 143 · 👏 41 · 💡 19 · 🎉 18_

## Content

_Neutral briefing rewritten by Anthropic Claude (`claude-haiku-4-5-20251001`) from the original post._

<polished Markdown verbatim — paragraphs, **inline bold lead-ins**, bullet lists>
```

If polish didn't run (no `ANTHROPIC_API_KEY`), `## Content` falls back to the raw post text as
a blockquote. Once the key is set and `polish_post.py` is re-run, the next render replaces it.

## Frontmatter (Obsidian Properties)

**Overview note:**
| Property | Value |
|---|---|
| `title` | `"<handle> overview"` |
| `description` | First 125 chars of bio (`author.info`) |
| `status` | `draft` |
| `handle`, `full_name`, `profile_kind` (`in` / `company`), `posts_loaded` | identifiers |
| `scraped_at`, `source` | provenance |
| `created`, `modified` | scrape timestamp; `created` preserved across re-renders |
| `tags` | `[LinkedIn, Overview]` |

**Per-post note:**
| Property | Value |
|---|---|
| `title` | derived from first sentence (engagement-bait stripped, max 80 chars) |
| `description` | LLM-polished `description_polished` (≤120 chars) or fallback truncation |
| `status` | `draft` |
| `handle`, `post_date`, `post_id`, `scraped_at`, `source` | identifiers + provenance |
| `created`, `modified` | `created` preserved across re-renders |
| `tags` | `[LinkedIn, <2-3 LLM content tags>]` (e.g. `LinkedIn, Personal-Philosophy, Solopreneur`) |

`likes`, `comments`, `shares`, `reactions_breakdown` are intentionally NOT in YAML — the
visible byline already shows them and including them would bloat the Properties panel.

The renderer **overwrites files in full** on re-render. `created` is the only field preserved.

## Polishing (`polish_post.py`)

For every post with content of meaningful length (≥80 chars), one Haiku call returns:
- `description` — third-person factual sentence (≤120 chars). Goes to YAML `description`.
- `content` — neutral Markdown briefing. Strips LinkedIn one-line-per-paragraph staggers,
  engagement hooks, and CTAs. Keeps every claim, number, name, step. Goes to `## Content`.
- `tags` — 2–3 Obsidian-safe content tags. Niche-specific (`Solopreneur`,
  `Personal-Branding`), not generic (`LinkedIn`, `Business`).

The system prompt is cacheable (Anthropic 5-min TTL): billed once per profile, reused per
post. Idempotent: skips posts already polished unless `--no-skip-existing` is passed.

## Themes (`themes_profile.py`)

One Sonnet 4.6 call per profile reads bio + all polished post briefings and returns a
Markdown body with five sections:

1. **Top themes** — 3–5 themes with post-count distribution (`Posts: 9/25 (posts 1, 10, 13…)`),
   so the reader sees how dominant each theme is.
2. **Recurring hooks** — 3–5 verbatim opening patterns the creator returns to. If no signature
   hook style exists, the model says so explicitly instead of fabricating.
3. **Tonality** — 1–2 paragraphs naming register, rhetorical moves, stylistic tells.
4. **Audience cues** — who the content is calibrated for + what the engagement pattern signals.
5. **Posting strategy** — cadence, format mix, notable absences.

Output language matches the bio (German bio → German brief; English bio → English brief).
The body is stored in JSON as `_themes_md` and rendered as `_<handle> themes.md` by
`render_report.py`. Idempotent: skips if `_themes_md` already set unless `--regenerate`.

Skipped automatically when fewer than 5 posts are loaded (synthesis becomes anecdotal).

To re-synthesize themes only:

```bash
"$PY" "$SKILL_DIR/scripts/themes_profile.py" --input "<path-to-overview.json>" --regenerate
"$PY" "$SKILL_DIR/scripts/render_report.py" --input "<path-to-overview.json>"
```

## Essence (`essence_profile.py`)

One Haiku call per profile returns a one-sentence essence (≤60 chars) describing what the
account stands for. Stored in the JSON envelope as `_essence`. Used by `scrape_profile.py` to
rename the folder `<handle>/` → `<handle> — <essence>/`. Source signals: `author.info` (bio /
LinkedIn headline) + first 3 polished post bodies.

Idempotent: skips if `_essence` is already set unless `--regenerate` is passed.

## Error handling

| # | Situation | Script behavior | Your behavior |
|---|---|---|---|
| 1 | `APIFY_API_TOKEN` missing | scrape exits 2 with setup snippet | Surface the snippet, ask user to add it, retry |
| 2 | Token rejected (401) | exit 3 | Tell user to verify the token at console.apify.com |
| 3 | Out of credit (402) | exit 3 | Top up; do not retry |
| 4 | 403 insufficient-permissions | exit 3 | Token Actor-ID misconfigured; see `references/apify-actor.md` |
| 5 | Profile not found / private | listed under `failed[]` | Continue with the rest; mention failures |
| 6 | `ANTHROPIC_API_KEY` missing | polish + essence print setup hint, exit 0; pipeline continues | Tell user to set the key, then `polish_post.py` + `essence_profile.py` + re-render |
| 7 | One post fails to polish | listed in polish output `failed[]`; others still polished | Re-run later; idempotent skip protects already-polished posts |
| 8 | LLM provider error mid-run (e.g. Anthropic HTTP 400 "credit balance too low", or a transient network error) | the affected post/step records the error in its `failed[]` entry or `reason` field and the pipeline continues (exit 0); posts render with raw text, no `_<handle> themes.md`, no essence folder-rename | Read the per-step `reason`/`failed[]` — not just `ok` (which reflects process exit, not work success). Fix the provider issue, then re-run `polish_post.py` + `themes_profile.py` + `essence_profile.py` (idempotent) + re-render |

## Pricing

| Item | Cost per profile (25 posts) |
|---|---|
| Apify scrape | ~$0.05 ($2 / 1.000 posts) |
| Polish (Haiku 4.5, system-prompt cached) | ~$0.05 |
| Themes (Sonnet 4.6, system-prompt cached, 1 call/profile) | ~$0.05 |
| Essence (1 Haiku call per profile) | ~$0.001 |
| **Total per profile end-to-end** | **~$0.15** |

Mention the cost ballpark **once** before the first scrape if the user hasn't acknowledged it.

## Going deeper

- `references/apify-actor.md` — Actor input/output schema, hex ID for token-permissions,
  pricing details, error catalogue, real Apify response shape.

## Out-of-scope (intentionally)

- Profile-stats actor (`apimaestro/linkedin-profile-batch-scraper-no-cookies-required`) for
  followers/about/experience — separate chained call, follow-up task.
- Reactions-bodies + comments-bodies (cost-decision; default off, opt-in flag will land later).
- Comments-only scraping, hashtag/feed search, articles-only, anything requiring login.
- Charting / trend visualizations.
- Scheduling regular re-scrapes (use `/schedule` skill).
- Multi-platform support — Instagram is a sibling skill (`instagram-scraper`); X is planned.

If the user asks for one of these, say it isn't supported yet and ask whether to extend the
skill — that's a separate, scoped task.
