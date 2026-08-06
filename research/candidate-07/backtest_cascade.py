"""Select the final causal strategy inside the existing Nautilus replay.

No replay, execution, account, fee, fill, or PnL logic is implemented here.
The module composes failed-absorption cascade memory, same-signal-shock
continuation, and three-R structural-target protection inside
``backtest.run_week``. Targets remain the opposing liquidity levels declared by
the causal scenario; all execution mechanics stay inside NautilusTrader's
BacktestEngine.
"""
from __future__ import annotations

import backtest as _base
from strategy_failed_continuation import Candidate07Strategy as _CausalStrategy
from strategy_progress import ThreeRProgressProtectionMixin


class Candidate07Strategy(ThreeRProgressProtectionMixin, _CausalStrategy):
    """Causal routing plus one-time cost-floor stop modification after +3R."""


_base.Candidate07Strategy = Candidate07Strategy
run_week = _base.run_week

__all__ = ["run_week"]
