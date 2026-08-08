"""Install Candidate 15's causal-resolution-token router.

Candidate 14's detector, execution semantics, cross-market leadership, exact
current-NAV risk sizing, global slot and Nautilus accounting remain unchanged.
Candidate 15 controls only which inherited branch may continue toward entry:

* fresh FAILURE may call FAR;
* fresh ACCEPTANCE may call AAC;
* UNRESOLVED or STALE admits neither.
"""
from __future__ import annotations

from dataclasses import replace

from logic import Auction, BarObs, CausalAuctionEngine, TradePlan
from sequential_response_router import (
    AuctionResolution,
    RouterSnapshot,
    SequentialAuctionRouter,
)


def _router(self: CausalAuctionEngine) -> SequentialAuctionRouter:
    value = getattr(self, "_candidate15_router", None)
    if value is None:
        value = SequentialAuctionRouter(
            calibration_bars=max(30, int(self.config.volume_period)),
        )
        setattr(self, "_candidate15_router", value)
    return value


def _amend_last_plan_event(
    self: CausalAuctionEngine,
    scenario_id: str,
    snapshot: RouterSnapshot,
) -> None:
    for event in reversed(self.events):
        if getattr(event, "scenario_id", None) != scenario_id:
            continue
        if getattr(event, "event_type", None) != "TRADE_PLAN_CONFIRMED":
            continue
        details = getattr(event, "details", None)
        if isinstance(details, dict):
            details["candidate15_router"] = snapshot.to_dict()
        break


def _annotate(
    self: CausalAuctionEngine,
    plan: TradePlan | None,
    snapshot: RouterSnapshot,
) -> TradePlan | None:
    if plan is None:
        return None
    details = dict(plan.details)
    details["candidate15_router"] = snapshot.to_dict()
    amended = replace(plan, details=details)
    _amend_last_plan_event(self, amended.scenario_id, snapshot)
    return amended


def _measure(
    self: CausalAuctionEngine,
    a: Auction,
    bar: BarObs,
) -> RouterSnapshot:
    boundary = float(
        a.last_crossed_level
        if a.last_crossed_level is not None
        else a.pool.level
    )
    observation = _router(self).observe(
        scenario_id=a.pool.scenario_id,
        sweep_ts_ns=int(a.sweep.ts_ns),
        swept_side=a.pool.side.value,
        boundary=boundary,
        atr=float(a.atr),
        bars=self.bars,
        current_index=self._index,
    )
    snapshot = observation.snapshot
    if observation.resolved_now:
        self._event(
            a.pool.scenario_id,
            "AUCTION_STATE_RESOLVED",
            int(a.sweep.ts_ns),
            int(bar.ts_ns),
            a.state,
            a.state,
            f"C15_{snapshot.state.value}",
            boundary,
            {
                **snapshot.to_dict(),
                "episode_reset": observation.reset,
                "previous_router_state": observation.previous_state.value,
                "interpretation": (
                    "SWEPT_SIDE_PRICE_ACCEPTANCE"
                    if snapshot.state is AuctionResolution.ACCEPTANCE
                    else "SWEPT_SIDE_FLOW_ABSORPTION_OR_RECLAIM"
                ),
            },
        )
    elif observation.expired_now:
        self._event(
            a.pool.scenario_id,
            "AUCTION_STATE_EXPIRED",
            int(a.sweep.ts_ns),
            int(bar.ts_ns),
            a.state,
            a.state,
            "C15_RESOLUTION_STALE",
            boundary,
            {
                **snapshot.to_dict(),
                "episode_reset": observation.reset,
                "previous_router_state": observation.previous_state.value,
                "interpretation": "RESOLUTION_NOT_CONFIRMED_WITHIN_SAME_NEW_AUCTION_LEG",
            },
        )
    return snapshot


def install() -> None:
    """Patch inherited confirmation methods once, after Candidate 14 installs."""
    if getattr(CausalAuctionEngine, "_candidate15_router_installed", False):
        return

    inherited_far = CausalAuctionEngine._confirm_far
    inherited_aac = CausalAuctionEngine._confirm_aac

    def routed_far(
        self: CausalAuctionEngine,
        a: Auction,
        bar: BarObs,
    ) -> TradePlan | None:
        snapshot = _measure(self, a, bar)
        if (
            snapshot.state is not AuctionResolution.FAILURE
            or not snapshot.fresh_for_entry
        ):
            return None
        return _annotate(self, inherited_far(self, a, bar), snapshot)

    def routed_aac(
        self: CausalAuctionEngine,
        a: Auction,
        bar: BarObs,
    ) -> TradePlan | None:
        snapshot = _measure(self, a, bar)
        if (
            snapshot.state is not AuctionResolution.ACCEPTANCE
            or not snapshot.fresh_for_entry
        ):
            return None
        return _annotate(self, inherited_aac(self, a, bar), snapshot)

    CausalAuctionEngine._confirm_far = routed_far
    CausalAuctionEngine._confirm_aac = routed_aac
    CausalAuctionEngine._candidate15_router_installed = True
