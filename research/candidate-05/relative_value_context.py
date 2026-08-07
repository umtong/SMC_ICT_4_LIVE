"""Prior-completed cross-asset observations for Candidate 05 v47."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class RelativeObservation:
    ts: int
    close: float
    atr: float


_HISTORY: dict[str, deque[RelativeObservation]] = {}
_MAXLEN = 512


def reset() -> None:
    _HISTORY.clear()


def publish(*, symbol: str, ts: int, close: float, atr: float) -> None:
    if not (symbol and ts > 0 and math.isfinite(close) and close > 0.0):
        return
    history = _HISTORY.setdefault(symbol, deque(maxlen=_MAXLEN))
    observation = RelativeObservation(ts=int(ts), close=float(close), atr=float(atr))
    if history and history[-1].ts == observation.ts:
        history[-1] = observation
    elif not history or history[-1].ts < observation.ts:
        history.append(observation)


def completed_history(symbol: str, *, before_ts: int, count: int) -> tuple[RelativeObservation, ...]:
    values = [item for item in _HISTORY.get(symbol, ()) if item.ts < before_ts]
    return tuple(values[-count:])
