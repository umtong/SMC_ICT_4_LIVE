"""Volume-route and causal-sequence extension of the coherent liquidity policy.

The event logic is unchanged.  This version adds information a skilled trader sees
when deciding whether the directional narrative really owns the path: accumulated
liquidity at the source, event-anchored VWAP, the multi-resolution price/volume path,
and volume density between entry and the first meaningful obstacle.  A lone weak 5m
pivot is not automatically promoted to the same structural role as accumulated or
higher-timeframe liquidity.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence
import math

import numpy as np
import pandas as pd

import coherent_policy as base
import hierarchical_liquidity_bpr as hl


POLICY_V2 = base.POLICY + ":VOLUME_ROUTE_SEQUENCE_AND_MEANINGFUL_OBSTACLE"
_ORIGINAL_MAKE_ACTION = base._make_action


def _meaningful_route_level(
    levels: Sequence[hl.LiquidityLevel],
    index: int,
    price: float,
    side: str,
) -> hl.LiquidityLevel | None:
    wanted = "HIGH" if side == "LONG" else "LOW"
    candidates = []
    for level in base._available_levels(levels, index, side=wanted, minimum_timeframe=5):
        ahead = level.price > price if side == "LONG" else level.price < price
        if not ahead:
            continue
        meaningful = (
            level.timeframe_minutes >= 15
            or level.defense_count >= 2
            or base._finite(level.strength_ratio, 0.0) >= 1.2
        )
        if meaningful:
            candidates.append(level)
    candidates.sort(
        key=lambda level: (
            abs(level.price - price),
            -level.timeframe_minutes,
            -level.defense_count,
            -level.strength_ratio,
            level.level_id,
        )
    )
    return candidates[0] if candidates else None


def _anchored_vwap_features(
    data: pd.DataFrame,
    interaction: int,
    emission: int,
    side: str,
) -> dict[str, float]:
    sign = base._sign(side)
    output: dict[str, float] = {}
    timestamp = pd.Timestamp(data.index[emission])
    anchors = {
        "event": interaction,
        "four_hour": int(data.index.searchsorted(timestamp.floor("4h"), side="left")),
        "day": int(data.index.searchsorted(timestamp.floor("1d"), side="left")),
    }
    price = float(data.iloc[emission].close)
    for name, start in anchors.items():
        frame = data.iloc[max(0, start):emission + 1]
        if frame.empty or float(frame.quote_volume.sum()) <= 0.0:
            output[f"vwap_{name}_distance_bps_signed"] = 0.0
            output[f"vwap_{name}_delta_share_signed"] = 0.0
            continue
        typical = (frame.high + frame.low + frame.close) / 3.0
        vwap = float((typical * frame.quote_volume).sum() / frame.quote_volume.sum())
        signed = 2.0 * frame.taker_buy_quote_volume - frame.quote_volume
        output[f"vwap_{name}_distance_bps_signed"] = sign * (price - vwap) / max(abs(price), base.EPS) * 10_000.0
        output[f"vwap_{name}_delta_share_signed"] = sign * base._finite(signed.sum() / max(frame.quote_volume.sum(), base.EPS), 0.0)
    return output


def _sequence_features(
    data: pd.DataFrame,
    emission: int,
    side: str,
) -> dict[str, float]:
    sign = base._sign(side)
    output: dict[str, float] = {}
    # Six non-overlapping five-minute blocks preserve order without a large neural model.
    for block in range(6):
        end = emission - 5 * block + 1
        start = max(0, end - 5)
        frame = data.iloc[start:end]
        prefix = f"sequence_block_{block}"
        if len(frame) < 2:
            output.update(
                {
                    f"{prefix}_return_bps_signed": 0.0,
                    f"{prefix}_delta_share_signed": 0.0,
                    f"{prefix}_activity_ratio": 0.0,
                    f"{prefix}_path_efficiency": 0.0,
                    f"{prefix}_impact_efficiency_signed": 0.0,
                }
            )
            continue
        first, last = float(frame.close.iloc[0]), float(frame.close.iloc[-1])
        net = last - first
        path = float(frame.close.diff().abs().sum())
        signed_quote = 2.0 * frame.taker_buy_quote_volume - frame.quote_volume
        delta = base._finite(signed_quote.sum() / max(frame.quote_volume.sum(), base.EPS), 0.0)
        return_bps = net / max(abs(last), base.EPS) * 10_000.0
        output.update(
            {
                f"{prefix}_return_bps_signed": sign * return_bps,
                f"{prefix}_delta_share_signed": sign * delta,
                f"{prefix}_activity_ratio": base._finite(frame.activity_ratio.median(), 0.0),
                f"{prefix}_path_efficiency": abs(net) / max(path, base.EPS),
                f"{prefix}_impact_efficiency_signed": sign * return_bps / max(abs(delta) + 0.02, 0.02),
            }
        )
    return output


def _source_accumulation_features(
    data: pd.DataFrame,
    source: hl.LiquidityLevel,
    interaction: int,
) -> dict[str, float]:
    frame = data.iloc[max(0, interaction - 240):interaction]
    if frame.empty:
        return {}
    atr = base._atr_price(data, interaction)
    tolerance = max(source.upper - source.lower, 0.2 * atr)
    near = frame[(frame.low <= source.price + tolerance) & (frame.high >= source.price - tolerance)]
    if near.empty:
        return {
            "source_accumulation_minutes_near": 0.0,
            "source_accumulation_quote_share": 0.0,
            "source_accumulation_delta_toward": 0.0,
            "source_accumulation_distinct_visits": 0.0,
        }
    toward = 1.0 if source.side == "HIGH" else -1.0
    signed = 2.0 * near.taker_buy_quote_volume - near.quote_volume
    membership = ((frame.low <= source.price + tolerance) & (frame.high >= source.price - tolerance)).astype(int)
    visits = int(((membership == 1) & (membership.shift(1, fill_value=0) == 0)).sum())
    return {
        "source_accumulation_minutes_near": float(len(near)),
        "source_accumulation_quote_share": base._finite(near.quote_volume.sum() / max(frame.quote_volume.sum(), base.EPS), 0.0),
        "source_accumulation_delta_toward": toward * base._finite(signed.sum() / max(near.quote_volume.sum(), base.EPS), 0.0),
        "source_accumulation_distinct_visits": float(visits),
    }


def _volume_route_features(data: pd.DataFrame, index: int, entry: float, target: float) -> dict[str, float]:
    try:
        values = hl._volume_profile_features(data, index, entry, target)
        return {f"route_{key}": value for key, value in values.items()}
    except Exception:
        frame = data.iloc[max(0, index - 1440):index]
        if frame.empty:
            return {}
        lower, upper = sorted((entry, target))
        in_route = frame[(frame.close >= lower) & (frame.close <= upper)]
        return {
            "route_history_fraction_inside": float(len(in_route) / max(len(frame), 1)),
            "route_quote_fraction_inside": base._finite(in_route.quote_volume.sum() / max(frame.quote_volume.sum(), base.EPS), 0.0),
        }


def _make_action_v2(symbol, data, levels, source, setup, response, event_meta, tick):
    made = _ORIGINAL_MAKE_ACTION(symbol, data, levels, source, setup, response, event_meta, tick)
    if made is None:
        return None
    action, destination = made
    # Reconstruct with the first meaningful obstacle instead of a weak lone 5m print.
    target_level = _meaningful_route_level(levels, action.emission_index, action.entry, action.side)
    if target_level is None:
        return None
    target = float(target_level.price)
    economics = base._economics(
        side=action.side,
        entry=action.entry,
        stop=action.stop,
        target=target,
        tick_size=tick,
        entry_style="MARKET",
    )
    if not economics or economics["gross_rr"] < 1.0 or economics["target_net_r"] <= 0.0:
        return None
    features = dict(action.feature_values)
    features.update(economics)
    features.update(_anchored_vwap_features(data, setup.interaction_index, action.emission_index, action.side))
    features.update(_sequence_features(data, action.emission_index, action.side))
    features.update(_source_accumulation_features(data, source, setup.interaction_index))
    features.update(_volume_route_features(data, action.emission_index, action.entry, target))
    features["target_scale_minutes"] = float(target_level.timeframe_minutes)
    features["target_strength_ratio"] = base._finite(target_level.strength_ratio, 0.0)
    features["target_defense_count"] = float(target_level.defense_count)
    features["target_age_minutes"] = float(action.emission_index - target_level.observed_index_1m)
    features["target_is_weak_single_5m"] = 0.0
    features["diagnostic_target_level_id"] = target_level.level_id
    revised = replace(
        action,
        target=target,
        objective_id=target_level.level_id,
        objective_kind=target_level.source_kind,
        objective_timeframe_minutes=target_level.timeframe_minutes,
        objective_strength_ratio=target_level.strength_ratio,
        feature_values=features,
    )
    destination = base._destination_label(data, levels, revised.emission_index, str(features["state_id"]))
    return revised, destination


base._nearest_route_level = _meaningful_route_level
base._make_action = _make_action_v2
run_research = base.run_research
label_fixed_horizon = base.label_fixed_horizon

__all__ = ["POLICY_V2", "run_research", "label_fixed_horizon"]
