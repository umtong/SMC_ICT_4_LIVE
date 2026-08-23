"""One coherent direction/liquidity/structure/event/location trading system.

Direction is owned by the semantic liquidity map and active structure.  A source
interaction can terminate as either a failed auction or an accepted auction; the first
causally completed interpretation owns the episode.  OB/FVG/BPR/IFVG and the first
retest refine entry location only.  Price/volume, basis/OI and cross-market state
explain ownership.  The whole position exits at the first meaningful opposing route
obstacle and is invalidated at the causal event/retest extreme.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence
import json
import math

import numpy as np
import pandas as pd

import coherent_policy as core
import coherent_policy_v2 as rich
import hierarchical_liquidity_bpr as hl
from auction_episode_research import CONTRACTS, ActionSpec, _economics, _stable_id, _time_ns
from derivatives_dislocation import prepare_market_state
from liquidity_displacement import _add_common_state
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics
from semantic_liquidity import PoolMeta, build_semantic_liquidity, direction_sources, route_levels


POLICY = (
    "SEMANTIC_LIQUIDITY_DIRECTION_THEN_ACTIVE_STRUCTURE_THEN_MUTUALLY_EXCLUSIVE_"
    "FAILED_OR_ACCEPTED_AUCTION_THEN_PRICE_VOLUME_OWNERSHIP_THEN_OB_FVG_BPR_"
    "LOCATION_THEN_FIRST_RETURN_RESPONSE_THEN_NEXT_OPEN_TO_FIRST_MEANINGFUL_"
    "OPPOSING_ROUTE_OBSTACLE"
)
MAX_HOLD_MINUTES = 360
ENTRY_SLIPPAGE_TICKS = 2
STOP_SLIPPAGE_TICKS = 2
TAKER_FEE = 0.0005
MAKER_FEE = 0.0002
TARGET_INSIDE_TICKS = 1
EPS = 1e-12


@dataclass(frozen=True, slots=True)
class ExecutionLabel:
    fill_state: str
    outcome: str
    fill_index: int | None
    fill_time_ns: int | None
    resolution_index: int | None
    resolution_time_ns: int | None
    entry_wait_minutes: float | None
    holding_minutes: float | None
    order_terminal_time_ns: int | None
    actual_entry: float | None
    actual_target_net_r: float | None
    actual_stop_net_r: float | None
    actual_gross_rr: float | None
    net_r: float | None
    mfe_r: float | None
    mae_r: float | None


@dataclass(frozen=True, slots=True)
class DestinationLabel:
    state_id: str
    upper_level_id: str | None
    lower_level_id: str | None
    upper_price: float | None
    lower_price: float | None
    destination_label: str
    destination_resolution_index: int | None
    destination_resolution_time_ns: int | None


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _available_route_levels(
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    index: int,
    side: str | None = None,
) -> list[hl.LiquidityLevel]:
    output: list[hl.LiquidityLevel] = []
    for level in route_levels(levels, metadata):
        if side is not None and level.side != side:
            continue
        if level.observed_index_1m >= index:
            continue
        if level.first_penetration_index is not None and level.first_penetration_index <= index:
            continue
        output.append(level)
    return output


def _nearest_route_level(
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    index: int,
    price: float,
    side: str,
) -> hl.LiquidityLevel | None:
    wanted = "HIGH" if side == "LONG" else "LOW"
    candidates = [
        level
        for level in _available_route_levels(levels, metadata, index, wanted)
        if (side == "LONG" and level.price > price) or (side == "SHORT" and level.price < price)
    ]
    candidates.sort(
        key=lambda level: (
            abs(level.price - price),
            -metadata[level.level_id].semantic_weight,
            -level.timeframe_minutes,
            level.level_id,
        )
    )
    return candidates[0] if candidates else None


def _nearest_two_sided(
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    index: int,
    price: float,
) -> tuple[hl.LiquidityLevel | None, hl.LiquidityLevel | None]:
    upper = [
        level for level in _available_route_levels(levels, metadata, index, "HIGH")
        if level.price > price
    ]
    lower = [
        level for level in _available_route_levels(levels, metadata, index, "LOW")
        if level.price < price
    ]
    key = lambda level: (
        abs(level.price - price),
        -metadata[level.level_id].semantic_weight,
        -level.timeframe_minutes,
        level.level_id,
    )
    upper.sort(key=key)
    lower.sort(key=key)
    return (upper[0] if upper else None, lower[0] if lower else None)


def _destination_label(
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    index: int,
    state_id: str,
) -> DestinationLabel:
    price = float(data.iloc[index].close)
    upper, lower = _nearest_two_sided(levels, metadata, index, price)
    if upper is None or lower is None:
        return DestinationLabel(
            state_id,
            upper.level_id if upper else None,
            lower.level_id if lower else None,
            upper.price if upper else None,
            lower.price if lower else None,
            "UNRESOLVED_MISSING_SIDE",
            None,
            None,
        )
    end = min(len(data) - 1, index + MAX_HOLD_MINUTES)
    for position in range(index + 1, end + 1):
        row = data.iloc[position]
        up = float(row.high) >= upper.price
        down = float(row.low) <= lower.price
        if up and down:
            label = "AMBIGUOUS_SAME_MINUTE"
        elif up:
            label = "UPPER_FIRST"
        elif down:
            label = "LOWER_FIRST"
        else:
            continue
        return DestinationLabel(
            state_id,
            upper.level_id,
            lower.level_id,
            upper.price,
            lower.price,
            label,
            position,
            int(data.index[position].value),
        )
    return DestinationLabel(
        state_id,
        upper.level_id,
        lower.level_id,
        upper.price,
        lower.price,
        "UNRESOLVED_HORIZON",
        None,
        None,
    )


def label_market_action(
    data: pd.DataFrame,
    action: ActionSpec,
    tick_size: float,
) -> ExecutionLabel:
    start = int(action.emission_index) + 1
    if start >= len(data):
        return ExecutionLabel("NO_FUTURE", "UNRESOLVED", None, None, None, None, None, None, None, None, None, None, None, None, None, None)
    sign = _sign(action.side)
    entry = float(data.iloc[start].open) + sign * ENTRY_SLIPPAGE_TICKS * tick_size
    stop_fill = float(action.stop) - sign * STOP_SLIPPAGE_TICKS * tick_size
    target_fill = float(action.target)
    geometry_valid = stop_fill < entry < target_fill if action.side == "LONG" else target_fill < entry < stop_fill
    if not geometry_valid:
        return ExecutionLabel(
            "CANCELED_AT_FILL_INVALID_GEOMETRY",
            "UNFILLED",
            None,
            None,
            None,
            None,
            1.0,
            None,
            int(data.index[start].value),
            entry,
            None,
            None,
            None,
            None,
            None,
            None,
        )
    planned_risk = abs(entry - float(action.stop))
    reward = abs(target_fill - entry)
    actual_gross_rr = reward / max(planned_risk, EPS)
    if actual_gross_rr < 1.0:
        return ExecutionLabel(
            "CANCELED_AT_FILL_RR_BELOW_ONE",
            "UNFILLED",
            None,
            None,
            None,
            None,
            1.0,
            None,
            int(data.index[start].value),
            entry,
            None,
            None,
            actual_gross_rr,
            None,
            None,
            None,
        )
    cash_risk = abs(entry - stop_fill)
    raw_target = sign * (target_fill - entry) / cash_risk
    raw_stop = sign * (stop_fill - entry) / cash_risk
    raw_target -= (TAKER_FEE * abs(entry) + MAKER_FEE * abs(target_fill)) / cash_risk
    raw_stop -= (TAKER_FEE * abs(entry) + TAKER_FEE * abs(stop_fill)) / cash_risk
    normalization = max(abs(raw_stop), EPS)
    target_account_r = raw_target / normalization
    stop_account_r = -1.0
    best, worst = 0.0, 0.0
    end = min(len(data) - 1, start + MAX_HOLD_MINUTES)
    for position in range(start, end + 1):
        row = data.iloc[position]
        if action.side == "LONG":
            target_hit = float(row.high) >= target_fill
            stop_hit = float(row.low) <= float(action.stop)
            favorable = (float(row.high) - entry) / cash_risk / normalization
            adverse = (float(row.low) - entry) / cash_risk / normalization
        else:
            target_hit = float(row.low) <= target_fill
            stop_hit = float(row.high) >= float(action.stop)
            favorable = (entry - float(row.low)) / cash_risk / normalization
            adverse = (entry - float(row.high)) / cash_risk / normalization
        best = max(best, favorable)
        worst = min(worst, adverse)
        if target_hit and stop_hit:
            outcome, result = "AMBIGUOUS_SAME_MINUTE", stop_account_r
        elif stop_hit:
            outcome, result = "STOP_FIRST", stop_account_r
        elif target_hit:
            outcome, result = "TARGET_FIRST", target_account_r
        else:
            continue
        timestamp = int(data.index[position].value)
        return ExecutionLabel(
            "FILLED_MARKET_NEXT_OPEN",
            outcome,
            start,
            int(data.index[start].value),
            position,
            timestamp,
            1.0,
            float(position - start),
            timestamp,
            entry,
            target_account_r,
            stop_account_r,
            actual_gross_rr,
            result,
            best,
            worst,
        )
    exit_price = float(data.iloc[end].close) - sign * STOP_SLIPPAGE_TICKS * tick_size
    raw_exit = sign * (exit_price - entry) / cash_risk
    raw_exit -= (TAKER_FEE * abs(entry) + TAKER_FEE * abs(exit_price)) / cash_risk
    account_exit = raw_exit / normalization
    timestamp = int(data.index[end].value)
    return ExecutionLabel(
        "FILLED_MARKET_NEXT_OPEN",
        "TIME_EXIT",
        start,
        int(data.index[start].value),
        end,
        timestamp,
        1.0,
        float(end - start),
        timestamp,
        entry,
        target_account_r,
        stop_account_r,
        actual_gross_rr,
        account_exit,
        best,
        worst,
    )


def _semantic_map_features(
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    index: int,
) -> dict[str, float]:
    price = float(data.iloc[index].close)
    atr = core._atr_price(data, index)
    upper, lower = _nearest_two_sided(levels, metadata, index, price)
    output: dict[str, float] = {}
    for name, level in (("upper", upper), ("lower", lower)):
        if level is None:
            output.update(
                {
                    f"semantic_{name}_present": 0.0,
                    f"semantic_{name}_distance_atr": 99.0,
                    f"semantic_{name}_scale_minutes": 0.0,
                    f"semantic_{name}_weight": 0.0,
                    f"semantic_{name}_accumulated": 0.0,
                    f"semantic_{name}_members": 0.0,
                }
            )
            continue
        meta = metadata[level.level_id]
        output.update(
            {
                f"semantic_{name}_present": 1.0,
                f"semantic_{name}_distance_atr": abs(level.price - price) / max(atr, EPS),
                f"semantic_{name}_scale_minutes": float(level.timeframe_minutes),
                f"semantic_{name}_weight": float(meta.semantic_weight),
                f"semantic_{name}_accumulated": float(meta.accumulated),
                f"semantic_{name}_members": float(meta.member_count),
            }
        )
    up = output["semantic_upper_weight"] / max(output["semantic_upper_distance_atr"] + 0.25, 0.25)
    down = output["semantic_lower_weight"] / max(output["semantic_lower_distance_atr"] + 0.25, 0.25)
    output["semantic_attraction_up_minus_down"] = up - down
    output["semantic_attraction_normalized"] = (up - down) / max(up + down, EPS)
    return output


def _event_candidates(
    data: pd.DataFrame,
    source: hl.LiquidityLevel,
    tick: float,
) -> list[tuple[int, int, hl.Setup, dict[str, Any], dict[str, Any]]]:
    output: list[tuple[int, int, hl.Setup, dict[str, Any], dict[str, Any]]] = []
    for detector in (core._reversal_setup, core._continuation_setup):
        detected = detector(data, source, tick)
        if detected is None:
            continue
        setup, meta = detected
        response = core._first_return_response(data, setup, tick)
        if response is None:
            continue
        output.append(
            (
                int(setup.confirmation_index),
                int(response["response_index"]),
                setup,
                meta,
                response,
            )
        )
    output.sort(key=lambda item: (item[0], item[1], item[3]["narrative_branch"]))
    if len(output) >= 2:
        first, second = output[0], output[1]
        if first[0] == second[0] and first[2].side != second[2].side:
            return []
    return output[:1]


def _make_action(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    source: hl.LiquidityLevel,
    setup: hl.Setup,
    event_meta: dict[str, Any],
    response: dict[str, Any],
    tick: float,
) -> tuple[ActionSpec, DestinationLabel] | None:
    emission = int(response["response_index"])
    entry = float(data.iloc[emission].close)
    branch = str(event_meta["narrative_branch"])
    stop = core._action_stop(setup, response, source, data, tick, branch)
    if (setup.side == "LONG" and stop >= entry) or (setup.side == "SHORT" and stop <= entry):
        return None
    target_level = _nearest_route_level(levels, metadata, emission, entry, setup.side)
    if target_level is None:
        return None
    target = float(target_level.price) - _sign(setup.side) * TARGET_INSIDE_TICKS * tick
    economics = _economics(
        side=setup.side,
        entry=entry,
        stop=stop,
        target=target,
        tick_size=tick,
        entry_style="MARKET",
    )
    if not economics or economics["gross_rr"] < 1.0 or economics["target_net_r"] <= 0.0 or economics["stop_net_r"] >= 0.0:
        return None
    event_ns = int(data.index[setup.interaction_index].value)
    source_meta = metadata[source.level_id]
    target_meta = metadata[target_level.level_id]
    state_id = f"CAS3STATE:{symbol}:{event_ns}:{branch}:{_stable_id(source.level_id, setup.setup_kind)}"
    episode_id = f"CAS3:{symbol}:{event_ns}:{_stable_id(source.level_id)}"
    action_id = f"{episode_id}:{branch}:{setup.setup_kind}:{event_meta['location_kind']}:{response['response_kind']}:MARKET"
    emission_ns = int(data.index[emission].value)
    features: dict[str, Any] = {
        **economics,
        **core._liquidity_map_features(data, levels, emission),
        **_semantic_map_features(data, levels, metadata, emission),
        **core._active_structure_features(data, levels, emission),
        **core._approach_features(data, setup.interaction_index, source),
        **core._row_state_features(data, setup.interaction_index, setup.side, "event"),
        **core._row_state_features(data, setup.confirmation_index, setup.side, "confirmation"),
        **core._row_state_features(data, emission, setup.side, "decision"),
        **core._clock_features(pd.Timestamp(data.index[emission])),
        **rich._anchored_vwap_features(data, setup.interaction_index, emission, setup.side),
        **rich._sequence_features(data, emission, setup.side),
        **rich._source_accumulation_features(data, source, setup.interaction_index),
        **rich._volume_route_features(data, emission, entry, target),
        **{key: value for key, value in response.items() if key not in {"departure_index", "touch_index", "response_index", "retest_extreme", "response_kind"}},
        "state_id": state_id,
        "narrative_branch": branch,
        "setup_kind": setup.setup_kind,
        "location_kind": event_meta["location_kind"],
        "response_kind": response["response_kind"],
        "entry_geometry": "COMPLETED_RESPONSE_MARKET_NEXT_OPEN",
        "source_pool_kind": source_meta.pool_kind,
        "source_pool_members": float(source_meta.member_count),
        "source_pool_accumulated": float(source_meta.accumulated),
        "source_semantic_weight": float(source_meta.semantic_weight),
        "source_scale_minutes": float(source.timeframe_minutes),
        "source_strength_ratio": _finite(source.strength_ratio, 0.0),
        "source_defense_count": float(source.defense_count),
        "source_age_minutes": float(emission - source.observed_index_1m),
        "target_pool_kind": target_meta.pool_kind,
        "target_pool_members": float(target_meta.member_count),
        "target_pool_accumulated": float(target_meta.accumulated),
        "target_semantic_weight": float(target_meta.semantic_weight),
        "target_scale_minutes": float(target_level.timeframe_minutes),
        "target_strength_ratio": _finite(target_level.strength_ratio, 0.0),
        "target_defense_count": float(target_level.defense_count),
        "target_age_minutes": float(emission - target_level.observed_index_1m),
        "event_penetration_bps": abs(setup.event_extreme - source.price) / max(abs(source.price), EPS) * 10_000.0,
        "event_to_confirmation_minutes": float(setup.confirmation_index - setup.interaction_index),
        "zone_width_bps": (setup.upper - setup.lower) / max(abs(entry), EPS) * 10_000.0,
        "directional_gap_body_ratio": setup.directional_gap.middle_body_ratio,
        "directional_gap_range_ratio": setup.directional_gap.middle_range_ratio,
        "directional_gap_activity_ratio": setup.directional_gap.middle_activity_ratio,
        "directional_gap_delta_signed": setup.directional_gap.middle_delta_signed,
        "order_block_present": float(event_meta.get("order_block_index", -1.0) >= 0.0),
        "order_block_age_to_decision": float(emission - int(event_meta.get("order_block_index", emission))) if event_meta.get("order_block_index", -1.0) >= 0.0 else 0.0,
        "diagnostic_event_time_ns": event_ns,
        "diagnostic_confirmation_time_ns": int(data.index[setup.confirmation_index].value),
        "diagnostic_departure_time_ns": int(data.index[int(response["departure_index"])].value),
        "diagnostic_first_return_time_ns": int(data.index[int(response["touch_index"])].value),
        "diagnostic_response_time_ns": emission_ns,
        "diagnostic_source_lower": source.lower,
        "diagnostic_source_upper": source.upper,
        "diagnostic_zone_lower": setup.lower,
        "diagnostic_zone_upper": setup.upper,
        "diagnostic_event_extreme": setup.event_extreme,
        "diagnostic_retest_extreme": response["retest_extreme"],
        "diagnostic_target_level_id": target_level.level_id,
        "diagnostic_target_structure_price": target_level.price,
    }
    action = ActionSpec(
        action_id=action_id,
        episode_id=episode_id,
        symbol=symbol,
        event_type=branch,
        decision_stage=f"{setup.setup_kind}_FIRST_RETURN_RESPONSE",
        side=setup.side,
        emission_index=emission,
        emission_time_ns=emission_ns,
        entry_style="MARKET",
        entry=entry,
        stop=stop,
        target=target,
        entry_expiry_minutes=1,
        source_level_id=source.level_id,
        source_kind=source.source_kind,
        source_timeframe_minutes=source.timeframe_minutes,
        source_span=source.span,
        source_price=source.price,
        source_lower=source.lower,
        source_upper=source.upper,
        source_strength_ratio=source.strength_ratio,
        source_defense_count=source.defense_count,
        source_age_minutes=float(emission - source.observed_index_1m),
        objective_id=target_level.level_id,
        objective_kind=target_level.source_kind,
        objective_timeframe_minutes=target_level.timeframe_minutes,
        objective_strength_ratio=target_level.strength_ratio,
        interaction_time_ns=event_ns,
        feature_values=features,
    )
    return action, _destination_label(data, levels, metadata, emission, state_id)


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[hl.LiquidityLevel],
    metadata: dict[str, PoolMeta],
    trading_start: date,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    tick = CONTRACTS[symbol].tick_size
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    sources = sorted(
        direction_sources(levels, metadata),
        key=lambda level: (
            int(level.first_penetration_index),
            level.side,
            -metadata[level.level_id].semantic_weight,
            level.level_id,
        ),
    )
    action_records: list[dict[str, Any]] = []
    state_records: list[dict[str, Any]] = []
    active_until = {"HIGH": -1, "LOW": -1}
    seen_same_clock: set[tuple[int, str]] = set()
    counts = {
        "semantic_sources": len(sources),
        "source_interactions": 0,
        "failed_auction_owned": 0,
        "accepted_auction_owned": 0,
        "conflicting_classifications": 0,
        "executable_actions": 0,
    }
    for source in sources:
        interaction = int(source.first_penetration_index)
        if interaction >= len(data) or int(data.index[interaction].value) < start_ns:
            continue
        if interaction <= active_until[source.side]:
            continue
        clock = (interaction, source.side)
        if clock in seen_same_clock:
            continue
        # Among levels struck in the same completed minute, only the strongest
        # semantic owner is considered.
        peers = [
            level for level in sources
            if level.side == source.side
            and int(level.first_penetration_index) == interaction
        ]
        owner = max(
            peers,
            key=lambda level: (
                metadata[level.level_id].semantic_weight,
                level.timeframe_minutes,
                level.defense_count,
                level.strength_ratio,
            ),
        )
        seen_same_clock.add(clock)
        if owner.level_id != source.level_id:
            continue
        counts["source_interactions"] += 1
        candidates = _event_candidates(data, owner, tick)
        if not candidates:
            continue
        classification, response_index, setup, event_meta, response = candidates[0]
        active_until[source.side] = max(active_until[source.side], response_index)
        branch = str(event_meta["narrative_branch"])
        if branch == "FAILED_AUCTION_REVERSAL":
            counts["failed_auction_owned"] += 1
        else:
            counts["accepted_auction_owned"] += 1
        made = _make_action(symbol, data, levels, metadata, owner, setup, event_meta, response, tick)
        if made is None:
            continue
        action, destination = made
        label = label_market_action(data, action, tick)
        record = {
            **{key: value for key, value in asdict(action).items() if key != "feature_values"},
            **action.feature_values,
            **asdict(label),
        }
        action_records.append(record)
        state_records.append(
            {
                "state_id": destination.state_id,
                "symbol": symbol,
                "episode_id": action.episode_id,
                "emission_index": action.emission_index,
                "emission_time_ns": action.emission_time_ns,
                "action_side": action.side,
                **action.feature_values,
                **asdict(destination),
            }
        )
        counts["executable_actions"] += 1
    actions = pd.DataFrame(action_records)
    states = pd.DataFrame(state_records)
    if not actions.empty and actions.action_id.duplicated().any():
        raise RuntimeError(f"duplicate coherent v3 action identity for {symbol}")
    if not states.empty:
        states = states.drop_duplicates("state_id", keep="first").reset_index(drop=True)
    summary = {
        "symbol": symbol,
        "bars": int(len(data)),
        "semantic_levels": int(len(levels)),
        **counts,
        "outcomes": actions.outcome.value_counts().to_dict() if not actions.empty else {},
        "branches": actions.narrative_branch.value_counts().to_dict() if not actions.empty else {},
    }
    return actions, states, summary


def run_research(
    *,
    start: date,
    end: date,
    warmup_days: int,
    symbols: Sequence[str],
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    from data_re1_flow import load_range_flow

    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    load_start = start - timedelta(days=warmup_days)
    prepared: dict[str, pd.DataFrame] = {}
    raw_by_symbol: dict[str, pd.DataFrame] = {}
    levels_by_symbol: dict[str, list[hl.LiquidityLevel]] = {}
    metadata_by_symbol: dict[str, dict[str, PoolMeta]] = {}
    for symbol in symbols:
        tick = CONTRACTS[symbol].tick_size
        raw = load_range_flow(symbol, load_start, end, cache)
        index_price = load_reference_range("indexPriceKlines", symbol, load_start, end, cache)
        mark_price = load_reference_range("markPriceKlines", symbol, load_start, end, cache)
        metrics = load_range_metrics(symbol, load_start, end, cache)
        state = prepare_market_state(raw, index_price, mark_price, metrics, tick)
        levels, metadata = build_semantic_liquidity(symbol, state, raw, tick)
        prepared[symbol] = state
        raw_by_symbol[symbol] = raw
        levels_by_symbol[symbol] = levels
        metadata_by_symbol[symbol] = metadata
    prepared = _add_common_state(prepared)

    action_frames: list[pd.DataFrame] = []
    state_frames: list[pd.DataFrame] = []
    by_symbol: dict[str, Any] = {}
    for symbol in symbols:
        actions, states, summary = generate_symbol(
            symbol,
            prepared[symbol],
            levels_by_symbol[symbol],
            metadata_by_symbol[symbol],
            start,
        )
        by_symbol[symbol] = summary
        if not actions.empty:
            actions.to_csv(output / f"{symbol}_coherent_actions.csv", index=False)
            action_frames.append(actions)
        if not states.empty:
            states.to_csv(output / f"{symbol}_destination_states.csv", index=False)
            state_frames.append(states)
    combined_actions = pd.concat(action_frames, ignore_index=True, sort=False) if action_frames else pd.DataFrame()
    combined_states = pd.concat(state_frames, ignore_index=True, sort=False) if state_frames else pd.DataFrame()
    combined_actions.to_csv(output / "coherent_actions.csv", index=False)
    combined_states.to_csv(output / "destination_states.csv", index=False)
    resolved = (
        combined_actions[combined_actions.outcome.isin(["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "TIME_EXIT"])]
        if not combined_actions.empty else combined_actions
    )
    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "warmup_days": int(warmup_days),
        "symbols": list(symbols),
        "actions": int(len(combined_actions)),
        "destination_states": int(len(combined_states)),
        "resolved_actions": int(len(resolved)),
        "wins": int((resolved.outcome == "TARGET_FIRST").sum()) if not resolved.empty else 0,
        "win_rate": float((resolved.outcome == "TARGET_FIRST").mean()) if not resolved.empty else None,
        "mean_account_r": float(pd.to_numeric(resolved.net_r, errors="coerce").mean()) if not resolved.empty else None,
        "by_symbol": by_symbol,
        "policy": POLICY,
        "future_information_in_features": False,
        "future_information_in_labels_only": True,
        "execution": {
            "entry": "next one-minute open plus two adverse ticks; cancel if actual gross RR below one",
            "risk": "account R normalized so modeled stop including fees/slippage equals -1R",
            "target": "one tick inside first semantic opposing route obstacle",
            "same_minute": "stop first",
            "maximum_hold_minutes": MAX_HOLD_MINUTES,
        },
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


__all__ = ["POLICY", "run_research", "generate_symbol", "label_market_action"]
