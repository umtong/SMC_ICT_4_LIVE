#!/usr/bin/env python3
"""Two-peer delivery failure in a laggard, rotated to opposite hourly liquidity."""
from __future__ import annotations

from collections import Counter
import json
from typing import Mapping

import pandas as pd

import cross_asset_laggard_v39 as base
import cross_asset_laggard_v39_quantity_fix as quantity_fix
import persistent_cross_asset_delivery_v40 as v40
from core import Side
from impact_regime_probe import ScenarioPlan
from nautilus_multi_tick_plan_backtest import SymbolScenarioPlan


def opposite(side: Side) -> Side:
    return Side.SHORT if side is Side.LONG else Side.LONG


def failure_candidate(
    *,
    symbol: str,
    consensus_side: Side,
    row: pd.Series,
    signal_index: int,
    signal_time_ns: int,
    leaders: tuple[str, ...],
    cost_fraction_per_side: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
) -> base.CandidateRow | None:
    required = (
        "external_high", "external_low", "internal_high", "internal_low",
        "range_median_bps", "return_bps", "range_bps", "flow_imbalance",
    )
    if any(pd.isna(row[name]) for name in required):
        return None
    if float(row["range_bps"]) < float(row["range_median_bps"]):
        return None

    entry = float(row["close"])
    high = float(row["high"])
    low = float(row["low"])
    external_high = float(row["external_high"])
    external_low = float(row["external_low"])
    internal_high = float(row["internal_high"])
    internal_low = float(row["internal_low"])
    return_bps = float(row["return_bps"])
    flow = float(row["flow_imbalance"])
    location = float(row["close_location"])
    trade_side = opposite(consensus_side)

    # The laggard must leave both frozen hourly edges unconsumed. It rejected
    # the peers' delivery and displaced through opposite internal structure.
    inside_hourly = high < external_high and low > external_low
    if consensus_side is Side.LONG:
        valid = (
            inside_hourly
            and entry < internal_low
            and return_bps < 0.0
            and flow < 0.0
            and location <= 0.5
        )
        stop = max(high, internal_high) * (1.0 + base.STOP_BUFFER_FRACTION)
        target = external_low
        hold = internal_low
        directional_close = 1.0 - location
    else:
        valid = (
            inside_hourly
            and entry > internal_high
            and return_bps > 0.0
            and flow > 0.0
            and location >= 0.5
        )
        stop = min(low, internal_low) * (1.0 - base.STOP_BUFFER_FRACTION)
        target = external_high
        hold = internal_high
        directional_close = location
    if not valid:
        return None

    geometry = base._execution_geometry(
        side=trade_side,
        entry=entry,
        stop=stop,
        target=target,
        cost_fraction_per_side=cost_fraction_per_side,
    )
    if geometry is None:
        return None
    price_fraction, net_rr = geometry
    if price_fraction < minimum_price_risk_fraction or net_rr < minimum_net_reward_risk:
        return None
    return base.CandidateRow(
        symbol=symbol,
        side=trade_side,
        signal_index=signal_index,
        signal_time_ns=signal_time_ns,
        leaders=leaders,
        entry_reference=entry,
        stop=stop,
        target=target,
        hold=hold,
        external_high=external_high,
        external_low=external_low,
        pulse_high=high,
        pulse_low=low,
        flow=flow,
        range_bps=float(row["range_bps"]),
        range_median_bps=float(row["range_median_bps"]),
        body_efficiency=float(row["body_efficiency"]),
        directional_close_location=directional_close,
        price_risk_fraction=price_fraction,
        net_reward_risk=net_rr,
    )


def to_plan(candidate: base.CandidateRow, *, variant: str) -> SymbolScenarioPlan:
    is_failure = variant == "primary"
    plan = ScenarioPlan(
        scenario_id=(
            f"v41:{variant}:{candidate.signal_time_ns}:{candidate.symbol}:"
            f"{candidate.side.value.lower()}"
        ),
        response="EXHAUSTION_REVERSAL" if is_failure else "CONTINUATION",
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
            if candidate.range_median_bps > 0.0 else 0.0
        ),
        pulse_path_efficiency=candidate.body_efficiency,
        pulse_close_location=candidate.directional_close_location,
        reason_code=(
            "TWO_PEER_DELIVERY_FAILED_ROTATE_TO_OPPOSITE_HOURLY_LIQUIDITY"
            if is_failure
            else "TWO_PEER_DELIVERY_ASSIMILATED_TO_SAME_SIDE_HOURLY_LIQUIDITY"
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
            counts[f"{consensus_side.value.lower()}_two_peer_delivery_minutes"] += 1
            candidates: list[base.CandidateRow] = []
            for symbol in base.SYMBOLS:
                peers = tuple(item for item in available if item != symbol)
                if len(peers) < 2 or not base.CORE_LEADERS.intersection(peers):
                    continue
                if variant == "primary":
                    candidate = failure_candidate(
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
                else:
                    candidate = base._laggard_candidate(
                        symbol=symbol,
                        side=consensus_side,
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
                    -len(item.leaders), -item.net_reward_risk,
                    -item.price_risk_fraction, item.symbol,
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
            "candidate": "cross-asset delivery-failure rotation",
            "version": 41,
            "frozen_random_seed": 4101,
            "scenario_contract": (
                "two peer accepted delivery states -> laggard leaves both hourly "
                "edges unconsumed and displaces through opposite internal structure "
                "with opposite flow -> first later own-symbol TradeTick -> opposite "
                "displacement invalidation -> opposite frozen hourly target"
            ),
            "primary_variable": (
                "trade opposite laggard delivery failure"
                if args.variant == "primary"
                else "trade aligned laggard assimilation"
            ),
        },
    )
    base.atomic_json(
        args.output / "cross_asset_delivery_failure_v41_summary.json",
        payload,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(run(base.build_parser().parse_args()))
