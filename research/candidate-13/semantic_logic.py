"""Candidate 13 structural execution semantics.

FAR and AAC carry different execution hazards.

* FAR has already reclaimed the raid, shifted local structure and displaced in
  the same direction as all peers.  When the confirmation close still offers
  the configured after-cost structural R, it enters immediately with a
  Nautilus MARKET parent; otherwise the inherited passive void order remains.
* AAC rests at the already-known defended pullback pivot and is invalidated only
  by reacceptance through the original external-liquidity boundary.

Targets, exact 3% NAV sizing, global position arbitration, fees, stop-market
children and Nautilus accounting are unchanged.
"""
from __future__ import annotations

from dataclasses import replace

from logic import (
    Auction,
    BarObs,
    CausalAuctionEngine,
    Direction,
    MINUTE_NS,
    Scenario,
    TradePlan,
)
from semantic_execution import MARKET_ENTRY_SENTINEL_NS


BASE_COSTED_LIMIT_PLAN = CausalAuctionEngine._costed_limit_plan


def aac_boundary_prices(
    *,
    direction: Direction,
    defended_pullback: float,
    source_boundary: float,
    atr: float,
    stop_buffer_atr: float,
) -> tuple[float, float]:
    """Enter at the defended pivot; invalidate beyond source-range reacceptance."""
    entry = defended_pullback
    allowance = stop_buffer_atr * atr
    stop = (
        min(defended_pullback, source_boundary) - allowance
        if direction == Direction.LONG
        else max(defended_pullback, source_boundary) + allowance
    )
    return entry, stop


def costed_market_economics(
    *,
    direction: Direction,
    entry: float,
    stop: float,
    target: float,
    taker_rate: float,
    target_maker_rate: float,
) -> tuple[float, float, float, float]:
    """Return risk, after-cost loss, after-cost gain and net structural R."""
    if direction == Direction.LONG:
        risk = entry - stop
        gross_gain = target - entry
    else:
        risk = stop - entry
        gross_gain = entry - target
    loss = risk + entry * taker_rate + stop * taker_rate
    net_gain = gross_gain - entry * taker_rate - target * target_maker_rate
    net_r = net_gain / loss if loss > 0.0 else float("-inf")
    return risk, loss, net_gain, net_r


def _structure_expiry(self: CausalAuctionEngine, confirmation_ts_ns: int) -> tuple[int, int]:
    minutes = self.config.retrace_expiry_bars * self.config.internal_tf_bars
    return confirmation_ts_ns + minutes * MINUTE_NS, minutes


def _amend_last_plan_event(
    self: CausalAuctionEngine,
    scenario_id: str,
    updates: dict[str, object],
) -> None:
    for event in reversed(self.events):
        if getattr(event, "scenario_id", None) != scenario_id:
            continue
        if getattr(event, "event_type", None) != "TRADE_PLAN_CONFIRMED":
            continue
        details = getattr(event, "details", None)
        if isinstance(details, dict):
            details.update(updates)
        break


def _far_plan(
    self: CausalAuctionEngine,
    a: Auction,
    confirmation_bar: BarObs,
    reason: str,
) -> TradePlan | None:
    inherited = BASE_COSTED_LIMIT_PLAN(self, a, confirmation_bar, reason)
    if inherited is None:
        return None

    expire_ts_ns, structural_minutes = _structure_expiry(self, confirmation_bar.ts_ns)
    passive_details = dict(inherited.details)
    passive_details["entry_expiry_structure_minutes"] = structural_minutes
    passive = replace(
        inherited,
        expire_ts_ns=expire_ts_ns,
        details=passive_details,
    )

    entry = confirmation_bar.close
    risk, loss, net_gain, net_r = costed_market_economics(
        direction=a.direction,
        entry=entry,
        stop=inherited.stop_price,
        target=inherited.target_price,
        taker_rate=self.config.effective_taker_rate,
        target_maker_rate=self.config.effective_maker_rate,
    )
    causal_order = (
        inherited.stop_price < entry < inherited.target_price
        if a.direction == Direction.LONG
        else inherited.target_price < entry < inherited.stop_price
    )
    immediate = (
        causal_order
        and risk > 0.0
        and risk / a.atr >= self.config.min_stop_atr
        and net_gain > 0.0
        and net_r >= self.config.min_net_r
    )

    if immediate:
        details = dict(passive.details)
        details.update(
            {
                "original_passive_entry": inherited.expected_entry,
                "entry_model": "CONFIRMED_RECLAIM_DISPLACEMENT_MARKET",
                "entry_cost_assumption": "TAKER",
                "entry_expiry_structure_minutes": 0,
                "market_parent_sentinel_ns": MARKET_ENTRY_SENTINEL_NS,
            },
        )
        market = replace(
            passive,
            expected_entry=entry,
            loss_per_unit=loss,
            gain_per_unit=net_gain,
            net_r=net_r,
            reason_code="FAR_CONFIRMED_RECLAIM_DISPLACEMENT_MARKET",
            expire_ts_ns=MARKET_ENTRY_SENTINEL_NS,
            entry_order_type="MARKET",
            entry_post_only=False,
            details=details,
        )
        _amend_last_plan_event(
            self,
            market.scenario_id,
            {
                "execution_reclassified": True,
                "entry_order_type": "MARKET",
                "entry_post_only": False,
                "expected_entry": entry,
                "original_passive_entry": inherited.expected_entry,
                "net_r": net_r,
                "entry_cost_assumption": "TAKER",
                "expire_ts_ns": MARKET_ENTRY_SENTINEL_NS,
            },
        )
        return market

    _amend_last_plan_event(
        self,
        passive.scenario_id,
        {
            "expire_ts_ns": expire_ts_ns,
            "entry_expiry_structure_minutes": structural_minutes,
            "market_entry_rejected_net_r": net_r,
        },
    )
    return passive


def _aac_plan(
    self: CausalAuctionEngine,
    a: Auction,
    confirmation_bar: BarObs,
) -> TradePlan | None:
    assert a.direction is not None and a.scenario is not None
    assert a.target_price is not None
    if a.pullback_extreme is None:
        self._terminal(a, confirmation_bar, "AAC_DEFENDED_PULLBACK_NOT_KNOWN")
        return None

    entry, stop = aac_boundary_prices(
        direction=a.direction,
        defended_pullback=float(a.pullback_extreme),
        source_boundary=a.pool.level,
        atr=a.atr,
        stop_buffer_atr=self.config.stop_buffer_atr,
    )
    target = a.target_price
    passive = (
        entry < confirmation_bar.close
        if a.direction == Direction.LONG
        else entry > confirmation_bar.close
    )
    if not passive:
        self._terminal(a, confirmation_bar, "AAC_PULLBACK_LIMIT_NOT_PASSIVE")
        return None

    if a.direction == Direction.LONG:
        risk = entry - stop
        gain = target - entry
    else:
        risk = stop - entry
        gain = entry - target
    if risk <= 0.0 or gain <= 0.0:
        self._terminal(a, confirmation_bar, "NON_CAUSAL_PRICE_ORDER")
        return None
    if risk / a.atr < self.config.min_stop_atr:
        self._terminal(a, confirmation_bar, "STOP_DISTANCE_BELOW_EXECUTION_FLOOR")
        return None

    loss = risk + entry * self.config.effective_maker_rate + stop * self.config.effective_taker_rate
    net_gain = gain - entry * self.config.effective_maker_rate - target * self.config.effective_maker_rate
    net_r = net_gain / loss
    if net_gain <= 0.0 or net_r < self.config.min_net_r:
        self._terminal(a, confirmation_bar, "INSUFFICIENT_COSTED_STRUCTURAL_R")
        return None

    expire_ts_ns, structural_minutes = _structure_expiry(self, confirmation_bar.ts_ns)
    a.stop_price = stop
    reason_code = "AAC_DEFENDED_PULLBACK_SOURCE_BOUNDARY_LIMIT"
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
            "source_boundary": a.pool.level,
            "entry_model": "DEFENDED_PULLBACK_PIVOT",
            "stop_model": "SOURCE_BOUNDARY_REACCEPTANCE",
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
            "source_boundary": a.pool.level,
            "entry_expiry_structure_minutes": structural_minutes,
        },
    )
    a.state = "PENDING_ENTRY"
    return plan


def semantic_costed_limit_plan(
    self: CausalAuctionEngine,
    a: Auction,
    confirmation_bar: BarObs,
    reason: str,
) -> TradePlan | None:
    if a.scenario == Scenario.FAR:
        return _far_plan(self, a, confirmation_bar, reason)
    if a.scenario == Scenario.AAC:
        return _aac_plan(self, a, confirmation_bar)
    return BASE_COSTED_LIMIT_PLAN(self, a, confirmation_bar, reason)


def install() -> None:
    CausalAuctionEngine._costed_limit_plan = semantic_costed_limit_plan
