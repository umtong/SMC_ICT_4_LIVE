#!/usr/bin/env python3
"""Launch Candidate 16 v8 with Candidate 05 module names taking precedence."""
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

from candidate_v8 import main  # noqa: E402


if __name__ == "__main__":
    main()
