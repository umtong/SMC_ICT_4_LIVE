"""Compose the strongest historical cascade route with source-owned memory.

No replay, execution, account, fee, fill, or PnL logic is implemented here.
The module selects the existing causal failed-absorption continuation strategy,
whose cascade gate is now owned by the failed liquidity source, and the existing
three-R cost-floor protection mixin inside the common NautilusTrader replay.
"""
from __future__ import annotations

import backtest as _base
from strategy_failed_continuation import Candidate07Strategy as _CausalStrategy
from strategy_progress import ThreeRProgressProtectionMixin


class Candidate07Strategy(ThreeRProgressProtectionMixin, _CausalStrategy):
    """Source-owned cascade plus one-time cost-floor stop after completed +3R."""


_base.Candidate07Strategy = Candidate07Strategy
run_week = _base.run_week

__all__ = ["run_week"]
