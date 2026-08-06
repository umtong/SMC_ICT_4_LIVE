"""Prior-only liquidity levels and objective sweep primitives."""

from __future__ import annotations

from collections import deque
from statistics import median
from typing import Any, Iterable, Mapping

from lrb_types import BarObservation, PrimitiveSnapshot, SweepPrimitive


class CausalPrimitiveDetector:
    """Builds levels and normalized response measures from prior observations only."""

    def __init__(self, params: Mapping[str, Any]):
        self.fast_lookback = int(params["fast_lookback"])
        self.slow_lookback = int(params["slow_lookback"])
        self.atr_lookback = int(params["atr_lookback"])
        self.volume_lookback = int(params["volume_lookback"])
        self.pool_tolerance_atr = float(params["pool_tolerance_atr"])
        capacity = max(self.slow_lookback, self.volume_lookback, self.atr_lookback) + 4
        self._history: deque[BarObservation] = deque(maxlen=capacity)
        self._true_ranges: deque[float] = deque(maxlen=capacity)
        self._volumes: deque[float] = deque(maxlen=capacity)
        self._index = -1

    def observe(self, obs: BarObservation) -> PrimitiveSnapshot:
        self._index += 1
        ready = (
            len(self._history) >= self.slow_lookback
            and len(self._true_ranges) >= self.atr_lookback
            and len(self._volumes) >= self.volume_lookback
        )

        atr = self._safe_mean(list(self._true_ranges)[-self.atr_lookback:]) if self._true_ranges else 0.0
        baseline_volume = median(list(self._volumes)[-self.volume_lookback:]) if self._volumes else 0.0
        rel_volume = obs.volume / baseline_volume if baseline_volume > 0.0 else 0.0

        upper_fast = lower_fast = upper_slow = lower_slow = slow_mid = range_position = None
        upper_touches = lower_touches = 0
        if ready:
            fast = list(self._history)[-self.fast_lookback:]
            slow = list(self._history)[-self.slow_lookback:]
            upper_fast = max(bar.high for bar in fast)
            lower_fast = min(bar.low for bar in fast)
            upper_slow = max(bar.high for bar in slow)
            lower_slow = min(bar.low for bar in slow)
            slow_mid = (upper_slow + lower_slow) / 2.0
            slow_width = upper_slow - lower_slow
            range_position = (obs.close - lower_slow) / slow_width if slow_width > 0.0 else 0.5
            tolerance = max(atr * self.pool_tolerance_atr, obs.close * 1e-7)
            upper_touches = sum(abs(bar.high - upper_fast) <= tolerance for bar in fast)
            lower_touches = sum(abs(bar.low - lower_fast) <= tolerance for bar in fast)

        candle_range = max(obs.high - obs.low, 0.0)
        body = abs(obs.close - obs.open)
        upper_wick = max(obs.high - max(obs.open, obs.close), 0.0)
        lower_wick = max(min(obs.open, obs.close) - obs.low, 0.0)
        close_location = (obs.close - obs.low) / candle_range if candle_range > 0.0 else 0.5

        snapshot = PrimitiveSnapshot(
            index=self._index,
            observation=obs,
            ready=ready,
            atr=atr,
            rel_volume=rel_volume,
            flow_ratio=obs.flow_ratio,
            body_atr=body / atr if atr > 0.0 else 0.0,
            range_atr=candle_range / atr if atr > 0.0 else 0.0,
            upper_wick_fraction=upper_wick / candle_range if candle_range > 0.0 else 0.0,
            lower_wick_fraction=lower_wick / candle_range if candle_range > 0.0 else 0.0,
            close_location=close_location,
            upper_fast=upper_fast,
            lower_fast=lower_fast,
            upper_slow=upper_slow,
            lower_slow=lower_slow,
            slow_mid=slow_mid,
            range_position=range_position,
            upper_pool_touches=upper_touches,
            lower_pool_touches=lower_touches,
        )

        previous_close = self._history[-1].close if self._history else obs.close
        true_range = max(
            obs.high - obs.low,
            abs(obs.high - previous_close),
            abs(obs.low - previous_close),
        )
        self._history.append(obs)
        self._true_ranges.append(true_range)
        self._volumes.append(obs.volume)
        return snapshot

    @staticmethod
    def _safe_mean(values: Iterable[float]) -> float:
        materialized = list(values)
        return sum(materialized) / len(materialized) if materialized else 0.0


class LiquiditySweepDetector:
    """Detects objective level breaches it does not make a trading decision."""

    def __init__(self, params: Mapping[str, Any]):
        self.sweep_min_atr = float(params["sweep_min_atr"])

    def detect(self, snapshot: PrimitiveSnapshot) -> tuple[SweepPrimitive, ...]:
        if not snapshot.ready or snapshot.atr <= 0.0:
            return ()
        assert snapshot.upper_fast is not None
        assert snapshot.lower_fast is not None
        assert snapshot.upper_slow is not None
        assert snapshot.lower_slow is not None
        obs = snapshot.observation
        events: list[SweepPrimitive] = []
        upper_depth = (obs.high - snapshot.upper_fast) / snapshot.atr
        if upper_depth >= self.sweep_min_atr:
            events.append(
                SweepPrimitive(
                    side="UPPER",
                    level=snapshot.upper_fast,
                    depth_atr=upper_depth,
                    pool_touches=snapshot.upper_pool_touches,
                    external_to_slow_range=abs(snapshot.upper_fast - snapshot.upper_slow) <= snapshot.atr * 0.05,
                ),
            )
        lower_depth = (snapshot.lower_fast - obs.low) / snapshot.atr
        if lower_depth >= self.sweep_min_atr:
            events.append(
                SweepPrimitive(
                    side="LOWER",
                    level=snapshot.lower_fast,
                    depth_atr=lower_depth,
                    pool_touches=snapshot.lower_pool_touches,
                    external_to_slow_range=abs(snapshot.lower_fast - snapshot.lower_slow) <= snapshot.atr * 0.05,
                ),
            )
        return tuple(events)

