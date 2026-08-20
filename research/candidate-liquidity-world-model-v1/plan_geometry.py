"""One destination-first plan and causal pending/TP/SL resolution per episode."""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any, Sequence

import numpy as np
import pandas as pd

from liquidity_world import choose_destination
from world_model_common import (
    EPS,
    LIMIT_TRADE_THROUGH_TICKS,
    MAKER_FEE,
    MEDIUM_SCALE,
    STOP_SLIPPAGE_TICKS,
    TAKER_FEE,
    EpisodeSignal,
    OrderLabel,
    SourceEvent,
    finite,
    prior_wick_noise,
    sign,
    stable,
)


def _last_opposite_body(
    data: pd.DataFrame, start: int, end: int, side: str
) -> tuple[float, float, str, int] | None:
    segment = data.iloc[max(0, start) : end + 1]
    opposite = segment.close < segment.open if side == "LONG" else segment.close > segment.open
    indices = np.flatnonzero(opposite.to_numpy())
    if not len(indices):
        return None
    index = max(0, start) + int(indices[-1])
    row = data.iloc[index]
    lower, upper = sorted((float(row.open), float(row.close)))
    return lower, upper, "LAST_OPPOSITE_BODY", index


def _last_fvg(
    data: pd.DataFrame, start: int, end: int, side: str
) -> tuple[float, float, str, int] | None:
    candidates: list[tuple[float, float, str, int]] = []
    for index in range(max(start + 2, 2), end + 1):
        if side == "LONG" and float(data.low.iloc[index]) > float(data.high.iloc[index - 2]):
            candidates.append(
                (float(data.high.iloc[index - 2]), float(data.low.iloc[index]), "BULLISH_FVG", index)
            )
        elif side == "SHORT" and float(data.high.iloc[index]) < float(data.low.iloc[index - 2]):
            candidates.append(
                (float(data.high.iloc[index]), float(data.low.iloc[index - 2]), "BEARISH_FVG", index)
            )
    return candidates[-1] if candidates else None


def _overlap(
    first: tuple[float, float, str, int],
    second: tuple[float, float, str, int],
) -> tuple[float, float] | None:
    lower = max(first[0], second[0])
    upper = min(first[1], second[1])
    return (lower, upper) if upper > lower else None


def entry_zone(
    data: pd.DataFrame,
    signal: EpisodeSignal,
    atr_price: float,
    tick: float,
) -> tuple[float, float, str]:
    body = _last_opposite_body(data, signal.impulse_start_index, signal.decision_index, signal.side)
    gap = _last_fvg(data, signal.impulse_start_index, signal.decision_index, signal.side)
    source_band = (
        (signal.source.lower, signal.source.upper, "TRANSFERRED_SOURCE", signal.interaction_index)
        if signal.source is not None
        else None
    )
    candidates: list[tuple[float, float, str, int, int]] = []
    if body is not None and gap is not None:
        shared = _overlap(body, gap)
        if shared is not None:
            candidates.append((shared[0], shared[1], "OB_FVG_OVERLAP", max(body[3], gap[3]), 4))
    for structure in (body, gap):
        if structure is None:
            continue
        if source_band is not None:
            shared = _overlap(structure, source_band)
            if shared is not None:
                candidates.append(
                    (shared[0], shared[1], f"{structure[2]}_SOURCE_OVERLAP", max(structure[3], source_band[3]), 5)
                )
        candidates.append(
            (structure[0], structure[1], structure[2], structure[3], 2 if "FVG" in structure[2] else 1)
        )
    if source_band is not None:
        candidates.append((source_band[0], source_band[1], source_band[2], source_band[3], 3))

    decision = float(data.close.iloc[signal.decision_index])
    favorable: list[tuple[float, float, str, int, int]] = []
    for lower, upper, kind, observed, priority in candidates:
        lower, upper = sorted((float(lower), float(upper)))
        valid = upper < decision - tick if signal.side == "LONG" else lower > decision + tick
        if valid:
            favorable.append((lower, upper, kind, observed, priority))
    if favorable:
        if signal.side == "LONG":
            favorable.sort(key=lambda item: (decision - item[1], -item[4], -item[3]))
        else:
            favorable.sort(key=lambda item: (item[0] - decision, -item[4], -item[3]))
        return favorable[0][0], favorable[0][1], favorable[0][2]

    width = max(0.08 * atr_price, 3.0 * tick)
    if signal.side == "LONG":
        return decision - width, decision - tick, "CAUSAL_DEPARTURE_BAND"
    return decision + tick, decision + width, "CAUSAL_DEPARTURE_BAND"


def entry_price(
    lower: float,
    upper: float,
    side: str,
    source: SourceEvent | None,
) -> float:
    lower, upper = sorted((float(lower), float(upper)))
    if source is not None and lower <= source.price <= upper:
        return float(source.price)
    width = max(upper - lower, EPS)
    return float(upper - 0.25 * width if side == "LONG" else lower + 0.25 * width)


def stop_price(
    data: pd.DataFrame,
    signal: EpisodeSignal,
    zone_lower: float,
    zone_upper: float,
    tick: float,
) -> float:
    noise = prior_wick_noise(data, signal.decision_index, signal.side, tick)
    if signal.family == "FAILED_AUCTION_REVERSAL":
        reference = signal.event_extreme
    elif signal.family == "ACCEPTED_AUCTION_CONTINUATION":
        reference = (
            min(signal.pullback_extreme, signal.source.lower if signal.source else zone_lower, zone_lower)
            if signal.side == "LONG"
            else max(signal.pullback_extreme, signal.source.upper if signal.source else zone_upper, zone_upper)
        )
    else:
        reference = (
            min(signal.pullback_extreme, zone_lower)
            if signal.side == "LONG"
            else max(signal.pullback_extreme, zone_upper)
        )
    return float(reference - noise if signal.side == "LONG" else reference + noise)


def economics(
    side: str, entry: float, stop: float, target: float, tick: float
) -> dict[str, float] | None:
    direction = sign(side)
    stop_fill = stop - direction * STOP_SLIPPAGE_TICKS * tick
    cash_risk = abs(entry - stop_fill)
    if cash_risk <= EPS:
        return None
    raw_stop = direction * (stop_fill - entry) / cash_risk - (
        MAKER_FEE * abs(entry) + TAKER_FEE * abs(stop_fill)
    ) / cash_risk
    normalization = max(abs(raw_stop), EPS)
    raw_target = direction * (target - entry) / cash_risk - (
        MAKER_FEE * abs(entry) + MAKER_FEE * abs(target)
    ) / cash_risk
    return {
        "target_net_r": raw_target / normalization,
        "normalization": normalization,
        "cash_risk": cash_risk,
        "stop_fill": stop_fill,
    }


def pending_expiry(
    signal: EpisodeSignal, small_nodes: Sequence[Any], data_length: int
) -> int:
    later = sorted(
        int(node.observed_index)
        for node in small_nodes
        if int(node.observed_index) > signal.decision_index
    )
    structural = later[1] if len(later) >= 2 else signal.decision_index + (
        60 if signal.family == "INITIATIVE_MITIGATION_CONTINUATION" else 90
    )
    return min(data_length - 1, structural)


def _empty_label(state: str, data: pd.DataFrame, index: int) -> OrderLabel:
    index = min(max(index, 0), len(data) - 1)
    return OrderLabel(
        fill_state=state,
        outcome="UNFILLED",
        fill_index=None,
        fill_time_ns=None,
        resolution_index=None,
        resolution_time_ns=None,
        order_terminal_index=index,
        order_terminal_time_ns=int(data.index[index].value),
        entry_wait_minutes=None,
        holding_minutes=None,
        actual_entry=None,
        actual_target_net_r=None,
        actual_stop_net_r=None,
        actual_gross_rr=None,
        net_r=None,
        mfe_r=None,
        mae_r=None,
    )


def resolve_order(
    data: pd.DataFrame,
    signal: EpisodeSignal,
    entry: float,
    stop: float,
    target: float,
    tick: float,
    expiry: int,
) -> OrderLabel:
    side = signal.side
    estimate = economics(side, entry, stop, target, tick)
    if estimate is None:
        return _empty_label("INVALID_GEOMETRY", data, signal.decision_index)
    fill_index: int | None = None
    for index in range(signal.decision_index + 1, min(expiry, len(data) - 1) + 1):
        row = data.iloc[index]
        invalid = float(row.low) <= stop if side == "LONG" else float(row.high) >= stop
        spent = float(row.high) >= target if side == "LONG" else float(row.low) <= target
        through = (
            float(row.low) <= entry - LIMIT_TRADE_THROUGH_TICKS * tick
            if side == "LONG"
            else float(row.high) >= entry + LIMIT_TRADE_THROUGH_TICKS * tick
        )
        if through:
            fill_index = index
            if invalid or spent:
                return OrderLabel(
                    "FILLED_LIMIT", "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE",
                    index, int(data.index[index].value), index, int(data.index[index].value),
                    index, int(data.index[index].value), float(index - signal.decision_index),
                    0.0, entry, float(estimate["target_net_r"]), -1.0,
                    abs(target - entry) / max(abs(entry - stop), EPS), -1.0, 0.0, -1.0,
                )
            break
        if invalid:
            return _empty_label("CANCELED_PRE_FILL_INVALIDATED", data, index)
        if spent:
            return _empty_label("CANCELED_PRE_FILL_TARGET_SPENT", data, index)
    if fill_index is None:
        return _empty_label("EXPIRED_UNFILLED", data, expiry)

    best, worst = 0.0, 0.0
    for index in range(fill_index, len(data)):
        row = data.iloc[index]
        if side == "LONG":
            stop_hit = float(row.low) <= stop
            target_hit = float(row.high) >= target
            favorable = (float(row.high) - entry) / estimate["cash_risk"] / estimate["normalization"]
            adverse = (float(row.low) - entry) / estimate["cash_risk"] / estimate["normalization"]
        else:
            stop_hit = float(row.high) >= stop
            target_hit = float(row.low) <= target
            favorable = (entry - float(row.low)) / estimate["cash_risk"] / estimate["normalization"]
            adverse = (entry - float(row.high)) / estimate["cash_risk"] / estimate["normalization"]
        best, worst = max(best, favorable), min(worst, adverse)
        if not (stop_hit or target_hit):
            continue
        if stop_hit and target_hit:
            outcome, result = "AMBIGUOUS_SAME_MINUTE", -1.0
        elif stop_hit:
            outcome, result = "STOP_FIRST", -1.0
        else:
            outcome, result = "TARGET_FIRST", float(estimate["target_net_r"])
        return OrderLabel(
            fill_state="FILLED_LIMIT",
            outcome=outcome,
            fill_index=fill_index,
            fill_time_ns=int(data.index[fill_index].value),
            resolution_index=index,
            resolution_time_ns=int(data.index[index].value),
            order_terminal_index=index,
            order_terminal_time_ns=int(data.index[index].value),
            entry_wait_minutes=float(fill_index - signal.decision_index),
            holding_minutes=float(index - fill_index + 1),
            actual_entry=entry,
            actual_target_net_r=float(estimate["target_net_r"]),
            actual_stop_net_r=-1.0,
            actual_gross_rr=abs(target - entry) / max(abs(entry - stop), EPS),
            net_r=result,
            mfe_r=best,
            mae_r=worst,
        )
    end = len(data) - 1
    return OrderLabel(
        "FILLED_LIMIT", "CENSORED_OPEN", fill_index, int(data.index[fill_index].value),
        None, None, end, int(data.index[end].value), float(fill_index - signal.decision_index),
        None, entry, float(estimate["target_net_r"]), -1.0,
        abs(target - entry) / max(abs(entry - stop), EPS), None, best, worst,
    )


def plan_from_signal(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    nodes_by_scale: dict[float, list[Any]],
    small_nodes: Sequence[Any],
    signal: EpisodeSignal,
    atr: np.ndarray,
    tick: float,
) -> tuple[dict[str, Any] | None, str]:
    control = signal.evidence
    if finite(control.get("control_move_atr")) <= 0.0 or finite(control.get("control_path_efficiency")) <= 0.0:
        return None, "NO_DIRECTIONAL_CONTROL"
    decision_price = float(data.close.iloc[signal.decision_index])
    zone_lower, zone_upper, zone_kind = entry_zone(
        data, signal, float(atr[signal.decision_index]), tick
    )
    entry = entry_price(zone_lower, zone_upper, signal.side, signal.source)
    favorable = entry < decision_price - tick if signal.side == "LONG" else entry > decision_price + tick
    if not favorable:
        return None, "ENTRY_NOT_A_FIRST_RETURN_PRICE"
    stop = stop_price(data, signal, zone_lower, zone_upper, tick)
    if not (stop < entry if signal.side == "LONG" else stop > entry):
        return None, "INVALID_CAUSAL_STOP"
    destination = choose_destination(
        data, levels, metadata, nodes_by_scale, signal.decision_index, entry,
        signal.side, atr, tick,
    )
    if destination is None:
        return None, "NO_FRESH_DESTINATION"
    target = destination.lower - tick if signal.side == "LONG" else destination.upper + tick
    risk = abs(entry - stop)
    gross_rr = sign(signal.side) * (target - entry) / max(risk, EPS)
    if gross_rr < 1.0:
        return None, "DESTINATION_PAYS_LESS_THAN_1R"
    estimate = economics(signal.side, entry, stop, target, tick)
    if estimate is None or estimate["target_net_r"] <= 0.0:
        return None, "NON_POSITIVE_POST_COST_TARGET"
    expiry = pending_expiry(signal, small_nodes, len(data))
    label = resolve_order(data, signal, entry, stop, target, tick, expiry)
    episode_id = (
        f"WM:{symbol}:{int(data.index[signal.interaction_index].value)}:{signal.family}:"
        f"{stable(signal.source.source_id if signal.source else signal.context_scale, signal.decision_index)}"
    )
    decision_quality = (
        1.20 * finite(control.get("control_path_efficiency"))
        + 0.35 * finite(control.get("control_flow_share_signed"))
        + 0.20 * math.log1p(max(finite(control.get("control_activity_ratio")), 0.0))
        + 0.12 * min(gross_rr, 4.0)
        + 0.10 * math.log1p(max(destination.strength, 0.0))
        + 0.08 * finite(control.get("common_breadth_signed"))
    )
    return {
        "order_exists": True,
        "action_id": f"{episode_id}:ONE_PLAN",
        "state_id": f"WMSTATE:{stable(episode_id, signal.decision_index)}",
        "episode_id": episode_id,
        "symbol": symbol,
        "side": signal.side,
        "family": signal.family,
        "interaction_time_ns": int(data.index[signal.interaction_index].value),
        "order_time_ns": int(data.index[signal.decision_index].value),
        "entry_geometry": zone_kind,
        "entry": float(entry),
        "stop": float(stop),
        "target": float(target),
        "gross_rr": float(gross_rr),
        "risk_bps": risk / max(abs(entry), EPS) * 10_000.0,
        "planned_target_net_r": float(estimate["target_net_r"]),
        "route_kind": destination.kind,
        "route_price": float(destination.price),
        "route_strength": float(destination.strength),
        "route_scale": float(destination.scale),
        "source_kind": signal.source.kind if signal.source else "LIVE_MEDIUM_AUCTION_LEG",
        "source_strength": float(signal.source.strength) if signal.source else float(1.0 + MEDIUM_SCALE),
        "source_scale": float(signal.source.scale) if signal.source else float(MEDIUM_SCALE * 60.0),
        "source_confluence_count": int(signal.source.confluence_count) if signal.source else 1,
        "zone_lower": float(zone_lower),
        "zone_upper": float(zone_upper),
        "event_extreme": float(signal.event_extreme),
        "pullback_extreme": float(signal.pullback_extreme),
        "decision_quality": float(decision_quality),
        **control,
        **asdict(label),
    }, "PLAN_CREATED"


def _future_excursions(
    data: pd.DataFrame, index: int, atr_price: float, horizon: int = 240
) -> tuple[float, float]:
    part = data.iloc[index + 1 : min(len(data), index + horizon + 1)]
    if part.empty:
        return 0.0, 0.0
    close = float(data.close.iloc[index])
    return (
        (float(part.high.max()) - close) / max(atr_price, EPS),
        (close - float(part.low.min())) / max(atr_price, EPS),
    )


def no_plan_record(
    symbol: str,
    signal: EpisodeSignal,
    data: pd.DataFrame,
    reason: str,
    atr: np.ndarray,
) -> dict[str, Any]:
    up, down = _future_excursions(data, signal.decision_index, float(atr[signal.decision_index]))
    episode_id = (
        f"WMNOPLAN:{symbol}:{int(data.index[signal.interaction_index].value)}:{signal.family}:"
        f"{stable(signal.decision_index, reason)}"
    )
    return {
        "order_exists": False,
        "action_id": episode_id,
        "state_id": f"WMSTATE:{stable(episode_id)}",
        "episode_id": episode_id,
        "symbol": symbol,
        "side": signal.side,
        "family": signal.family,
        "interaction_time_ns": int(data.index[signal.interaction_index].value),
        "order_time_ns": int(data.index[signal.decision_index].value),
        "no_trade_reason": reason,
        "future_up_atr_diagnostic": float(up),
        "future_down_atr_diagnostic": float(down),
        "decision_quality": float("nan"),
        **signal.evidence,
    }
