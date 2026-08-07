#!/usr/bin/env python3
"""Exactly-two-peer capital rotation versus at-least-two systemic breadth."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
from typing import Mapping

import pandas as pd

import cross_asset_laggard_v39 as base
import cross_asset_laggard_v39_quantity_fix as quantity_fix
import cross_asset_delivery_failure_v41 as v41
import persistent_cross_asset_delivery_v40 as v40
from core import Side
from nautilus_multi_tick_plan_backtest import SymbolScenarioPlan


def to_plan(candidate: base.CandidateRow, *, variant: str) -> SymbolScenarioPlan:
    source = v41.to_plan(candidate, variant="primary")
    exact = variant == "primary"
    plan = replace(
        source.plan,
        scenario_id=(
            f"v42:{variant}:{candidate.signal_time_ns}:{candidate.symbol}:"
            f"{candidate.side.value.lower()}"
        ),
        reason_code=(
            "EXACT_TWO_PEER_CAPITAL_ROTATION_TO_OPPOSITE_HOURLY_LIQUIDITY"
            if exact
            else "AT_LEAST_TWO_PEER_FAILURE_ROTATION_CONTROL"
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
    if variant not in base.VARIANTS:
        raise ValueError(f"unknown variant {variant}")
    if set(featured_by_symbol) != set(base.SYMBOLS):
        raise ValueError("all four allowed symbols are required")

    common: set[int] | None = None
    for frame in featured_by_symbol.values():
        values = {int(value) for value in frame.index}
        common = values if common is None else common & values

    active: dict[str, v40.LeaderDeliveryState] = {}
    plans: list[SymbolScenarioPlan] = []
    diagnostics: list[base.LaggardDiagnostic] = []
    counts: Counter[str] = Counter()
    emitted_targets: set[tuple[str, str, int]] = set()

    for signal_index, minute_start_ns in enumerate(sorted(common or ())):
        signal_time_ns = base._minute_end_ns(minute_start_ns)
        rows = {
            symbol: featured_by_symbol[symbol].loc[minute_start_ns]
            for symbol in base.SYMBOLS
        }
        for symbol in tuple(active):
            if v40._state_invalidated(active[symbol], rows[symbol]):
                del active[symbol]
                counts["leader_states_invalidated"] += 1
        for symbol, row in rows.items():
            side = base._leader_side(row)
            if side is not None:
                active[symbol] = v40._arm_state(
                    symbol=symbol,
                    side=side,
                    row=row,
                    signal_time_ns=signal_time_ns,
                )
                counts["leader_states_armed_or_refreshed"] += 1

        if not evaluation_start_ns <= signal_time_ns < evaluation_end_ns:
            continue
        counts["joint_completed_minutes"] += 1
        for consensus_side in (Side.LONG, Side.SHORT):
            available = tuple(
                symbol for symbol in base.SYMBOLS
                if symbol in active and active[symbol].side is consensus_side
            )
            if len(available) < 2 or not base.CORE_LEADERS.intersection(available):
                continue
            counts[f"{consensus_side.value.lower()}_peer_delivery_minutes"] += 1
            candidates: list[base.CandidateRow] = []
            for symbol in base.SYMBOLS:
                peers = tuple(item for item in available if item != symbol)
                breadth_ok = len(peers) == 2 if variant == "primary" else len(peers) >= 2
                if not breadth_ok or not base.CORE_LEADERS.intersection(peers):
                    continue
                candidate = v41.failure_candidate(
                    symbol=symbol,
                    consensus_side=consensus_side,
                    row=rows[symbol],
                    signal_index=signal_index,
                    signal_time_ns=signal_time_ns,
                    leaders=peers,
                    cost_fraction_per_side=cost_fraction_per_side,
                    minimum_price_risk_fraction=minimum_price_risk_fraction,
                    minimum_net_reward_risk=minimum_net_reward_risk,
                )
                if candidate is None or base._target_key(candidate) in emitted_targets:
                    continue
                candidates.append(candidate)
            if not candidates:
                continue
            chosen = sorted(
                candidates,
                key=lambda item: (
                    -item.net_reward_risk,
                    -item.price_risk_fraction,
                    item.symbol,
                ),
            )[0]
            emitted_targets.add(base._target_key(chosen))
            plans.append(to_plan(chosen, variant=variant))
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
            counts[f"{len(chosen.leaders)}_peer_plans"] += 1
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
            "candidate": "exactly-two-peer capital rotation",
            "version": 42,
            "frozen_random_seed": 4201,
            "scenario_contract": (
                "exactly two peer accepted-delivery states including BTC or ETH "
                "-> laggard leaves both hourly edges unconsumed and displaces "
                "through opposite internal structure with opposite flow -> first "
                "later own-symbol TradeTick -> local invalidation -> opposite "
                "frozen hourly target"
            ),
            "primary_variable": (
                "exactly two peer leaders; exclude systemic three-peer breadth"
                if args.variant == "primary"
                else "at least two peer leaders"
            ),
        },
    )
    base.atomic_json(args.output / "pairwise_rotation_v42_summary.json", payload)
    return code


if __name__ == "__main__":
    raise SystemExit(run(base.build_parser().parse_args()))
