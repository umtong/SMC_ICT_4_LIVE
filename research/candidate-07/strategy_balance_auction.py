"""NautilusTrader strategy for the balance-to-initiative auction."""
from __future__ import annotations

import json

from model_balance_auction import BalanceInitiativeRouter, BalanceLogicConfig
from strategy import Candidate07Strategy as ExecutionStrategy
from strategy_positioning import Candidate07PositioningStrategy


class Candidate07BalanceStrategy(Candidate07PositioningStrategy):
    """Reuse verified flow/positioning transport and Nautilus execution."""

    def __init__(self, config):
        # The parent positioning strategy parses the retired sweep schema. The
        # shared Nautilus execution state is initialized directly, while its
        # completed-data subscriptions and on_bar transport remain inherited.
        ExecutionStrategy.__init__(self, config)
        self.logic = BalanceLogicConfig.from_mapping(
            json.loads(config.positioning_logic_json)
        )
        self.router = BalanceInitiativeRouter(self.logic)
        self._bucket = []
        self._signal_index = 0
        self._flow_by_ts = {}
        self._positioning_by_ts = {}


__all__ = ["Candidate07BalanceStrategy"]
