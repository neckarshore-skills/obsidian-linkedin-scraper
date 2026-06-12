# Reference: Apify `harvestapi/linkedin-profile-posts`

Quick reference for the Apify LinkedIn Profile Posts Scraper used by this skill.
Read this when extending the skill (other actors, different inputs, edge cases, pricing math).

> **Status:** First-build-session reference. Identity, endpoint, input + output schemas are
> all verified against the actor's public docs and a real scrape (generic example, originally
> tested 2026-04-25 against a real profile, anonymized for OSS release). Post-type encodings
> beyond plain text and error-item shape are still TBD — they'll be filled in as the next
> session's render step encounters them.

## Actor identity

| Field | Value |
|---|---|
| Owner / username | `harvestapi` |
| Slug | `linkedin-profile-posts` |
| URL form (use in API paths) | `harvestapi~linkedin-profile-posts` |
| Internal hex ID (use in token resource-permissions) | `A3cAPGpwBEG8RJwse` |
| Public actor URL | <https://apify.com/harvestapi/linkedin-profile-posts> |

**Important when creating Apify API tokens:** The "Resource-specific permissions → Actor ID"
field requires the **17-character hex ID** (`A3cAPGpwBEG8RJwse`), NOT the slug
`harvestapi/linkedin-profile-posts` or the URL form `harvestapi~linkedin-profile-posts`. Apify
silently accepts the wrong format but the token then can't see the actor (404 on metadata,
403 on run-sync). If the slug-form keeps failing, fall back to **Account-level Actors:
Run + Read** which works regardless of which actor.

If you already have a working token for the IG scraper (hex `shu8hvrXbJbY3Eb9W`) and want to
use the same env var, **add this LinkedIn actor's hex ID** (`A3cAPGpwBEG8RJwse`) to the same
token's resource permissions — don't create a second token unless your secret-management
demands separation.

## Endpoint

| # | Mode | URL | Use when |
|---|---|---|---|
| 1 | Run-sync (returns dataset items) | `POST https://api.apify.com/v2/acts/harvestapi~linkedin-profile-posts/run-sync-get-dataset-items` (Authorization: Bearer …) | Up to ~25 profiles, expected runtime < 10 min. **This is what `scrape_profile.py` uses.** |
| 2 | Async run | `POST .../runs` → poll → `GET /datasets/<id>/items` | Larger batches, custom polling, webhooks |

Token goes in the `Authorization: Bearer <TOKEN>` header so it never appears in URLs, server
access logs, or stack traces.

## Input fields

Body of the run-sync call. Source of truth:
<https://apify.com/harvestapi/linkedin-profile-posts/input-schema>.

| # | Field | Type | Notes |
|---|---|---|---|
| 1 | `targetUrls` | `string[]` | LinkedIn profile or company URLs (full URLs only — bare handles not accepted by the actor; the skill normalizes them client-side). Max 10.000 items. **Required.** |
| 2 | `maxPosts` | `number` | Max posts per profile. Default 10. **Skill default 25.** Pass `0` for "all available". |
| 3 | `postedLimit` | `string` | Time filter: `any`, `1h`, `24h`, `week`, `month`, `3months`, `6months`, `year`. |
| 4 | `postedLimitDate` | `string` | ISO date or timestamp; alternative to `postedLimit`. |
| 5 | `includeQuotePosts` | `boolean` | Quote posts with comments. Default `true`. |
| 6 | `includeReposts` | `boolean` | Bare reposts (no added comment). Default `true`. |
| 7 | `scrapeReactions` | `boolean` | Pull reaction-author bodies. Default `false`. **Skill default `false`** (cost control). |
| 8 | `maxReactions` | `number` | Per post. Default 5. |
| 9 | `scrapeComments` | `boolean` | Pull comment bodies. Default `false`. **Skill default `false`** (cost control). |
| 10 | `maxComments` | `number` | Per post. Default 5. |
| 11 | `commentsPostedLimit` | `string` | Comment time filter: `any`, `1h`, `24h`, `week`, `month`. |

## Output fields

Verified against the first successful run (generic example, originally tested 2026-04-25 against a real profile, anonymized for OSS release).

harvestapi returns **one dataset item per post** (not one per profile). Each item carries
embedded author metadata so the skill groups items back to their requested handle via
`author.publicIdentifier` (the canonical slug — matches our `_handle`).

### Top-level post fields

| # | Field | Type | Notes |
|---|---|---|---|
| 1 | `id` | `string` | LinkedIn share/post ID. Mostly numeric. |
| 2 | `type` | `string` | `"post"` for all 25 posts in the test scrape. Other values likely for articles/videos/polls (TBD). |
| 3 | `postType` | `string \| null` | Always `null` in test scrape; reserved for sub-type info. |
| 4 | `linkedinUrl` | `string` | Direct post URL — `https://www.linkedin.com/posts/<slug>_<title-slug>-activity-<id>-<hash>` |
| 5 | `content` | `string` | Post text body. **This is the field to render.** Length range in test: 800–1739 chars, mean ~1100. |
| 6 | `contentAttributes` | `array` | Empty `[]` for plain text posts; carries link cards / mentions / formatting metadata when present. Schema TBD on first non-empty case. |
| 7 | `postedAt` | `object` | `{timestamp: int (ms), date: ISO 8601 string, postedAgoShort: "7h", postedAgoText: "7 hours ago • Edited • Visible to anyone"}` |
| 8 | `postImages` | `array` | Image media. Empty for text-only posts; one entry per image otherwise. Schema TBD. |
| 9 | `socialContent` | `object` | Display flags (mostly hide-toggles) + **`shareUrl`** — the canonical LinkedIn URL with the title-slug embedded (`...activity-7453...-hXlH`). Useful for filename derivation. |
| 10 | `engagement` | `object` | **THE counts live here.** See section below. |
| 11 | `reactions` | `array` | Reaction *bodies* — empty when `scrapeReactions: false`. Counts-by-type are in `engagement.reactions`. |
| 12 | `comments` | `array` | Comment *bodies* — empty when `scrapeComments: false`. |
| 13 | `reactionIds`, `commentIds` | `string[]` | IDs only; empty in default scrape. |
| 14 | `entityId`, `shareUrn` | `string` | LinkedIn-internal IDs; not used by render. |
| 15 | `header` | `object` | `{text: null}` for plain posts; carries quote-post / reshare metadata when applicable. Schema TBD. |
| 16 | `query` | `object` | Echoes the input parameters for traceability. |
| 17 | `author` | `object` | See section below. |

### `engagement` sub-object

```json
{
  "id": "7453771889719660544",
  "likes": 1446,
  "comments": 461,
  "shares": 35,
  "reactions": [
    {"type": "LIKE",         "count": 1225},
    {"type": "EMPATHY",      "count": 143},
    {"type": "APPRECIATION", "count": 41},
    {"type": "INTEREST",     "count": 19},
    {"type": "PRAISE",       "count": 18}
  ]
}
```

- `likes` is the total reaction count across all types (LinkedIn UI shows this aggregated).
  The breakdown lives in `reactions[]`.
- `engagement.reactions[]` is populated **even when** the input flag `scrapeReactions: false`.
  The flag controls whether *reaction-author bodies* (who reacted) are pulled, not the typed
  counts. This is good news: render can show the `LIKE / EMPATHY / APPRECIATION / INTEREST /
  PRAISE / FUNNY / MAYBE` distribution without paying for body scraping.

### `author` sub-object

```json
{
  "id": "ACoAAA<redacted-internal-id>",
  "publicIdentifier": "example-creator",
  "type": "profile",
  "name": "Example Creator",
  "linkedinUrl": "https://www.linkedin.com/in/example-creator?miniProfileUrn=...",
  "info": "Example bio describing the creator's focus area and value proposition.",
  "website": "https://example.com/newsletter",
  "websiteLabel": "View my newsletter",
  "avatar": {"url": "...", "width": 800, "height": 800, "expiresAt": 1778716800000},
  "urn": "00000000"
}
```

**Critical:** the embedded `author.info` is the profile's **bio/headline snippet** —
already enough material for the future `essence_profile.py` to derive a one-line account
essence WITHOUT a separate profile-stats actor call. The chained-actor cost (~$5/1k profiles)
may be unnecessary for the essence use-case; only follower counts and detailed experience
require it.

### Grouping logic in `scripts/scrape_profile.py`

`slug_from_item()` matches against `linkedinUrl` (post URL → extracts profile slug) and
`author.publicIdentifier` (direct match). For the test run, all 25 items grouped cleanly
via `author.publicIdentifier`; `unmatched_count` was 0.

### Post-type encoding (TBD)

All 25 posts in the test scrape are `type: "post", postType: null` (text-only). To document:
- Articles → likely `type: "article"`
- Videos → likely `type: "video"` or `postImages` containing a video object
- Polls → unknown
- Reposts (with comment) → probably populated `header.text`
- Document carousels → unknown

These will surface naturally when render is built in the next session — render walks the
`type` field and falls back to a generic post layout for unknowns.

### Errors / unreachable profiles

Not yet observed. Likely path (based on harvestapi conventions): a top-level item with an
`error` field instead of `content`/`engagement`. The skill's `failed[]` aggregator already
handles "no items returned for slug X" via `__unmatched__` bucket; per-item error fields
would need `slug_from_item()` to skip them or the consumer to filter on `.error` presence.

## Pricing

Pay-per-result, billed at the end of each run.

| # | Item | Price |
|---|---|---|
| 1 | Post | $2.00 / 1.000 results ($0.002/post) |
| 2 | Reaction | extra (only when `scrapeReactions: true`) — TBD per page |
| 3 | Comment | extra (only when `scrapeComments: true`) — TBD per page |

**Math for the default skill call** (`maxPosts: 25`, reactions/comments off):
- 1 profile ≈ 25 posts × $0.002 = **$0.05 per profile**.
- 20 profiles ≈ **$1.00**.

Source of truth: <https://apify.com/harvestapi/linkedin-profile-posts>.

## Common errors

| # | Symptom | Likely cause | Fix |
|---|---|---|---|
| 1 | HTTP 401 | Invalid token | Re-check `APIFY_API_TOKEN`, regenerate at console.apify.com |
| 2 | HTTP 402 | No credit | Top up at console.apify.com/billing |
| 3 | HTTP 403 `insufficient-permissions` | Token's Resource-specific Actor list doesn't include `A3cAPGpwBEG8RJwse` | Open the token at <https://console.apify.com/account/integrations>, click the token, and either (a) add `A3cAPGpwBEG8RJwse` to the Actor resource list, or (b) switch the token to Account-level "Actors: Run + Read". |
| 4 | Empty array response | Profile private, deleted, or rate-limited | Re-run later, or switch to a different LinkedIn actor with proxy support |
| 5 | Item with `error` field for one URL | Single profile failed (typo, region-block, deactivated) | Skill logs to `failed[]`, continues with others |
| 6 | Run-sync timeout (≥ 10 min) | Too many URLs in one call | Switch to async run + dataset poll |

## Future-proofing notes

- harvestapi historically renames input fields (`maxPosts` ↔ `postsLimit` ↔ `resultsLimit`).
  If the script's payload starts coming back with "unknown field" warnings, re-check the
  input-schema page and update the payload literal in `scrape_profile.py`.
- LinkedIn occasionally hard-blocks IPs; harvestapi handles retries internally but a sustained
  401/403 on otherwise-valid input means the upstream profile is blocked for everyone.
- Apify version pinning: `harvestapi~linkedin-profile-posts` resolves to the latest stable
  build. To pin, use the build hash in the URL path.

## Related actors (chained / future-session)

- `apimaestro/linkedin-profile-batch-scraper-no-cookies-required` — profile stats only
  (followers, about, experience, education). Hex ID TBD when wired in. ~$5 / 1.000 profiles.
- `harvestapi/linkedin-post-search` — keyword/hashtag search across LinkedIn posts. Hex ID
  TBD. Different price tier.
