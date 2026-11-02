#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/.."
export FUSIONEDGES_HOME="$PWD"
if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "Run from the terminal where GEMINI_API_KEY is exported."
  exit 1
fi
echo "Resuming archived v1 unchanged. This version has a known Gemini tool-signature bug; use the corrected v2 pilot for new evaluation."
mkdir -p experiments/logs
PILOT_LOG="experiments/logs/adaptive-pilot-v1-$(date +%Y%m%d-%H%M%S).log"
python3 -u -c 'import sys; sys.path.insert(0,"experiments/frozen/adaptive-pilot-v1"); from fusionedges.pilot import main; main()' experiments/adaptive-pilot-v1.json --run 2>&1 | tee "$PILOT_LOG"
