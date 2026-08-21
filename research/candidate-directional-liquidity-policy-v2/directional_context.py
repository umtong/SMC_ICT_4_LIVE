"""Causal multi-scale direction and liquidity-objective measurements.

The module deliberately contains no symbol identity, fitted model, future label or
period-specific parameter.  It translates the same price/volume observations into
one directional state for every liquid market in the research universe.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pandas as pd

EPS = 1e-12
_HORIZONS = (15, 60, 240, 720)
_WEIGHTS = (0.18, 0.27, 0.33, 0.22)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _direction(side: str) -> float:
    if side == "LONG":
        return 1.0
    if side == "SHORT":
        return -1.0
    raise ValueError(f"unsupported side: {side}")


@dataclass(frozen=True, slots=True)
class DirectionalSnapshot:
    trend_alignment: float
    trend_consensus: float
    path_efficiency: float
    signed_flow_share: float
    activity_ratio: float
    effort_result: float
    short_move_atr: float
    medium_move_atr: float
    long_move_atr: float
    range_location_signed: float
    common_factor_alignment: float
    relative_strength_alignment: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ObjectiveSnapshot:
    objective_alignment: float
    route_room_atr: float
    route_strength: float
    opposite_pull: float
    two_sided_liquidity: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def _series(data: pd.DataFrame, name: str) -> pd.Series:
    if name not in data:
        return pd.Series(0.0, index=data.index, dtype=float)
    return pd.to_numeric(data[name], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _signed_flow(data: pd.DataFrame, start: int, end: int) -> tuple[float, float]:
    part = data.iloc[start : end + 1]
    quote = _series(part, "quote_volume")
    if not float(quote.sum()) and "volume" in part:
        quote = _series(part, "volume") * _series(part, "close")
    if "signed_quote_flow" in part:
        signed = _series(part, "signed_quote_flow")
    elif "delta_share" in part:
        signed = _series(part, "delta_share") * quote
    else:
        # Causal OHLCV proxy used only when taker-side data is absent.  The body is
        # scaled by the full candle range so a tiny body cannot masquerade as flow.
        body = _series(part, "close") - _series(part, "open")
        span = (_series(part, "high") - _series(part, "low")).abs().clip(lower=EPS)
        signed = quote * (body / span).clip(-1.0, 1.0)
    share = float(signed.sum()) / max(float(quote.sum()), EPS)
    activity = float(quote.mean()) if len(quote) else 0.0
    return float(np.clip(share, -1.0, 1.0)), activity


def build_directional_snapshot(
    data: pd.DataFrame,
    decision_index: int,
    side: str,
    atr_price: float,
) -> DirectionalSnapshot:
    """Measure the state visible at ``decision_index`` in the proposed direction."""
    if data.empty:
        raise ValueError("data is empty")
    if decision_index < 0 or decision_index >= len(data):
        raise IndexError("decision_index outside data")
    direction = _direction(side)
    atr_price = max(abs(float(atr_price)), EPS)
    close = _series(data, "close").to_numpy(float)

    aligned_moves: list[float] = []
    efficiencies: list[float] = []
    signs: list[float] = []
    for horizon in _HORIZONS:
        start = max(0, decision_index - horizon)
        path = close[start : decision_index + 1]
        if len(path) < 2:
            move_atr = 0.0
            efficiency = 0.0
        else:
            raw_move = float(path[-1] - path[0])
            move_atr = direction * raw_move / atr_price
            travel = float(np.abs(np.diff(path)).sum())
            efficiency = abs(raw_move) / max(travel, EPS)
        aligned_moves.append(move_atr)
        efficiencies.append(float(np.clip(efficiency, 0.0, 1.0)))
        signs.append(float(np.sign(move_atr)))

    trend_components = [
        weight * math.tanh(move) * (0.35 + 0.65 * efficiency)
        for weight, move, efficiency in zip(_WEIGHTS, aligned_moves, efficiencies)
    ]
    trend_alignment = float(sum(trend_components))
    trend_consensus = float(sum(weight * sign for weight, sign in zip(_WEIGHTS, signs)))
    path_efficiency = float(sum(weight * value for weight, value in zip(_WEIGHTS, efficiencies)))

    flow_start = max(0, decision_index - 60)
    flow_share_raw, current_activity = _signed_flow(data, flow_start, decision_index)
    prior_start = max(0, flow_start - 180)
    _, prior_activity = _signed_flow(data, prior_start, max(prior_start, flow_start - 1))
    activity_baseline = prior_activity if prior_activity > EPS else current_activity
    activity_ratio = current_activity / max(activity_baseline, EPS)
    signed_flow_share = direction * flow_share_raw
    medium_move = aligned_moves[1]
    effort_result = medium_move / max(0.20, activity_ratio * (abs(signed_flow_share) + 0.12))

    range_start = max(0, decision_index - 720)
    prior = data.iloc[range_start : decision_index + 1]
    low = _finite(pd.to_numeric(prior.low, errors="coerce").min(), close[decision_index])
    high = _finite(pd.to_numeric(prior.high, errors="coerce").max(), close[decision_index])
    location = (close[decision_index] - low) / max(high - low, EPS)
    # +1 means price already occupies the directionally favorable half of the
    # public range; -1 means it remains on the wrong side of that range.
    range_location_signed = direction * (2.0 * location - 1.0)

    row = data.iloc[decision_index]
    common = _finite(row.get("common_return_5m", row.get("factor_return", 0.0)))
    relative = _finite(
        row.get(
            "residual_return_5m",
            row.get("relative_return_5m", row.get("residual_return", 0.0)),
        )
    )
    return DirectionalSnapshot(
        trend_alignment=trend_alignment,
        trend_consensus=trend_consensus,
        path_efficiency=path_efficiency,
        signed_flow_share=float(signed_flow_share),
        activity_ratio=float(max(activity_ratio, 0.0)),
        effort_result=float(np.clip(effort_result, -8.0, 8.0)),
        short_move_atr=float(aligned_moves[0]),
        medium_move_atr=float(aligned_moves[1]),
        long_move_atr=float(0.55 * aligned_moves[2] + 0.45 * aligned_moves[3]),
        range_location_signed=float(np.clip(range_location_signed, -1.0, 1.0)),
        common_factor_alignment=float(direction * common),
        relative_strength_alignment=float(direction * relative),
    )


def _destination_pull(destination: Any | None, price: float, atr_price: float) -> tuple[float, float, float]:
    if destination is None:
        return 0.0, 0.0, 0.0
    distance = abs(_finite(getattr(destination, "price", price)) - price) / max(atr_price, EPS)
    strength = max(_finite(getattr(destination, "strength", 0.0)), 0.0)
    scale = max(_finite(getattr(destination, "scale", 1.0)), 1.0)
    pull = math.log1p(strength) * (1.0 + 0.10 * math.log1p(scale)) / (0.50 + distance)
    return float(pull), float(distance), float(strength)


def build_objective_snapshot(
    *,
    side: str,
    price: float,
    atr_price: float,
    long_destination: Any | None,
    short_destination: Any | None,
) -> ObjectiveSnapshot:
    """Compare the nearest still-live liquidity on both sides of price."""
    long_pull, long_room, long_strength = _destination_pull(long_destination, price, atr_price)
    short_pull, short_room, short_strength = _destination_pull(short_destination, price, atr_price)
    direction = _direction(side)
    signed_pull = direction * (long_pull - short_pull)
    selected_room = long_room if side == "LONG" else short_room
    selected_strength = long_strength if side == "LONG" else short_strength
    opposite_pull = short_pull if side == "LONG" else long_pull
    two_sided = min(long_pull, short_pull)
    return ObjectiveSnapshot(
        objective_alignment=float(math.tanh(signed_pull)),
        route_room_atr=float(selected_room),
        route_strength=float(selected_strength),
        opposite_pull=float(opposite_pull),
        two_sided_liquidity=float(two_sided),
    )


def mechanism_coherence(
    family: str,
    evidence: dict[str, Any],
    direction: DirectionalSnapshot,
    objective: ObjectiveSnapshot,
    *,
    source_strength: float,
    source_confluence: int,
) -> float:
    """Translate a completed event into one continuous, causal coherence value.

    Detection supplies the categorical event.  This value does not create a new
    threshold lattice; it only asks whether the event, broader direction and live
    objective tell the same story.  Zero is the natural boundary between agreement
    and contradiction.
    """
    control_move = _finite(evidence.get("control_move_atr"))
    control_efficiency = _finite(evidence.get("control_path_efficiency"))
    control_flow = _finite(evidence.get("control_flow_share_signed"))
    common_breadth = _finite(evidence.get("common_breadth_signed"))
    relative = _finite(evidence.get("relative_return_signed"))
    source_term = math.log1p(max(source_strength, 0.0)) + 0.20 * math.log1p(max(source_confluence, 1))

    shared = (
        0.62 * math.tanh(control_move)
        + 0.58 * control_efficiency
        + 0.28 * control_flow
        + 0.22 * direction.signed_flow_share
        + 0.25 * objective.objective_alignment
        + 0.08 * math.tanh(common_breadth)
        + 0.08 * math.tanh(relative)
        + 0.07 * source_term
    )
    if family == "FAILED_AUCTION_REVERSAL":
        # A completed sweep/reclaim may legitimately reverse the prior trend.  The
        # event receives more weight and only a strongly opposing long-horizon state
        # counts against it.
        context = 0.20 * direction.trend_alignment + 0.10 * direction.trend_consensus
        exhaustion = 0.18 * max(-direction.long_move_atr, 0.0)
        return float(shared + context + exhaustion)
    if family == "ACCEPTED_AUCTION_CONTINUATION":
        return float(
            shared
            + 0.55 * direction.trend_alignment
            + 0.24 * direction.trend_consensus
            + 0.12 * direction.range_location_signed
        )
    if family == "INITIATIVE_MITIGATION_CONTINUATION":
        return float(
            shared
            + 0.48 * direction.trend_alignment
            + 0.20 * direction.trend_consensus
            + 0.10 * math.tanh(direction.relative_strength_alignment)
        )
    return float("-inf")


__all__ = [
    "DirectionalSnapshot",
    "ObjectiveSnapshot",
    "build_directional_snapshot",
    "build_objective_snapshot",
    "mechanism_coherence",
]
