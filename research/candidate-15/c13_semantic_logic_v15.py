"""Candidate 13 V15 FAR execution geometry.

V15 changes exactly one trading decision relative to V4: a confirmed FAR may
no longer chase the confirmation close with a MARKET parent, and its
invalidation may no longer be pulled inside the confirmation displacement
void.  The causal displacement-zone retrace LIMIT and the original structural
stop beyond the sweep remain paired with the original external-liquidity
target.

Direction, market-state classification, leadership semantics, AAC execution,
3% NAV risk sizing, fees, global arbitration, targets and NautilusTrader order
ownership are unchanged.
"""
from __future__ import annotations

from dataclasses import replace

from logic import Auction, BarObs, CausalAuctionEngine, Scenario, TradePlan
from c13_semantic_logic_base import (
    BASE_COSTED_LIMIT_PLAN,
    _aac_plan,
    _amend_last_plan_event,
    _structure_expiry,
    _void_repair_candidate,
)


def _far_structural_retrace_plan(
    self: CausalAuctionEngine,
    auction: Auction,
    confirmation_bar: BarObs,
    reason: str,
) -> TradePlan | None:
    """Keep FAR entry, invalidation and target inside one causal auction plan."""
    inherited = BASE_COSTED_LIMIT_PLAN(self, auction, confirmation_bar, reason)
    if inherited is None:
        return None

    expire_ts_ns, structural_minutes = _structure_expiry(
        self,
        confirmation_bar.ts_ns,
    )
    details = dict(inherited.details)
    details.update(
        {
            "confirmation_close": confirmation_bar.close,
            "structural_stop": inherited.stop_price,
            "entry_model": "FAR_CAUSAL_DISPLACEMENT_RETRACE_LIMIT",
            "stop_model": "SWEEP_EXTREME_STRUCTURAL_INVALIDATION",
            "entry_cost_assumption": "MAKER",
            "entry_expiry_structure_minutes": structural_minutes,
            # Retained as diagnostic evidence only.  V15 never activates it.
            "void_repair_candidate": _void_repair_candidate(
                self,
                auction,
                confirmation_bar,
                inherited,
            ),
            "v15_market_chase_disabled": True,
            "v15_void_stop_disabled": True,
        },
    )
    plan = replace(
        inherited,
        expire_ts_ns=expire_ts_ns,
        entry_order_type="LIMIT",
        entry_post_only=True,
        details=details,
    )
    _amend_last_plan_event(
        self,
        plan.scenario_id,
        {
            "execution_reclassified": False,
            "entry_order_type": "LIMIT",
            "entry_post_only": True,
            "expected_entry": plan.expected_entry,
            "stop": plan.stop_price,
            "net_r": plan.net_r,
            "entry_cost_assumption": "MAKER",
            "entry_model": "FAR_CAUSAL_DISPLACEMENT_RETRACE_LIMIT",
            "stop_model": "SWEEP_EXTREME_STRUCTURAL_INVALIDATION",
            "expire_ts_ns": expire_ts_ns,
            "entry_expiry_structure_minutes": structural_minutes,
            "void_repair_candidate": details["void_repair_candidate"],
            "v15_market_chase_disabled": True,
            "v15_void_stop_disabled": True,
        },
    )
    return plan


def v15_costed_plan(
    self: CausalAuctionEngine,
    auction: Auction,
    confirmation_bar: BarObs,
    reason: str,
) -> TradePlan | None:
    if auction.scenario == Scenario.FAR:
        return _far_structural_retrace_plan(
            self,
            auction,
            confirmation_bar,
            reason,
        )
    if auction.scenario == Scenario.AAC:
        return _aac_plan(self, auction, confirmation_bar)
    return BASE_COSTED_LIMIT_PLAN(self, auction, confirmation_bar, reason)


def install() -> None:
    CausalAuctionEngine._costed_limit_plan = v15_costed_plan
