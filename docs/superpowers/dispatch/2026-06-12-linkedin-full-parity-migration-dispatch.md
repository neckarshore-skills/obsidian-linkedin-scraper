# LinkedIn Scraper — Full IG-Parity Migration Dispatch

> **Status:** Dispatch brief — scopes a gated build. NOT a build record.
> **Date:** 2026-06-12
> **Author:** Obi (Skill Master)
> **Decision authority:** User chose **Full IG-parity** on 2026-06-12.
> **Supersedes:** `OBI-2026-05-30-1` reconstruct-net-new decision (2026-06-01 a) — premise falsified, see § 1.

## 1. Premise Correction (R2 — verified against the ground 2026-06-12)

The entire `OBI-2026-05-30-1` chain rested on "the LinkedIn source is lost and unrecoverable." That is **empirically false** as of 2026-06-12.

| # | Claim in `OBI-2026-05-30-1` | Reality on disk (2026-06-12) |
|---|------------------------------|-------------------------------|
| 1 | Legacy `~/.claude/skills/linkedin-scraper/` is gone and unrecoverable | Fully intact: `SKILL.md` (15.5 KB) + `references/` + `requirements.txt` + `.venv` |
| 2 | "Only the LinkedIn platform-specific scripts truly lost" | All 5 present: `scrape_profile.py`, `polish_post.py`, `render_report.py`, `essence_profile.py`, `themes_profile.py` |
| 3 | Apify logic must be reconstructed from memory (Do-No-Harm risk) | Real actor wired: `harvestapi/linkedin-profile-posts` (`scrape_profile.py:35`), `_social_common` already vendored |
| 4 | Public repo `obsidian-linkedin-scraper` is README-only | **Correct** — `LICENSE` + `README.md` only; last commit = social-preview hero (PR #1) |

**Consequence:** This is a **migration**, not a reconstruction. The expensive, risk-bearing concern — "rebuilding Apify field-mapping from memory and shipping it into a public repo" — is dead. A working, `_social_common`-aware source serves as the authoritative field-mapping reference.

### 1a. The honest unknown

How can the 2026-05-30 "6-class recovery audit exhausted, gone" finding coexist with an intact source whose script mtimes are 26 Apr and `SKILL.md` 18 May? Two possibilities, no evidence either way:

1. The source was restored after 2026-05-30 — but **no open-item documents a restore**.
2. The 2026-05-30 audit searched the wrong location or had a defect.

**Status: Unknown.** Not papered over. Flagged to MASCHIN in § 9 for ledger correction.

## 2. Scope Decision

**Target = Full IG-parity** (user, 2026-06-12). The migrated LinkedIn scraper ships at the same hardening level as today's Instagram scraper (`obsidian-instagram-scraper` origin/main `80dc47b`), not the mid-May source state.

## 3. Ground Truth — What Exists

### 3a. Legacy source (`~/.claude/skills/linkedin-scraper/`)

| Asset | State | Notes |
|-------|-------|-------|
| `scrape_profile.py` (17.8 KB) | Working reference | Actor `harvestapi/linkedin-profile-posts`; 600 s timeout; `_social_common.tokens/folder_rename/timestamps` imported |
| `polish_post.py` (13.2 KB) | Working reference | Haiku polish; `_social_common.tokens/llm_helpers` |
| `render_report.py` (35.2 KB) | Working reference | Note rendering |
| `essence_profile.py` (9.1 KB) | Working reference | Folder-essence naming |
| `themes_profile.py` (11.6 KB) | Working reference | `_<handle> themes.md` strategy brief — **LinkedIn-only, no IG equivalent** |
| `SKILL.md` (15.5 KB) | Working reference | Frontmatter + triggers intact |
| Out-dir default | `./data/linkedin` | Safe-relative; **no** B4 hardcoded-vault bug; lacks `$OBSIDIAN_VAULT_PATH` env-default |

### 3b. Parity target (`obsidian-instagram-scraper` `80dc47b`)

Scripts: `scrape_profile.py`, `polish_post.py`, `render_report.py`, `essence_profile.py`, `transcribe_videos.py`, `test_visual_anchor.py` + CI `.github/workflows/test.yml`.

### 3c. Shared base (`obsidian-social-scrapers-common` origin/main `b8a0e10`)

Modules: `tokens.py`, `folder_rename.py`, `timestamps.py`, `llm_helpers.py`, `render_helpers.py`, `cleanup.py`, `test_smoke.py`.
**Not yet present:** `llm_client.py` + generalized visual-anchor embed/`_media` helper + whisper-default. These land via `OBI-2026-06-11-6` (see § 7).

## 4. Parity Feature Map (IG hardened → LinkedIn)

| # | IG-hardened feature | LinkedIn migration action | Class |
|---|---------------------|----------------------------|-------|
| 1 | env-default out-dir → `$OBSIDIAN_VAULT_PATH/<Skill> Scraper/` | Replace `./data/linkedin` default with canonical env resolution | Port (safety) |
| 2 | `_social_common` canonical seam (`llm_client`, visual-anchor helper, whisper-default) | Consume from common **after** `OBI-2026-06-11-6` backport | Consume (dep) |
| 3 | Visual-anchor media (`download_post_media` + `![]( _media/… )` embed + on-disk check) | New for LinkedIn — needs platform image-field design (**DQ1**) | Design + build |
| 4 | Video transcription (`transcribe_videos.py`, whisper `large-v3-turbo-q5_0`) | In-scope only if LinkedIn native video is wanted (**DQ2**) | Conditional |
| 5 | Provider-neutral byline (F-UAT-7) | Apply: model id, not "Anthropic Claude" | Port (fix) |
| 6 | Essence-language bilingual anchoring (F-UAT-6) | Apply: bilingual prompt examples + do-not-copy-example-language rule | Port (fix) |
| 7 | Subprocess `stderr`-surfacing (diagnosability) | Apply `_run_or_raise`-style stderr tails | Port (fix) |
| 8 | CI (`test.yml` runs all suites on push/PR) | New for LinkedIn | Build |
| 9 | `test_visual_anchor.py` equivalent | New TDD suite for LinkedIn visual-anchor | Build |
| 10 | `themes_profile.py` strategy brief | **Keep** — LinkedIn superset, no IG equivalent to port | Retain |

## 5. Platform-Specific Design Questions (resolve in the BUILD-session brainstorm — do NOT pre-answer here)

1. **DQ1 — Visual-anchor source field.** What does `harvestapi/linkedin-profile-posts` deliver for post images? Native image vs. article-share thumbnail vs. document/carousel. IG's `displayUrl` has no guaranteed LinkedIn analog. Needs real actor output to design. (Gated on token — see § 7.)
2. **DQ2 — Video transcription in scope?** Does the actor return native-video URLs, and is LinkedIn-video transcription wanted? LinkedIn video is less central than IG Reels — defer with a parity-asterisk if absent/unwanted.
3. **DQ3 — Post identity for `_media/<id>.jpg`.** IG uses `shortCode`; LinkedIn posts are activity URNs. Pick a stable, filesystem-safe slug.
4. **DQ4 — Engagement-field mapping.** LinkedIn reaction types (like/celebrate/support/love/insightful/funny) vs IG likes/comments. Confirm renderer + polish handle the LinkedIn shape.

## 6. Gates & Dependencies

| # | Gate | Type | State |
|---|------|------|-------|
| 1 | `OBI-2026-06-11-6` — `_social_common` backport (llm_client + visual-anchor helper + whisper-default) + X re-vendor | Prerequisite | Open (P2). **Recommend re-prio: this now blocks LinkedIn full-parity.** |
| 2 | Apify token 401 (`OBI-2026-05-19-2`) | UAT blocker | Open — user-action |
| 3 | UAT-before-public-push | Do-No-Harm hard gate | Target repo is **PUBLIC**; no public push until full UAT PASS pronounced by user |
| 4 | Obi terminal-only (Red-List) | Process | Satisfied (Obi runs in a dedicated terminal, never Agent-Tool) |

## 7. Recommended Build Sequence

1. **Phase 0 — Backport first (`OBI-2026-06-11-6`).** Land `llm_client.py` + generalized visual-anchor embed/`_media` helper + whisper-default into `_social_common`; re-vendor X. Closes the seam so LinkedIn consumes canonical, not divergent.
2. **Phase 1 — Scaffold migration.** Copy legacy LinkedIn scripts into `obsidian-linkedin-scraper`; wire `_social_common`; apply env-default out-dir (#1) + the three F-UAT ports (#5/#6/#7); add CI (#8). No new features yet — get a clean, hardened port green.
3. **Phase 2 — Brainstorm DQ1–DQ4** against real actor output (needs token). Then build visual-anchor (#3) TDD + suite (#9); decide DQ2 video.
4. **Phase 3 — UAT** on a small public LinkedIn account, env-default out-dir, production-vault untouched. User pronounces PASS.
5. **Phase 4 — Public push** only after Phase-3 PASS.

> **Note:** Phases 0–1 are not token-gated and can proceed now. Phases 2–4 are gated on the Apify token (`OBI-2026-05-19-2`).

## 8. FOR MASCHIN

1. **Correct `OBI-2026-05-30-1`:** premise "source lost/unrecoverable" is **false** (R2, 2026-06-12). Re-scope from "reconstruct net-new" to "Full IG-parity migration." Real blocker is the Apify token (`OBI-2026-05-19-2`), not a lost source.
2. **Correct `OBI-2026-05-19-4`:** the "PREMISE EMPIRICALLY FALSE / source removed" annotation is itself now falsified — source is present. Migration can proceed (gated on token + backport, not on source-restore).
3. **Open the honest unknown (§ 1a):** no open-item explains how the 2026-05-30 "exhausted recovery audit" and the intact-on-disk source coexist. Either a silent restore (undocumented) or a defective audit. Recommend a one-line ledger note rather than an invented narrative.
4. **Re-prioritize `OBI-2026-06-11-6`** from P2 → effective prerequisite for LinkedIn full-parity (Phase 0).
5. **Declaration note:** `obsidian-linkedin-scraper` is not yet in Obi `repos_write`; consistent with the IG/X precedent ("declaration lagging authorized work, not maverick"), recommend adding it on user authorization.
