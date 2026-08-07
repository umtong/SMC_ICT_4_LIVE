#!/usr/bin/env python3
"""Shared-account research using corrected same-day NAV reporting."""
from __future__ import annotations

from pathlib import Path

import shared_account_research as _base


_base.SHARED_BACKTEST = Path(__file__).resolve().parent / "shared_account_backtest_v2.py"


def main() -> None:
    _base.main()


if __name__ == "__main__":
    main()
