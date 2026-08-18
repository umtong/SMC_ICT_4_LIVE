"""Execution bindings for the hierarchical integrated auction policy."""
from __future__ import annotations

from typing import Any

from execution_breakthrough import (
    IntrinsicAuctionExecutionStrategy,
    IntrinsicAuctionShadowStrategy,
)


class _PropagateCommonFactorMixin:
    """Make the completed four-market state visible to each local auction.

    ML features already observed the factor, but the prior bundle never received
    it and therefore could not distinguish a local trap from an active common
    shock.  Propagation occurs before the engines process the same completed
    bucket, so no future information is introduced.
    """

    def _observe_common_factor(self) -> None:
        super()._observe_common_factor()
        for bundle in self.scenario_engines.values():
            setter = getattr(bundle, "set_market_factor_state", None)
            if setter is not None:
                setter(getattr(self, "factor_state", None))


class IntegratedAuctionShadowStrategy(
    _PropagateCommonFactorMixin,
    IntrinsicAuctionShadowStrategy,
):
    pass


class IntegratedAuctionExecutionStrategy(IntrinsicAuctionExecutionStrategy):
    """Baseline market-entry transport; selection is added only after evidence."""

    pass
