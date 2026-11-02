#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
export FUSIONEDGES_HOME="$PWD"
echo "Optional GitHub connection service. Open FusionEdges at http://127.0.0.1:8000 and click Connect GitHub."
python3 -u -m fusionedges.server --port 8101 --github-workspace --no-open
