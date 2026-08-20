#!/usr/bin/env python3
"""Harvest every causal semantic departure, including future first-return failures.

The previous prototype moved the order time back to the departure bar but inherited
an action universe which existed only after a successful future first-return response.
That conditioned candidate existence on the future.  This module fixes the structural
error: reversal/continuation classification is completed at confirmation, departure is
observed causally, and every such departure emits immutable pending-limit plans before
any future return is known.

A pending order may be canceled when the setup is invalidated, the predeclared target
is consumed before entry, the first return passes without reaching the order, or the
causal first-return lifetime ends.  After fill the only exits are the predeclared TP
and SL.  No vertical barrier liquidates a position.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

import coherent_policy as core
import coherent_policy_v2 as rich
import coherent_system as v3
import coherent_system_v4 as v4
import hierarchical_liquidity_bpr as hl
from auction_episode_research import CONTRACTS, _economics, _stable_id
from derivatives_dislocation import prepare_market_state
from liquidity_displacement import _add_common_state
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics
from semantic_liquidity_v4 import PoolMeta, build_semantic_liquidity, direction_sources


POLICY = (
    "ALL_CAUSAL_SEMANTIC_DEPARTURES_THEN_FIRST_RETURN_LIMIT_"
    "PENDING_INVALIDATION_OR_TARGET_SPENT_OR_FIRST_RETURN_PASSED_"
    "FILLED_POSITION_TP_OR_SL_ONLY"
)
RR_VARIANTS = (1.0, 1.25, 1.5, 1.75, 2.0)
ENTRY_STYLES = ("ZONE_PROXIMAL_LIMIT", "ZONE_MID_LIMIT")
LIMIT_TRADE_THROUGH_TICKS = 1
STOP_SLIPPAGE_TICKS = 2
MAKER_FEE = 0.0002
TAKER_FEE = 0.0005
EPS = 1e-12


@dataclass(frozen=True, slots=True)
class DepartureCandidate:
    confirmation_index: int
    departure_index: int
    setup: hl.Setup
    event_meta: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BarrierLabel:
    fill_state: str
    outcome: str
    fill_index: int | None
    fill_time_ns: int | None
    resolution_index: int | None
    resolution_time_ns: int | None
    order_terminal_index: int
    order_terminal_time_ns: int
    entry_wait_minutes: float | None
    holding_minutes: float | None
    actual_entry: float | None
    actual_target_net_r: float | None
    actual_stop_net_r: float | None
    actual_gross_rr: float | None
    net_r: float | None
    mfe_r: float | None
    mae_r: float | None


def _sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _departure_index(data: pd.DataFrame, setup: hl.Setup, tick: float) -> int | None:
    for index in range(
        setup.confirmation_index + 1,
        min(len(data), setup.confirmation_index + core.MAX_DEPARTURE_MINUTES + 1),
    ):
        close = float(data.iloc[index].close)
        away = close > setup.upper + tick if setup.side == "LONG" else close < setup.lower - tick
        if away:
            return index
    return None


def _departure_candidates(data: pd.DataFrame, source: hl.LiquidityLevel, tick: float) -> list[DepartureCandidate]:
    output: list[DepartureCandidate] = []
    for detector in (core._reversal_setup, core._continuation_setup):
        detected = detector(data, source, tick)
        if detected is None:
            continue
        setup, event_meta = detected
        departure = _departure_index(data, setup, tick)
        if departure is None:
            continue
        output.append(
            DepartureCandidate(
                confirmation_index=int(setup.confirmation_index),
                departure_index=int(departure),
                setup=setup,
                event_meta=event_meta,
            )
        )
    output.sort(
        key=lambda item: (
            item.confirmation_index,
            item.departure_index,
            str(item.event_meta["narrative_branch"]),
        )
    )
    if len(output) >= 2:
        first, second = output[0], output[1]
        if first.confirmation_index == second.confirmation_index and first.setup.side != second.setup.side:
            return []
    return output[:1]


def _entry_variants(data: pd.DataFrame, candidate: DepartureCandidate, tick: float):
    setup = candidate.setup
    decision = float(data.iloc[candidate.departure_index].close)
    proximal = float(setup.upper if setup.side == "LONG" else setup.lower)
    midpoint = float(0.5 * (setup.lower + setup.upper))
    output = []
    for name, price in (("ZONE_PROXIMAL_LIMIT", proximal), ("ZONE_MID_LIMIT", midpoint)):
        favorable = price <= decision - tick if setup.side == "LONG" else price >= decision + tick
        if favorable:
            output.append((name, float(price)))
    return output


def _causal_stop(data, candidate, source, tick):
    setup, departure = candidate.setup, candidate.departure_index
    branch = str(candidate.event_meta["narrative_branch"])
    buffer = max(2.0 * tick, 0.05 * core._atr_price(data, departure))
    if branch == "FAILED_AUCTION_REVERSAL":
        reference = float(setup.event_extreme)
    else:
        reference = min(float(source.lower), float(setup.lower)) if setup.side == "LONG" else max(float(source.upper), float(setup.upper))
    return reference - buffer if setup.side == "LONG" else reference + buffer


def _pending_expiry(candidate: DepartureCandidate, source: hl.LiquidityLevel) -> int:
    # The first-return opportunity is event-time, not a position exit.  It expires
    # no later than the original 45-minute first-return window and scales down for
    # smaller semantic sources.
    lifetime = max(10, min(core.MAX_RETURN_MINUTES, int(round(2.0 * source.timeframe_minutes))))
    return int(candidate.departure_index + lifetime)


def _raw_economics(side, entry, stop, target, tick):
    sign = _sign(side)
    stop_fill = float(stop) - sign * STOP_SLIPPAGE_TICKS * tick
    risk = abs(float(entry) - stop_fill)
    if risk <= EPS:
        return None
    raw_target = sign * (float(target) - float(entry)) / risk - (
        MAKER_FEE * abs(float(entry)) + MAKER_FEE * abs(float(target))
    ) / risk
    raw_stop = sign * (stop_fill - float(entry)) / risk - (
        MAKER_FEE * abs(float(entry)) + TAKER_FEE * abs(stop_fill)
    ) / risk
    normalization = max(abs(raw_stop), EPS)
    return {
        "target_net_r": raw_target / normalization,
        "stop_net_r": -1.0,
        "normalization": normalization,
        "stop_fill": stop_fill,
        "cash_risk": risk,
    }


def _same_bar_stop_label(data, position, departure, entry, stop, target, side, tick):
    economics = _raw_economics(side, entry, stop, target, tick)
    timestamp = int(data.index[position].value)
    return BarrierLabel(
        fill_state="FILLED_LIMIT",
        outcome="AMBIGUOUS_FILL_BARRIER_SAME_MINUTE",
        fill_index=position,
        fill_time_ns=timestamp,
        resolution_index=position,
        resolution_time_ns=timestamp,
        order_terminal_index=position,
        order_terminal_time_ns=timestamp,
        entry_wait_minutes=float(position - departure),
        holding_minutes=0.0,
        actual_entry=float(entry),
        actual_target_net_r=float(economics["target_net_r"]),
        actual_stop_net_r=-1.0,
        actual_gross_rr=abs(float(target) - float(entry)) / max(abs(float(entry) - float(stop)), EPS),
        net_r=-1.0,
        mfe_r=0.0,
        mae_r=-1.0,
    )


def _resolve_after_fill(data, fill_index, departure, entry, stop, target, side, tick):
    economics = _raw_economics(side, entry, stop, target, tick)
    sign = _sign(side)
    best, worst = 0.0, 0.0
    for position in range(fill_index, len(data)):
        row = data.iloc[position]
        if side == "LONG":
            target_hit = float(row.high) >= float(target)
            stop_hit = float(row.low) <= float(stop)
            favorable = (float(row.high) - float(entry)) / economics["cash_risk"] / economics["normalization"]
            adverse = (float(row.low) - float(entry)) / economics["cash_risk"] / economics["normalization"]
        else:
            target_hit = float(row.low) <= float(target)
            stop_hit = float(row.high) >= float(stop)
            favorable = (float(entry) - float(row.low)) / economics["cash_risk"] / economics["normalization"]
            adverse = (float(entry) - float(row.high)) / economics["cash_risk"] / economics["normalization"]
        best, worst = max(best, favorable), min(worst, adverse)
        if target_hit and stop_hit:
            outcome, result = "AMBIGUOUS_SAME_MINUTE", -1.0
        elif stop_hit:
            outcome, result = "STOP_FIRST", -1.0
        elif target_hit:
            outcome, result = "TARGET_FIRST", float(economics["target_net_r"])
        else:
            continue
        timestamp = int(data.index[position].value)
        return BarrierLabel(
            fill_state="FILLED_LIMIT",
            outcome=outcome,
            fill_index=fill_index,
            fill_time_ns=int(data.index[fill_index].value),
            resolution_index=position,
            resolution_time_ns=timestamp,
            order_terminal_index=position,
            order_terminal_time_ns=timestamp,
            entry_wait_minutes=float(fill_index - departure),
            holding_minutes=float(position - fill_index),
            actual_entry=float(entry),
            actual_target_net_r=float(economics["target_net_r"]),
            actual_stop_net_r=-1.0,
            actual_gross_rr=abs(float(target) - float(entry)) / max(abs(float(entry) - float(stop)), EPS),
            net_r=result,
            mfe_r=best,
            mae_r=worst,
        )
    end = len(data) - 1
    return BarrierLabel(
        fill_state="FILLED_LIMIT",
        outcome="CENSORED_OPEN",
        fill_index=fill_index,
        fill_time_ns=int(data.index[fill_index].value),
        resolution_index=None,
        resolution_time_ns=None,
        order_terminal_index=end,
        order_terminal_time_ns=int(data.index[end].value),
        entry_wait_minutes=float(fill_index - departure),
        holding_minutes=None,
        actual_entry=float(entry),
        actual_target_net_r=float(economics["target_net_r"]),
        actual_stop_net_r=-1.0,
        actual_gross_rr=abs(float(target) - float(entry)) / max(abs(float(entry) - float(stop)), EPS),
        net_r=None,
        mfe_r=best,
        mae_r=worst,
    )


def label_pending(data, candidate, entry, stop, target, tick):
    setup, departure = candidate.setup, candidate.departure_index
    side = setup.side
    expiry = min(len(data) - 1, _pending_expiry(candidate, candidate.source) if hasattr(candidate, "source") else departure + core.MAX_RETURN_MINUTES)
    touch_index = None
    for position in range(departure + 1, expiry + 1):
        row = data.iloc[position]
        invalidated = float(row.low) <= float(stop) if side == "LONG" else float(row.high) >= float(stop)
        target_spent = float(row.high) >= float(target) if side == "LONG" else float(row.low) <= float(target)
        traded_through = float(row.low) <= float(entry) - LIMIT_TRADE_THROUGH_TICKS * tick if side == "LONG" else float(row.high) >= float(entry) + LIMIT_TRADE_THROUGH_TICKS * tick
        overlaps_zone = float(row.low) <= float(setup.upper) and float(row.high) >= float(setup.lower)
        if traded_through:
            if invalidated or target_spent:
                return _same_bar_stop_label(data, position, departure, entry, stop, target, side, tick)
            return _resolve_after_fill(data, position, departure, entry, stop, target, side, tick)
        if invalidated or target_spent:
            timestamp = int(data.index[position].value)
            state = "CANCELED_PRE_FILL_INVALIDATED" if invalidated else "CANCELED_PRE_FILL_TARGET_SPENT"
            return BarrierLabel(state, "UNFILLED", None, None, None, None, position, timestamp, None, None, None, None, None, None, None, None)
        if touch_index is None and overlaps_zone:
            touch_index = position
        elif touch_index is not None:
            close_away = float(row.close) >= float(setup.upper) if side == "LONG" else float(row.close) <= float(setup.lower)
            if close_away or position - touch_index > core.MAX_RESPONSE_BARS:
                timestamp = int(data.index[position].value)
                return BarrierLabel("CANCELED_FIRST_RETURN_PASSED", "UNFILLED", None, None, None, None, position, timestamp, None, None, None, None, None, None, None, None)
    timestamp = int(data.index[expiry].value)
    return BarrierLabel("EXPIRED_UNFILLED", "UNFILLED", None, None, None, None, expiry, timestamp, None, None, None, None, None, None, None, None)


def _plan_features(data, levels, metadata, source, candidate, obstacle, route_features, entry, stop):
    setup, departure = candidate.setup, candidate.departure_index
    source_meta = metadata[source.level_id]
    features = {
        **core._liquidity_map_features(data, levels, departure),
        **v3._semantic_map_features(data, levels, metadata, departure),
        **core._active_structure_features(data, levels, departure),
        **core._approach_features(data, setup.interaction_index, source),
        **core._row_state_features(data, setup.interaction_index, setup.side, "event"),
        **core._row_state_features(data, setup.confirmation_index, setup.side, "confirmation"),
        **core._row_state_features(data, departure, setup.side, "departure"),
        **core._clock_features(pd.Timestamp(data.index[departure])),
        **rich._anchored_vwap_features(data, setup.interaction_index, departure, setup.side),
        **rich._sequence_features(data, departure, setup.side),
        **rich._source_accumulation_features(data, source, setup.interaction_index),
        **rich._volume_route_features(data, departure, entry, obstacle.order_price),
        **route_features,
        "narrative_branch": candidate.event_meta["narrative_branch"],
        "setup_kind": setup.setup_kind,
        "location_kind": candidate.event_meta["location_kind"],
        "source_pool_kind": source_meta.pool_kind,
        "source_pool_members": float(source_meta.member_count),
        "source_pool_accumulated": float(source_meta.accumulated),
        "source_semantic_weight": float(source_meta.semantic_weight),
        "source_scale_minutes": float(source.timeframe_minutes),
        "source_strength_ratio": _finite(source.strength_ratio),
        "source_defense_count": float(source.defense_count),
        "source_age_minutes": float(departure - source.observed_index_1m),
        "target_scale_minutes": float(obstacle.timeframe_minutes),
        "target_strength_ratio": float(obstacle.strength),
        "event_penetration_bps": abs(setup.event_extreme - source.price) / max(abs(source.price), EPS) * 10_000.0,
        "event_to_confirmation_minutes": float(setup.confirmation_index - setup.interaction_index),
        "departure_minutes": float(departure - setup.confirmation_index),
        "zone_width_bps": (setup.upper - setup.lower) / max(abs(entry), EPS) * 10_000.0,
        "directional_gap_body_ratio": setup.directional_gap.middle_body_ratio,
        "directional_gap_range_ratio": setup.directional_gap.middle_range_ratio,
        "directional_gap_activity_ratio": setup.directional_gap.middle_activity_ratio,
        "directional_gap_delta_signed": setup.directional_gap.middle_delta_signed,
        "order_block_present": float(candidate.event_meta.get("order_block_index", -1.0) >= 0.0),
        "diagnostic_event_time_ns": int(data.index[setup.interaction_index].value),
        "diagnostic_confirmation_time_ns": int(data.index[setup.confirmation_index].value),
        "diagnostic_departure_time_ns": int(data.index[departure].value),
        "diagnostic_source_lower": float(source.lower),
        "diagnostic_source_upper": float(source.upper),
        "diagnostic_zone_lower": float(setup.lower),
        "diagnostic_zone_upper": float(setup.upper),
        "diagnostic_event_extreme": float(setup.event_extreme),
        "diagnostic_target_level_id": obstacle.obstacle_id,
        "diagnostic_target_structure_price": float(obstacle.structure_price),
    }
    return features


def generate_symbol(symbol, data, levels, metadata, trading_start):
    tick = CONTRACTS[symbol].tick_size
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    sources = sorted(
        direction_sources(levels, metadata),
        key=lambda level: (
            int(level.first_penetration_index), level.side,
            -metadata[level.level_id].semantic_weight,
            -level.timeframe_minutes, level.level_id,
        ),
    )
    records = []
    active_until = {"HIGH": -1, "LOW": -1}
    seen = set()
    counts = {"semantic_sources": len(sources), "source_interactions": 0, "causal_departures": 0, "plans": 0}
    for source in sources:
        interaction = int(source.first_penetration_index)
        if interaction >= len(data) or int(data.index[interaction].value) < start_ns or interaction <= active_until[source.side]:
            continue
        clock = (interaction, source.side)
        if clock in seen:
            continue
        peers = [level for level in sources if level.side == source.side and int(level.first_penetration_index) == interaction]
        owner = max(
            peers,
            key=lambda level: (
                metadata[level.level_id].semantic_weight,
                level.timeframe_minutes,
                level.defense_count,
                level.strength_ratio,
            ),
        )
        seen.add(clock)
        if owner.level_id != source.level_id:
            continue
        counts["source_interactions"] += 1
        candidates = _departure_candidates(data, owner, tick)
        if not candidates:
            continue
        candidate = candidates[0]
        # Suppress only source penetrations which occurred before this departure;
        # future first-return success is deliberately irrelevant to ownership.
        active_until[source.side] = max(active_until[source.side], candidate.departure_index)
        counts["causal_departures"] += 1
        stop = _causal_stop(data, candidate, owner, tick)
        event_ns = int(data.index[candidate.setup.interaction_index].value)
        episode_id = f"D5:{symbol}:{event_ns}:{_stable_id(owner.level_id)}"
        state_id = f"D5STATE:{symbol}:{event_ns}:{candidate.event_meta['narrative_branch']}:{_stable_id(owner.level_id,candidate.setup.setup_kind)}"
        for entry_name, entry in _entry_variants(data, candidate, tick):
            valid_stop = stop < entry if candidate.setup.side == "LONG" else stop > entry
            if not valid_stop:
                continue
            obstacle, route_features = v4._first_obstacle(
                data, levels, metadata, candidate.departure_index, entry, candidate.setup.side, tick
            )
            if obstacle is None:
                continue
            risk = abs(entry - stop)
            for gross_rr in RR_VARIANTS:
                target = entry + _sign(candidate.setup.side) * gross_rr * risk
                route_clear = target <= obstacle.order_price + tick if candidate.setup.side == "LONG" else target >= obstacle.order_price - tick
                if not route_clear:
                    continue
                economics = _raw_economics(candidate.setup.side, entry, stop, target, tick)
                if economics is None or economics["target_net_r"] <= 0.0:
                    continue
                # Attach source only for the causal order-lifetime calculation.
                object.__setattr__(candidate, "source", owner)
                label = label_pending(data, candidate, entry, stop, target, tick)
                features = _plan_features(data, levels, metadata, owner, candidate, obstacle, route_features, entry, stop)
                action_id = f"{episode_id}:{entry_name}:{gross_rr:.2f}:{obstacle.kind}"
                records.append(
                    {
                        "action_id": action_id,
                        "state_id": state_id,
                        "episode_id": episode_id,
                        "symbol": symbol,
                        "side": candidate.setup.side,
                        "family": candidate.event_meta["narrative_branch"],
                        "order_time_ns": int(data.index[candidate.departure_index].value),
                        "entry_geometry": entry_name,
                        "entry": float(entry),
                        "stop": float(stop),
                        "target": float(target),
                        "gross_rr": float(gross_rr),
                        "risk_bps": risk / max(abs(entry), EPS) * 10_000.0,
                        "route_kind": obstacle.kind,
                        "route_price": float(obstacle.order_price),
                        "route_rr": abs(float(obstacle.order_price) - entry) / max(risk, EPS),
                        "planned_target_net_r": float(economics["target_net_r"]),
                        **features,
                        **asdict(label),
                    }
                )
                counts["plans"] += 1
    frame = pd.DataFrame(records)
    return frame, counts


def run_research(*, start: date, end: date, warmup_days: int, symbols: Sequence[str], cache: Path, output: Path):
    from data_re1_flow import load_range_flow

    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    load_start = start - timedelta(days=warmup_days)
    prepared, levels_by, metadata_by = {}, {}, {}
    for symbol in symbols:
        tick = CONTRACTS[symbol].tick_size
        raw = load_range_flow(symbol, load_start, end + timedelta(days=3), cache)
        index_price = load_reference_range("indexPriceKlines", symbol, load_start, end + timedelta(days=3), cache)
        mark_price = load_reference_range("markPriceKlines", symbol, load_start, end + timedelta(days=3), cache)
        metrics = load_range_metrics(symbol, load_start, end + timedelta(days=3), cache)
        state = prepare_market_state(raw, index_price, mark_price, metrics, tick)
        levels, metadata = build_semantic_liquidity(symbol, state, raw, tick)
        prepared[symbol] = state
        levels_by[symbol] = levels
        metadata_by[symbol] = metadata
    prepared = _add_common_state(prepared)
    frames, by_symbol = [], {}
    for symbol in symbols:
        frame, counts = generate_symbol(symbol, prepared[symbol], levels_by[symbol], metadata_by[symbol], start)
        by_symbol[symbol] = counts
        if not frame.empty:
            frame.to_csv(output / f"{symbol}_departure_actions.csv.gz", index=False, compression="gzip")
            frames.append(frame)
    actions = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    actions.to_csv(output / "departure_actions.csv.gz", index=False, compression="gzip")
    summary = {
        "policy": POLICY,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "symbols": list(symbols),
        "plans": int(len(actions)),
        "states": int(actions.state_id.nunique()) if not actions.empty else 0,
        "outcomes": actions.outcome.value_counts(dropna=False).to_dict() if not actions.empty else {},
        "by_symbol": by_symbol,
        "candidate_existence_uses_future_first_return": False,
        "filled_position_exit_contract": "TAKE_PROFIT_OR_STOP_LOSS_ONLY",
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=45)
    parser.add_argument("--symbols", nargs="+", default=list(SYMBOLS) if 'SYMBOLS' in globals() else ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"])
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_research(start=args.start, end=args.end, warmup_days=args.warmup_days, symbols=args.symbols, cache=args.cache, output=args.output), indent=2, default=str))


if __name__ == "__main__":
    main()
