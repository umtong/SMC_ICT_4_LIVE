"""Nautilus replay composition for clock-alert initiative execution."""
from __future__ import annotations

import backtest as _base
from backtest_initiative_auction import Candidate07Strategy as _InitiativeStrategy
from strategy_clock_alert_entry import ClockAlertCausalEntryMixin


class Candidate07Strategy(
    ClockAlertCausalEntryMixin,
    _InitiativeStrategy,
):
    """Initiative candidate submitted between data events by the engine clock."""


_base.Candidate07Strategy = Candidate07Strategy
run_week = _base.run_week

__all__ = ["Candidate07Strategy", "run_week"]
