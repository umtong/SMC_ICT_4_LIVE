#!/usr/bin/env python3
"""Run the existing shared Nautilus account with candidate-16's fixed v47 map."""
from __future__ import annotations

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
CANDIDATE05 = HERE.parents[1] / "candidate-05"

# The active mapping must precede the base module directory.  Importing the
# base runner (rather than executing its file path) prevents Python from
# silently restoring candidate-05 as sys.path[0].
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(CANDIDATE05))

import shared_account_backtest_v2 as app  # noqa: E402


if __name__ == "__main__":
    app.main()
