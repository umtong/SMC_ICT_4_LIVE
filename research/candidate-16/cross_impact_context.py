"""Causal shared state for lagged cross-asset order-flow transmission.

All observations are completed one-minute states.  A strategy evaluating time
``t`` may only consume states with ``ts_event < t``.  This module owns no order,
position, fill, or PnL logic.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import math
from statistics import median
from threading import RLock
from typing import Iterable


PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
MAX_STATE_AGE_NS = 75 * 1_000_000_000
MIN_RANK_HISTORY = 60
RANK_HISTORY = 180
FLOW_SHOCK_PERCENTILE = 0.85
MIN_ABS_PEER_FLOW = 0.05
MIN_PEER_RETURN_ATR = 0.08
MIN_INITIAL_LAG_GAP_ATR = 0.12
MIN_REMAINING_LAG_GAP_ATR = 0.04
MIN_LOCAL_TAIL_FLOW = 0.08


@dataclass(frozen=True, slots=True)
class CrossImpactObservation:
    symbol: str
    ts_event: int
    flow_15s: float
    flow_60s: float
    flow_3m: float
    ret_atr: float
    efficiency_60s: float
    notional_burst: float
    depth_imbalance_1: float


@dataclass(frozen=True, slots=True)
class CrossImpactDecision:
    actionable: bool
    reason: str
    side: int
    peer_symbols: tuple[str, ...] = ()
    peer_event_time_ns: int = 0
    peer_flow_percentiles: tuple[float, ...] = ()
    peer_directional_return_atr: float = 0.0
    target_prior_directional_return_atr: float = 0.0
    current_directional_return_atr: float = 0.0
    initial_lag_gap_atr: float = 0.0
    remaining_lag_gap_atr: float = 0.0
    local_transition_votes: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _sign(value: float) -> int:
    return 1 if value > 0.0 else -1 if value < 0.0 else 0


class LaggedCrossImpactContext:
    """Thread-safe completed-state memory shared by four strategy instances."""

    def __init__(self, maxlen: int = 720) -> None:
        self._lock = RLock()
        self._maxlen = int(maxlen)
        self._run_key: tuple[int, int] | None = None
        self._states = {
            symbol: deque(maxlen=self._maxlen)
            for symbol in PROJECT_SYMBOLS
        }

    def ensure_run(self, run_key: tuple[int, int]) -> None:
        with self._lock:
            if self._run_key == run_key:
                return
            self._run_key = run_key
            self._states = {
                symbol: deque(maxlen=self._maxlen)
                for symbol in PROJECT_SYMBOLS
            }

    def reset(self) -> None:
        with self._lock:
            self._run_key = None
            self._states = {
                symbol: deque(maxlen=self._maxlen)
                for symbol in PROJECT_SYMBOLS
            }

    def publish(self, observation: CrossImpactObservation) -> None:
        if observation.symbol not in self._states:
            raise ValueError(f"unsupported project symbol: {observation.symbol}")
        with self._lock:
            history = self._states[observation.symbol]
            if history and observation.ts_event < history[-1].ts_event:
                raise RuntimeError(
                    f"non-monotonic cross-impact state for {observation.symbol}: "
                    f"{observation.ts_event} < {history[-1].ts_event}",
                )
            if history and observation.ts_event == history[-1].ts_event:
                history[-1] = observation
            else:
                history.append(observation)

    def history_before(
        self,
        symbol: str,
        ts_event: int,
    ) -> list[CrossImpactObservation]:
        with self._lock:
            return [state for state in self._states[symbol] if state.ts_event < ts_event]

    def latest_before(
        self,
        symbol: str,
        ts_event: int,
        *,
        maximum_age_ns: int = MAX_STATE_AGE_NS,
    ) -> CrossImpactObservation | None:
        with self._lock:
            for state in reversed(self._states[symbol]):
                if state.ts_event >= ts_event:
                    continue
                age = int(ts_event) - int(state.ts_event)
                if age < 0:
                    raise RuntimeError("future cross-impact observation reached evaluator")
                return state if age <= maximum_age_ns else None
        return None

    def absolute_flow_percentile(
        self,
        observation: CrossImpactObservation,
    ) -> float:
        with self._lock:
            prior = [
                abs(float(state.flow_3m))
                for state in self._states[observation.symbol]
                if state.ts_event < observation.ts_event and _finite(state.flow_3m)
            ][-RANK_HISTORY:]
        if len(prior) < MIN_RANK_HISTORY or not _finite(observation.flow_3m):
            return float("nan")
        value = abs(float(observation.flow_3m))
        return (sum(item <= value for item in prior) + 1.0) / (len(prior) + 1.0)

    def decide(
        self,
        *,
        target_symbol: str,
        current: CrossImpactObservation,
    ) -> CrossImpactDecision:
        if target_symbol != current.symbol:
            raise ValueError("target symbol and current observation differ")

        target_prior = self.latest_before(target_symbol, current.ts_event)
        if target_prior is None:
            return CrossImpactDecision(False, "TARGET_PRIOR_STATE_UNAVAILABLE", 0)

        peer_states: list[CrossImpactObservation] = []
        peer_percentiles: list[float] = []
        for symbol in PROJECT_SYMBOLS:
            if symbol == target_symbol:
                continue
            state = self.latest_before(symbol, current.ts_event)
            if state is None:
                continue
            percentile = self.absolute_flow_percentile(state)
            if not _finite(percentile):
                continue
            peer_states.append(state)
            peer_percentiles.append(percentile)

        if len(peer_states) < 2:
            return CrossImpactDecision(False, "INSUFFICIENT_PRIOR_PEER_STATES", 0)

        shocks = [
            (state, percentile)
            for state, percentile in zip(peer_states, peer_percentiles)
            if percentile >= FLOW_SHOCK_PERCENTILE
            and abs(float(state.flow_3m)) >= MIN_ABS_PEER_FLOW
        ]
        if len(shocks) < 2:
            return CrossImpactDecision(False, "NO_MULTI_PEER_FLOW_SHOCK", 0)

        positive = sum(_sign(state.flow_3m) > 0 for state, _ in shocks)
        negative = sum(_sign(state.flow_3m) < 0 for state, _ in shocks)
        side = 1 if positive >= 2 else -1 if negative >= 2 else 0
        if side == 0:
            return CrossImpactDecision(False, "PEER_FLOW_SHOCK_DISAGREEMENT", 0)

        aligned = [
            (state, percentile)
            for state, percentile in shocks
            if _sign(state.flow_3m) == side
            and side * float(state.ret_atr) > 0.0
        ]
        if len(aligned) < 2:
            return CrossImpactDecision(False, "PEER_FLOW_WITHOUT_PRICE_TRANSMISSION", side)

        peer_return = median(side * float(state.ret_atr) for state, _ in aligned)
        if peer_return < MIN_PEER_RETURN_ATR:
            return CrossImpactDecision(False, "PEER_REPRICING_TOO_SMALL", side)

        target_prior_return = side * float(target_prior.ret_atr)
        initial_gap = peer_return - target_prior_return
        if initial_gap < MIN_INITIAL_LAG_GAP_ATR:
            return CrossImpactDecision(False, "TARGET_NOT_MATERIALLY_LAGGING", side)

        current_return = side * float(current.ret_atr)
        remaining_gap = initial_gap - max(current_return, 0.0)
        if remaining_gap < MIN_REMAINING_LAG_GAP_ATR:
            return CrossImpactDecision(False, "LAG_ALREADY_CONSUMED", side)

        directional_tail = side * float(current.flow_15s)
        directional_minute = side * float(current.flow_60s)
        flow_transition = (
            directional_tail >= MIN_LOCAL_TAIL_FLOW
            and directional_minute > 0.0
            and directional_tail >= directional_minute
        )
        if not flow_transition:
            return CrossImpactDecision(False, "LOCAL_FLOW_TRANSITION_ABSENT", side)

        votes = sum(
            (
                current_return > 0.0,
                side * float(current.depth_imbalance_1) >= -0.05,
                float(current.notional_burst) >= 1.0,
            ),
        )
        if votes < 2:
            return CrossImpactDecision(False, "LOCAL_AUCTION_TRANSITION_WEAK", side)

        selected_states = tuple(state.symbol for state, _ in aligned)
        selected_percentiles = tuple(float(percentile) for _, percentile in aligned)
        return CrossImpactDecision(
            actionable=True,
            reason="LAGGED_MULTI_PEER_OFI_TRANSMISSION",
            side=side,
            peer_symbols=selected_states,
            peer_event_time_ns=max(state.ts_event for state, _ in aligned),
            peer_flow_percentiles=selected_percentiles,
            peer_directional_return_atr=float(peer_return),
            target_prior_directional_return_atr=float(target_prior_return),
            current_directional_return_atr=float(current_return),
            initial_lag_gap_atr=float(initial_gap),
            remaining_lag_gap_atr=float(remaining_gap),
            local_transition_votes=int(votes),
        )


LAGGED_CROSS_IMPACT_CONTEXT = LaggedCrossImpactContext()


__all__ = [
    "CrossImpactDecision",
    "CrossImpactObservation",
    "LAGGED_CROSS_IMPACT_CONTEXT",
    "LaggedCrossImpactContext",
    "PROJECT_SYMBOLS",
]
