#!/usr/bin/env bash
# Fallback runner for WSL / Linux / macOS, for when GitHub's servers can't
# reach your university's Moodle (some Israeli universities block foreign IPs).
# Runs the sync locally (Moodle → Todoist), then pushes the updated state file.
#
# Schedule from WSL crontab (daily 10:00):   0 10 * * * /path/to/repo/scripts/run_local.sh >> /tmp/moodle-sync.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --quiet --rebase
python3 -m moodle_sync
git add data/
if ! git diff --cached --quiet; then
  git commit --quiet -m "sync (local): update Moodle → Todoist state"
  git push --quiet
fi
