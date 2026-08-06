"""Select the final causal strategy inside the existing Nautilus replay.

No replay, execution, account, fee, fill, or PnL logic is implemented here.
The module composes the cascade, same-shock failed-absorption continuation, and
actual-entry target geometry into ``backtest.run_week``; all mechanics remain
in NautilusTrader's BacktestEngine.
"""
from __future__ import annotations

import backtest as _base
from strategy_failed_continuation import Candidate07Strategy as _CausalStrategy
from strategy_geometry import Candidate07Strategy as _GeometryStrategy


class Candidate07Strategy(_CausalStrategy, _GeometryStrategy):
    """Final MRO: causal states first, corrected submission geometry second."""


_base.Candidate07Strategy = Candidate07Strategy
run_week = _base.run_week

__all__ = ["run_week"]
