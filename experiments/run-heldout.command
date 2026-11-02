#!/bin/zsh -l
# Double-click once a day. Runs the held-out experiment in order (screening -> main -> multi-step), resuming where
# it stopped; it stops by itself when the day's free quota is used up.
cd "$(dirname "$0")/.."
LOG="experiments/logs/heldout-$(date +%Y%m%d-%H%M%S).log"
echo "FusionEdges held-out experiment. Log: $LOG"
# checks v5 + per-run metrics for every earlier run (no AI calls), then condition M (port v3): no quota needed
python3 -u -m fusionedges rescore 2>&1 | tail -3 | tee -a "$LOG"
python3 -u -m fusionedges experiment experiments/heldout-port-v3.json 2>&1 | tee -a "$LOG"
python3 -u -m fusionedges metrics 2>&1 | tee -a "$LOG"
if ! python3 -m fusionedges providers | grep -q '^ON   gemini'; then
  echo "NO_KEY: GEMINI_API_KEY is not visible here. Add 'export GEMINI_API_KEY=...' to ~/.zshrc, or run: export GEMINI_API_KEY=... && zsh experiments/run-heldout.command" | tee -a "$LOG"; exit 1
fi
python3 -c "import playwright" 2>/dev/null || echo "NOTE: Playwright missing, so no browser check. Install once: pip3 install playwright && python3 -m playwright install chromium" | tee -a "$LOG"
for stage in screening main multistep; do
  f="experiments/heldout-$stage.json"
  if grep -q '"FILL FROM SCREENING"' "$f"; then
    echo "\n$stage: waiting for the screening result to pick the tasks (Claude fills $f by the frozen rule)." | tee -a "$LOG"; break
  fi
  python3 -u -m fusionedges experiment "$f" 2>&1 | tee -a "$LOG"
  if tail -5 "$LOG" | grep -q "QUOTA REACHED"; then echo "\nStopped for today (quota). Double-click again tomorrow." | tee -a "$LOG"; exit 0; fi
done
echo "DONE for now" | tee -a "$LOG"
