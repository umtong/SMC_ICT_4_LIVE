"""Select the combined auction strategy inside the existing Nautilus replay."""
from __future__ import annotations

import backtest_flow as _backtest_flow
from strategy_auction import Candidate07AuctionStrategy

_backtest_flow.Candidate07FlowStrategy = Candidate07AuctionStrategy
run_week = _backtest_flow.run_week

__all__ = ["run_week"]
