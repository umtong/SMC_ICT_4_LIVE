"""Shared causal objects and measurements for the liquidity world model."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any

import numpy as np
import pandas as pd

import departure_first_return_harvest_fixed as fixed
import event_time_auction_harvest as dc

core = fixed.core
EPS = 1e-12
EVENT_SCALE = 0.45
MEDIUM_SCALE = 1.50
LARGE_SCALE = 3.00
SOURCE_DC_SCALES = (MEDIUM_SCALE, LARGE_SCALE)
MAKER_FEE = 0.0002
TAKER_FEE = 0.0005
STOP_SLIPPAGE_TICKS = 2
LIMIT_TRADE_THROUGH_TICKS = 1
RISK_FRACTION = 0.03


@dataclass(frozen=True, slots=True)
class SourceEvent:
    source_id: str
    side: str
    lower: float
    upper: float
    price: float
    observed_index: int
    interaction_index: int
    scale: float
    strength: float
    kind: str
    confluence_count: int = 1


@dataclass(frozen=True, slots=True)
class Destination:
    destination_id: str
    side: str
    lower: float
    upper: float
    price: float
    observed_index: int
    scale: float
    strength: float
    kind: str


@dataclass(frozen=True, slots=True)
class EpisodeSignal:
    family: str
    side: str
    interaction_index: int
    decision_index: int
    event_extreme: float
    pullback_extreme: float
    source: SourceEvent | None
    context_scale: float
    impulse_start_index: int
    impulse_end_index: int
    evidence: dict[str, float]


@dataclass(frozen=True, slots=True)
class OrderLabel:
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


def stable(*values: Any) -> str:
    return hashlib.sha1("|".join(map(str, values)).encode()).hexdigest()[:16]


def sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def value(row: pd.Series, *names: str, default: float = 0.0) -> float:
    for name in names:
        if name not in row:
            continue
        number = finite(row.get(name), float("nan"))
        if math.isfinite(number):
            return number
    return default


def atr_array(data: pd.DataFrame) -> np.ndarray:
    """Causal prior-only ATR median without backward-filling from future bars."""
    previous = data.close.shift(1)
    true_range = pd.concat(
        [
            data.high - data.low,
            (data.high - previous).abs(),
            (data.low - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    rolling = true_range.rolling(90, min_periods=30).median().shift(1)
    expanding = true_range.expanding(min_periods=1).median().shift(1)
    causal = rolling.fillna(expanding)
    if len(causal):
        causal.iloc[0] = max(float(true_range.iloc[0]), EPS)
    return causal.ffill().fillna(EPS).to_numpy(float)


def control_features(
    data: pd.DataFrame,
    start: int,
    end: int,
    side: str,
    atr_price: float,
) -> dict[str, float]:
    direction = sign(side)
    segment = data.iloc[start : end + 1]
    close = segment.close.to_numpy(float)
    move = direction * (close[-1] - close[0]) if len(close) else 0.0
    travel = float(np.abs(np.diff(close)).sum()) if len(close) > 1 else 0.0
    quote = pd.to_numeric(
        segment.get("quote_volume", pd.Series(0.0, index=segment.index)),
        errors="coerce",
    ).fillna(0.0)
    if "signed_quote_flow" in segment:
        signed = pd.to_numeric(segment.signed_quote_flow, errors="coerce").fillna(0.0)
    elif "delta_share" in segment:
        signed = pd.to_numeric(segment.delta_share, errors="coerce").fillna(0.0) * quote
    else:
        signed = pd.Series(0.0, index=segment.index)
    prior = (
        pd.to_numeric(data.quote_volume.iloc[max(0, start - 60) : start], errors="coerce").median()
        if "quote_volume" in data
        else float("nan")
    )
    activity = float(quote.mean()) / max(finite(prior, float(quote.mean())), EPS)
    flow = direction * float(signed.sum()) / max(float(quote.sum()), EPS)
    row = data.iloc[end]
    efficiency = move / max(travel, EPS)
    return {
        "control_move_atr": move / max(atr_price, EPS),
        "control_path_efficiency": efficiency,
        "control_flow_share_signed": flow,
        "control_activity_ratio": activity,
        "control_effort_result": move / max(atr_price, EPS) / max(0.08, activity * (abs(flow) + 0.08)),
        "common_factor_signed": direction * value(row, "common_return_5m", "factor_return"),
        "common_breadth_signed": direction * value(row, "common_breadth", "breadth"),
        "relative_return_signed": direction * value(row, "relative_return_5m", "residual_return"),
        "oi_log_change": value(row, "metric_oi_log_change_1", "oi_log_change_1"),
        "basis_change_signed_bps": direction * value(row, "basis_change_3m_bps", "basis_change_bps"),
    }


def prior_wick_noise(data: pd.DataFrame, decision: int, side: str, tick: float) -> float:
    frame = data.iloc[max(0, decision - 120) : decision]
    if frame.empty:
        return 2.0 * tick
    if side == "LONG":
        wick = np.minimum(frame.open.to_numpy(float), frame.close.to_numpy(float)) - frame.low.to_numpy(float)
    else:
        wick = frame.high.to_numpy(float) - np.maximum(frame.open.to_numpy(float), frame.close.to_numpy(float))
    wick = wick[np.isfinite(wick) & (wick >= 0.0)]
    return max(2.0 * tick, float(np.median(wick)) if len(wick) else 0.0)
