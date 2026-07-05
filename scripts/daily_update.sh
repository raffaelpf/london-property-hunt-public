#!/usr/bin/env bash
# Daily flat-hunt run for scheduled (cron) sessions.
#
# Runs the search on the code branch, then commits the refreshed tracker to a
# dedicated branch (claude/daily-tracker) so the dev branch stays clean and the
# tracker has one commit per day. Requires config.md to already exist locally
# (the trigger writes it; it is gitignored and never committed).
set -euo pipefail

cd "$(dirname "$0")/.."
CODE_BRANCH="claude/london-property-search-analysis-et853y"
TRACKER_BRANCH="claude/daily-tracker"
TRACKER="tracker/london_flat_hunt.xlsx"

git fetch origin --quiet
git checkout "$CODE_BRANCH" --quiet
git pull --ff-only origin "$CODE_BRANCH" --quiet || true

pip install -q -r requirements.txt

if [ ! -f config.md ]; then
  echo "ERROR: config.md missing — the trigger must write it before running." >&2
  exit 1
fi

python3 run_hunt.py --config config.md | tee /tmp/hunt_summary.txt

# Move the fresh tracker onto the dedicated branch and commit just that file.
cp "$TRACKER" /tmp/london_flat_hunt.xlsx
git fetch origin --quiet
if git ls-remote --exit-code --heads origin "$TRACKER_BRANCH" >/dev/null 2>&1; then
  git checkout -f -B "$TRACKER_BRANCH" "origin/$TRACKER_BRANCH" --quiet
else
  git checkout -f -B "$TRACKER_BRANCH" --quiet
fi
cp /tmp/london_flat_hunt.xlsx "$TRACKER"
git add "$TRACKER"
if git -c user.email=noreply@anthropic.com -c user.name=Claude \
     commit -m "Daily tracker update $(date +%F)" --quiet; then
  git push -u origin "$TRACKER_BRANCH"
  echo "Pushed tracker update to $TRACKER_BRANCH"
else
  echo "No tracker changes to commit today"
fi
