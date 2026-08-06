#!/usr/bin/env python3
"""Run candidate-02 v90 exclusively through NautilusTrader."""
from __future__ import annotations

import v53_nt_backtest as runner
from v90_common_failed_auction_core import CommonFailedAuctionConfig, build_rotation_signals, build_state

runner.RotationConfig = CommonFailedAuctionConfig
runner.build_state = build_state
runner.build_rotation_signals = build_rotation_signals

if __name__ == "__main__":
    raise SystemExit(runner.main())
