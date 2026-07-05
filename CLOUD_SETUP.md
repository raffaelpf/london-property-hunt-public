# Running the hunt in a Claude Code cloud environment

The original skill (`skill.md`) is designed to run **locally** with the
"Claude in Chrome" extension + a Gmail connector. This directory adds a
**self-contained Python pipeline** so the hunt can run **entirely in a Claude
Code web/cloud session** instead — fetching pages over HTTP through the
environment's agent proxy (no browser required).

> **Current scope:** scrape → dedup → prioritise → update the Excel tracker →
> print a summary in chat. **No email is sent yet** (that's a later add-on).

---

## What it does

`run_hunt.py` orchestrates the modules in `scraper/`:

| Module | Role |
|---|---|
| `scraper/config.py` | Parse `config.md` (`KEY=value` blocks) |
| `scraper/fetch.py` | HTTP fetch through the agent proxy (`HTTPS_PROXY` + CA bundle) |
| `scraper/platforms/` | Per-site parse: SpareRoom (`data-listing-*`), OpenRent (DOM), Rightmove (`__NEXT_DATA__`), Zoopla (JSON-LD) |
| `scraper/prioritise.py` | HIGH/MEDIUM/LOW + the mandatory 4+‑bed skip and age flags |
| `scraper/tracker.py` | `openpyxl` read/write with URL dedup + coloured rows (schema in `tracker/README.md`) |
| `scraper/outreach.py` | A `<100`‑word `.txt` message per HIGH listing |

> **Why HTTP, not a browser?** The listing data is server-rendered / embedded
> in the HTML, so no JS execution is needed. Playwright's headless Chromium also
> can't open a CONNECT tunnel through this environment's proxy (the tunnel is
> reset), whereas `urllib`/`curl` work — so we fetch over HTTP.

The tracker `.xlsx` is intended to live in **OneDrive** (via the Microsoft 365
connector): the agent downloads it before a run and uploads the updated file
after. Locally, `--tracker` points at any path.

---

## Prerequisites (set on claude.ai — not from inside the container)

1. **Network policy** — the four property domains are blocked by default. Open
   this environment's network policy on **claude.ai/code** to allow
   `rightmove.co.uk`, `zoopla.co.uk`, `spareroom.co.uk`, `openrent.co.uk`
   (or unrestricted outbound). Verify with:
   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' https://www.spareroom.co.uk/robots.txt   # want 200
   ```
2. **Microsoft 365 connector** — enable it *in this chat* (connector settings)
   so the agent can read/write the tracker in OneDrive.

## Install

```bash
pip install -r requirements.txt   # openpyxl + beautifulsoup4
```

## Configure

```bash
cp config.example.md config.md   # then edit your details + SpareRoom URLs
```

## Run

```bash
# Start narrow to confirm scraping works, then widen:
python run_hunt.py --platforms SpareRoom,OpenRent --debug-dir debug

# Full run:
python run_hunt.py
```

Useful flags: `--platforms` (subset), `--limit N` (cap per search),
`--debug-dir DIR` (dump fetched HTML for selector fixes),
`--tracker PATH`, `--config PATH`.

Verified live: SpareRoom, OpenRent, and Rightmove return listings; **Zoopla is
blocked by Cloudflare (403)** for non-interactive clients and is skipped.

---

## Notes & caveats

- **Anti-bot blocking is the main risk.** Rightmove/Zoopla/SpareRoom fingerprint
  headless + datacenter traffic. Each platform is isolated — if one is blocked
  the run still completes and reports the error. Use `--debug-dir` to capture
  the served HTML and adjust the parser in `scraper/platforms/`.
- **Selectors will drift.** The DOM/JSON shapes here are best-effort and should
  be validated on the first live run; expect to tweak `scraper/platforms/*`.
- **No Gmail in this org.** Email (when re-enabled) would go via Outlook
  (Microsoft 365), not Gmail.
- **Idempotent.** Dedup is by listing URL, so it's safe to run repeatedly.
