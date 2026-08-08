#!/usr/bin/env python3
"""CLI wrapper for clock-alert initiative-auction execution."""
from __future__ import annotations

import candidate as _base
from backtest_initiative_auction_clock import run_week


_base.run_week = run_week


if __name__ == "__main__":
    raise SystemExit(_base.main())
