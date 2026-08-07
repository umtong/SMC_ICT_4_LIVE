#!/usr/bin/env python3
"""NautilusTrader-only runner for candidate-02 v106."""
from __future__ import annotations

import v53_nt_backtest as _base
from v106_failed_auction_core import (
    FailedAuctionConfig,
    build_rotation_signals,
    build_state,
)

# Reuse the audited NautilusTrader execution/account path. Only the causal
# scenario builder and its configuration type are replaced.
_base.RotationConfig = FailedAuctionConfig
_base.build_state = build_state
_base.build_rotation_signals = build_rotation_signals

run_first_week = _base.run_first_week

if __name__ == "__main__":
    raise SystemExit(_base.main())
