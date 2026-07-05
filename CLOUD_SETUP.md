# Running the hunt in a Claude Code cloud environment

The original skill (`skill.md`) is designed to run **locally** with the
"Claude in Chrome" extension + a Gmail connector. This directory adds a
**self-contained Python pipeline** so the hunt can run **entirely in a Claude
Code web/cloud session** instead — using the pre-installed headless Chromium.

> **Current scope:** scrape → dedup → prioritise → update the Excel tracker →
> print a summary in chat. **No email is sent yet** (that's a later add-on).

---

## What it does

`run_hunt.py` orchestrates the modules in `scraper/`:

| Module | Role |
|---|---|
| `scraper/config.py` | Parse `config.md` (`KEY=value` blocks) |
| `scraper/browser.py` | Launch the pre-installed headless Chromium (`/opt/pw-browsers`) |
| `scraper/platforms/` | Per-site search + parse: SpareRoom, OpenRent (DOM); Rightmove, Zoopla (embedded JSON) |
| `scraper/prioritise.py` | HIGH/MEDIUM/LOW + the mandatory 4+‑bed skip and age flags |
| `scraper/tracker.py` | `openpyxl` read/write with URL dedup + coloured rows (schema in `tracker/README.md`) |
| `scraper/outreach.py` | A `<100`‑word `.txt` message per HIGH listing |

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
pip install -r requirements.txt   # openpyxl + playwright
# Do NOT run `playwright install` — Chromium is pre-installed at /opt/pw-browsers
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
`--debug-dir DIR` (dump fetched HTML for selector fixes), `--headful`,
`--tracker PATH`, `--config PATH`.

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
