"""Publish five-minute common displacement state to factor-continuation engines."""
from __future__ import annotations

from execution_re1_market_displacement import EasyChartRE1MarketDisplacementStrategy


class FactorDisplacementContinuationMarketStrategy(EasyChartRE1MarketDisplacementStrategy):
    """Broadcast the current broad displacement state before each symbol advances."""

    def _observe_common_factor(self) -> None:
        super()._observe_common_factor()
        for engine in self.scenario_engines.values():
            setter = getattr(engine, "set_market_factor_state", None)
            if setter is not None:
                setter(self.factor_state)
