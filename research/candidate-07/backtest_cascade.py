"""Select the final causal strategy inside the existing Nautilus replay.

No replay, execution, account, fee, fill, or PnL logic is implemented here.
The module composes failed-absorption cascade memory with the same-signal-shock
continuation route inside ``backtest.run_week``. Targets remain the opposing
liquidity levels declared by the causal scenario; all execution mechanics stay
inside NautilusTrader's BacktestEngine.
"""
from __future__ import annotations

import backtest as _base
from strategy_failed_continuation import Candidate07Strategy


_base.Candidate07Strategy = Candidate07Strategy
run_week = _base.run_week

__all__ = ["run_week"]
