"""Persistent common-auction routing for EasyChart RE1.

A one-minute common shock is evidence of broad initiative, but neither its
single-candle midpoint nor a permanent last-direction label is a sufficient
market state.  The router therefore keeps the ordered sequence of completed
BTC/ETH/SOL/XRP common-initiative events and distinguishes two responsibilities:

* the event history identifies persistence or turbulence;
* the currently active midpoint state identifies whether generic plans may be
  routed immediately, while a persistent footprint may survive the later
  rebalance through a dedicated continuation engine.

The latest six common shocks define the state without a fitted clock timeout:
PERSISTENT has at most one direction change, TURBULENT has at least three, and
TRANSITIONAL lies between.  Generic plans in a persistent state require the
active common factor, matching direction, and participation by the traded
symbol.  Turbulent states abstain.  Transitional states retain completed visual
OB/FVG and major-liquidity plans but do not let one flow proxy originate a trade.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Side
from execution_re1_market_factor import (
    CROSS_ASSET_COMMON_INITIATIVE_RULE,
    EasyChartRE1MarketFactorStrategy,
)

PERSISTENT_COMMON_AUCTION_RULE = (
    "EXTERNAL_METHOD:ORDERED_COMMON_FLOW_EVENTS_DEFINE_PERSISTENT_TRANSITIONAL_AND_TURBULENT_AUCTION_STATES"
)
PERSISTENT_ALIGNED_ROUTING_RULE = (
    "RESEARCH_HYPOTHESIS:PERSISTENT_COMMON_AUCTION_EXECUTES_GENERIC_PLANS_ONLY_WHILE_THE_ACTIVE_FACTOR_IS_ALIGNED_AND_THE_SYMBOL_PARTICIPATED"
)
TURBULENT_ABSTENTION_RULE = (
    "RESEARCH_HYPOTHESIS:THREE_OR_MORE_DIRECTION_CHANGES_INSIDE_THE_LATEST_SIX_COMMON_SHOCKS_REQUIRE_A_DEDICATED_CONTROL_TRANSFER_BEFORE_ANY_TRADE"
)
TRANSITIONAL_VISUAL_OWNERSHIP_RULE = (
    "RESEARCH_HYPOTHESIS:TRANSITIONAL_COMMON_AUCTION_RETAINS_VISUAL_OB_FVG_OR_MAJOR_LIQUIDITY_PLANS_BUT_NOT_SINGLE_BAR_FLOW_SUBSTITUTION"
)
if PERSISTENT_COMMON_AUCTION_RULE not in _contracts.EXTERNAL_RULES:
    _contracts.EXTERNAL_RULES += (PERSISTENT_COMMON_AUCTION_RULE,)
for _rule in (
    PERSISTENT_ALIGNED_ROUTING_RULE,
    TURBULENT_ABSTENTION_RULE,
    TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class CommonAuctionRegime(str, Enum):
    UNKNOWN = "UNKNOWN"
    PERSISTENT = "PERSISTENT"
    TRANSITIONAL = "TRANSITIONAL"
    TURBULENT = "TURBULENT"


@dataclass(frozen=True, slots=True)
class CommonAuctionEvent:
    side: Side
    event_time_ns: int
    agreeing_symbols: tuple[str, ...]
    sequence: int


@dataclass(frozen=True, slots=True)
class CommonAuctionSnapshot:
    regime: CommonAuctionRegime
    side: Side | None
    flips: int
    events: int
    latest_event_time_ns: int | None
    latest_agreeing_symbols: tuple[str, ...]
    active_side: Side | None
    active_event_time_ns: int | None

    @property
    def active_matches_history(self) -> bool:
        return self.side is not None and self.active_side is self.side


class EasyChartRE1PersistentFactorStrategy(EasyChartRE1MarketFactorStrategy):
    """One-account router using common-flow persistence and selective abstention."""

    HISTORY_EVENTS = 6

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.common_event_history: deque[CommonAuctionEvent] = deque(maxlen=self.HISTORY_EVENTS)
        self._last_recorded_factor_time_ns: int | None = None
        self.persistent_factor_counts: dict[str, int] = {}

    def _pinc(self, key: str) -> None:
        self.persistent_factor_counts[key] = self.persistent_factor_counts.get(key, 0) + 1

    def _publish_snapshot(self, snapshot: CommonAuctionSnapshot) -> None:
        for engine in self.scenario_engines.values():
            setter = getattr(engine, "set_common_auction_snapshot", None)
            if setter is not None:
                setter(snapshot)

    def _observe_common_factor(self) -> None:
        super()._observe_common_factor()
        state = self.factor_state
        if state is not None and state.event_time_ns != self._last_recorded_factor_time_ns:
            self._last_recorded_factor_time_ns = state.event_time_ns
            self.common_event_history.append(
                CommonAuctionEvent(
                    side=state.side,
                    event_time_ns=state.event_time_ns,
                    agreeing_symbols=tuple(state.agreeing_symbols),
                    sequence=state.sequence,
                )
            )
            self._pinc("common_event_recorded")
        self._publish_snapshot(self._auction_snapshot())

    def _auction_snapshot(self) -> CommonAuctionSnapshot:
        events = tuple(self.common_event_history)
        active = self.factor_state
        if not events:
            return CommonAuctionSnapshot(
                CommonAuctionRegime.UNKNOWN,
                None,
                0,
                0,
                None,
                (),
                None if active is None else active.side,
                None if active is None else active.event_time_ns,
            )
        flips = sum(events[i].side is not events[i - 1].side for i in range(1, len(events)))
        latest = events[-1]
        if len(events) < self.HISTORY_EVENTS:
            regime = CommonAuctionRegime.UNKNOWN
        elif flips <= 1:
            regime = CommonAuctionRegime.PERSISTENT
        elif flips >= 3:
            regime = CommonAuctionRegime.TURBULENT
        else:
            regime = CommonAuctionRegime.TRANSITIONAL
        return CommonAuctionSnapshot(
            regime,
            latest.side,
            flips,
            len(events),
            latest.event_time_ns,
            latest.agreeing_symbols,
            None if active is None else active.side,
            None if active is None else active.event_time_ns,
        )

    @staticmethod
    def _visual_trigger(plan: V5TradePlan) -> bool:
        kind = str(plan.trigger_zone_kind)
        return kind in {"ORDER_BLOCK", "FVG"} or (
            not kind.startswith("FLOW_")
            and "ABSORPTION" not in kind
            and "INITIATIVE" not in kind
        )

    def _record_regime_rejection(
        self,
        plan: V5TradePlan,
        snapshot: CommonAuctionSnapshot,
        reason: str,
    ) -> None:
        self._record(
            "persistent_factor_plan_rejected",
            plan_id=plan.plan_id,
            instrument_id=plan.symbol,
            plan_side=plan.side.name,
            regime=snapshot.regime.value,
            regime_side=None if snapshot.side is None else snapshot.side.name,
            active_side=None if snapshot.active_side is None else snapshot.active_side.name,
            regime_flips=snapshot.flips,
            regime_events=snapshot.events,
            latest_factor_event_time_ns=snapshot.latest_event_time_ns,
            active_factor_event_time_ns=snapshot.active_event_time_ns,
            latest_agreeing_symbols=list(snapshot.latest_agreeing_symbols),
            scenario_path=plan.scenario_path,
            scale_name=plan.scale_name,
            trigger_zone_kind=str(plan.trigger_zone_kind),
            reason=reason,
            rule_provenance=(
                PERSISTENT_COMMON_AUCTION_RULE,
                PERSISTENT_ALIGNED_ROUTING_RULE,
                TURBULENT_ABSTENTION_RULE,
                TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
            ),
        )

    def _factor_allows(self, plan: V5TradePlan) -> bool:
        snapshot = self._auction_snapshot()
        regime = snapshot.regime
        self._pinc(f"plan_seen_{regime.value.lower()}")
        if regime is CommonAuctionRegime.PERSISTENT and snapshot.active_matches_history:
            aligned = snapshot.side is plan.side
            participated = plan.symbol in snapshot.latest_agreeing_symbols
            if aligned and participated:
                self._pinc("persistent_active_aligned_participant_allowed")
                return True
            self._pinc("persistent_nonparticipant_or_counterplan_rejected")
            self._record_regime_rejection(
                plan,
                snapshot,
                "PERSISTENT_REQUIRES_ACTIVE_ALIGNED_LATEST_PARTICIPATING_SYMBOL",
            )
            return False
        if regime is CommonAuctionRegime.TURBULENT:
            self._pinc("turbulent_abstention")
            self._record_regime_rejection(
                plan,
                snapshot,
                "TURBULENT_REQUIRES_DEDICATED_CONTROL_TRANSFER",
            )
            return False
        if plan.scale_name == "LIQUIDITY" or self._visual_trigger(plan):
            if super()._factor_allows(plan):
                self._pinc("transitional_visual_or_liquidity_allowed")
                return True
        self._pinc("transitional_flow_proxy_rejected")
        self._record_regime_rejection(
            plan,
            snapshot,
            "TRANSITIONAL_REQUIRES_VISUAL_FOOTPRINT_OR_MAJOR_LIQUIDITY",
        )
        return False

    @property
    def persistent_factor_diagnostics(self) -> dict[str, Any]:
        snapshot = self._auction_snapshot()
        return {
            "counts": dict(sorted(self.persistent_factor_counts.items())),
            "snapshot": {
                "regime": snapshot.regime.value,
                "side": None if snapshot.side is None else snapshot.side.name,
                "active_side": None if snapshot.active_side is None else snapshot.active_side.name,
                "flips": snapshot.flips,
                "events": snapshot.events,
                "latest_event_time_ns": snapshot.latest_event_time_ns,
                "active_event_time_ns": snapshot.active_event_time_ns,
                "latest_agreeing_symbols": snapshot.latest_agreeing_symbols,
            },
            "rules": (
                CROSS_ASSET_COMMON_INITIATIVE_RULE,
                PERSISTENT_COMMON_AUCTION_RULE,
                PERSISTENT_ALIGNED_ROUTING_RULE,
                TURBULENT_ABSTENTION_RULE,
                TRANSITIONAL_VISUAL_OWNERSHIP_RULE,
            ),
        }
