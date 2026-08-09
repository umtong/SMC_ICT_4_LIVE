"""Causal online Markov state-sequence router adapted from jehumtine's public strategy.

The source's useful mechanism is retained: price/ATR x volume states, sequence
transition probabilities, and ATR brackets. Source backtest defects (left-labeled
4H bars, backward filling, reset accounts and incomplete cost accounting) are
not retained. Every transition here is learned only after the next completed
15-minute state exists.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

MARKOV_STATE = "PUBLIC_CAUSAL_MARKOV_15M"
SMA_OFFSET_STATE = MARKOV_STATE
UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class BarObservation:
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    observed_time_ns: int
    ready: bool = True
    flow_open_10s: float = math.nan
    notional_open_10s_burst: float = math.nan
    flow_60s: float = math.nan
    efficiency_60s: float = math.nan
    oi_change_15m: float = math.nan
    premium_z: float = math.nan


@dataclass(frozen=True, slots=True)
class RouteConfig:
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_participation_ratio: float = 0.85
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65

    markov_sequence_length: int = 2
    markov_min_transition_count: int = 10
    markov_min_direction_probability: float = 0.80
    markov_atr_period: int = 18
    markov_volume_period: int = 24
    markov_price_state_threshold: float = 0.774325295833127
    markov_volume_high_multiplier: float = 2.96381982151409
    markov_volume_low_multiplier: float = 0.76691565071116
    markov_stop_atr: float = 3.2935543511297
    markov_target_atr: float = 2.04204584013095
    markov_long_only: bool = False


@dataclass(frozen=True, slots=True)
class RouteDecision:
    symbol: str
    state: str
    side: int
    score: float
    entry_reference: float
    stop_reference: float
    objective_reference: float
    episode_ts: int
    reasons: tuple[str, ...] = ()
    diagnostics: Mapping[str, float | int | str] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.side in (-1, 1) and self.state != UNRESOLVED


def _unresolved(symbol: str, ts_event: int, reason: str, **diagnostics) -> RouteDecision:
    return RouteDecision(
        symbol=symbol,
        state=UNRESOLVED,
        side=0,
        score=0.0,
        entry_reference=math.nan,
        stop_reference=math.nan,
        objective_reference=math.nan,
        episode_ts=int(ts_event),
        reasons=(reason,),
        diagnostics=diagnostics,
    )


def _wilder_atr(candles: Sequence[BarObservation], period: int) -> float:
    if period <= 0 or len(candles) <= period:
        return math.nan
    true_ranges: list[float] = []
    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]
        true_ranges.append(
            max(
                float(current.high) - float(current.low),
                abs(float(current.high) - float(previous.close)),
                abs(float(current.low) - float(previous.close)),
            )
        )
    if len(true_ranges) < period:
        return math.nan
    atr = sum(true_ranges[:period]) / period
    for value in true_ranges[period:]:
        atr = (atr * (period - 1) + value) / period
    return atr


def classify_state(
    candles: Sequence[BarObservation],
    config: RouteConfig,
) -> tuple[int, float, float, float] | None:
    needed = max(int(config.markov_atr_period) + 1, int(config.markov_volume_period), 2)
    if len(candles) < needed:
        return None
    atr = _wilder_atr(candles, int(config.markov_atr_period))
    if not math.isfinite(atr) or atr <= 0.0:
        return None
    current = candles[-1]
    previous = candles[-2]
    normalized = (float(current.close) - float(previous.close)) / atr
    threshold = float(config.markov_price_state_threshold)
    if normalized > threshold:
        price_state = 0
    elif normalized > 0.0:
        price_state = 1
    elif normalized > -threshold:
        price_state = 2
    else:
        price_state = 3

    period = int(config.markov_volume_period)
    sample = candles[-period:]
    volume_mean = sum(max(0.0, float(item.volume)) for item in sample) / period
    current_volume = max(0.0, float(current.volume))
    volume_ratio = current_volume / volume_mean if volume_mean > 0.0 else 1.0
    if current_volume > volume_mean * float(config.markov_volume_high_multiplier):
        volume_state = 0
    elif current_volume < volume_mean * float(config.markov_volume_low_multiplier):
        volume_state = 1
    else:
        volume_state = 2
    return price_state * 3 + volume_state, atr, normalized, volume_ratio


class OnlineMarkovRouter:
    """Per-symbol online transition learner with causal update ordering."""

    def __init__(self, config: RouteConfig, symbols: Sequence[str]) -> None:
        if int(config.markov_sequence_length) < 1:
            raise ValueError("markov_sequence_length must be positive")
        self.config = config
        self.states = {
            symbol: deque(maxlen=20_000) for symbol in symbols
        }
        self.transitions = {
            symbol: defaultdict(Counter) for symbol in symbols
        }
        self.last_bar_ts = {symbol: -1 for symbol in symbols}
        self.signal_signature: dict[str, tuple[tuple[int, ...], int] | None] = {
            symbol: None for symbol in symbols
        }
        self.signal_episode_ts = {symbol: -1 for symbol in symbols}
        self.observations = {symbol: 0 for symbol in symbols}
        self.learned_transitions = {symbol: 0 for symbol in symbols}

    def advance(
        self,
        symbol: str,
        candles: Sequence[BarObservation],
    ) -> RouteDecision:
        ts_event = int(candles[-1].ts_event) if candles else 0
        if ts_event <= self.last_bar_ts[symbol]:
            return _unresolved(symbol, ts_event, "MARKOV_DUPLICATE_BAR")
        classified = classify_state(candles, self.config)
        if classified is None:
            self.last_bar_ts[symbol] = ts_event
            return _unresolved(symbol, ts_event, "MARKOV_WARMUP")
        state, atr, normalized, volume_ratio = classified
        history = self.states[symbol]
        sequence_length = int(self.config.markov_sequence_length)

        # Learn sequence -> current state only now, when current completed bar exists.
        if len(history) >= sequence_length:
            prior_sequence = tuple(list(history)[-sequence_length:])
            self.transitions[symbol][prior_sequence][state] += 1
            self.learned_transitions[symbol] += 1
        history.append(state)
        self.last_bar_ts[symbol] = ts_event
        self.observations[symbol] += 1

        if len(history) < sequence_length:
            self.signal_signature[symbol] = None
            return _unresolved(symbol, ts_event, "MARKOV_SEQUENCE_WARMUP", state=state)
        sequence = tuple(list(history)[-sequence_length:])
        counts = self.transitions[symbol].get(sequence, Counter())
        total = int(sum(counts.values()))
        if total < int(self.config.markov_min_transition_count):
            self.signal_signature[symbol] = None
            return _unresolved(
                symbol, ts_event, "MARKOV_SAMPLE_TOO_SMALL",
                state=state, sample_count=total, sequence=str(sequence),
                atr=atr, normalized_move=normalized, volume_ratio=volume_ratio,
            )

        bullish = sum(int(counts.get(next_state, 0)) for next_state in range(0, 6))
        bearish = sum(int(counts.get(next_state, 0)) for next_state in range(6, 12))
        bull_probability = bullish / total
        bear_probability = bearish / total
        if bull_probability >= bear_probability:
            side = 1
            probability = bull_probability
        else:
            side = -1
            probability = bear_probability
        if probability < float(self.config.markov_min_direction_probability):
            self.signal_signature[symbol] = None
            return _unresolved(
                symbol, ts_event, "MARKOV_PROBABILITY_TOO_LOW",
                state=state, sample_count=total, sequence=str(sequence),
                bull_probability=bull_probability, bear_probability=bear_probability,
                atr=atr, normalized_move=normalized, volume_ratio=volume_ratio,
            )
        if bool(self.config.markov_long_only) and side < 0:
            self.signal_signature[symbol] = None
            return _unresolved(
                symbol, ts_event, "MARKOV_SHORT_DISABLED",
                state=state, sample_count=total, sequence=str(sequence),
                bull_probability=bull_probability, bear_probability=bear_probability,
                atr=atr, normalized_move=normalized, volume_ratio=volume_ratio,
            )

        signature = (sequence, side)
        if self.signal_signature[symbol] != signature:
            self.signal_signature[symbol] = signature
            self.signal_episode_ts[symbol] = ts_event
        episode_ts = int(self.signal_episode_ts[symbol])
        entry = float(candles[-1].close)
        stop_distance = float(self.config.markov_stop_atr) * atr
        target_distance = float(self.config.markov_target_atr) * atr
        stop = entry - side * stop_distance
        target = entry + side * target_distance
        if stop <= 0.0 or target <= 0.0:
            return _unresolved(symbol, ts_event, "MARKOV_INVALID_GEOMETRY")
        score = 100.0 * probability + min(10.0, math.log1p(total))
        return RouteDecision(
            symbol=symbol,
            state=MARKOV_STATE,
            side=side,
            score=score,
            entry_reference=entry,
            stop_reference=stop,
            objective_reference=target,
            episode_ts=episode_ts,
            reasons=("CAUSAL_SEQUENCE_TRANSITION",),
            diagnostics={
                "current_state": state,
                "sequence": str(sequence),
                "sample_count": total,
                "bull_probability": bull_probability,
                "bear_probability": bear_probability,
                "selected_probability": probability,
                "atr_at_entry": atr,
                "normalized_move": normalized,
                "volume_ratio": volume_ratio,
                "stop_atr": float(self.config.markov_stop_atr),
                "target_atr": float(self.config.markov_target_atr),
            },
        )


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
):
    """Compatibility surface. Stateful research uses OnlineMarkovRouter instead."""
    del features_by_symbol, config
    decisions = {
        symbol: _unresolved(
            symbol,
            int(bars[-1].ts_event) if bars else 0,
            "MARKOV_STATEFUL_ROUTER_REQUIRED",
        )
        for symbol, bars in bars_by_symbol.items()
    }
    return None, decisions


__all__ = [
    "BarObservation", "FeatureObservation", "RouteConfig", "RouteDecision",
    "OnlineMarkovRouter", "MARKOV_STATE", "SMA_OFFSET_STATE", "UNRESOLVED",
    "classify_state", "route_universe",
]
