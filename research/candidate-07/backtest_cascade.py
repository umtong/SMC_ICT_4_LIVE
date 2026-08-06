"""Select the final causal strategy inside the existing Nautilus replay.

No replay, execution, account, fee, fill, or PnL logic is implemented here.
The module only injects the cascade plus failed-absorption continuation strategy
into ``backtest.run_week``; all mechanics remain in NautilusTrader's
BacktestEngine.
"""
from __future__ import annotations

import backtest as _base
from strategy_failed_continuation import Candidate07Strategy


_base.Candidate07Strategy = Candidate07Strategy
run_week = _base.run_week

__all__ = ["run_week"]
