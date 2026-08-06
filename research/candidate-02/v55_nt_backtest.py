#!/usr/bin/env python3
"""Run candidate-02 v55 through the existing NautilusTrader-only runner."""

from __future__ import annotations

import v53_nt_backtest as runner
from v55_nt_core import FrozenAuctionConfig, build_rotation_signals, build_state

# Reuse the exact audited NautilusTrader venue, bracket-order, risk-sizing,
# account, fill, position and NAV path. Only the causal signal state machine is
# replaced for candidate v55.
runner.RotationConfig = FrozenAuctionConfig
runner.build_state = build_state
runner.build_rotation_signals = build_rotation_signals


if __name__ == "__main__":
    raise SystemExit(runner.main())
