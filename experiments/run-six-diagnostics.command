#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/.."
export FUSIONEDGES_HOME="$PWD"
mkdir -p experiments/logs
python3 -u experiments/frozen/diagnostic-six-2026-11-02/run.py 2>&1 | tee -a experiments/logs/diagnostic-six-2026-11-02.log
