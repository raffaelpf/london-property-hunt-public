# Running the hunt in a Claude Code cloud environment

The original skill (`skill.md`) is designed to run **locally** with the
"Claude in Chrome" extension + a Gmail connector. This directory adds a
**self-contained Python pipeline** so the hunt can run **entirely in a Claude
Code web/cloud session** instead — fetching pages over HTTP through the
environment's agent proxy (no browser required).

> **Scope:** search → dedup → prioritise → write the Excel tracker (committed to
> the repo) → print a summary in chat. **No email is sent** (deferred add-on).
>
> **Current search (see `config.md`):** whole **1–2 bed flats**, **£3,000–£4,500**,
> in central London (Soho, Waterloo, Farringdon, Covent Garden, Southwark,
> Bloomsbury), with a **balcony/terrace** and **unfurnished / part-furnished /
> flexible** furnishing, preferring **> 650 sq ft**.

---

## What it does

`run_hunt.py` orchestrates the modules in `scraper/`:

| Module | Role |
|---|---|
| `scraper/config.py` | Parse `config.md` (`KEY=value` blocks) |
| `scraper/fetch.py` | HTTP fetch through the agent proxy (`HTTPS_PROXY` + CA bundle) |
| `scraper/platforms/` | Rightmove (`__NEXT_DATA__`), OnTheMarket (`__NEXT_DATA__` + detail enrich), OpenRent (DOM + detail enrich) |
| `scraper/features.py` | Regex classifier for outdoor space (private / communal / juliet / none), furnishing, size from listing text |
| `scraper/classify.py` | LLM outdoor-space classifier (Claude) — used during detail-page enrichment; falls back to `features.py` when no API key |
| `scraper/prioritise.py` | HIGH/MEDIUM/LOW; drops out-of-budget / out-of-bed-range; balcony & furnishing flagged not dropped |
| `scraper/tracker.py` | `openpyxl` `Flats` sheet with URL dedup + coloured rows |
| `scraper/outreach.py` | A `<100`‑word `.txt` enquiry per HIGH listing |

> **Why HTTP, not a browser?** Listing data is server-rendered / embedded in the
> HTML, so no JS execution is needed. Playwright's headless Chromium also can't
> open a CONNECT tunnel through this environment's proxy (the tunnel resets),
> whereas `urllib`/`curl` work — so we fetch over HTTP.

**Two-stage per platform:** a filtered search returns candidates; then for
OnTheMarket/OpenRent the detail page is fetched and its full description run
through `features.analyze_text` to confirm balcony/terrace, furnishing and size.
Rightmove exposes those in search results (`keyFeatures`, keyword-match flags,
`displaySize`), so it needs no detail fetch.

### Outdoor-space classification (Claude)

Deciding whether a flat has outdoor space from listing text is error-prone with
regex: a flat in **Covent Garden** (or Hatton Garden, Kensington Gardens, …) has
"garden" all over its description as a *place name*, which the regex read as a
communal garden — so a flat with no outdoor space survived the "must have
outdoor space" gate and showed as MEDIUM.

During detail-page enrichment, `scraper/classify.py` asks **Claude** to read the
listing text and return `private` / `communal` / `juliet` / `none`. An LLM knows
"Covent Garden" is a location, not a garden, so these place-name traps go away.

- **Only new flats are classified.** When Claude is active, every flat it judges
  in a run is recorded in a hidden `Seen` sheet in the tracker workbook (by URL
  and by price/beds/postcode) — including flats *dropped* for having no outdoor
  space, which never reach the visible `Flats` sheet. Before enrichment the run
  skips any flat already in that ledger, so a listing is fetched and sent to
  Claude **once**, not re-classified every day it re-appears in search results.
  An existing flat's outdoor space doesn't change, so this is safe. The ledger is
  only populated when a key is present (so adding a key later doesn't find every
  flat already cached as done); it lives in the same committed `.xlsx`, so delete
  the `Seen` sheet to force re-classification.
- **Enabled automatically** when `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`)
  is set in the environment. The call runs only on enriched (new) candidates
  (≤ `MAX_ENRICH` per run), not on every search result.
- **Graceful fallback:** with no key, the SDK missing, or an API error, it falls
  back to the regex in `features.py` — which now also strips the common
  "…garden(s)" place names, so the Covent Garden case is handled either way.
- **Env knobs:** `HUNT_LLM_MODEL` overrides the model (default
  `claude-opus-4-8`); `HUNT_DISABLE_LLM=1` forces the regex path. The run logs
  once to stderr which path is active (`[classify] …`).

### Priority rules

- **Drop (hard gate):** price outside £3–4.5k, bedrooms outside 1–2, or **no confirmed
  balcony/terrace/outdoor space** — Juliet-only and "not stated" listings are dropped
  entirely (never tracked, never notified).
- **HIGH:** private balcony/terrace + furnishing unfurnished/part-furnished/flexible +
  ≥ 650 sq ft confirmed.
- **MEDIUM:** communal/shared outdoor space, size unknown or under 650 sq ft, furnishing
  not stated — **or listed furnished but otherwise HIGH** (private + big).
- **LOW (kept + flagged):** listed **furnished** and otherwise MEDIUM (small/unknown size
  or communal). Furnished is a one-tier demotion, since some landlords are flexible.

### The tracker

Committed at **`tracker/london_flat_hunt.xlsx`** (un-ignored in `.gitignore`).
Each run updates it in place and it is committed to the repo, so it's versioned
and viewable on GitHub. Dedup is by listing URL — safe to run repeatedly.

---

## Prerequisites

1. **Network policy** — the property domains must be allowed by this
   environment's egress policy (set on **claude.ai/code**). Verify:
   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' https://www.rightmove.co.uk/robots.txt   # want 200
   ```

## Install & run

```bash
pip install -r requirements.txt          # openpyxl + beautifulsoup4
cp config.example.md config.md           # then edit your details
python run_hunt.py                        # all sources
python run_hunt.py --platforms Rightmove --debug-dir debug --limit 20   # narrow test
```

Flags: `--platforms` (subset), `--limit N` (cap per search), `--debug-dir DIR`
(dump fetched HTML for selector fixes), `--tracker PATH`, `--config PATH`.

---

## Notes & caveats

- **Sources:** Rightmove + OnTheMarket + OpenRent. **Zoopla is disabled**
  (Cloudflare 403s non-interactive clients); SpareRoom removed (rooms-only).
  OnTheMarket (per-area) is the main workhorse; Rightmove is London-wide +
  post-filtered to target areas (lower yield); OpenRent is a bonus.
- **Furnishing "flexible" caveat:** Rightmove is URL-filtered to
  unfurnished/part-furnished, so a *flexible* listing tagged "furnished" there
  can be missed; OnTheMarket/OpenRent furnished listings are kept as LOW+flagged.
- **Selectors/JSON shapes can drift.** Each platform is isolated — if one breaks
  the run still completes and reports the error. Use `--debug-dir` to capture
  HTML and adjust `scraper/platforms/*`.
- **Idempotent.** Dedup by listing URL.
