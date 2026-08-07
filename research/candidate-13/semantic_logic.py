"""Candidate 13 accepted-auction execution semantics.

The detector confirms AAC only after an outside hold, a causally known defended
pullback and reacceleration.  The inherited executor nevertheless rested at the
nearest edge of the reacceleration void for only twelve *one-minute* bars.  That
made the order late in price and early in time even though the configuration's
structure interval is five minutes.

For AAC only, this module executes at the premium/discount equilibrium between
that first void edge and the already-known defended pullback pivot.  Structural
invalidation sits beyond the pivot by the detector's existing acceptance-retest
allowance plus its existing stop buffer.  The order remains live for twelve
completed structure bars.  FAR execution is unchanged.
"""
from __future__ import annotations

from typing import Any

from logic import (
    Auction,
    BarObs,
    CausalAuctionEngine,
    Direction,
    MINUTE_NS,
    Scenario,
    TradePlan,
)


BASE_COSTED_LIMIT_PLAN = CausalAuctionEngine._costed_limit_plan


def aac_equilibrium_prices(
    *,
    direction: Direction,
    void_entry: float,
    defended_pullback: float,
    atr: float,
    acceptance_retest_atr: float,
    stop_buffer_atr: float,
) -> tuple[float, float]:
    """Return causal 50% pullback entry and structural pivot invalidation."""
    entry = (void_entry + defended_pullback) / 2.0
    allowance = (acceptance_retest_atr + stop_buffer_atr) * atr
    stop = (
        defended_pullback - allowance
        if direction == Direction.LONG
        else defended_pullback + allowance
    )
    return entry, stop


def semantic_costed_limit_plan(
    self: CausalAuctionEngine,
    a: Auction,
    confirmation_bar: BarObs,
    reason: str,
) -> TradePlan | None:
    if a.scenario != Scenario.AAC:
        return BASE_COSTED_LIMIT_PLAN(self, a, confirmation_bar, reason)

    assert a.direction is not None and a.scenario is not None
    assert a.target_price is not None
    assert a.zone_low is not None and a.zone_high is not None
    if a.pullback_extreme is None:
        self._terminal(a, confirmation_bar, "AAC_DEFENDED_PULLBACK_NOT_KNOWN")
        return None

    void_entry = a.zone_high if a.direction == Direction.LONG else a.zone_low
    entry, stop = aac_equilibrium_prices(
        direction=a.direction,
        void_entry=void_entry,
        defended_pullback=float(a.pullback_extreme),
        atr=a.atr,
        acceptance_retest_atr=self.config.acceptance_retest_atr,
        stop_buffer_atr=self.config.stop_buffer_atr,
    )
    target = a.target_price

    causal_order = (
        float(a.pullback_extreme) < entry < confirmation_bar.close
        if a.direction == Direction.LONG
        else confirmation_bar.close < entry < float(a.pullback_extreme)
    )
    if not causal_order:
        self._terminal(a, confirmation_bar, "AAC_NON_CAUSAL_EQUILIBRIUM_ORDER")
        return None

    if a.direction == Direction.LONG:
        risk = entry - stop
        gain = target - entry
        passive = entry < confirmation_bar.close
    else:
        risk = stop - entry
        gain = entry - target
        passive = entry > confirmation_bar.close
    if not passive:
        self._terminal(a, confirmation_bar, "LIMIT_NOT_PASSIVE_AT_CONFIRMATION")
        return None
    if risk <= 0.0 or gain <= 0.0:
        self._terminal(a, confirmation_bar, "NON_CAUSAL_PRICE_ORDER")
        return None
    if risk / a.atr < self.config.min_stop_atr:
        self._terminal(a, confirmation_bar, "STOP_DISTANCE_BELOW_EXECUTION_FLOOR")
        return None

    loss = (
        risk
        + entry * self.config.effective_maker_rate
        + stop * self.config.effective_taker_rate
    )
    net_gain = (
        gain
        - entry * self.config.effective_maker_rate
        - target * self.config.effective_maker_rate
    )
    net_r = net_gain / loss
    if net_gain <= 0.0 or net_r < self.config.min_net_r:
        self._terminal(a, confirmation_bar, "INSUFFICIENT_COSTED_STRUCTURAL_R")
        return None

    structural_minutes = self.config.retrace_expiry_bars * self.config.internal_tf_bars
    expire_ts_ns = confirmation_bar.ts_ns + structural_minutes * MINUTE_NS
    a.stop_price = stop
    reason_code = "AAC_DEFENDED_PULLBACK_EQUILIBRIUM_LIMIT"
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
        expire_ts_ns=expire_ts_ns,
        entry_order_type="LIMIT",
        entry_post_only=True,
        details={
            "pool_level": a.pool.level,
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
            "defended_pullback": float(a.pullback_extreme),
            "original_void_entry": void_entry,
            "entry_model": "VOID_TO_DEFENDED_PULLBACK_50PCT",
            "stop_model": "PULLBACK_PLUS_ACCEPTANCE_RETEST_AND_BUFFER",
            "entry_cost_assumption": "MAKER",
            "entry_expiry_bars": self.config.retrace_expiry_bars,
            "entry_expiry_structure_minutes": structural_minutes,
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
            "expire_ts_ns": expire_ts_ns,
            "target": target,
            "stop": stop,
            "net_r": net_r,
            "defended_pullback": float(a.pullback_extreme),
            "original_void_entry": void_entry,
        },
    )
    a.state = "PENDING_ENTRY"
    return plan


def install() -> None:
    CausalAuctionEngine._costed_limit_plan = semantic_costed_limit_plan
