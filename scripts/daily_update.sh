#!/usr/bin/env bash
# Daily flat-hunt run for scheduled (cron) sessions.
#
# Stays on the checked-out branch the whole time (main, in a routine — the repo
# is cloned fresh from the default branch each run). It never switches branches:
# it seeds the working tracker from the latest committed copy on the data branch
# so dedup/accumulation carries across days, runs the hunt, then commits ONLY
# the updated tracker onto claude/daily-tracker via git plumbing and pushes.
# Requires config.md to exist locally (the routine writes it; gitignored).
set -euo pipefail

cd "$(dirname "$0")/.."
TRACKER_BRANCH="claude/daily-tracker"
TRACKER="tracker/london_flat_hunt.xlsx"

git fetch origin --quiet || true
pip install -q -r requirements.txt

if [ ! -f config.md ]; then
  echo "ERROR: config.md missing — the routine must write it before running." >&2
  exit 1
fi

# Seed the working tracker with the latest accumulated copy from the data branch
# (without checking it out) so today's run dedups against all prior days.
if git cat-file -e "origin/$TRACKER_BRANCH:$TRACKER" 2>/dev/null; then
  git show "origin/$TRACKER_BRANCH:$TRACKER" > "$TRACKER"
fi

python3 run_hunt.py --config config.md | tee /tmp/hunt_summary.txt

# Commit ONLY the updated tracker onto the data branch using plumbing — the
# working tree and current branch (main) are left untouched.
if git rev-parse --verify "origin/$TRACKER_BRANCH" >/dev/null 2>&1; then
  BASE=$(git rev-parse "origin/$TRACKER_BRANCH")
else
  BASE=$(git rev-parse HEAD)   # first run: branch off the current commit
fi

BLOB=$(git hash-object -w "$TRACKER")
TMP_INDEX=$(mktemp)
GIT_INDEX_FILE="$TMP_INDEX" git read-tree "$BASE"
GIT_INDEX_FILE="$TMP_INDEX" git update-index --add --cacheinfo "100644,$BLOB,$TRACKER"
TREE=$(GIT_INDEX_FILE="$TMP_INDEX" git write-tree)
rm -f "$TMP_INDEX"

if [ "$TREE" = "$(git rev-parse "$BASE^{tree}")" ]; then
  echo "No tracker changes to commit today"
  exit 0
fi

COMMIT=$(GIT_AUTHOR_NAME=Claude GIT_AUTHOR_EMAIL=noreply@anthropic.com \
         GIT_COMMITTER_NAME=Claude GIT_COMMITTER_EMAIL=noreply@anthropic.com \
         git commit-tree "$TREE" -p "$BASE" -m "Daily tracker update $(date +%F)")
git push origin "$COMMIT:refs/heads/$TRACKER_BRANCH"
echo "Pushed tracker update to $TRACKER_BRANCH ($COMMIT)"
