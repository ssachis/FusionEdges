#!/bin/zsh -l
# Double-click to run the Next.js -> React navbar pilot (4 tasks x agent alone / with report).
cd "$(dirname "$0")/.."
LOG="experiments/logs/pilot-$(date +%Y%m%d-%H%M%S).log"
echo "FusionEdges pilot. Log: $LOG"
python3 -m fusionedges providers | tee -a "$LOG"
if ! python3 -m fusionedges providers | grep -q '^ON'; then
  echo "NO_KEYS: no AI key is visible to this Terminal. Add 'export GEMINI_API_KEY=...' to ~/.zshrc or run the command yourself." | tee -a "$LOG"
  exit 1
fi
# one fixed provider for a fair comparison (proposal: same pinned model for every condition)
if [ -n "$GEMINI_API_KEY" ]; then export FUSIONEDGES_PROVIDERS=gemini; fi
echo "Using providers: ${FUSIONEDGES_PROVIDERS:-fallback chain}" | tee -a "$LOG"
python3 -u -m fusionedges experiment experiments/next-to-react-navbar.json 2>&1 | tee -a "$LOG"
echo "DONE" | tee -a "$LOG"
