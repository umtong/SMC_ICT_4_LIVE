"""Causal price-discovery leadership approval for Candidate 11.

This module does not size positions, submit orders, infer fills, calculate PnL,
or change the project risk fraction.  It is a binary approval layer applied to
already-complete SCDAM plans.  Leadership is recomputed from trailing completed
one-minute quote notional and is therefore not hard-coded to a symbol.

A leader may express an idiosyncratic FAR or AAC.  A follower FAR is approved
only when every peer market has moved in the proposed reversal direction from
the source sweep to plan confirmation.  Follower AAC abstains because accepted
price discovery should originate in the current notional leader.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import isfinite
from typing import Mapping

MINUTE_NS = 60_000_000_000


@dataclass(frozen=True, slots=True)
class MarketPoint:
    ts_ns: int
    close: float
    quote_notional: float


@dataclass(frozen=True, slots=True)
class LeadershipDecision:
    approved: bool
    reason: str
    leader: str | None
    symbol: str
    scenario: str
    direction: str
    sweep_ts_ns: int
    confirmation_ts_ns: int
    peer_returns: dict[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "approved": self.approved,
            "reason": self.reason,
            "leader": self.leader,
            "symbol": self.symbol,
            "scenario": self.scenario,
            "direction": self.direction,
            "sweep_ts_ns": self.sweep_ts_ns,
            "confirmation_ts_ns": self.confirmation_ts_ns,
            "peer_returns": dict(sorted(self.peer_returns.items())),
        }


class MarketLeadershipGate:
    """Binary causal approval; it never changes quantity or risk."""

    def __init__(
        self,
        symbols: tuple[str, ...],
        *,
        lookback_bars: int = 1440,
        max_history_bars: int | None = None,
    ) -> None:
        if len(symbols) < 3 or len(set(symbols)) != len(symbols):
            raise ValueError("leadership requires at least three unique symbols")
        if lookback_bars < 2:
            raise ValueError("lookback_bars must be at least two")
        history_bars = max_history_bars or max(lookback_bars * 2, lookback_bars + 720)
        if history_bars < lookback_bars:
            raise ValueError("max_history_bars cannot be shorter than lookback")
        self.symbols = tuple(symbols)
        self.lookback_bars = int(lookback_bars)
        self._history = {
            symbol: deque(maxlen=int(history_bars))
            for symbol in self.symbols
        }
        self._last_batch_ts = -1

    def observe_batch(
        self,
        ts_ns: int,
        observations: Mapping[str, tuple[float, float]],
    ) -> None:
        """Observe one completed synchronized minute for every market."""
        if ts_ns <= self._last_batch_ts:
            raise ValueError("leadership batches must be strictly increasing")
        if set(observations) != set(self.symbols):
            missing = sorted(set(self.symbols) - set(observations))
            extra = sorted(set(observations) - set(self.symbols))
            raise ValueError(f"incomplete leadership batch: missing={missing} extra={extra}")
        points: dict[str, MarketPoint] = {}
        for symbol in self.symbols:
            close, volume = observations[symbol]
            close = float(close)
            volume = float(volume)
            if not isfinite(close) or not isfinite(volume) or close <= 0 or volume < 0:
                raise ValueError(f"invalid completed observation for {symbol}")
            points[symbol] = MarketPoint(ts_ns, close, close * volume)
        for symbol, point in points.items():
            self._history[symbol].append(point)
        self._last_batch_ts = ts_ns

    def _point_at(self, symbol: str, ts_ns: int) -> MarketPoint | None:
        for point in reversed(self._history[symbol]):
            if point.ts_ns == ts_ns:
                return point
            if point.ts_ns < ts_ns:
                break
        return None

    def _leader_at(self, ts_ns: int) -> str | None:
        start_exclusive = ts_ns - self.lookback_bars * MINUTE_NS
        totals: dict[str, float] = {}
        for symbol in self.symbols:
            sample = [
                point for point in self._history[symbol]
                if start_exclusive < point.ts_ns <= ts_ns
            ]
            if len(sample) < self.lookback_bars or self._point_at(symbol, ts_ns) is None:
                return None
            totals[symbol] = sum(point.quote_notional for point in sample)
        # Deterministic lexical tie-break only; leadership is never hard-coded.
        return sorted(self.symbols, key=lambda symbol: (-totals[symbol], symbol))[0]

    def decide(
        self,
        *,
        symbol: str,
        scenario: str,
        direction: str,
        sweep_ts_ns: int,
        confirmation_ts_ns: int,
    ) -> LeadershipDecision:
        if symbol not in self._history:
            raise ValueError(f"unsupported symbol: {symbol}")
        if scenario not in {"FAR", "AAC"}:
            raise ValueError(f"unsupported scenario: {scenario}")
        if direction not in {"LONG", "SHORT"}:
            raise ValueError(f"unsupported direction: {direction}")

        def decision(
            approved: bool,
            reason: str,
            leader: str | None,
            peer_returns: dict[str, float] | None = None,
        ) -> LeadershipDecision:
            return LeadershipDecision(
                approved=approved,
                reason=reason,
                leader=leader,
                symbol=symbol,
                scenario=scenario,
                direction=direction,
                sweep_ts_ns=int(sweep_ts_ns),
                confirmation_ts_ns=int(confirmation_ts_ns),
                peer_returns=peer_returns or {},
            )

        if confirmation_ts_ns != self._last_batch_ts:
            return decision(False, "ASYNCHRONOUS_CONFIRMATION", None)
        if sweep_ts_ns < 0 or sweep_ts_ns >= confirmation_ts_ns:
            return decision(False, "INVALID_SWEEP_CONFIRMATION_ORDER", None)

        leader = self._leader_at(sweep_ts_ns)
        if leader is None:
            return decision(False, "INSUFFICIENT_LEADERSHIP_HISTORY", None)
        if symbol == leader:
            return decision(True, "LEADER_PRICE_DISCOVERY", leader)
        if scenario == "AAC":
            return decision(False, "FOLLOWER_AAC_WITHOUT_LEADERSHIP", leader)

        peer_returns: dict[str, float] = {}
        for peer in self.symbols:
            if peer == symbol:
                continue
            sweep = self._point_at(peer, sweep_ts_ns)
            confirmation = self._point_at(peer, confirmation_ts_ns)
            if sweep is None or confirmation is None:
                return decision(False, "MISSING_SYNCHRONIZED_PEER_SNAPSHOT", leader, peer_returns)
            peer_returns[peer] = confirmation.close / sweep.close - 1.0

        aligned = (
            all(value > 0.0 for value in peer_returns.values())
            if direction == "LONG"
            else all(value < 0.0 for value in peer_returns.values())
        )
        if not aligned:
            return decision(False, "FOLLOWER_FAR_PEER_DISAGREEMENT", leader, peer_returns)
        return decision(True, "FOLLOWER_FAR_UNANIMOUS_PEERS", leader, peer_returns)
