#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
CANDIDATE16 = HERE.parent
CANDIDATE05 = HERE.parents[1] / "candidate-05"
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(CANDIDATE16))
sys.path.insert(2, str(CANDIDATE05))

import shared_account_backtest_v2 as app  # noqa: E402

if __name__ == "__main__":
    app.main()
