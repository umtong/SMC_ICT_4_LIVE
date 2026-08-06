"""Select limit-parent execution for the combined causal auction router."""
from __future__ import annotations

import backtest_flow as _backtest_flow
from strategy_auction_limit import Candidate07AuctionLimitStrategy

_backtest_flow.Candidate07FlowStrategy = Candidate07AuctionLimitStrategy
run_week = _backtest_flow.run_week

__all__ = ["run_week"]
