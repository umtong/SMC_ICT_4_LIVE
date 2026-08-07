"""Causal price-discovery approval for Candidate 11.

The gate is deliberately binary. It never sizes a position, changes the fixed
risk fraction, infers a fill, or calculates PnL. It approves only complete SCDAM
plans using synchronized observations that were already visible at confirmation.

The distinction between liquidity leadership and directional price discovery is
explicit:

* quote-notional leadership identifies where liquidity is concentrated;
* trailing direction-signed return ranks identify who is leading the proposed
  move;
* sweep-to-confirmation returns identify whether that move actually recovered;
* a volatility-normalized directional-path score rejects counter-trend bounces
  inside an unresolved market-wide one-sided auction.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import isfinite, log, sqrt
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
    directional_trend_scores: dict[str, float]
    candidate_event_move: float | None
    peer_event_median: float | None

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
            "directional_trend_scores": dict(sorted(self.directional_trend_scores.items())),
            "candidate_event_move": self.candidate_event_move,
            "peer_event_median": self.peer_event_median,
        }


class MarketLeadershipGate:
    """Binary causal approval; it never changes quantity or risk."""

    def __init__(
        self,
        symbols: tuple[str, ...],
        *,
        lookback_bars: int = 1440,
        max_history_bars: int | None = None,
        severe_adverse_trend_score: float = -1.5,
    ) -> None:
        if len(symbols) < 3 or len(set(symbols)) != len(symbols):
            raise ValueError("leadership requires at least three unique symbols")
        if lookback_bars < 2:
            raise ValueError("lookback_bars must be at least two")
        if not isfinite(severe_adverse_trend_score) or severe_adverse_trend_score >= 0:
            raise ValueError("severe_adverse_trend_score must be finite and negative")
        history_bars = max_history_bars or max(lookback_bars * 2, lookback_bars + 720)
        if history_bars < lookback_bars:
            raise ValueError("max_history_bars cannot be shorter than lookback")
        self.symbols = tuple(symbols)
        self.lookback_bars = int(lookback_bars)
        self.severe_adverse_trend_score = float(severe_adverse_trend_score)
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

    def _window_at(self, symbol: str, ts_ns: int) -> list[MarketPoint] | None:
        start_exclusive = ts_ns - self.lookback_bars * MINUTE_NS
        sample = [
            point for point in self._history[symbol]
            if start_exclusive < point.ts_ns <= ts_ns
        ]
        if len(sample) < self.lookback_bars or sample[-1].ts_ns != ts_ns:
            return None
        return sample

    def _leader_at(self, ts_ns: int) -> str | None:
        totals: dict[str, float] = {}
        for symbol in self.symbols:
            sample = self._window_at(symbol, ts_ns)
            if sample is None:
                return None
            totals[symbol] = sum(point.quote_notional for point in sample)
        # Deterministic lexical tie-break only; leadership is never hard-coded.
        return sorted(self.symbols, key=lambda symbol: (-totals[symbol], symbol))[0]

    def _directional_state_at(
        self,
        ts_ns: int,
        direction: str,
    ) -> tuple[dict[str, float], dict[str, float]] | None:
        """Return trailing direction-signed returns and path-normalized drift.

        The trend score is signed cumulative log return divided by the square
        root of realized one-minute squared log returns. It is dimensionless and
        therefore portable across instruments with different price and volatility
        scales. A large negative value means the proposed reversal direction is
        fighting a still unresolved one-sided auction.
        """
        sign = 1.0 if direction == "LONG" else -1.0
        returns: dict[str, float] = {}
        trend_scores: dict[str, float] = {}
        for symbol in self.symbols:
            sample = self._window_at(symbol, ts_ns)
            if sample is None:
                return None
            closes = [point.close for point in sample]
            returns[symbol] = sign * (closes[-1] / closes[0] - 1.0)
            log_returns = [log(curr / prev) for prev, curr in zip(closes, closes[1:])]
            realized_path = sqrt(sum(value * value for value in log_returns))
            signed_cumulative = sign * log(closes[-1] / closes[0])
            trend_scores[symbol] = signed_cumulative / max(realized_path, 1e-12)
        return returns, trend_scores

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
            directional_trend_scores: dict[str, float] | None = None,
            candidate_event_move: float | None = None,
            peer_event_median: float | None = None,
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
                directional_trend_scores=directional_trend_scores or {},
                candidate_event_move=candidate_event_move,
                peer_event_median=peer_event_median,
            )

        if confirmation_ts_ns != self._last_batch_ts:
            return decision(False, "ASYNCHRONOUS_CONFIRMATION", None)
        if sweep_ts_ns < 0 or sweep_ts_ns >= confirmation_ts_ns:
            return decision(False, "INVALID_SWEEP_CONFIRMATION_ORDER", None)

        leader = self._leader_at(sweep_ts_ns)
        directional_state = self._directional_state_at(sweep_ts_ns, direction)
        if leader is None or directional_state is None:
            return decision(False, "INSUFFICIENT_LEADERSHIP_HISTORY", None)
        directional_returns, trend_scores = directional_state

        candidate_sweep = self._point_at(symbol, sweep_ts_ns)
        candidate_confirmation = self._point_at(symbol, confirmation_ts_ns)
        if candidate_sweep is None or candidate_confirmation is None:
            return decision(
                False,
                "MISSING_SYNCHRONIZED_CANDIDATE_SNAPSHOT",
                leader,
                directional_returns=directional_returns,
                directional_trend_scores=trend_scores,
            )

        peer_returns: dict[str, float] = {}
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
                    trend_scores,
                )
            peer_returns[peer] = confirmation.close / sweep.close - 1.0

        sign = 1.0 if direction == "LONG" else -1.0
        signed_peer_moves = [sign * value for value in peer_returns.values()]
        candidate_move = sign * (
            candidate_confirmation.close / candidate_sweep.close - 1.0
        )
        peer_median = median(signed_peer_moves)
        directional_rank = 1 + sum(
            value > directional_returns[symbol]
            for peer, value in directional_returns.items()
            if peer != symbol
        )
        top_half_limit = max(1, (len(self.symbols) + 1) // 2)
        directionally_supported = directional_rank <= top_half_limit
        event_recovered = candidate_move > 0.0 and candidate_move > peer_median

        if scenario == "AAC":
            if symbol != leader:
                return decision(
                    False,
                    "FOLLOWER_AAC_WITHOUT_LEADERSHIP",
                    leader,
                    peer_returns,
                    directional_returns,
                    trend_scores,
                    candidate_move,
                    peer_median,
                )
            # Continuation is a stronger claim than reversal. It must already be
            # directionally led by the liquidity leader and then outperform the
            # peer median from sweep to confirmation; either condition alone is
            # insufficient evidence that the new price area was accepted.
            if not (directionally_supported and event_recovered):
                return decision(
                    False,
                    "AAC_WITHOUT_DIRECTIONAL_ACCEPTANCE",
                    leader,
                    peer_returns,
                    directional_returns,
                    trend_scores,
                    candidate_move,
                    peer_median,
                )
            return decision(
                True,
                "LEADER_AAC_DIRECTIONAL_ACCEPTANCE",
                leader,
                peer_returns,
                directional_returns,
                trend_scores,
                candidate_move,
                peer_median,
            )

        if symbol == leader:
            if directionally_supported:
                reason = "LEADER_DIRECTIONAL_ALIGNMENT"
            elif event_recovered:
                reason = "LEADER_EVENT_RECOVERY"
            else:
                return decision(
                    False,
                    "LEADER_DIRECTIONAL_DISAGREEMENT",
                    leader,
                    peer_returns,
                    directional_returns,
                    trend_scores,
                    candidate_move,
                    peer_median,
                )
            return decision(
                True,
                reason,
                leader,
                peer_returns,
                directional_returns,
                trend_scores,
                candidate_move,
                peer_median,
            )

        all_peers_aligned = all(value > 0.0 for value in signed_peer_moves)
        if all_peers_aligned:
            market_trend_score = median(trend_scores.values())
            severe_adverse_auction = (
                trend_scores[symbol] <= self.severe_adverse_trend_score
                and market_trend_score <= self.severe_adverse_trend_score
            )
            if severe_adverse_auction:
                return decision(
                    False,
                    "FOLLOWER_FAR_UNRESOLVED_ADVERSE_AUCTION",
                    leader,
                    peer_returns,
                    directional_returns,
                    trend_scores,
                    candidate_move,
                    peer_median,
                )
            return decision(
                True,
                "FOLLOWER_FAR_UNANIMOUS_PEERS",
                leader,
                peer_returns,
                directional_returns,
                trend_scores,
                candidate_move,
                peer_median,
            )

        relative_recovery = (
            directionally_supported
            and event_recovered
            and any(value > 0.0 for value in signed_peer_moves)
        )
        if relative_recovery:
            return decision(
                True,
                "FOLLOWER_FAR_DIRECTIONAL_LEADER_RECOVERY",
                leader,
                peer_returns,
                directional_returns,
                trend_scores,
                candidate_move,
                peer_median,
            )
        return decision(
            False,
            "FOLLOWER_FAR_PEER_DISAGREEMENT",
            leader,
            peer_returns,
            directional_returns,
            trend_scores,
            candidate_move,
            peer_median,
        )
