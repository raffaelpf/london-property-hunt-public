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
| `scraper/features.py` | Classify outdoor space (private / communal / juliet / none), furnishing, size from listing text |
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
