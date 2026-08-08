"""Cost-aware large-auction-event router for Candidate 37 v2.

The module is pure decision logic.  It consumes completed one-minute rows whose
feature timestamps are no later than the bar close, and it never reads a future
bar.  Score is used only for global arbitration, never for risk sizing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping

import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


@dataclass(frozen=True)
class LargeEventConfig:
    minimum_objective_bps: float = 65.0
    maximum_objective_bps: float = 180.0
    minimum_price_risk_bps: float = 10.0
    maximum_price_risk_bps: float = 90.0
    round_trip_cost_bps: float = 21.0
    breakout_range_minutes: int = 60
    breakout_max_range_bps: float = 120.0
    breakout_max_range_atr: float = 12.0
    breakout_min_impulse_atr: float = 1.10
    breakout_min_impulse_bps: float = 8.0
    breakout_min_flow_60s: float = 0.25
    breakout_min_flow_15s: float = -0.05
    breakout_min_efficiency: float = 0.25
    breakout_min_notional_burst: float = 1.50
    breakout_min_oi_expansion_15m: float = 0.00010
    breakout_max_against_premium_15m: float = 0.00020
    cascade_min_move_bps: float = 50.0
    cascade_min_move_atr: float = 5.0
    cascade_max_oi_change_15m: float = -0.0020
    cascade_min_flow_60s: float = 0.25
    cascade_min_notional_burst: float = 2.0
    cascade_min_peer_breadth: int = 2
    continuation_shock_age: int = 2
    continuation_min_retention: float = 0.60
    continuation_extension_fraction: float = 0.75
    reversal_min_reclaim: float = 0.35
    reversal_min_opposite_flow: float = 0.15
    arbitration_gap: float = 0.20


@dataclass(frozen=True)
class EventRoute:
    symbol: str
    state: str
    side: int
    score: float
    signal_time_ns: int
    episode_time_ns: int
    stop_reference: float
    objective_reference: float
    reason: str
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return (
            self.side in (-1, 1)
            and self.state in {
                "RISK_BUILD_BREAKOUT",
                "DELEVERAGING_CONTINUATION",
                "FAILED_CASCADE_REVERSAL",
            }
            and math.isfinite(self.stop_reference)
            and math.isfinite(self.objective_reference)
        )


def _finite(row: pd.Series, *names: str) -> bool:
    return all(math.isfinite(float(row[name])) for name in names)


def _bps(side: int, start: float, end: float) -> float:
    return side * (end - start) / start * 10_000.0


def _valid_geometry(
    *, side: int, entry: float, stop: float, objective: float,
    config: LargeEventConfig,
) -> tuple[bool, float, float]:
    risk = _bps(side, entry, stop) * -1.0
    reward = _bps(side, entry, objective)
    valid = (
        side in (-1, 1)
        and config.minimum_price_risk_bps <= risk <= config.maximum_price_risk_bps
        and config.minimum_objective_bps <= reward <= config.maximum_objective_bps
    )
    return valid, risk, reward


def _peer_breadth(
    frames: Mapping[str, pd.DataFrame], index: int, side: int, exclude: str,
) -> int:
    breadth = 0
    for symbol in SYMBOLS:
        if symbol == exclude:
            continue
        row = frames[symbol].iloc[index]
        if math.isfinite(float(row["ret5_bps"])) and side * float(row["ret5_bps"]) > 0.5 * float(row["atr_bps"]):
            breadth += 1
    return breadth


def _risk_build_breakout(
    *, symbol: str, frames: Mapping[str, pd.DataFrame], index: int,
    config: LargeEventConfig,
) -> EventRoute | None:
    row = frames[symbol].iloc[index]
    required = (
        "close", "prior_high60", "prior_low60", "prior_range60_bps",
        "atr_bps", "ret1_bps", "flow_15s", "flow_60s", "efficiency_60s",
        "notional_burst", "oi_change_15m", "premium_change_15m",
    )
    if not _finite(row, *required) or not bool(row.get("feature_ready", True)):
        return None
    if float(row["close"]) > float(row["prior_high60"]):
        side = 1
    elif float(row["close"]) < float(row["prior_low60"]):
        side = -1
    else:
        return None
    atr = float(row["atr_bps"])
    prior_range = float(row["prior_range60_bps"])
    if prior_range > min(config.breakout_max_range_bps, config.breakout_max_range_atr * atr):
        return None
    impulse = side * float(row["ret1_bps"])
    if impulse < max(config.breakout_min_impulse_bps, config.breakout_min_impulse_atr * atr):
        return None
    if side * float(row["flow_60s"]) < config.breakout_min_flow_60s:
        return None
    if side * float(row["flow_15s"]) < config.breakout_min_flow_15s:
        return None
    if float(row["efficiency_60s"]) < config.breakout_min_efficiency:
        return None
    if float(row["notional_burst"]) < config.breakout_min_notional_burst:
        return None
    # OI expansion is positive for both long and short new-risk breakouts.
    if float(row["oi_change_15m"]) < config.breakout_min_oi_expansion_15m:
        return None
    if side * float(row["premium_change_15m"]) < -config.breakout_max_against_premium_15m:
        return None
    breadth = _peer_breadth(frames, index, side, symbol)
    if breadth < 1:
        return None
    entry = float(row["close"])
    width = float(row["prior_high60"]) - float(row["prior_low60"])
    boundary = float(row["prior_high60"]) if side > 0 else float(row["prior_low60"])
    stop = boundary - side * 0.35 * width
    objective = boundary + side * width
    valid, risk, reward = _valid_geometry(
        side=side, entry=entry, stop=stop, objective=objective, config=config,
    )
    if not valid:
        return None
    score = (
        1.0 + 0.45 * breadth + 0.45 * min(2.0, impulse / max(atr, 1e-9))
        + 0.45 * min(1.5, side * float(row["flow_60s"]))
        + 0.35 * min(2.0, float(row["notional_burst"]) / config.breakout_min_notional_burst)
        + 0.30 * min(2.0, float(row["oi_change_15m"]) / config.breakout_min_oi_expansion_15m)
        + 0.25 * min(2.0, reward / config.minimum_objective_bps)
    )
    return EventRoute(
        symbol=symbol, state="RISK_BUILD_BREAKOUT", side=side, score=score,
        signal_time_ns=int(row["ts"]), episode_time_ns=int(row["ts"]),
        stop_reference=stop, objective_reference=objective,
        reason="COMPRESSED_RANGE_BROKE_WITH_NEW_OI_AND_ALIGNED_AGGRESSOR_FLOW",
        diagnostics={
            "peer_breadth": breadth, "impulse_bps": impulse,
            "prior_range_bps": prior_range, "price_risk_bps": risk,
            "objective_bps": reward, "flow_60s_signed": side * float(row["flow_60s"]),
            "oi_change_15m": float(row["oi_change_15m"]),
        },
    )


def _cascade_shock(
    *, symbol: str, frames: Mapping[str, pd.DataFrame], shock_index: int,
    config: LargeEventConfig,
) -> tuple[int, float, float, float, int] | None:
    frame = frames[symbol]
    if shock_index < 6:
        return None
    shock = frame.iloc[shock_index]
    pre = float(frame.iloc[shock_index - 5]["close"])
    move = (float(shock["close"]) / pre - 1.0) * 10_000.0
    side = 1 if move > 0.0 else -1 if move < 0.0 else 0
    if not side or not _finite(
        shock, "atr_bps", "flow_60s", "notional_burst", "oi_change_15m",
    ):
        return None
    if abs(move) < max(config.cascade_min_move_bps, config.cascade_min_move_atr * float(shock["atr_bps"])):
        return None
    if side * float(shock["flow_60s"]) < config.cascade_min_flow_60s:
        return None
    if float(shock["notional_burst"]) < config.cascade_min_notional_burst:
        return None
    if float(shock["oi_change_15m"]) > config.cascade_max_oi_change_15m:
        return None
    breadth = _peer_breadth(frames, shock_index, side, symbol)
    if breadth < config.cascade_min_peer_breadth:
        return None
    return side, move, pre, float(shock["atr_bps"]), breadth


def _deleveraging_continuation(
    *, symbol: str, frames: Mapping[str, pd.DataFrame], index: int,
    config: LargeEventConfig,
) -> EventRoute | None:
    shock_index = index - config.continuation_shock_age
    classified = _cascade_shock(
        symbol=symbol, frames=frames, shock_index=shock_index, config=config,
    )
    if classified is None:
        return None
    side, move, pre, atr, breadth = classified
    frame = frames[symbol]
    row = frame.iloc[index]
    shock = frame.iloc[shock_index]
    if not _finite(row, "close", "open", "flow_60s", "ret1_bps"):
        return None
    retained = side * (float(row["close"]) - pre) / pre * 10_000.0 / abs(move)
    if retained < config.continuation_min_retention:
        return None
    if side * float(row["flow_60s"]) < 0.15 or side * float(row["ret1_bps"]) < 0.0:
        return None
    entry = float(row["close"])
    post = frame.iloc[shock_index : index + 1]
    stop = (
        float(post["low"].min()) - 0.10 * atr / 10_000.0 * entry
        if side > 0 else
        float(post["high"].max()) + 0.10 * atr / 10_000.0 * entry
    )
    shock_extreme = float(shock["high"] if side > 0 else shock["low"])
    objective = shock_extreme * (
        1.0 + side * config.continuation_extension_fraction * abs(move) / 10_000.0
    )
    valid, risk, reward = _valid_geometry(
        side=side, entry=entry, stop=stop, objective=objective, config=config,
    )
    if not valid:
        return None
    score = (
        1.2 + 0.45 * breadth + 0.40 * min(2.0, abs(move) / config.cascade_min_move_bps)
        + 0.55 * min(1.5, retained) + 0.35 * min(1.5, side * float(row["flow_60s"]))
        + 0.25 * min(2.0, reward / config.minimum_objective_bps)
    )
    return EventRoute(
        symbol=symbol, state="DELEVERAGING_CONTINUATION", side=side, score=score,
        signal_time_ns=int(row["ts"]), episode_time_ns=int(shock["ts"]),
        stop_reference=stop, objective_reference=objective,
        reason="COMMON_FORCED_DELEVERAGING_RETAINED_THEN_REACCELERATED",
        diagnostics={
            "peer_breadth": breadth, "shock_move_bps": move,
            "retention": retained, "price_risk_bps": risk,
            "objective_bps": reward,
        },
    )


def _failed_cascade_reversal(
    *, symbol: str, frames: Mapping[str, pd.DataFrame], index: int,
    config: LargeEventConfig,
) -> EventRoute | None:
    frame = frames[symbol]
    best: EventRoute | None = None
    for age in (1, 2):
        shock_index = index - age
        classified = _cascade_shock(
            symbol=symbol, frames=frames, shock_index=shock_index, config=config,
        )
        if classified is None:
            continue
        direction, move, pre, atr, breadth = classified
        side = -direction
        row = frame.iloc[index]
        shock = frame.iloc[shock_index]
        if not _finite(row, "close", "open", "flow_60s"):
            continue
        extent = float(shock["high"]) - pre if direction > 0 else pre - float(shock["low"])
        if extent <= 0.0:
            continue
        reclaim = (
            (float(shock["high"]) - float(row["close"])) / extent
            if direction > 0 else
            (float(row["close"]) - float(shock["low"])) / extent
        )
        if reclaim < config.reversal_min_reclaim:
            continue
        if side * float(row["flow_60s"]) < config.reversal_min_opposite_flow:
            continue
        if side * (float(row["close"]) - float(row["open"])) <= 0.0:
            continue
        entry = float(row["close"])
        stop = (
            float(shock["high"]) + 0.10 * atr / 10_000.0 * entry
            if side < 0 else
            float(shock["low"]) - 0.10 * atr / 10_000.0 * entry
        )
        objective = pre
        valid, risk, reward = _valid_geometry(
            side=side, entry=entry, stop=stop, objective=objective, config=config,
        )
        if not valid:
            continue
        score = (
            1.1 + 0.40 * breadth + 0.45 * min(2.0, abs(move) / config.cascade_min_move_bps)
            + 0.70 * min(1.5, reclaim)
            + 0.40 * min(1.5, side * float(row["flow_60s"]))
            + 0.25 * min(2.0, reward / config.minimum_objective_bps)
        )
        candidate = EventRoute(
            symbol=symbol, state="FAILED_CASCADE_REVERSAL", side=side, score=score,
            signal_time_ns=int(row["ts"]), episode_time_ns=int(shock["ts"]),
            stop_reference=stop, objective_reference=objective,
            reason="COMMON_DELEVERAGING_EXTREME_FAILED_AND_RECLAIMED",
            diagnostics={
                "peer_breadth": breadth, "shock_move_bps": move,
                "reclaim_fraction": reclaim, "price_risk_bps": risk,
                "objective_bps": reward,
            },
        )
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def route_large_event(
    *, frames: Mapping[str, pd.DataFrame], index: int,
    config: LargeEventConfig | None = None,
) -> tuple[EventRoute | None, list[EventRoute]]:
    config = config or LargeEventConfig()
    if tuple(frames) != SYMBOLS:
        raise ValueError(f"frames must preserve exact universe order {SYMBOLS}")
    timestamps = {int(frames[symbol].iloc[index]["ts"]) for symbol in SYMBOLS}
    if len(timestamps) != 1:
        raise ValueError("four-symbol latest completed bars are not aligned")
    candidates: list[EventRoute] = []
    for symbol in SYMBOLS:
        for builder in (
            _risk_build_breakout,
            _deleveraging_continuation,
            _failed_cascade_reversal,
        ):
            candidate = builder(
                symbol=symbol, frames=frames, index=index, config=config,
            )
            if candidate is not None and candidate.actionable:
                candidates.append(candidate)
    candidates.sort(key=lambda item: (-item.score, item.symbol, item.state))
    if not candidates:
        return None, candidates
    if len(candidates) > 1 and candidates[0].score - candidates[1].score < config.arbitration_gap:
        return None, candidates
    return candidates[0], candidates
