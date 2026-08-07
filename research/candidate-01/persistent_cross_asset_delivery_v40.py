#!/usr/bin/env python3
"""Persistent cross-asset delivery state to laggard hourly liquidity.

V39 required two peer markets to accept beyond their frozen hourly external
liquidity in the exact same completed minute as the laggard's internal
structure break. That produced a clean winning trade but only one opportunity.
V40 changes one structural variable rather than loosening a threshold:

* primary: each peer acceptance becomes an active delivery state and remains
  active until that market closes back through its frozen breakout boundary;
* control: require the same two peer acceptances in the laggard signal minute.

Both paths still require two independent peer leaders including BTC or ETH,
the same laggard displacement, unconsumed hourly target, cost-after geometry,
3% shared-NAV loss budget, and one global pending entry or position.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from typing import Mapping

import pandas as pd

import cross_asset_laggard_v39 as base
import cross_asset_laggard_v39_quantity_fix as quantity_fix
from core import Side
from impact_regime_probe import ScenarioPlan
from nautilus_multi_tick_plan_backtest import SymbolScenarioPlan


@dataclass(frozen=True, slots=True)
class LeaderDeliveryState:
    symbol: str
    side: Side
    boundary: float
    created_time_ns: int


def _state_invalidated(state: LeaderDeliveryState, row: pd.Series) -> bool:
    close = float(row["close"])
    return close <= state.boundary if state.side is Side.LONG else close >= state.boundary


def _arm_state(
    *,
    symbol: str,
    side: Side,
    row: pd.Series,
    signal_time_ns: int,
) -> LeaderDeliveryState:
    boundary = (
        float(row["external_high"])
        if side is Side.LONG
        else float(row["external_low"])
    )
    return LeaderDeliveryState(
        symbol=symbol,
        side=side,
        boundary=boundary,
        created_time_ns=signal_time_ns,
    )


def _to_plan(candidate: base.CandidateRow, *, variant: str) -> SymbolScenarioPlan:
    persistent = variant == "primary"
    plan = ScenarioPlan(
        scenario_id=(
            f"v40:{variant}:{candidate.signal_time_ns}:{candidate.symbol}:"
            f"{candidate.side.value.lower()}"
        ),
        response="CONTINUATION",
        side=candidate.side,
        signal_bar_index=candidate.signal_index,
        signal_time_ns=candidate.signal_time_ns,
        stop_price=candidate.stop,
        target_price=candidate.target,
        confirmation_hold_price=candidate.hold,
        structure_high=max(candidate.external_high, candidate.pulse_high),
        structure_low=min(candidate.external_low, candidate.pulse_low),
        structure_midpoint=0.5 * (candidate.external_high + candidate.external_low),
        pulse_high=candidate.pulse_high,
        pulse_low=candidate.pulse_low,
        pulse_flow_score=candidate.flow,
        pulse_move_atr=(
            abs(candidate.range_bps) / candidate.range_median_bps
            if candidate.range_median_bps > 0.0
            else 0.0
        ),
        pulse_path_efficiency=candidate.body_efficiency,
        pulse_close_location=candidate.directional_close_location,
        reason_code=(
            "TWO_PERSISTENT_PEER_DELIVERY_STATES_LAGGARD_TO_HOURLY_LIQUIDITY"
            if persistent
            else "TWO_CONTEMPORANEOUS_PEER_ACCEPTANCES_LAGGARD_TO_HOURLY_LIQUIDITY"
        ),
    )
    return SymbolScenarioPlan(symbol=candidate.symbol, plan=plan)


def generate_symbol_plans(
    featured_by_symbol: Mapping[str, pd.DataFrame],
    *,
    variant: str,
    evaluation_start_ns: int,
    evaluation_end_ns: int,
    cost_fraction_per_side: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
):
    """Route laggard plans from persistent or contemporaneous peer delivery."""

    if variant not in base.VARIANTS:
        raise ValueError(f"unknown variant {variant}")
    if set(featured_by_symbol) != set(base.SYMBOLS):
        raise ValueError("all four allowed symbols are required")

    common_index: set[int] | None = None
    for frame in featured_by_symbol.values():
        values = {int(value) for value in frame.index}
        common_index = values if common_index is None else common_index & values
    minute_starts = sorted(common_index or ())

    active: dict[str, LeaderDeliveryState] = {}
    plans: list[SymbolScenarioPlan] = []
    diagnostics: list[base.LaggardDiagnostic] = []
    counts: Counter[str] = Counter()
    emitted_targets: set[tuple[str, str, int]] = set()

    for signal_index, minute_start_ns in enumerate(minute_starts):
        signal_time_ns = base._minute_end_ns(minute_start_ns)
        rows = {
            symbol: featured_by_symbol[symbol].loc[minute_start_ns]
            for symbol in base.SYMBOLS
        }

        for symbol in tuple(active):
            if _state_invalidated(active[symbol], rows[symbol]):
                counts["leader_states_invalidated"] += 1
                del active[symbol]

        current_sides = {
            symbol: base._leader_side(row)
            for symbol, row in rows.items()
        }
        for symbol, side in current_sides.items():
            if side is None:
                continue
            active[symbol] = _arm_state(
                symbol=symbol,
                side=side,
                row=rows[symbol],
                signal_time_ns=signal_time_ns,
            )
            counts["leader_states_armed_or_refreshed"] += 1

        if not evaluation_start_ns <= signal_time_ns < evaluation_end_ns:
            continue
        counts["joint_completed_minutes"] += 1

        for side in (Side.LONG, Side.SHORT):
            if variant == "primary":
                available = tuple(
                    symbol
                    for symbol in base.SYMBOLS
                    if symbol in active and active[symbol].side is side
                )
                if available:
                    counts[f"{side.value.lower()}_persistent_delivery_minutes"] += 1
            else:
                available = tuple(
                    symbol
                    for symbol in base.SYMBOLS
                    if current_sides[symbol] is side
                )
                if available:
                    counts[f"{side.value.lower()}_contemporaneous_delivery_minutes"] += 1

            candidates: list[base.CandidateRow] = []
            for symbol in base.SYMBOLS:
                peer_leaders = tuple(item for item in available if item != symbol)
                if len(peer_leaders) < 2:
                    continue
                if not base.CORE_LEADERS.intersection(peer_leaders):
                    continue
                candidate = base._laggard_candidate(
                    symbol=symbol,
                    side=side,
                    row=rows[symbol],
                    signal_index=signal_index,
                    signal_time_ns=signal_time_ns,
                    leaders=peer_leaders,
                    cost_fraction_per_side=cost_fraction_per_side,
                    minimum_price_risk_fraction=minimum_price_risk_fraction,
                    minimum_net_reward_risk=minimum_net_reward_risk,
                )
                if candidate is None:
                    continue
                if base._target_key(candidate) in emitted_targets:
                    counts["duplicate_active_target_rejected"] += 1
                    continue
                candidates.append(candidate)

            if not candidates:
                continue
            chosen = sorted(
                candidates,
                key=lambda item: (
                    -len(item.leaders),
                    -item.net_reward_risk,
                    -item.price_risk_fraction,
                    item.symbol,
                ),
            )[0]
            emitted_targets.add(base._target_key(chosen))
            plans.append(_to_plan(chosen, variant=variant))
            diagnostics.append(
                base.LaggardDiagnostic(
                    variant=variant,
                    symbol=chosen.symbol,
                    side=chosen.side.value,
                    signal_time_ns=chosen.signal_time_ns,
                    leader_symbols="|".join(chosen.leaders),
                    leader_count=len(chosen.leaders),
                    external_target=chosen.target,
                    internal_boundary=chosen.hold,
                    structural_stop=chosen.stop,
                    signal_close=chosen.entry_reference,
                    signal_flow_imbalance=chosen.flow,
                    signal_range_bps=chosen.range_bps,
                    prior_range_median_bps=chosen.range_median_bps,
                    signal_net_reward_risk=chosen.net_reward_risk,
                    signal_price_risk_fraction=chosen.price_risk_fraction,
                ),
            )
            counts["plans_emitted"] += 1
            counts[f"{chosen.symbol}_plans"] += 1
            counts[f"{chosen.side.value.lower()}_plans"] += 1

    return plans, diagnostics, counts


def run(args) -> int:
    quantity_fix.install_period_quantity_specs()
    original = base.generate_symbol_plans
    base.generate_symbol_plans = generate_symbol_plans
    try:
        code = base.run(args)
    finally:
        base.generate_symbol_plans = original

    source = args.output / "cross_asset_laggard_v39_summary.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload.update(
        {
            "candidate": "persistent cross-asset delivery state to hourly liquidity",
            "version": 40,
            "frozen_random_seed": 4001,
            "scenario_contract": (
                "two peer hourly-liquidity acceptance states remain active until "
                "their frozen boundary fails -> laggard internal displacement while "
                "its hourly target remains unconsumed -> first later own-symbol "
                "TradeTick -> local-path invalidation -> frozen hourly target"
            ),
            "primary_variable": (
                "peer acceptance persists until frozen-boundary re-entry"
                if args.variant == "primary"
                else "peer acceptance must occur in the laggard signal minute"
            ),
        },
    )
    base.atomic_json(
        args.output / "persistent_cross_asset_delivery_v40_summary.json",
        payload,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(run(base.build_parser().parse_args()))
