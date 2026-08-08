#!/usr/bin/env python3
"""NautilusTrader-only runner for candidate-02 v142."""
from __future__ import annotations

import v53_nt_backtest as _base
from v142_dual_auction_core import DualAuctionConfig, build_rotation_signals, build_state

_base.RotationConfig = DualAuctionConfig
_base.build_state = build_state
_base.build_rotation_signals = build_rotation_signals
run_first_week = _base.run_first_week

if __name__ == "__main__":
    raise SystemExit(_base.main())
