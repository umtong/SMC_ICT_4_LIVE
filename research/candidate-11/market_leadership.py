"""Causal price-discovery leadership approval for Candidate 11.

This module does not size positions, submit orders, infer fills, calculate PnL,
or change the project risk fraction.  It is a binary approval layer applied to
already-complete SCDAM plans.  Leadership is recomputed from trailing completed
one-minute quote notional and is therefore not hard-coded to a symbol.

A notional leader may express an idiosyncratic FAR or AAC. A follower FAR is
approved either when every peer market confirms the reversal, or when the
candidate was in the directional top half over the trailing causal window and
then outperforms the peer median from sweep to confirmation. The second path
captures a relative-strength liquidity grab without weakening the first path.
Follower AAC abstains because accepted price discovery should originate in the
current notional leader.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import isfinite
from statistics import median
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
    directional_returns: dict[str, float]

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
            "directional_returns": dict(sorted(self.directional_returns.items())),
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

    def _directional_returns_at(
        self,
        ts_ns: int,
        direction: str,
    ) -> dict[str, float] | None:
        """Return causal trailing returns signed into the proposed direction."""
        sign = 1.0 if direction == "LONG" else -1.0
        start_exclusive = ts_ns - self.lookback_bars * MINUTE_NS
        returns: dict[str, float] = {}
        for symbol in self.symbols:
            sample = [
                point for point in self._history[symbol]
                if start_exclusive < point.ts_ns <= ts_ns
            ]
            if len(sample) < self.lookback_bars or sample[-1].ts_ns != ts_ns:
                return None
            returns[symbol] = sign * (sample[-1].close / sample[0].close - 1.0)
        return returns

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
            directional_returns: dict[str, float] | None = None,
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
                directional_returns=directional_returns or {},
            )

        if confirmation_ts_ns != self._last_batch_ts:
            return decision(False, "ASYNCHRONOUS_CONFIRMATION", None)
        if sweep_ts_ns < 0 or sweep_ts_ns >= confirmation_ts_ns:
            return decision(False, "INVALID_SWEEP_CONFIRMATION_ORDER", None)

        leader = self._leader_at(sweep_ts_ns)
        directional_returns = self._directional_returns_at(sweep_ts_ns, direction)
        if leader is None or directional_returns is None:
            return decision(False, "INSUFFICIENT_LEADERSHIP_HISTORY", None)
        if symbol == leader:
            return decision(
                True,
                "LEADER_PRICE_DISCOVERY",
                leader,
                directional_returns=directional_returns,
            )
        if scenario == "AAC":
            return decision(
                False,
                "FOLLOWER_AAC_WITHOUT_LEADERSHIP",
                leader,
                directional_returns=directional_returns,
            )

        peer_returns: dict[str, float] = {}
        candidate_sweep = self._point_at(symbol, sweep_ts_ns)
        candidate_confirmation = self._point_at(symbol, confirmation_ts_ns)
        if candidate_sweep is None or candidate_confirmation is None:
            return decision(
                False,
                "MISSING_SYNCHRONIZED_CANDIDATE_SNAPSHOT",
                leader,
                directional_returns=directional_returns,
            )
        for peer in self.symbols:
            if peer == symbol:
                continue
            sweep = self._point_at(peer, sweep_ts_ns)
            confirmation = self._point_at(peer, confirmation_ts_ns)
            if sweep is None or confirmation is None:
                return decision(
                    False,
                    "MISSING_SYNCHRONIZED_PEER_SNAPSHOT",
                    leader,
                    peer_returns,
                    directional_returns,
                )
            peer_returns[peer] = confirmation.close / sweep.close - 1.0

        sign = 1.0 if direction == "LONG" else -1.0
        signed_peer_moves = [sign * value for value in peer_returns.values()]
        if all(value > 0.0 for value in signed_peer_moves):
            return decision(
                True,
                "FOLLOWER_FAR_UNANIMOUS_PEERS",
                leader,
                peer_returns,
                directional_returns,
            )

        candidate_move = sign * (
            candidate_confirmation.close / candidate_sweep.close - 1.0
        )
        directional_rank = 1 + sum(
            value > directional_returns[symbol]
            for peer, value in directional_returns.items()
            if peer != symbol
        )
        top_half_limit = max(1, (len(self.symbols) + 1) // 2)
        relative_recovery = (
            directional_rank <= top_half_limit
            and candidate_move > 0.0
            and candidate_move > median(signed_peer_moves)
        )
        if relative_recovery:
            return decision(
                True,
                "FOLLOWER_FAR_DIRECTIONAL_LEADER_RECOVERY",
                leader,
                peer_returns,
                directional_returns,
            )
        return decision(
            False,
            "FOLLOWER_FAR_PEER_DISAGREEMENT",
            leader,
            peer_returns,
            directional_returns,
        )
