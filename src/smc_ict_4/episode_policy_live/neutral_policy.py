"""Neutral market-policy contract for a replacement trading policy.

This module intentionally depends on only :class:`domain.Bar`.  It does not
inherit the legacy episode families, evidence schema, plan object, arbitration,
or sizing rules.  Account sizing and order execution remain adapter concerns.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping, Protocol, runtime_checkable

from .domain import Bar


MARKET_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
)


def _finite(name: str, value: float) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class MarketFrame:
    """The four completed one-clock market observations seen by a policy."""

    bars: tuple[Bar, ...]

    def __post_init__(self) -> None:
        ordered = tuple(sorted(tuple(self.bars), key=lambda bar: bar.symbol))
        if len(ordered) != len(MARKET_SYMBOLS):
            raise ValueError("market frame must contain exactly four bars")
        if {bar.symbol for bar in ordered} != set(MARKET_SYMBOLS):
            raise ValueError("market frame must contain each configured symbol exactly once")
        clocks = {
            (bar.interval_minutes, bar.open_time_ns, bar.close_time_ns)
            for bar in ordered
        }
        if len(clocks) != 1:
            raise ValueError("market frame bars must share one completed interval")
        object.__setattr__(self, "bars", ordered)

    @property
    def interval_minutes(self) -> int:
        return self.bars[0].interval_minutes

    @property
    def open_time_ns(self) -> int:
        return self.bars[0].open_time_ns

    @property
    def close_time_ns(self) -> int:
        return self.bars[0].close_time_ns

    def bar(self, symbol: str) -> Bar:
        for item in self.bars:
            if item.symbol == symbol:
                return item
        raise KeyError(symbol)

    def to_dict(self) -> dict[str, Any]:
        return {"bars": [bar.to_dict() for bar in self.bars]}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MarketFrame":
        return cls(tuple(Bar.from_dict(item) for item in payload["bars"]))


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """Policy-owned entry, stop, and target geometry; execution owns quantity."""

    intent_id: str
    symbol: str
    side: str
    decision_time_ns: int
    entry: float
    stop: float
    target: float
    # ``None`` means structural validity: the policy keeps the intent alive
    # until it emits an explicit invalidation (source failure, target spent,
    # supersession, or the first defended return failing).  A wall-clock lease
    # is optional execution metadata, never an alpha/lifecycle requirement.
    valid_until_ns: int | None = None
    entry_order_type: str = "STOP"

    def __post_init__(self) -> None:
        if not self.intent_id:
            raise ValueError("intent_id cannot be empty")
        if self.symbol not in MARKET_SYMBOLS:
            raise ValueError(f"unsupported symbol: {self.symbol}")
        if self.side not in {"LONG", "SHORT"}:
            raise ValueError("side must be LONG or SHORT")
        order_type = str(self.entry_order_type).upper()
        if order_type not in {"STOP", "LIMIT"}:
            raise ValueError("entry_order_type must be STOP or LIMIT")
        object.__setattr__(self, "entry_order_type", order_type)
        if self.valid_until_ns is not None and self.valid_until_ns <= self.decision_time_ns:
            raise ValueError("valid_until_ns must follow decision_time_ns")
        for name in ("entry", "stop", "target"):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
        if self.side == "LONG" and not self.stop < self.entry < self.target:
            raise ValueError("LONG intent must satisfy stop < entry < target")
        if self.side == "SHORT" and not self.target < self.entry < self.stop:
            raise ValueError("SHORT intent must satisfy target < entry < stop")
        if self.gross_rr < 1.0 - 1e-12:
            raise ValueError("gross planned reward/risk must be at least 1.0")

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_distance(self) -> float:
        return abs(self.target - self.entry)

    @property
    def gross_rr(self) -> float:
        return self.reward_distance / self.risk_distance


@dataclass(frozen=True, slots=True)
class ExecutionFeedback:
    """An execution event delivered back to the policy without broker coupling."""

    intent_id: str
    event_time_ns: int
    status: str
    fill_price: float | None = None
    filled_quantity: float | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.intent_id or not self.status:
            raise ValueError("execution feedback requires intent_id and status")
        if self.event_time_ns < 0:
            raise ValueError("event_time_ns cannot be negative")
        if self.fill_price is not None:
            object.__setattr__(self, "fill_price", _finite("fill_price", self.fill_price))
        if self.filled_quantity is not None:
            quantity = _finite("filled_quantity", self.filled_quantity)
            if quantity <= 0:
                raise ValueError("filled_quantity must be positive")
            object.__setattr__(self, "filled_quantity", quantity)


@dataclass(frozen=True, slots=True)
class IntentValidity:
    """The policy's current verdict for an already submitted intent."""

    intent_id: str
    valid: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.intent_id:
            raise ValueError("intent_id cannot be empty")
        if self.valid and self.reason is not None:
            raise ValueError("a valid intent cannot have an invalidation reason")
        if not self.valid and not self.reason:
            raise ValueError("an invalid intent requires a reason")


@dataclass(frozen=True, slots=True)
class PolicyOutput:
    """New order geometry plus validity updates for outstanding intents."""

    intents: tuple[OrderIntent, ...] = ()
    validity: tuple[IntentValidity, ...] = ()

    def __post_init__(self) -> None:
        intent_ids = [intent.intent_id for intent in self.intents]
        validity_ids = [item.intent_id for item in self.validity]
        if len(intent_ids) != len(set(intent_ids)):
            raise ValueError("policy output contains duplicate intents")
        if len(validity_ids) != len(set(validity_ids)):
            raise ValueError("policy output contains duplicate validity updates")


@runtime_checkable
class TradingPolicy(Protocol):
    """Small execution-facing API implemented by a replacement policy."""

    def on_market_frame(self, frame: MarketFrame) -> PolicyOutput: ...

    def on_execution_feedback(self, feedback: ExecutionFeedback) -> None: ...


class SynchronizedMarketFrameBuffer:
    """Build canonical four-symbol frames from sequential per-symbol bars."""

    _STATE_VERSION = 1

    def __init__(self, *, interval_minutes: int = 1) -> None:
        if interval_minutes <= 0:
            raise ValueError("interval_minutes must be positive")
        self.interval_minutes = int(interval_minutes)
        self._pending: dict[tuple[int, int], dict[str, Bar]] = {}
        self._last_close_time_ns: int | None = None
        self._last_frame: MarketFrame | None = None

    def push(self, bar: Bar) -> tuple[MarketFrame, ...]:
        if bar.interval_minutes != self.interval_minutes:
            raise ValueError("bar interval does not match the frame buffer")
        if self._last_close_time_ns is not None and bar.close_time_ns <= self._last_close_time_ns:
            if (
                self._last_frame is not None
                and bar.close_time_ns == self._last_close_time_ns
                and self._last_frame.bar(bar.symbol) == bar
            ):
                return ()
            raise ValueError("bar is stale or conflicts with an emitted frame")

        key = (bar.open_time_ns, bar.close_time_ns)
        bucket = self._pending.setdefault(key, {})
        previous = bucket.get(bar.symbol)
        if previous is not None:
            if previous == bar:
                return ()
            raise ValueError("conflicting duplicate bar")
        bucket[bar.symbol] = bar

        emitted: list[MarketFrame] = []
        while self._pending:
            earliest = min(self._pending)
            members = self._pending[earliest]
            if set(members) != set(MARKET_SYMBOLS):
                break
            frame = MarketFrame(tuple(members.values()))
            del self._pending[earliest]
            self._last_close_time_ns = frame.close_time_ns
            self._last_frame = frame
            emitted.append(frame)
        return tuple(emitted)

    def snapshot(self) -> str:
        payload = {
            "version": self._STATE_VERSION,
            "interval_minutes": self.interval_minutes,
            "last_frame": None if self._last_frame is None else self._last_frame.to_dict(),
            "pending": [
                bar.to_dict()
                for key in sorted(self._pending)
                for bar in sorted(self._pending[key].values(), key=lambda item: item.symbol)
            ],
        }
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )

    @classmethod
    def from_snapshot(cls, snapshot: str) -> "SynchronizedMarketFrameBuffer":
        payload = json.loads(snapshot)
        if payload.get("version") != cls._STATE_VERSION:
            raise ValueError("unsupported frame-buffer snapshot version")
        buffer = cls(interval_minutes=int(payload["interval_minutes"]))
        last_payload = payload.get("last_frame")
        if last_payload is not None:
            buffer._last_frame = MarketFrame.from_dict(last_payload)
            buffer._last_close_time_ns = buffer._last_frame.close_time_ns
        for raw in payload.get("pending", []):
            bar = Bar.from_dict(raw)
            if bar.interval_minutes != buffer.interval_minutes:
                raise ValueError("snapshot contains a mismatched interval")
            if buffer._last_close_time_ns is not None and bar.close_time_ns <= buffer._last_close_time_ns:
                raise ValueError("snapshot contains a stale pending bar")
            key = (bar.open_time_ns, bar.close_time_ns)
            bucket = buffer._pending.setdefault(key, {})
            if bar.symbol in bucket:
                raise ValueError("snapshot contains a duplicate pending bar")
            bucket[bar.symbol] = bar
        return buffer
