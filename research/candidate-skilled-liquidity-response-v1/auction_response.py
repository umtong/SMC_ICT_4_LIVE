"""Causal price-volume impulse response around a public liquidity boundary.

A boundary interaction is treated as a shock-response experiment rather than a
collection of unrelated indicators.  The attempted break is the input; signed
flow, activity and range describe its effort; overshoot, occupancy, reclaim and
settling describe the market response.  Every baseline is computed from bars
strictly before the interaction so the same measurements can be used live.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

EPS = 1e-12


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def trade_sign(side: str) -> float:
    if side == "LONG":
        return 1.0
    if side == "SHORT":
        return -1.0
    raise ValueError(f"unsupported side: {side}")


def outward_sign(source_side: str) -> float:
    if source_side == "HIGH":
        return 1.0
    if source_side == "LOW":
        return -1.0
    raise ValueError(f"unsupported source side: {source_side}")


def _numeric(data: pd.DataFrame, name: str) -> pd.Series:
    if name not in data:
        return pd.Series(0.0, index=data.index, dtype=float)
    return (
        pd.to_numeric(data[name], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
        .astype(float)
    )


def quote_activity(data: pd.DataFrame) -> pd.Series:
    quote = _numeric(data, "quote_volume")
    if float(quote.abs().sum()) <= EPS and "volume" in data:
        quote = _numeric(data, "volume") * _numeric(data, "close")
    return quote.clip(lower=0.0)


def signed_flow(data: pd.DataFrame) -> pd.Series:
    quote = quote_activity(data)
    if "signed_quote_flow" in data:
        return _numeric(data, "signed_quote_flow")
    if "delta_share" in data:
        return _numeric(data, "delta_share").clip(-1.0, 1.0) * quote
    # Sparse-safe OHLCV proxy.  A small body inside a large wick carries little
    # directional flow even when its printed volume is large.
    body = _numeric(data, "close") - _numeric(data, "open")
    span = (_numeric(data, "high") - _numeric(data, "low")).abs().clip(lower=EPS)
    return quote * (body / span).clip(-1.0, 1.0)


def _prior_median(values: np.ndarray, interaction: int, lookback: int = 720) -> float:
    start = max(0, int(interaction) - int(lookback))
    prior = np.asarray(values[start:int(interaction)], dtype=float)
    prior = prior[np.isfinite(prior) & (prior >= 0.0)]
    if not len(prior):
        return EPS
    positive = prior[prior > EPS]
    if len(positive):
        return max(float(np.median(positive)), EPS)
    return EPS


def _log_ratio(value: float, baseline: float) -> float:
    return float(np.clip(math.log(max(value, EPS) / max(baseline, EPS)), -3.0, 3.0))


def _flow_share(data: pd.DataFrame, start: int, end: int) -> float:
    start = max(0, int(start))
    end = min(len(data) - 1, int(end))
    if end < start:
        return 0.0
    quote = quote_activity(data).iloc[start : end + 1]
    flow = signed_flow(data).iloc[start : end + 1]
    return float(np.clip(float(flow.sum()) / max(float(quote.sum()), EPS), -1.0, 1.0))


def _window_energy(data: pd.DataFrame, interaction: int, start: int, end: int) -> dict[str, float]:
    start = max(0, int(start))
    end = min(len(data) - 1, int(end))
    if end < start:
        return {
            "activity_surprise": 0.0,
            "range_surprise": 0.0,
            "body_surprise": 0.0,
            "shock_energy": 0.0,
        }
    activity = quote_activity(data).to_numpy(float)
    ranges = (_numeric(data, "high") - _numeric(data, "low")).abs().to_numpy(float)
    bodies = (_numeric(data, "close") - _numeric(data, "open")).abs().to_numpy(float)
    part = slice(start, end + 1)
    activity_ratio = _log_ratio(float(np.mean(activity[part])), _prior_median(activity, interaction))
    range_ratio = _log_ratio(float(np.mean(ranges[part])), _prior_median(ranges, interaction))
    body_ratio = _log_ratio(float(np.mean(bodies[part])), _prior_median(bodies, interaction))
    # Zero means the event expended ordinary effort.  Positive values identify a
    # genuinely unusual auction shock without asset-specific absolute thresholds.
    shock = (activity_ratio + range_ratio + body_ratio) / 3.0
    return {
        "activity_surprise": activity_ratio,
        "range_surprise": range_ratio,
        "body_surprise": body_ratio,
        "shock_energy": float(shock),
    }


def _path_efficiency(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    if len(values) < 2:
        return 0.0
    travel = float(np.abs(np.diff(values)).sum())
    return float(np.clip(abs(float(values[-1] - values[0])) / max(travel, EPS), 0.0, 1.0))


def boundary_response_path(
    data: pd.DataFrame,
    *,
    interaction: int,
    decision: int,
    source_side: str,
    boundary: float,
    atr_price: float,
) -> dict[str, float]:
    """Describe the observed step response in source-outward coordinates."""
    interaction = max(0, int(interaction))
    decision = min(len(data) - 1, int(decision))
    outward = outward_sign(source_side)
    atr_price = max(abs(float(atr_price)), EPS)
    close = _numeric(data, "close").iloc[interaction : decision + 1].to_numpy(float)
    if not len(close):
        close = np.asarray([float(boundary)])
    distance = outward * (close - float(boundary)) / atr_price
    outside = distance > 0.0
    states = np.sign(distance)
    crossings = int(np.sum(states[1:] * states[:-1] < 0.0)) if len(states) > 1 else 0
    return {
        "terminal_outward_atr": float(distance[-1]),
        "outside_close_fraction": float(outside.mean()),
        "boundary_crossings": float(crossings),
        "response_path_efficiency": _path_efficiency(distance),
    }


def failed_auction_evidence(
    data: pd.DataFrame,
    *,
    interaction: int,
    extreme_index: int,
    decision: int,
    source_side: str,
    boundary: float,
    event_extreme: float,
    atr_price: float,
) -> dict[str, float]:
    """Measure an overshoot which settled back through the public boundary."""
    outward = outward_sign(source_side)
    side = "SHORT" if source_side == "HIGH" else "LONG"
    atr_price = max(abs(float(atr_price)), EPS)
    interaction = int(interaction)
    extreme_index = min(max(int(extreme_index), interaction), int(decision))
    decision = int(decision)
    path = boundary_response_path(
        data,
        interaction=interaction,
        decision=decision,
        source_side=source_side,
        boundary=boundary,
        atr_price=atr_price,
    )
    energy = _window_energy(data, interaction, interaction, extreme_index)
    penetration = max(outward * (float(event_extreme) - float(boundary)) / atr_price, 0.0)
    terminal = float(path["terminal_outward_atr"])
    inside_settle = max(-terminal, 0.0)
    recovery_ratio = (penetration - terminal) / max(penetration, 0.05)
    outbound_flow = outward * _flow_share(data, interaction, extreme_index)
    reaction_flow = trade_sign(side) * _flow_share(data, extreme_index, decision)
    recovery_close = _numeric(data, "close").iloc[extreme_index : decision + 1].to_numpy(float)
    recovery_distance = trade_sign(side) * (recovery_close - recovery_close[0]) / atr_price
    recovery_efficiency = _path_efficiency(recovery_distance)
    trapped_effort = max(outbound_flow, 0.0) * max(recovery_ratio - 1.0, 0.0)
    score = (
        energy["shock_energy"]
        + 0.70 * inside_settle
        + 0.35 * (0.50 - float(path["outside_close_fraction"]))
        + 0.35 * recovery_efficiency
        + 0.30 * reaction_flow
        + 0.25 * trapped_effort
        - 0.10 * max(float(path["boundary_crossings"]) - 2.0, 0.0)
    )
    return {
        **energy,
        **path,
        "auction_response_kind": "OVERSHOOT_SETTLED_INSIDE",
        "penetration_atr": float(penetration),
        "inside_settle_atr": float(inside_settle),
        "recovery_ratio": float(recovery_ratio),
        "outbound_flow_share": float(outbound_flow),
        "reaction_flow_share": float(reaction_flow),
        "recovery_path_efficiency": float(recovery_efficiency),
        "trapped_effort": float(trapped_effort),
        "auction_response_score": float(score),
    }


def accepted_auction_evidence(
    data: pd.DataFrame,
    *,
    interaction: int,
    impulse_extreme_index: int,
    decision: int,
    source_side: str,
    boundary: float,
    impulse_extreme: float,
    pullback_extreme: float,
    atr_price: float,
) -> dict[str, float]:
    """Measure a break which settled outside and survived its first pullback."""
    outward = outward_sign(source_side)
    atr_price = max(abs(float(atr_price)), EPS)
    interaction = int(interaction)
    impulse_extreme_index = min(max(int(impulse_extreme_index), interaction), int(decision))
    decision = int(decision)
    path = boundary_response_path(
        data,
        interaction=interaction,
        decision=decision,
        source_side=source_side,
        boundary=boundary,
        atr_price=atr_price,
    )
    energy = _window_energy(data, interaction, interaction, impulse_extreme_index)
    penetration = max(outward * (float(impulse_extreme) - float(boundary)) / atr_price, 0.0)
    hold_margin = outward * (float(pullback_extreme) - float(boundary)) / atr_price
    continuation_flow = outward * _flow_share(data, interaction, decision)
    terminal = float(path["terminal_outward_atr"])
    score = (
        energy["shock_energy"]
        + 0.65 * terminal
        + 0.50 * (float(path["outside_close_fraction"]) - 0.50)
        + 0.35 * hold_margin
        + 0.30 * continuation_flow
        + 0.20 * float(path["response_path_efficiency"])
        - 0.10 * max(float(path["boundary_crossings"]) - 1.0, 0.0)
    )
    return {
        **energy,
        **path,
        "auction_response_kind": "BREAK_SETTLED_OUTSIDE",
        "penetration_atr": float(penetration),
        "pullback_hold_margin_atr": float(hold_margin),
        "continuation_flow_share": float(continuation_flow),
        "auction_response_score": float(score),
    }


def initiative_evidence(
    data: pd.DataFrame,
    *,
    impulse_start: int,
    decision: int,
    side: str,
    atr_price: float,
) -> dict[str, float]:
    """Measure a new initiative leg and its first mitigation response."""
    direction = trade_sign(side)
    impulse_start = max(0, int(impulse_start))
    decision = min(len(data) - 1, int(decision))
    atr_price = max(abs(float(atr_price)), EPS)
    energy = _window_energy(data, impulse_start, impulse_start, decision)
    close = _numeric(data, "close").iloc[impulse_start : decision + 1].to_numpy(float)
    if len(close) < 2:
        move_atr = 0.0
        efficiency = 0.0
    else:
        move_atr = direction * float(close[-1] - close[0]) / atr_price
        efficiency = _path_efficiency(direction * (close - close[0]) / atr_price)
    flow = direction * _flow_share(data, impulse_start, decision)
    score = (
        energy["shock_energy"]
        + 0.60 * move_atr
        + 0.35 * efficiency
        + 0.30 * flow
    )
    return {
        **energy,
        "auction_response_kind": "INITIATIVE_DISPLACEMENT_MITIGATION",
        "initiative_move_atr": float(move_atr),
        "initiative_path_efficiency": float(efficiency),
        "initiative_flow_share": float(flow),
        "auction_response_score": float(score),
    }


__all__ = [
    "accepted_auction_evidence",
    "boundary_response_path",
    "failed_auction_evidence",
    "finite",
    "initiative_evidence",
    "outward_sign",
    "quote_activity",
    "signed_flow",
    "trade_sign",
]
