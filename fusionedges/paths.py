"""Where FusionEdges keeps downloaded repos (examples/) and results (output/).

Defaults to folders next to the fusionedges package; override with FUSIONEDGES_HOME.
"""
import os
from pathlib import Path

HOME = Path(os.environ.get("FUSIONEDGES_HOME") or Path(__file__).resolve().parent.parent).resolve()
EXAMPLES = HOME / "examples"
OUTPUT = HOME / "output"
DB = OUTPUT / "runs.db"
