"""Use persistent broad flow only as an opposing local-auction veto.

An instantaneous BTC/ETH-led one-minute shock is useful but often relinquishes
its midpoint before the broader information-processing episode has finished.
At that point a local countertrend BOS can look complete even while common
initiative is still persistent across successive events.  Requiring persistent
common flow to create every trade was previously too sparse; ignoring it after
one midpoint loss was too permissive.

This policy reuses the existing persistent common-auction state for one narrow
responsibility: when no newer instantaneous common shock exists, a persistent
same-direction history acts as the effective common-factor veto against an
opposing local continuation.  Persistent flow is never an additional AND gate
for aligned entries.  Turbulent, transitional and unknown histories impose no
veto.  A new instantaneous common shock takes precedence, allowing genuine
transitions to be processed immediately.

All scenario, response, stop, objective, account, risk and execution rules are
unchanged from v5.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from easychart_re1_auction_router_v5 import EasyChartRE1AuctionRouterV5Bundle
from execution_re1_factor_persistence import (
    CommonAuctionRegime,
    CommonAuctionSnapshot,
    EasyChartRE1PersistentFactorStrategy,
    PERSISTENT_COMMON_AUCTION_RULE,
)
from execution_re1_market_factor import CommonFactorState


PERSISTENT_COMMON_VETO_ONLY_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "PERSISTENT_BTC_ETH_LED_COMMON_INITIATIVE_IS_AN_OPPOSING_LOCAL_CONTINUATION_VETO_ONLY_AND_NEVER_AN_ENTRY_REQUIREMENT"
)
if PERSISTENT_COMMON_VETO_ONLY_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (PERSISTENT_COMMON_VETO_ONLY_RULE,)


class EasyChartRE1AuctionRouterV6Bundle(EasyChartRE1AuctionRouterV5Bundle):
    """v5 auctions with instantaneous-or-persistent effective common context."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._instantaneous_common_state: CommonFactorState | None = None
        self._persistent_common_snapshot: CommonAuctionSnapshot | None = None
        self._persistent_veto_counts: dict[str, int] = {}

    def _pvinc(self, key: str) -> None:
        self._persistent_veto_counts[key] = self._persistent_veto_counts.get(key, 0) + 1

    @staticmethod
    def _persistent_effective(snapshot: CommonAuctionSnapshot | None) -> bool:
        if snapshot is None:
            return False
        return (
            snapshot.regime is CommonAuctionRegime.PERSISTENT
            and snapshot.side is not None
            and bool(getattr(snapshot, "active_matches_history", True))
        )

    def _effective_state(self) -> CommonFactorState | None:
        if self._instantaneous_common_state is not None:
            self._pvinc("instantaneous_common_state_used")
            return self._instantaneous_common_state
        snapshot = self._persistent_common_snapshot
        if not self._persistent_effective(snapshot):
            self._pvinc("no_effective_common_veto")
            return None
        assert snapshot is not None and snapshot.side is not None
        self._pvinc("persistent_common_state_used_as_veto")
        return CommonFactorState(
            side=snapshot.side,
            event_time_ns=int(getattr(snapshot, "latest_event_time_ns", 0) or 0),
            event_midpoints={},
            agreeing_symbols=tuple(getattr(snapshot, "latest_agreeing_symbols", ()) or ()),
            sequence=int(getattr(snapshot, "events", 0) or getattr(snapshot, "flips", 0) or 1),
        )

    def _apply_effective_state(self) -> None:
        super().set_market_factor_state(self._effective_state())

    def set_market_factor_state(self, state: CommonFactorState | None) -> None:
        self._instantaneous_common_state = state
        self._apply_effective_state()

    def set_common_auction_snapshot(self, snapshot: CommonAuctionSnapshot) -> None:
        self._persistent_common_snapshot = snapshot
        self._apply_effective_state()

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        snapshot = self._persistent_common_snapshot
        output["persistent_common_veto_only"] = {
            "counts": dict(sorted(self._persistent_veto_counts.items())),
            "snapshot": None
            if snapshot is None
            else {
                "regime": snapshot.regime.value,
                "side": None if snapshot.side is None else snapshot.side.name,
                "active_matches_history": getattr(snapshot, "active_matches_history", None),
                "latest_event_time_ns": getattr(snapshot, "latest_event_time_ns", None),
                "flips": getattr(snapshot, "flips", None),
            },
            "rules": (
                PERSISTENT_COMMON_AUCTION_RULE,
                PERSISTENT_COMMON_VETO_ONLY_RULE,
            ),
        }
        return output


class EasyChartRE1PersistentVetoStrategy(EasyChartRE1PersistentFactorStrategy):
    """Propagate both fast and persistent common states to every symbol bundle."""

    def _snapshot(self) -> CommonAuctionSnapshot | None:
        for name in ("common_snapshot", "_common_snapshot", "auction_snapshot"):
            value = getattr(self, name, None)
            if isinstance(value, CommonAuctionSnapshot):
                return value
        return None

    def _observe_common_factor(self) -> None:
        super()._observe_common_factor()
        snapshot = self._snapshot()
        instantaneous = getattr(self, "factor_state", None)
        for bundle in self.scenario_engines.values():
            persistent_setter = getattr(bundle, "set_common_auction_snapshot", None)
            if snapshot is not None and persistent_setter is not None:
                persistent_setter(snapshot)
            fast_setter = getattr(bundle, "set_market_factor_state", None)
            if fast_setter is not None:
                fast_setter(instantaneous)


MultiScaleScenarioBundle = EasyChartRE1AuctionRouterV6Bundle
StrategyClass = EasyChartRE1PersistentVetoStrategy
