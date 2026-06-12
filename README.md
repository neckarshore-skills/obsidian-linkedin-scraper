# obsidian-linkedin-scraper

<p align="center">
  <img src=".github/social-preview.jpg" alt="obsidian-linkedin-scraper — LinkedIn in Markdown" width="100%"/>
</p>

> Scrape LinkedIn profiles + posts into Obsidian-friendly Markdown briefings.
> Part of the [neckarshore-ai](https://github.com/neckarshore-ai) Obsidian toolkit.

Companion to [obsidian-vault-autopilot](https://github.com/neckarshore-skills/obsidian-vault-autopilot).

## What it does

Pulls a profile's recent posts from LinkedIn via the Apify actor `harvestapi/linkedin-profile-posts`, polishes each post into a neutral Markdown briefing via the AI model of your choice (Anthropic Haiku by default, or a fully local Ollama model), and renders everything as Obsidian-friendly notes — one folder per profile, plus a one-page strategy brief (`_<handle> themes.md`) per account. No engagement-bait formatting.

## Install

In Claude Code:

```
/plugin marketplace add neckarshore-skills/obsidian-linkedin-scraper
```

```
/plugin install obsidian-linkedin-scraper@neckarshore-ai
```

Run each command as a **separate Claude Code input** (one chat submission per command). Pasting both at once will mangle them into a single failing argument.

## First-Run Setup (one-time, manual)

After `/plugin install`, the Python virtual environment used by the skill scripts is **not** bootstrapped automatically. Claude Code's auto-mode classifier blocks unattended `python3 -m venv` + `pip install` on first invocation — by design, since installing Python dependencies into your shell environment is a deliberate action that should not happen silently.

Do it once, in a regular terminal (not inside Claude Code):

```bash
# 1. Locate the skill install directory (Marketplace install).
#    sort -V | tail -1 picks the newest cached version deterministically.
SKILL_DIR=$(find ~/.claude/plugins/cache -path '*/obsidian-linkedin-scraper/*/skills/linkedin-scraper' -type d 2>/dev/null | sort -V | tail -1)
echo "$SKILL_DIR"   # verify the version in this path matches your installed version; empty means the plugin is not installed yet

# 2. Bootstrap the venv.
cd "$SKILL_DIR"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

For non-Marketplace installs (direct symlink or local clone), point `SKILL_DIR` at the `skills/linkedin-scraper` subfolder of your clone and run step 2 only.

Once the venv is built, the skill works from any Claude Code chat. You do not repeat this setup unless `requirements.txt` changes (which is rare — `requests` + `anthropic` are the only runtime deps).

## Prerequisites

| What | Why | Where |
|---|---|---|
| Apify API token | Calls the `harvestapi/linkedin-profile-posts` actor | https://console.apify.com/account/integrations — set as `APIFY_API_TOKEN` |
| LLM provider (one of two) | Polishes posts + extracts profile essence + themes | Default: Anthropic key from https://console.anthropic.com as `ANTHROPIC_API_KEY`. Fully local alternative: [Ollama](https://ollama.com) with `SOCIAL_LLM_PROVIDER=ollama` + `SOCIAL_LLM_MODEL=<pulled model>` — no key, no per-call cost. Details in `skills/linkedin-scraper/SKILL.md` Setup. |
| Python 3.10+ with `requests` + `anthropic` | Skill runtime | `pip install -r skills/linkedin-scraper/requirements.txt` |
| `OBSIDIAN_VAULT_PATH` env var | Output destination | Path to your local Obsidian vault root |

## Usage

In Claude Code, point it at a handle or URL (single or batch):

```
scrape linkedin.com/in/example-creator
scrape @example-creator @another-creator
```

Output lands in `${OBSIDIAN_VAULT_PATH}/LinkedIn Scraper/`.

## Output Structure

```
LinkedIn Scraper/
└── <handle> — <essence>/
    ├── _<handle> overview.json   # raw Apify response
    ├── _<handle> overview.md     # profile card + posts index
    ├── _<handle> themes.md       # one-page strategy brief (themes / hooks / tonality / audience)
    └── <date> <title-slug>.md    # one note per post (neutral polished briefing)
```

## Pricing

~$0.15 per profile end-to-end on the default Anthropic provider (~$0.05 Apify + ~$0.05 Haiku polish + ~$0.05 Sonnet themes synthesis + ~$0.001 essence on 25 posts) — or **$0** LLM cost with the local Ollama provider (slower: ~20-60s per call on Apple Silicon).

## Architecture

The plugin ships:

- `skills/linkedin-scraper/SKILL.md` — Claude Code skill manifest with triggers + workflow
- `skills/linkedin-scraper/scripts/` — `scrape_profile.py`, `polish_post.py`, `essence_profile.py`, `themes_profile.py`, `render_report.py`
- `skills/linkedin-scraper/_social_common/` — vendored shared utilities (canonical source at [obsidian-social-scrapers-common](https://github.com/neckarshore-skills/obsidian-social-scrapers-common))
- `scripts/sync-common.sh` — re-vendoring script for maintainers (pulls the latest common-lib from `main`)

## See also

- [obsidian-instagram-scraper](https://github.com/neckarshore-skills/obsidian-instagram-scraper) — sister skill for Instagram (incl. Reel transcripts)
- [obsidian-vault-autopilot](https://github.com/neckarshore-skills/obsidian-vault-autopilot) — vault organization toolkit
- [obsidian-social-scrapers-common](https://github.com/neckarshore-skills/obsidian-social-scrapers-common) — shared utilities

## Legal & Compliance

This skill retrieves publicly visible LinkedIn content via the [Apify](https://apify.com/) infrastructure for **personal research and curation**. Output is written to your local Obsidian vault and is not redistributed.

| Platform | ToS Status | Action |
|---|---|---|
| LinkedIn | Restricted (User Agreement Section 8.2 prohibits scraping) | Personal research use only |

**GDPR (EU users):** You are the data controller for any scraped EU person. Article 14 GDPR requires you to inform them within one month. LinkedIn data is frequently directly identifying — assume Article 14 applies. See [NOTICE.md](NOTICE.md) for full disclosure.

**Commercial use is not recommended** without independent legal review.

> Personal research use only. The maintainers disclaim liability for misuse, redistribution, or non-compliant processing.

## License

MIT — see [LICENSE](LICENSE).
