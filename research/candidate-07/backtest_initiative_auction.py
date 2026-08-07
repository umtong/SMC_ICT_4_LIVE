"""Nautilus replay composition for initiative-auction ownership.

The existing failed-absorption continuation and three-R cost-floor execution are
retained unchanged. ``strategy_failed_continuation`` already inherits the
source-owned cascade strategy; placing the initiative strategy next in the MRO
adds auction-leg ownership while the source-specific gate remains a harmless,
strict subset. No order, fill, account, PnL, or replay engine is implemented
here.
"""
from __future__ import annotations

import backtest as _base
from strategy_failed_continuation import Candidate07Strategy as _FailedStrategy
from strategy_initiative_auction import Candidate07Strategy as _InitiativeStrategy
from strategy_progress import ThreeRProgressProtectionMixin


class Candidate07Strategy(
    ThreeRProgressProtectionMixin,
    _FailedStrategy,
    _InitiativeStrategy,
):
    """Failed-continuation + initiative-auction state + cost-floor protection."""


_base.Candidate07Strategy = Candidate07Strategy
run_week = _base.run_week

__all__ = ["run_week"]
