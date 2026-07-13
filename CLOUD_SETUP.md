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
| `scraper/features.py` | Shared vocabulary: tracker outdoor labels + a structured furnishing-label reader (no regex classifier) |
| `scraper/classify.py` | Claude classifier (via the `claude` CLI, no API key) — outdoor + furnishing from source attributes + description; batched |
| `scraper/prioritise.py` | HIGH/MEDIUM/LOW; drops out-of-budget / out-of-bed-range; balcony & furnishing flagged not dropped |
| `scraper/tracker.py` | `openpyxl` `Flats` sheet with URL dedup + coloured rows |
| `scraper/outreach.py` | A `<100`‑word `.txt` enquiry per HIGH listing |

> **Why HTTP, not a browser?** Listing data is server-rendered / embedded in the
> HTML, so no JS execution is needed. Playwright's headless Chromium also can't
> open a CONNECT tunnel through this environment's proxy (the tunnel resets),
> whereas `urllib`/`curl` work — so we fetch over HTTP.

**Two-stage per platform:** a filtered search returns candidates; then the detail
page is fetched and its structured attributes + description are classified (see
below). Search collects each portal's structured attributes (OnTheMarket feature
tags, Rightmove `keyFeatures` + keyword-match flags + `displaySize`) and carries
them on the listing; the detail fetch adds the rest.

### Outdoor-space + furnishing classification (source attributes → Claude)

Classification order is **structured source attributes first, then Claude** —
there is no free-text regex. The old regex read place names as gardens: a flat in
**Covent Garden** (or Hatton Garden, Kensington Gardens, …) has "garden" all over
its description as a *location*, which the regex counted as a communal garden, so
a flat with no outdoor space survived the "must have outdoor space" gate and
showed as MEDIUM.

Enrichment collects each new flat's structured attributes + description; then
`scraper/classify.py` hands **Claude** the attributes (feature tags like
`Balcony`/`Communal garden`, the letting furnishing label, Rightmove's
keyword-match hints) as the primary evidence, plus the description as backup, and
gets back `outdoor` (`private`/`communal`/`juliet`/`none`) and `furnishing`.
Claude knows "Covent Garden" is a location, so place-name traps are gone. Size
comes from the portal's numeric field (`minimumAreaSqFt`/`displaySize`) when
present, else from Claude.

- **No API key needed.** Classification shells out to the **`claude` CLI**
  (`claude -p`), which is already authenticated inside a Claude Code session /
  routine — the same way the hunt already runs. If the `claude` binary isn't on
  `PATH` (or `HUNT_DISABLE_LLM=1`), classification is skipped: structured fields
  still fill furnishing and size, but outdoor stays `none`, and since outdoor is
  a hard gate, nothing passes. The run logs which happened (`[classify] …`).
- **Batched.** All the new flats in a run are classified in a few batched CLI
  calls (`BATCH_SIZE` per call) rather than one call each, to amortise the CLI's
  per-call startup.
- **Only new flats are classified.** Every flat Claude judges is recorded in a
  hidden `Seen` sheet in the tracker workbook (by URL and by price/beds/postcode)
  — including flats *dropped* for having no outdoor space, which never reach the
  visible `Flats` sheet. Before enrichment the run skips any flat already in that
  ledger, so a listing is sent to Claude **once**, not re-classified every day it
  re-appears in search results. An existing flat's outdoor space doesn't change,
  so this is safe. The ledger lives in the same committed `.xlsx`; delete the
  `Seen` sheet to force re-classification.
- **Env knobs:** `HUNT_LLM_MODEL` overrides the model alias (default `sonnet`);
  `HUNT_DISABLE_LLM=1` skips Claude.

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
