"""Nautilus replay composition for initiative-auction ownership.

The failed-absorption continuation runs directly over the initiative-auction
state machine. The old source-specific cascade is deliberately absent because
initiative ownership subsumes it; layering both would assign two persistent
owners to one terminal market event. Three-R cost-floor execution remains the
existing mixin. No order, fill, account, PnL, or replay engine is implemented
here.
"""
from __future__ import annotations

import backtest as _base
from strategy_failed_continuation_initiative import (
    Candidate07Strategy as _FailedInitiativeStrategy,
)
from strategy_progress import ThreeRProgressProtectionMixin


class Candidate07Strategy(
    ThreeRProgressProtectionMixin,
    _FailedInitiativeStrategy,
):
    """Initiative state + failed continuation + cost-floor protection."""


_base.Candidate07Strategy = Candidate07Strategy
run_week = _base.run_week

__all__ = ["run_week"]
