"""Causal cross-sectional quarter-hour pulse registry.

Each strategy instance publishes only a completed minute observation.  A
consensus is queryable only from timestamps strictly earlier than the current
bar, so strategy registration order inside one NautilusTrader node cannot leak
same-timestamp peer information.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from threading import RLock

PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
MIN_ABS_OPENING_FLOW = 0.08
MIN_OPENING_NOTIONAL_BURST = 1.15
MIN_MINUTE_EFFICIENCY = 0.08
MIN_ALIGNED_SYMBOLS = 3
MAX_RETAINED_BOUNDARIES = 64


@dataclass(frozen=True, slots=True)
class QuarterHourPulse:
    symbol: str
    ts_event: int
    direction: int
    flow_open_10s: float
    notional_open_10s_burst: float
    ret_60s_bps: float
    efficiency_60s: float
    close: float
    high: float
    low: float
    atr: float

    @property
    def score(self) -> float:
        if self.direction == 0:
            return 0.0
        response = 1.0 + min(abs(self.ret_60s_bps) / 10.0, 2.0)
        return (
            abs(self.flow_open_10s)
            * min(max(self.notional_open_10s_burst, 0.0), 4.0)
            * max(self.efficiency_60s, 0.05)
            * response
        )


@dataclass(frozen=True, slots=True)
class QuarterHourConsensus:
    boundary_ts: int
    direction: int
    owner: str
    aligned_symbols: tuple[str, ...]
    median_abs_flow: float
    median_burst: float
    owner_score: float
    owner_pulse: QuarterHourPulse


class CompletedQuarterHourContext:
    """Store completed boundary pulses and derive an order-independent consensus."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._by_boundary: dict[int, dict[str, QuarterHourPulse]] = {}
        self._last_by_symbol: dict[str, int] = {}
        self._active: tuple[str, int, int] | None = None

    def reset(self) -> None:
        with self._lock:
            self._by_boundary.clear()
            self._last_by_symbol.clear()
            self._active = None

    def publish(self, pulse: QuarterHourPulse) -> None:
        if pulse.symbol not in PROJECT_SYMBOLS:
            raise ValueError(f"unsupported project symbol: {pulse.symbol}")
        with self._lock:
            previous = self._last_by_symbol.get(pulse.symbol)
            if previous is not None and pulse.ts_event < previous:
                raise ValueError("quarter-hour pulses must be published monotonically")
            self._last_by_symbol[pulse.symbol] = pulse.ts_event
            bucket = self._by_boundary.setdefault(pulse.ts_event, {})
            bucket[pulse.symbol] = pulse
            if len(self._by_boundary) > MAX_RETAINED_BOUNDARIES:
                for timestamp in sorted(self._by_boundary)[:-MAX_RETAINED_BOUNDARIES]:
                    self._by_boundary.pop(timestamp, None)

    @staticmethod
    def _eligible(pulse: QuarterHourPulse) -> bool:
        values = (
            pulse.flow_open_10s,
            pulse.notional_open_10s_burst,
            pulse.ret_60s_bps,
            pulse.efficiency_60s,
            pulse.close,
            pulse.high,
            pulse.low,
            pulse.atr,
        )
        if not all(math.isfinite(value) for value in values):
            return False
        if pulse.atr <= 0.0 or pulse.direction not in (-1, 1):
            return False
        return (
            abs(pulse.flow_open_10s) >= MIN_ABS_OPENING_FLOW
            and pulse.notional_open_10s_burst >= MIN_OPENING_NOTIONAL_BURST
            and pulse.efficiency_60s >= MIN_MINUTE_EFFICIENCY
            and pulse.direction * pulse.ret_60s_bps > 0.0
        )

    def latest_consensus(self, *, before_ts: int) -> QuarterHourConsensus | None:
        """Return the latest completed >=3-market information pulse before ``before_ts``."""
        with self._lock:
            timestamps = sorted(
                (timestamp for timestamp in self._by_boundary if timestamp < before_ts),
                reverse=True,
            )
            for timestamp in timestamps:
                pulses = tuple(self._by_boundary[timestamp].values())
                eligible = tuple(pulse for pulse in pulses if self._eligible(pulse))
                positive = tuple(pulse for pulse in eligible if pulse.direction > 0)
                negative = tuple(pulse for pulse in eligible if pulse.direction < 0)
                aligned = positive if len(positive) >= len(negative) else negative
                if len(aligned) < MIN_ALIGNED_SYMBOLS:
                    continue
                direction = aligned[0].direction
                owner_pulse = max(aligned, key=lambda item: (item.score, item.symbol))
                return QuarterHourConsensus(
                    boundary_ts=timestamp,
                    direction=direction,
                    owner=owner_pulse.symbol,
                    aligned_symbols=tuple(sorted(item.symbol for item in aligned)),
                    median_abs_flow=float(median(abs(item.flow_open_10s) for item in aligned)),
                    median_burst=float(median(item.notional_open_10s_burst for item in aligned)),
                    owner_score=float(owner_pulse.score),
                    owner_pulse=owner_pulse,
                )
        return None

    def claim(self, *, owner: str, boundary_ts: int, direction: int) -> bool:
        if owner not in PROJECT_SYMBOLS or direction not in (-1, 1):
            return False
        with self._lock:
            requested = (owner, int(boundary_ts), int(direction))
            if self._active is None:
                self._active = requested
                return True
            return self._active == requested

    def release(self, *, owner: str, boundary_ts: int) -> bool:
        with self._lock:
            if self._active is None:
                return False
            if self._active[0] != owner or self._active[1] != int(boundary_ts):
                return False
            self._active = None
            return True

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "boundaries": len(self._by_boundary),
                "last_by_symbol": dict(self._last_by_symbol),
                "active": self._active,
            }


SHARED_QUARTER_HOUR_CONTEXT = CompletedQuarterHourContext()


def reset_shared_quarter_hour_context() -> None:
    SHARED_QUARTER_HOUR_CONTEXT.reset()


__all__ = [
    "CompletedQuarterHourContext",
    "PROJECT_SYMBOLS",
    "QuarterHourConsensus",
    "QuarterHourPulse",
    "SHARED_QUARTER_HOUR_CONTEXT",
    "reset_shared_quarter_hour_context",
]
