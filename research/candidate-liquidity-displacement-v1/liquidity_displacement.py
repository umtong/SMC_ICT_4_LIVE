"""Causal liquidity-displacement-first-return research.

This candidate does not score or filter legacy plans. It reconstructs the sequence
repeated in the source material and in the reviewed human trades:

pre-existing external liquidity / accepted structure
-> sweep-reclaim or body-break-and-hold
-> directional displacement that breaks local structure and leaves a fresh FVG/OB origin
-> first return to that origin
-> completed response proving local control
-> next-minute market entry with event invalidation and the nearest pre-existing
   unconsumed opposing structure as the objective.

Future bars are used only by the conservative first-passage labeler inherited from
``auction_episode_research``. The same code and normalized features are used for
BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence
import json
import math

import numpy as np
import pandas as pd

from auction_episode_research import (
    CONTRACTS,
    ActionSpec,
    PivotLevel,
    _economics,
    _objective_candidates,
    _resample_flow,
    _stable_id,
    _time_ns,
    detect_pivots,
    label_action,
    prepare_one_minute,
)
from metrics_state import load_range_metrics, metric_features


POLICY = (
    "LIQUIDITY_DISPLACEMENT_FIRST_RETURN:PREEXISTING_15M_60M_BOUNDARY_THEN_"
    "FAILED_OR_ACCEPTED_AUCTION_THEN_LOCAL_BOS_DISPLACEMENT_FRESH_FVG_OR_OB_"
    "THEN_FIRST_RETURN_COMPLETED_RESPONSE_THEN_NEXT_MINUTE_ENTRY"
)
MAX_EVENT_TO_DISPLACEMENT = 15
MAX_ZONE_RETURN_MINUTES = 45
MAX_RESPONSE_BARS = 3
MAX_LABEL_HOLD_MINUTES = 360


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _side_sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _directional(side: str, value: float) -> float:
    return _side_sign(side) * float(value)


def _merge_metrics(data: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    features = metric_features(metrics)
    return pd.merge_asof(
        data.sort_index(),
        features.sort_index(),
        left_index=True,
        right_index=True,
        direction="backward",
        allow_exact_matches=True,
    )


def _add_common_state(prepared: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    common_index: pd.DatetimeIndex | None = None
    for frame in prepared.values():
        common_index = frame.index if common_index is None else common_index.intersection(frame.index)
    if common_index is None or len(common_index) == 0:
        raise RuntimeError("no synchronized one-minute state")
    aligned = {symbol: frame.reindex(common_index).copy() for symbol, frame in prepared.items()}
    symbols = tuple(aligned)
    for minutes in (1, 3, 5, 15, 30, 60):
        returns = pd.concat(
            {symbol: np.log(aligned[symbol]["close"]).diff(minutes) for symbol in symbols},
            axis=1,
        )
        common = returns.median(axis=1)
        breadth = np.sign(returns).mean(axis=1)
        dispersion = returns.std(axis=1, ddof=0)
        ranks = returns.rank(axis=1, pct=True)
        for symbol in symbols:
            aligned[symbol][f"common_return_{minutes}m"] = common
            aligned[symbol][f"residual_return_{minutes}m"] = returns[symbol] - common
            aligned[symbol][f"common_breadth_{minutes}m"] = breadth
            aligned[symbol][f"cross_dispersion_{minutes}m"] = dispersion
            aligned[symbol][f"return_rank_{minutes}m"] = ranks[symbol]
    delta = pd.concat({symbol: aligned[symbol]["delta_share"] for symbol in symbols}, axis=1)
    common_delta = delta.median(axis=1)
    for symbol in symbols:
        aligned[symbol]["common_delta_share_1m"] = common_delta
        aligned[symbol]["residual_delta_share_1m"] = delta[symbol] - common_delta
        aligned[symbol]["delta_rank_1m"] = delta.rank(axis=1, pct=True)[symbol]
    return aligned


def _structure_state(levels: Sequence[PivotLevel], emission_ns: int, side: str) -> dict[str, float]:
    known = [level for level in levels if level.observed_time_ns < emission_ns]
    output: dict[str, float] = {}
    sign = _side_sign(side)
    for timeframe in (5, 15, 60):
        for level_side in ("HIGH", "LOW"):
            selected = [x for x in known if x.timeframe_minutes == timeframe and x.side == level_side][-2:]
            if len(selected) == 2:
                change = (selected[-1].price - selected[-2].price) / max(abs(selected[-2].price), 1e-12) * 10_000.0
                output[f"structure_{timeframe}m_{level_side.lower()}_change_signed_bps"] = sign * change
                output[f"structure_{timeframe}m_{level_side.lower()}_spacing_minutes"] = (
                    selected[-1].event_time_ns - selected[-2].event_time_ns
                ) / 60_000_000_000.0
            else:
                output[f"structure_{timeframe}m_{level_side.lower()}_change_signed_bps"] = 0.0
                output[f"structure_{timeframe}m_{level_side.lower()}_spacing_minutes"] = 0.0
    return output


def _find_local_control(data: pd.DataFrame, index: int, side: str, lookback: int = 8) -> float:
    prior = data.iloc[max(0, index - lookback):index]
    if prior.empty:
        return float(data.iloc[index]["close"])
    return float(prior["high"].max()) if side == "LONG" else float(prior["low"].min())


def _displacement_zone(data: pd.DataFrame, index: int, side: str, tick: float) -> dict[str, Any] | None:
    """Return a fresh origin only when a completed bar owns local BOS displacement."""
    if index < 4:
        return None
    row = data.iloc[index]
    two_back = data.iloc[index - 2]
    control = _find_local_control(data, index, side)
    close = float(row["close"])
    aligned_close = close > control + tick if side == "LONG" else close < control - tick
    aligned_body = float(row["close"] - row["open"]) * _side_sign(side) > 0.0
    close_location = _finite(row.get("close_location"), 0.5)
    close_at_edge = close_location >= 0.65 if side == "LONG" else close_location <= 0.35
    body_ratio = _finite(row.get("body_ratio"), 0.0)
    range_ratio = _finite(row.get("range_ratio"), 0.0)
    expansion = body_ratio >= 1.20 and range_ratio >= 1.05
    if not (aligned_close and aligned_body and close_at_edge and expansion):
        return None

    if side == "LONG":
        fvg_lower = float(two_back["high"])
        fvg_upper = float(row["low"])
        fvg = fvg_upper > fvg_lower + tick
    else:
        fvg_lower = float(row["high"])
        fvg_upper = float(two_back["low"])
        fvg = fvg_upper > fvg_lower + tick

    ob_index: int | None = None
    for j in range(index - 1, max(-1, index - 6), -1):
        candidate = data.iloc[j]
        opposite = float(candidate["close"] - candidate["open"]) * _side_sign(side) < 0.0
        if opposite:
            ob_index = j
            break
    ob = None
    if ob_index is not None:
        ob_row = data.iloc[ob_index]
        ob = (
            min(float(ob_row["open"]), float(ob_row["close"])),
            max(float(ob_row["open"]), float(ob_row["close"])),
        )
    if not fvg and ob is None:
        return None
    if fvg and ob is not None:
        overlap_lower = max(fvg_lower, ob[0])
        overlap_upper = min(fvg_upper, ob[1])
        if overlap_upper > overlap_lower + tick:
            lower, upper, origin_kind = overlap_lower, overlap_upper, "FVG_OB_OVERLAP"
        else:
            lower, upper, origin_kind = fvg_lower, fvg_upper, "FVG"
    elif fvg:
        lower, upper, origin_kind = fvg_lower, fvg_upper, "FVG"
    else:
        assert ob is not None
        lower, upper, origin_kind = ob[0], ob[1], "ORDER_BLOCK"
    if upper <= lower + tick:
        return None
    return {
        "lower": float(lower),
        "upper": float(upper),
        "origin_kind": origin_kind,
        "displacement_index": index,
        "displacement_control": control,
        "displacement_body_ratio": body_ratio,
        "displacement_range_ratio": range_ratio,
        "displacement_activity_ratio": _finite(row.get("activity_ratio"), 0.0),
        "displacement_delta_share_signed": _directional(side, _finite(row.get("delta_share"), 0.0)),
        "displacement_impact_per_activity": _finite(row.get("impact_per_activity"), 0.0),
        "displacement_fvg": float(fvg),
        "displacement_ob": float(ob is not None),
        "displacement_ob_age_bars": float(index - ob_index) if ob_index is not None else 0.0,
    }


def _first_return_response(data: pd.DataFrame, zone: dict[str, Any], side: str, tick: float) -> dict[str, Any] | None:
    start = int(zone["displacement_index"]) + 1
    end = min(len(data), start + MAX_ZONE_RETURN_MINUTES)
    touched: int | None = None
    retest_extreme: float | None = None
    for index in range(start, end):
        row = data.iloc[index]
        overlaps = float(row["low"]) <= zone["upper"] and float(row["high"]) >= zone["lower"]
        if touched is None:
            if not overlaps:
                continue
            touched = index
            retest_extreme = float(row["low"] if side == "LONG" else row["high"])
        else:
            retest_extreme = (
                min(float(retest_extreme), float(row["low"]))
                if side == "LONG"
                else max(float(retest_extreme), float(row["high"]))
            )
        if index - touched > MAX_RESPONSE_BARS:
            return None
        spent = (
            float(row["close"]) < zone["lower"] - tick
            if side == "LONG"
            else float(row["close"]) > zone["upper"] + tick
        )
        if spent:
            return None
        aligned_body = float(row["close"] - row["open"]) * _side_sign(side) > 0.0
        closes_away = float(row["close"]) >= zone["upper"] if side == "LONG" else float(row["close"]) <= zone["lower"]
        previous = data.iloc[index - 1]
        local_control = float(row["close"]) > float(previous["high"]) if side == "LONG" else float(row["close"]) < float(previous["low"])
        signed_delta = _directional(side, _finite(row.get("delta_share"), 0.0))
        price_progress = _directional(side, float(row["close"] - row["open"]))
        initiative = signed_delta > 0.0
        absorption = signed_delta <= 0.0 and price_progress > 0.0
        if aligned_body and closes_away and local_control and (initiative or absorption):
            return {
                "touch_index": touched,
                "response_index": index,
                "retest_extreme": float(retest_extreme),
                "return_wait_minutes": float(touched - zone["displacement_index"]),
                "response_delay_minutes": float(index - touched),
                "response_kind": "ALIGNED_INITIATIVE" if initiative else "ADVERSE_FLOW_ABSORBED",
                "response_delta_share_signed": signed_delta,
                "response_body_ratio": _finite(row.get("body_ratio"), 0.0),
                "response_range_ratio": _finite(row.get("range_ratio"), 0.0),
                "response_activity_ratio": _finite(row.get("activity_ratio"), 0.0),
                "response_impact_per_activity": _finite(row.get("impact_per_activity"), 0.0),
                "response_close_location_signed": _directional(
                    side,
                    2.0 * (_finite(row.get("close_location"), 0.5) - 0.5),
                ),
            }
    return None


def _event_candidate(data: pd.DataFrame, interaction: int, boundary: PivotLevel, tick: float) -> dict[str, Any] | None:
    """Freeze either failed auction or accepted transfer before displacement."""
    end = min(len(data), interaction + 8)
    source_low = boundary.side == "LOW"
    reversal_side = "LONG" if source_low else "SHORT"
    continuation_side = "SHORT" if source_low else "LONG"
    extreme = float(data.iloc[interaction]["low"] if source_low else data.iloc[interaction]["high"])
    first_outside: int | None = None
    for index in range(interaction, end):
        row = data.iloc[index]
        extreme = min(extreme, float(row["low"])) if source_low else max(extreme, float(row["high"]))
        penetrated = float(row["low"]) < boundary.lower if source_low else float(row["high"]) > boundary.upper
        reclaimed = float(row["close"]) >= boundary.upper if source_low else float(row["close"]) <= boundary.lower
        outside_close = float(row["close"]) < boundary.lower if source_low else float(row["close"]) > boundary.upper
        if penetrated and reclaimed:
            return {
                "event_type": "FAILED_AUCTION",
                "side": reversal_side,
                "event_index": index,
                "event_extreme": extreme,
                "event_stop_reference": extreme,
                "event_bars": float(index - interaction + 1),
            }
        if outside_close:
            if first_outside is None:
                first_outside = index
            elif index == first_outside + 1:
                prior = data.iloc[first_outside]
                prior_outside = float(prior["close"]) < boundary.lower if source_low else float(prior["close"]) > boundary.upper
                if prior_outside:
                    before = data.iloc[max(0, interaction - 8):interaction]
                    origin = (
                        float(before["high"].max())
                        if continuation_side == "SHORT" and not before.empty
                        else float(before["low"].min())
                        if continuation_side == "LONG" and not before.empty
                        else extreme
                    )
                    return {
                        "event_type": "ACCEPTED_AUCTION",
                        "side": continuation_side,
                        "event_index": index,
                        "event_extreme": extreme,
                        "event_stop_reference": origin,
                        "event_bars": float(index - interaction + 1),
                    }
        elif first_outside is not None:
            first_outside = None
    return None


def _action_from_sequence(
    *,
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[PivotLevel],
    boundary: PivotLevel,
    interaction_index: int,
    event: dict[str, Any],
    zone: dict[str, Any],
    response: dict[str, Any],
    tick: float,
) -> ActionSpec | None:
    side = str(event["side"])
    emission_index = int(response["response_index"])
    emission_ns = _time_ns(data.index, emission_index)
    entry = float(data.iloc[emission_index]["close"])
    buffer = max(2.0 * tick, 0.05 * _finite(data.iloc[emission_index].get("prior_range_1m"), tick))
    if event["event_type"] == "FAILED_AUCTION":
        reference = float(event["event_stop_reference"])
    else:
        reference = (
            min(float(event["event_stop_reference"]), float(response["retest_extreme"]))
            if side == "LONG"
            else max(float(event["event_stop_reference"]), float(response["retest_extreme"]))
        )
    stop = reference - buffer if side == "LONG" else reference + buffer
    objectives = _objective_candidates(
        levels,
        side=side,
        entry=entry,
        emission_index=emission_index,
        emission_time_ns=emission_ns,
        micro_reference=None,
        tick_size=tick,
    )
    if not objectives:
        return None
    objective = objectives[0]
    economics = _economics(
        side=side,
        entry=entry,
        stop=stop,
        target=objective.price,
        tick_size=tick,
        entry_style="MARKET",
    )
    if (
        not economics
        or economics["gross_rr"] < 1.0
        or economics["target_net_r"] <= 0.0
        or economics["stop_net_r"] >= 0.0
    ):
        return None
    interaction_ns = _time_ns(data.index, interaction_index)
    episode_id = f"LDFR:{symbol}:{interaction_ns}:{boundary.side}:{_stable_id(boundary.level_id)}"
    action_id = f"{episode_id}:{event['event_type']}:{zone['origin_kind']}:{response['response_kind']}"
    row = data.iloc[emission_index]
    sign = _side_sign(side)
    feature_values: dict[str, Any] = {
        **economics,
        **_structure_state(levels, emission_ns, side),
        "origin_kind": zone["origin_kind"],
        "response_kind": response["response_kind"],
        "source_side": boundary.side,
        "event_bars": event["event_bars"],
        "event_to_displacement_minutes": float(zone["displacement_index"] - event["event_index"]),
        "source_strength_ratio": boundary.strength_ratio,
        "source_defense_count": boundary.defense_count,
        "source_age_minutes": (emission_ns - boundary.observed_time_ns) / 60_000_000_000.0,
        "event_penetration_bps": abs(float(event["event_extreme"]) - boundary.price) / max(abs(boundary.price), 1e-12) * 10_000.0,
        "origin_width_bps": (zone["upper"] - zone["lower"]) / max(abs(entry), 1e-12) * 10_000.0,
        **{
            key: value
            for key, value in zone.items()
            if key not in {"lower", "upper", "origin_kind", "displacement_index", "displacement_control"}
        },
        **{
            key: value
            for key, value in response.items()
            if key not in {"touch_index", "response_index", "retest_extreme", "response_kind"}
        },
        "decision_delta_share_signed": sign * _finite(row.get("delta_share"), 0.0),
        "decision_activity_ratio": _finite(row.get("activity_ratio"), 0.0),
        "decision_body_ratio": _finite(row.get("body_ratio"), 0.0),
        "decision_range_ratio": _finite(row.get("range_ratio"), 0.0),
        "decision_impact_per_activity": _finite(row.get("impact_per_activity"), 0.0),
        "decision_close_location_signed": sign * 2.0 * (_finite(row.get("close_location"), 0.5) - 0.5),
    }
    for minutes in (1, 3, 5, 15, 30, 60):
        prior = data.iloc[max(0, emission_index - minutes):emission_index + 1]
        if len(prior) < 2:
            continue
        feature_values[f"return_{minutes}m_signed"] = sign * math.log(
            float(prior.iloc[-1]["close"]) / float(prior.iloc[0]["close"])
        )
        feature_values[f"delta_share_{minutes}m_signed"] = sign * _finite(
            prior["signed_quote"].sum() / max(prior["quote_volume"].sum(), 1e-12),
            0.0,
        )
        feature_values[f"activity_{minutes}m"] = _finite(prior["activity_ratio"].median(), 0.0)
        feature_values[f"common_return_{minutes}m_signed"] = sign * _finite(
            row.get(f"common_return_{minutes}m"),
            0.0,
        )
        feature_values[f"residual_return_{minutes}m_signed"] = sign * _finite(
            row.get(f"residual_return_{minutes}m"),
            0.0,
        )
        feature_values[f"common_breadth_{minutes}m_signed"] = sign * _finite(
            row.get(f"common_breadth_{minutes}m"),
            0.0,
        )
    for column in row.index:
        if str(column).startswith("metric_"):
            feature_values[str(column)] = _finite(row[column], 0.0)
    return ActionSpec(
        action_id=action_id,
        episode_id=episode_id,
        symbol=symbol,
        event_type=str(event["event_type"]),
        decision_stage="DISPLACEMENT_FIRST_RETURN_RESPONSE",
        side=side,
        emission_index=emission_index,
        emission_time_ns=emission_ns,
        entry_style="MARKET",
        entry=entry,
        stop=stop,
        target=float(objective.price),
        entry_expiry_minutes=1,
        source_level_id=boundary.level_id,
        source_kind=boundary.source_kind,
        source_timeframe_minutes=boundary.timeframe_minutes,
        source_span=boundary.span,
        source_price=boundary.price,
        source_lower=boundary.lower,
        source_upper=boundary.upper,
        source_strength_ratio=boundary.strength_ratio,
        source_defense_count=boundary.defense_count,
        source_age_minutes=(emission_ns - boundary.observed_time_ns) / 60_000_000_000.0,
        objective_id=objective.objective_id,
        objective_kind=objective.kind,
        objective_timeframe_minutes=objective.timeframe_minutes,
        objective_strength_ratio=objective.strength_ratio,
        interaction_time_ns=_time_ns(data.index, interaction_index),
        feature_values=feature_values,
    )


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[PivotLevel],
    *,
    trading_start: date,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    tick = CONTRACTS[symbol].tick_size
    source_levels = [level for level in levels if level.timeframe_minutes in (15, 60)]
    source_levels.sort(key=lambda level: (level.observed_index_1m, level.level_id))
    available: list[PivotLevel] = []
    next_source = 0
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    records: list[dict[str, Any]] = []
    episode_count = 0
    seen_episodes: set[str] = set()
    for index, (timestamp, row) in enumerate(data.iterrows()):
        while next_source < len(source_levels) and source_levels[next_source].observed_index_1m < index:
            available.append(source_levels[next_source])
            next_source += 1
        if int(timestamp.value) < start_ns or not available:
            continue
        touched = [
            level
            for level in available
            if not level.retired_as_source
            and float(row["low"]) <= level.upper
            and float(row["high"]) >= level.lower
        ]
        if not touched:
            continue
        for source_side in ("LOW", "HIGH"):
            candidates = [level for level in touched if level.side == source_side]
            if not candidates:
                continue
            boundary = max(
                candidates,
                key=lambda level: (
                    level.timeframe_minutes,
                    level.defense_count,
                    level.strength_ratio,
                    -abs(level.price - float(row["close"])),
                ),
            )
            episode_key = f"{symbol}:{int(timestamp.value)}:{source_side}:{_stable_id(boundary.level_id)}"
            if episode_key in seen_episodes:
                continue
            seen_episodes.add(episode_key)
            episode_count += 1
            width = max(boundary.upper - boundary.lower, 4.0 * tick)
            for level in available:
                if (
                    level.side == source_side
                    and not level.retired_as_source
                    and abs(level.price - boundary.price) <= 1.5 * width
                ):
                    level.retired_as_source = True
            event = _event_candidate(data, index, boundary, tick)
            if event is None:
                continue
            displacement = None
            for displacement_index in range(
                int(event["event_index"]) + 1,
                min(len(data), int(event["event_index"]) + MAX_EVENT_TO_DISPLACEMENT + 1),
            ):
                displacement = _displacement_zone(data, displacement_index, str(event["side"]), tick)
                if displacement is not None:
                    break
            if displacement is None:
                continue
            response = _first_return_response(data, displacement, str(event["side"]), tick)
            if response is None:
                continue
            action = _action_from_sequence(
                symbol=symbol,
                data=data,
                levels=levels,
                boundary=boundary,
                interaction_index=index,
                event=event,
                zone=displacement,
                response=response,
                tick=tick,
            )
            if action is None:
                continue
            label = label_action(data, action, tick)
            if label.holding_minutes is not None and label.holding_minutes > MAX_LABEL_HOLD_MINUTES:
                continue
            record = {
                **{key: value for key, value in asdict(action).items() if key != "feature_values"},
                **action.feature_values,
                **asdict(label),
            }
            records.append(record)
    frame = pd.DataFrame(records)
    if not frame.empty and frame.action_id.duplicated().any():
        raise RuntimeError(f"duplicate action id {symbol}")
    summary = {
        "symbol": symbol,
        "bars": len(data),
        "levels": len(levels),
        "source_levels": len(source_levels),
        "episodes": episode_count,
        "actions": len(frame),
        "outcomes": frame.outcome.value_counts().to_dict() if not frame.empty else {},
    }
    return frame, summary


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
    raw_by_symbol = {symbol: load_range_flow(symbol, load_start, end, cache) for symbol in symbols}
    prepared: dict[str, pd.DataFrame] = {}
    levels_by_symbol: dict[str, list[PivotLevel]] = {}
    for symbol, raw in raw_by_symbol.items():
        tick = CONTRACTS[symbol].tick_size
        data = prepare_one_minute(raw, tick)
        metrics = load_range_metrics(symbol, load_start, end, cache)
        data = _merge_metrics(data, metrics)
        prepared[symbol] = data
        aggregates = {minutes: _resample_flow(raw, minutes) for minutes in (5, 15, 60)}
        levels_by_symbol[symbol] = detect_pivots(symbol, data, aggregates, tick)
    prepared = _add_common_state(prepared)
    frames: list[pd.DataFrame] = []
    by_symbol: dict[str, Any] = {}
    for symbol in symbols:
        frame, summary = generate_symbol(
            symbol,
            prepared[symbol],
            levels_by_symbol[symbol],
            trading_start=start,
        )
        by_symbol[symbol] = summary
        if not frame.empty:
            frame.to_csv(output / f"{symbol}_liquidity_displacement_actions.csv", index=False)
            frames.append(frame)
    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    combined.to_csv(output / "liquidity_displacement_actions.csv", index=False)
    resolved = (
        combined[combined.outcome.isin(["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"])]
        if not combined.empty
        else combined
    )
    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "warmup_days": warmup_days,
        "symbols": list(symbols),
        "actions": len(combined),
        "resolved": len(resolved),
        "wins": int((resolved.outcome == "TARGET_FIRST").sum()) if not resolved.empty else 0,
        "win_rate": float((resolved.outcome == "TARGET_FIRST").mean()) if not resolved.empty else None,
        "by_symbol": by_symbol,
        "policy": POLICY,
        "future_information_in_features": False,
        "future_information_in_labels_only": True,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


__all__ = [
    "run_research",
    "generate_symbol",
    "_displacement_zone",
    "_first_return_response",
]
