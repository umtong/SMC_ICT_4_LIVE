"""Unified continuation context with persistent common-flow memory.

A single BTC/ETH-led one-minute common shock can end its midpoint hold before the
broader information-processing episode has truly changed.  The unified context
router then sees no fast factor and may accept a counter-macro continuation too
early.  Conversely, requiring persistent common flow for every entry would make
opportunity unnecessarily sparse.

This final context policy gives persistent common flow exactly one responsibility:
when no newer instantaneous common shock exists, a persistent same-direction
history acts as the effective common state used by the existing continuation
router.  It can veto an opposing continuation or support a faster counter-macro
transition, but is never an extra requirement for macro-aligned trades.

All auction families, entries, stops, frozen objectives, fixed 3% NAV risk and
the single global position remain unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from easychart_re1_complete_bot_policy_v2 import (
    UNIFIED_CONTINUATION_CONTEXT_RULE,
    EasyChartRE1CompleteBotPolicyV2Bundle,
)
from easychart_re1_auction_router_v6 import PERSISTENT_COMMON_VETO_ONLY_RULE
from execution_re1_factor_persistence import (
    CommonAuctionRegime,
    CommonAuctionSnapshot,
    EasyChartRE1PersistentFactorStrategy,
    PERSISTENT_COMMON_AUCTION_RULE,
)
from execution_re1_market_factor import CommonFactorState


PERSISTENT_UNIFIED_CONTEXT_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "WHEN_NO_NEW_FAST_COMMON_SHOCK_EXISTS_PERSISTENT_BTC_ETH_LED_COMMON_INITIATIVE_SUPPLIES_THE_UNIFIED_CONTINUATION_CONTEXT_WITHOUT_BECOMING_AN_ENTRY_GATE"
)
if PERSISTENT_UNIFIED_CONTEXT_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (PERSISTENT_UNIFIED_CONTEXT_RULE,)


class EasyChartRE1CompleteBotPolicyV3Bundle(EasyChartRE1CompleteBotPolicyV2Bundle):
    """Complete bot policy with fast-or-persistent effective common context."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._fast_common_state: CommonFactorState | None = None
        self._persistent_snapshot: CommonAuctionSnapshot | None = None
        self._persistent_context_counts: dict[str, int] = {}

    def _pcinc(self, key: str) -> None:
        self._persistent_context_counts[key] = self._persistent_context_counts.get(key, 0) + 1

    @staticmethod
    def _persistent_is_effective(snapshot: CommonAuctionSnapshot | None) -> bool:
        return bool(
            snapshot is not None
            and snapshot.regime is CommonAuctionRegime.PERSISTENT
            and snapshot.side is not None
            and getattr(snapshot, "active_matches_history", True)
        )

    def _effective_common_state(self) -> CommonFactorState | None:
        if self._fast_common_state is not None:
            self._pcinc("fast_common_state_used")
            return self._fast_common_state
        snapshot = self._persistent_snapshot
        if not self._persistent_is_effective(snapshot):
            self._pcinc("no_effective_common_state")
            return None
        assert snapshot is not None and snapshot.side is not None
        self._pcinc("persistent_common_state_used")
        return CommonFactorState(
            side=snapshot.side,
            event_time_ns=int(getattr(snapshot, "latest_event_time_ns", 0) or 0),
            event_midpoints={},
            agreeing_symbols=tuple(getattr(snapshot, "latest_agreeing_symbols", ()) or ()),
            sequence=int(getattr(snapshot, "events", 0) or getattr(snapshot, "flips", 0) or 1),
        )

    def _apply_common_state(self) -> None:
        super().set_market_factor_state(self._effective_common_state())

    def set_market_factor_state(self, state: CommonFactorState | None) -> None:
        self._fast_common_state = state
        self._apply_common_state()

    def set_common_auction_snapshot(self, snapshot: CommonAuctionSnapshot) -> None:
        self._persistent_snapshot = snapshot
        self._apply_common_state()

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        snapshot = self._persistent_snapshot
        output["persistent_unified_context"] = {
            "counts": dict(sorted(self._persistent_context_counts.items())),
            "snapshot": None
            if snapshot is None
            else {
                "regime": snapshot.regime.value,
                "side": None if snapshot.side is None else snapshot.side.name,
                "active_matches_history": getattr(snapshot, "active_matches_history", None),
                "latest_event_time_ns": getattr(snapshot, "latest_event_time_ns", None),
            },
            "rules": (
                PERSISTENT_COMMON_AUCTION_RULE,
                PERSISTENT_COMMON_VETO_ONLY_RULE,
                UNIFIED_CONTINUATION_CONTEXT_RULE,
                PERSISTENT_UNIFIED_CONTEXT_RULE,
            ),
        }
        return output


class EasyChartRE1CompletePersistentStrategy(EasyChartRE1PersistentFactorStrategy):
    """Propagate the fast factor and the persistent snapshot to every bundle."""

    def _current_snapshot(self) -> CommonAuctionSnapshot | None:
        for name in (
            "common_snapshot",
            "_common_snapshot",
            "auction_snapshot",
            "_auction_snapshot",
            "current_common_snapshot",
        ):
            value = getattr(self, name, None)
            if isinstance(value, CommonAuctionSnapshot):
                return value
        for value in vars(self).values():
            if isinstance(value, CommonAuctionSnapshot):
                return value
        return None

    def _observe_common_factor(self) -> None:
        super()._observe_common_factor()
        snapshot = self._current_snapshot()
        fast = getattr(self, "factor_state", None)
        for bundle in self.scenario_engines.values():
            if snapshot is not None:
                setter = getattr(bundle, "set_common_auction_snapshot", None)
                if setter is not None:
                    setter(snapshot)
            setter = getattr(bundle, "set_market_factor_state", None)
            if setter is not None:
                setter(fast)


MultiScaleScenarioBundle = EasyChartRE1CompleteBotPolicyV3Bundle
StrategyClass = EasyChartRE1CompletePersistentStrategy
