"""PALIMPSEST x LongMemEval harness.

The eval is a separate uv project so it can pin python >=3.11 independently of
the box default (3.9.6), but it imports `memory/` from the repo root READ-ONLY:
`memory/config.py` is THE shared constant module (FalkorDB host/port, EMBED_DIM)
and `memory/taxonomy.py` is THE relation vocabulary. The benchmark and the demo
share one schema module on purpose — the eval is the removal test for the demo's
central claim, not a side project.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: eval/src/lme/__init__.py -> eval/src/lme -> eval/src -> eval -> repo root
EVAL_ROOT: Path = Path(__file__).resolve().parents[2]
REPO_ROOT: Path = EVAL_ROOT.parent

# Make `memory` importable without installing the repo root as a package.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

__all__ = ["EVAL_ROOT", "REPO_ROOT"]
