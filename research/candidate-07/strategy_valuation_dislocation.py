"""NautilusTrader strategy for valuation-dislocation reversion."""
from __future__ import annotations

import json

from model_valuation_dislocation import (
    ValuationDislocationRouter,
    ValuationLogicConfig,
)
from strategy import Candidate07Strategy as ExecutionStrategy
from strategy_positioning import Candidate07PositioningStrategy


class Candidate07ValuationStrategy(Candidate07PositioningStrategy):
    """Reuse verified flow/OI transport and Nautilus execution only."""

    def __init__(self, config):
        # Bypass the retired external-sweep schema parser while retaining the
        # completed-data subscriptions and on_bar transport implemented by
        # Candidate07PositioningStrategy.
        ExecutionStrategy.__init__(self, config)
        self.logic = ValuationLogicConfig.from_mapping(
            json.loads(config.positioning_logic_json)
        )
        self.router = ValuationDislocationRouter(self.logic)
        self._bucket = []
        self._signal_index = 0
        self._flow_by_ts = {}
        self._positioning_by_ts = {}


__all__ = ["Candidate07ValuationStrategy"]
