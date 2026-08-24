"""Warm-started paper strategy for the canonical RE1 policy."""
from __future__ import annotations

from execution_re1_flow import EasyChartRE1FlowStrategy
from paper_re1_fixed import EasyChartRE1CoherentPaperStrategy


class EasyChartRE1BotPaperStrategy(
    EasyChartRE1CoherentPaperStrategy,
    EasyChartRE1FlowStrategy,
):
    """Use the same flow candles, decisions and account lifecycle as replay."""


__all__ = ["EasyChartRE1BotPaperStrategy"]
