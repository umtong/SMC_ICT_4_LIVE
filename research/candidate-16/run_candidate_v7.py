#!/usr/bin/env python3
"""Launch Candidate 16 v7 with Candidate 05 module names taking precedence.

Executing a script by path prepends its directory to ``sys.path`` ahead of the
``PYTHONPATH`` environment. Candidate 16 and Candidate 05 intentionally contain
several historical modules with the same short name (for example
``strategy_v4``), while the reused v52 lineage requires Candidate 05's module.
This launcher establishes the correct source namespace before importing the
registration-only adapter. It changes no economic code or configuration.
"""
from __future__ import annotations

from pathlib import Path
import sys


RESEARCH_ROOT = Path(__file__).resolve().parents[1]
CANDIDATE05 = str(RESEARCH_ROOT / "candidate-05")
CANDIDATE16 = str(RESEARCH_ROOT / "candidate-16")
for value in (CANDIDATE05, CANDIDATE16):
    while value in sys.path:
        sys.path.remove(value)
sys.path.insert(0, CANDIDATE16)
sys.path.insert(0, CANDIDATE05)

from candidate_v7 import main  # noqa: E402


if __name__ == "__main__":
    main()
