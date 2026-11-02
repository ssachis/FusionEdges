#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
export FUSIONEDGES_HOME="$PWD"
python3 -u -m fusionedges.server --port 8101
