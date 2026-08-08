"""Candidate 14 v7 accepted-auction entry ownership.

The frozen AAC detector already observes this ordered scenario:

    outside acceptance -> causal defended pullback -> later reacceleration

Once reacceleration is complete, that new initiative leg owns the entry. Resting
again at the already-defended pullback asks for a second deep return whose fill
is conditional on weakening acceptance. Candidate 14 v7 therefore submits a
market parent at the completed reacceleration close only when the unchanged
source-boundary stop, live external target and declared costs retain the frozen
structural-R contract. Otherwise the auction terminates; there is no passive
fallback and no inferred fill.

No detector threshold, target, stop boundary, cost, risk fraction, symbol or
session rule is changed. NautilusTrader remains the sole execution and account
engine.
"""
from __future__ import annotations

from typing import Callable

from logic import Auction, BarObs, CausalAuctionEngine, Direction, Scenario, TradePlan
from semantic_execution import MARKET_ENTRY_SENTINEL_NS
from semantic_logic import aac_boundary_prices, qualify_market_entry


CostedPlan = Callable[[CausalAuctionEngine, Auction, BarObs, str], TradePlan | None]


def aac_reacceleration_market_plan(
    self: CausalAuctionEngine,
    a: Auction,
    confirmation_bar: BarObs,
) -> TradePlan | None:
    """Own the already-confirmed AAC initiative at its causal observation time."""
    assert a.direction is not None and a.scenario == Scenario.AAC
    assert a.target_price is not None
    if a.pullback_extreme is None:
        self._terminal(a, confirmation_bar, "AAC_DEFENDED_PULLBACK_NOT_KNOWN")
        return None

    defended_pullback = float(a.pullback_extreme)
    source_boundary = float(a.pool.level)
    _old_entry, stop = aac_boundary_prices(
        direction=a.direction,
        defended_pullback=defended_pullback,
        source_boundary=source_boundary,
        atr=a.atr,
        stop_buffer_atr=self.config.stop_buffer_atr,
    )
    entry = float(confirmation_bar.close)
    target = float(a.target_price)
    qualified, _risk, loss, net_gain, net_r = qualify_market_entry(
        direction=a.direction,
        entry=entry,
        stop=stop,
        target=target,
        atr=a.atr,
        min_stop_atr=self.config.min_stop_atr,
        min_net_r=self.config.min_net_r,
        taker_rate=self.config.effective_taker_rate,
        target_maker_rate=self.config.effective_maker_rate,
    )
    if not qualified:
        self._terminal(
            a,
            confirmation_bar,
            "AAC_OWNED_REACCELERATION_NOT_COST_EXECUTABLE",
        )
        return None

    a.stop_price = stop
    reason_code = "AAC_CONFIRMED_PULLBACK_REACCELERATION_MARKET"
    plan = TradePlan(
        scenario_id=a.pool.scenario_id,
        scenario=a.scenario,
        direction=a.direction,
        observed_ts_ns=confirmation_bar.ts_ns,
        expected_entry=entry,
        stop_price=stop,
        target_price=target,
        atr=a.atr,
        loss_per_unit=loss,
        gain_per_unit=net_gain,
        net_r=net_r,
        reason_code=reason_code,
        expire_ts_ns=MARKET_ENTRY_SENTINEL_NS,
        entry_order_type="MARKET",
        entry_post_only=False,
        details={
            "pool_level": source_boundary,
            "pool_source": a.pool.source,
            "range_id": a.pool.range_id,
            "sweep_ts_ns": (
                a.initial_sweep_ts_ns
                if a.initial_sweep_ts_ns is not None
                else a.sweep.ts_ns
            ),
            "sweep_extreme": a.sweep_extreme,
            "draw_side": None if a.draw_side is None else a.draw_side.value,
            "draw_score": a.draw_score,
            "draw_method": a.framed_draw_method,
            "zone_low": a.zone_low,
            "zone_high": a.zone_high,
            "confirmation_close": confirmation_bar.close,
            "defended_pullback": defended_pullback,
            "source_boundary": source_boundary,
            "entry_model": "CONFIRMED_PULLBACK_REACCELERATION_MARKET",
            "stop_model": "SOURCE_BOUNDARY_REACCEPTANCE",
            "entry_cost_assumption": "TAKER",
            "entry_expiry_bars": 0,
            "entry_expiry_structure_minutes": 0,
            "market_parent_sentinel_ns": MARKET_ENTRY_SENTINEL_NS,
            "second_pullback_fallback_allowed": False,
        },
    )
    self._event(
        a.pool.scenario_id,
        "TRADE_PLAN_CONFIRMED",
        a.sweep.ts_ns,
        confirmation_bar.ts_ns,
        a.state,
        "PENDING_ENTRY",
        reason_code,
        entry,
        {
            "scenario": a.scenario.value,
            "direction": a.direction.value,
            "entry_order_type": plan.entry_order_type,
            "entry_post_only": plan.entry_post_only,
            "expire_ts_ns": MARKET_ENTRY_SENTINEL_NS,
            "target": target,
            "stop": stop,
            "net_r": net_r,
            "defended_pullback": defended_pullback,
            "source_boundary": source_boundary,
            "entry_cost_assumption": "TAKER",
            "entry_model": "CONFIRMED_PULLBACK_REACCELERATION_MARKET",
            "second_pullback_fallback_allowed": False,
        },
    )
    a.state = "PENDING_ENTRY"
    return plan


def install() -> None:
    """Install AAC ownership after the preserved semantic execution adapter."""
    previous: CostedPlan = CausalAuctionEngine._costed_limit_plan

    def dispatch(
        self: CausalAuctionEngine,
        a: Auction,
        confirmation_bar: BarObs,
        reason: str,
    ) -> TradePlan | None:
        if a.scenario == Scenario.AAC:
            return aac_reacceleration_market_plan(self, a, confirmation_bar)
        return previous(self, a, confirmation_bar, reason)

    dispatch.__name__ = "candidate14_v7_costed_plan"
    CausalAuctionEngine._costed_limit_plan = dispatch
