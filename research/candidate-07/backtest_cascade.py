"""Select the cascade-aware strategy inside the existing Nautilus replay.

No replay, execution, account, fee, fill, or PnL logic is implemented here.
The module only injects the final strategy class into ``backtest.run_week``;
all mechanics remain in NautilusTrader's BacktestEngine.
"""
from __future__ import annotations

import backtest as _base
from strategy_cascade import Candidate07Strategy


_base.Candidate07Strategy = Candidate07Strategy
run_week = _base.run_week

__all__ = ["run_week"]
