"""Nautilus replay composition for immediate initiative-auction execution."""
from __future__ import annotations

import backtest as _base
from backtest_initiative_auction import Candidate07Strategy as _InitiativeStrategy
from strategy_immediate_causal_entry import ImmediateCausalEntryMixin


class Candidate07Strategy(
    ImmediateCausalEntryMixin,
    _InitiativeStrategy,
):
    """Initiative candidate with only the artificial minute delay removed."""


_base.Candidate07Strategy = Candidate07Strategy
run_week = _base.run_week

__all__ = ["Candidate07Strategy", "run_week"]
