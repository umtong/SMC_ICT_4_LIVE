#!/usr/bin/env python3
"""CLI compatibility wrapper for the persistent initiative-probe candidate."""
from __future__ import annotations

import candidate as _base
from backtest_initiative_probe import run_week


_base.run_week = run_week


if __name__ == "__main__":
    raise SystemExit(_base.main())
