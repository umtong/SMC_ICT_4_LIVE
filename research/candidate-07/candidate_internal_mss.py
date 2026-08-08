#!/usr/bin/env python3
"""CLI wrapper for the internal-boundary MSS initiative candidate."""
from __future__ import annotations

import candidate as _base
from backtest_internal_mss import run_week


_base.run_week = run_week


if __name__ == "__main__":
    raise SystemExit(_base.main())
