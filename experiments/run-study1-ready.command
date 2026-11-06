#!/bin/zsh
# Study 1 readiness check: builds each pinned host and checks its page. No AI, no tokens, nothing written to runs.db.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p experiments/logs
LOG="experiments/logs/study-1-ready-$(date +%Y%m%d-%H%M%S).log"
python3 -u experiments/study1_ready.py experiments/study-1-navbar.json 2>&1 | tee "$LOG"
echo "\nDone. Results: experiments/study-1/readiness.json  (log: $LOG)"
