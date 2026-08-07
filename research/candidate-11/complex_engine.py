"""Synchronized four-market SCDAM detector.

This module is detector logic only.  It receives one completed one-minute
observation for each allowed market at the same timestamp, evaluates a
homologous completed-session range, and emits causal FAR/AAC trade plans.
It contains no matching, fill, fee, position, or account logic.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from typing import Mapping

from market_complex import BoundarySide, ComplexObservation, MarketComplex, SourceRange

MINUTE_NS = 60_000_000_000
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class Scenario(StrEnum):
    FAR = "FAR"
    AAC = "AAC"


@dataclass(frozen=True, slots=True)
class BarObs:
    symbol: str
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float

    def __post_init__(self) -> None:
        if self.symbol not in SYMBOLS:
            raise ValueError(f"unsupported symbol: {self.symbol}")
        values = (self.open, self.high, self.low, self.close, self.volume, self.taker_buy_volume)
        if self.ts_ns < 0 or not all(isfinite(v) for v in values):
            raise ValueError("invalid completed observation")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("inconsistent OHLC")
        if self.volume < 0 or not 0 <= self.taker_buy_volume <= self.volume + 1e-9:
            raise ValueError("invalid volume")

    @property
    def span(self) -> float:
        return max(self.high - self.low, 1e-12)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def signed_flow(self) -> float:
        if self.volume <= 0:
            return 0.0
        return max(-1.0, min(1.0, 2.0 * self.taker_buy_volume / self.volume - 1.0))


@dataclass(frozen=True, slots=True)
class AuctionContext:
    source_session: str
    target_session: str
    source_low: float
    source_high: float
    external_low: float
    external_high: float
    valid_until_ns: int

    def __post_init__(self) -> None:
        if not self.source_session or not self.target_session:
            raise ValueError("session labels are required")
        if not 0 < self.source_low < self.source_high:
            raise ValueError("invalid source range")
        if not 0 < self.external_low <= self.source_low:
            raise ValueError("external low must be at or below source low")
        if self.external_high < self.source_high:
            raise ValueError("external high must be at or above source high")
        if self.valid_until_ns <= 0:
            raise ValueError("invalid context expiry")

    @property
    def width(self) -> float:
        return self.source_high - self.source_low


@dataclass(frozen=True, slots=True)
class EngineConfig:
    atr_period: int = 30
    min_raid_fraction: float = 0.02
    max_episode_minutes: int = 90
    entry_gtd_minutes: int = 12
    min_displacement_atr: float = 0.15
    min_flow_alignment: float = 0.03
    min_aac_outside_closes: int = 2
    min_pullback_fraction: float = 0.20
    max_pullback_fraction: float = 0.60
    stop_buffer_atr: float = 0.08
    min_net_r: float = 1.25
    maker_rate: float = 0.0004
    taker_rate: float = 0.0008
    adverse_fill_reserve: float = 0.0002
    funding_reserve: float = 0.00005

    def __post_init__(self) -> None:
        if self.atr_period < 2:
            raise ValueError("atr_period must be >= 2")
        if not 0 < self.min_raid_fraction < 0.5:
            raise ValueError("invalid raid fraction")
        if self.max_episode_minutes < 5 or self.entry_gtd_minutes < 1:
            raise ValueError("invalid episode timing")
        if self.min_displacement_atr <= 0 or self.min_flow_alignment < 0:
            raise ValueError("invalid displacement settings")
        if self.min_aac_outside_closes < 2:
            raise ValueError("AAC requires persistent outside closes")
        if not 0 < self.min_pullback_fraction < self.max_pullback_fraction < 1:
            raise ValueError("invalid pullback fraction")
        if self.stop_buffer_atr <= 0 or self.min_net_r <= 0:
            raise ValueError("invalid risk geometry")


@dataclass(frozen=True, slots=True)
class TradePlan:
    symbol: str
    scenario_id: str
    scenario: Scenario
    direction: Direction
    observed_ts_ns: int
    expected_entry: float
    stop_price: float
    target_price: float
    atr: float
    loss_per_unit: float
    gain_per_unit: float
    net_r: float
    expire_ts_ns: int
    reason_code: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class _Episode:
    scenario_id: str
    symbol: str
    scenario: Scenario
    side: BoundarySide
    context: AuctionContext
    start_ts_ns: int
    last_ts_ns: int
    raid_extreme: float
    state: str
    reclaimed: bool = False
    post_reclaim: list[BarObs] = field(default_factory=list)
    outside_closes: int = 0
    impulse_extreme: float | None = None
    pullback_bars: list[BarObs] = field(default_factory=list)
    pullback_pivot: float | None = None


class ComplexSCDAMEngine:
    """Cross-market nonconfirmation/breadth plus causal local confirmation."""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        self.market_complex = MarketComplex(
            far_min_residual=Decimal("0.18"),
            far_max_peer_raids=1,
            aac_min_outside_closes=3,
            min_penetration=Decimal(str(self.config.min_raid_fraction)),
        )
        self._prev_close: dict[str, float] = {}
        self._tr: dict[str, deque[float]] = {
            symbol: deque(maxlen=self.config.atr_period) for symbol in SYMBOLS
        }
        self._active: dict[str, _Episode] = {}
        # A completed source-session boundary represents one finite liquidity
        # pool. Once that side is first traded through, later candles cannot
        # manufacture a new independent hypothesis from the same consumed pool.
        self._consumed_boundaries: set[tuple[object, ...]] = set()
        self.events: list[dict[str, object]] = []
        self.skip_reasons: dict[str, int] = {}
        self._seq = 0

    def _skip(self, reason: str) -> None:
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1

    def _event(self, symbol: str, ts_ns: int, kind: str, details: dict[str, object]) -> None:
        self._seq += 1
        self.events.append(
            {
                "sequence": self._seq,
                "symbol": symbol,
                "ts_ns": ts_ns,
                "event_type": kind,
                "details": details,
            },
        )

    def _update_atr(self, bar: BarObs) -> None:
        previous = self._prev_close.get(bar.symbol)
        tr = bar.high - bar.low if previous is None else max(
            bar.high - bar.low,
            abs(bar.high - previous),
            abs(bar.low - previous),
        )
        self._tr[bar.symbol].append(tr)
        self._prev_close[bar.symbol] = bar.close

    def _atr(self, symbol: str) -> float | None:
        sample = self._tr[symbol]
        if len(sample) < self.config.atr_period:
            return None
        return sum(sample) / len(sample)

    @staticmethod
    def _same_context(contexts: Mapping[str, AuctionContext]) -> bool:
        keys = {(c.source_session, c.target_session) for c in contexts.values()}
        return len(keys) == 1

    def _complex_observations(
        self,
        snapshot: Mapping[str, BarObs],
        contexts: Mapping[str, AuctionContext],
    ) -> dict[str, ComplexObservation]:
        result: dict[str, ComplexObservation] = {}
        for symbol, bar in snapshot.items():
            context = contexts[symbol]
            result[symbol] = ComplexObservation(
                symbol=symbol,
                ts_ns=bar.ts_ns,
                high=Decimal(str(bar.high)),
                low=Decimal(str(bar.low)),
                close=Decimal(str(bar.close)),
                signed_flow=Decimal(str(bar.signed_flow)),
                source_range=SourceRange(
                    Decimal(str(context.source_low)),
                    Decimal(str(context.source_high)),
                ),
            )
        return result

    @staticmethod
    def _side_trade_through(bar: BarObs, context: AuctionContext, side: BoundarySide, fraction: float) -> bool:
        penetration = context.width * fraction
        if side == BoundarySide.HIGH:
            return bar.high >= context.source_high + penetration
        return bar.low <= context.source_low - penetration

    @staticmethod
    def _outside_close(bar: BarObs, context: AuctionContext, side: BoundarySide) -> bool:
        return bar.close > context.source_high if side == BoundarySide.HIGH else bar.close < context.source_low

    @staticmethod
    def _inside_close(bar: BarObs, context: AuctionContext, side: BoundarySide) -> bool:
        return bar.close < context.source_high if side == BoundarySide.HIGH else bar.close > context.source_low

    @staticmethod
    def _boundary_key(
        symbol: str,
        context: AuctionContext,
        side: BoundarySide,
    ) -> tuple[object, ...]:
        return (
            symbol,
            context.source_session,
            context.target_session,
            context.source_low,
            context.source_high,
            context.valid_until_ns,
            side.value,
        )

    def _start_episode(
        self,
        *,
        symbol: str,
        bar: BarObs,
        context: AuctionContext,
        scenario: Scenario,
        side: BoundarySide,
        evidence: dict[str, object],
    ) -> None:
        boundary_key = self._boundary_key(symbol, context, side)
        if boundary_key in self._consumed_boundaries:
            raise RuntimeError("consumed boundary cannot start a new episode")
        self._consumed_boundaries.add(boundary_key)
        extreme = bar.high if side == BoundarySide.HIGH else bar.low
        episode = _Episode(
            scenario_id=(
                f"{symbol}-{context.source_session}-{context.target_session}-"
                f"{bar.ts_ns}-{side.value}-{scenario.value}"
            ),
            symbol=symbol,
            scenario=scenario,
            side=side,
            context=context,
            start_ts_ns=bar.ts_ns,
            last_ts_ns=bar.ts_ns,
            raid_extreme=extreme,
            state="RAID" if scenario == Scenario.FAR else "OUTSIDE",
            reclaimed=self._inside_close(bar, context, side) if scenario == Scenario.FAR else False,
            outside_closes=1 if scenario == Scenario.AAC and self._outside_close(bar, context, side) else 0,
            impulse_extreme=extreme if scenario == Scenario.AAC else None,
        )
        self._active[symbol] = episode
        self._event(
            symbol,
            bar.ts_ns,
            f"{scenario.value}_EPISODE_STARTED",
            {
                "scenario_id": episode.scenario_id,
                "side": side.value,
                "source_session": context.source_session,
                "target_session": context.target_session,
                **evidence,
            },
        )

    @staticmethod
    def _confirmed_pivot(bars: list[BarObs], direction: Direction) -> float | None:
        if len(bars) < 3:
            return None
        left, center, right = bars[-3:]
        if direction == Direction.SHORT:
            if center.low < left.low and center.low < right.low:
                return center.low
        else:
            if center.high > left.high and center.high > right.high:
                return center.high
        return None

    def _execution_plan(
        self,
        episode: _Episode,
        bar: BarObs,
        *,
        atr: float,
        direction: Direction,
        target: float,
        pivot: float,
        reason: str,
    ) -> TradePlan | None:
        # The confirmation bar is the displacement/reacceleration bar.  The
        # first passive retracement rests at 50% of its body, preserving causal
        # confirmation while recovering price.
        if direction == Direction.LONG:
            body = bar.close - bar.open
            if body <= 0:
                self._skip("DISPLACEMENT_BODY_WRONG_SIGN")
                return None
            entry = bar.close - 0.50 * body
            stop = (
                episode.context.source_high - self.config.stop_buffer_atr * atr
                if episode.scenario == Scenario.AAC
                else episode.raid_extreme - self.config.stop_buffer_atr * atr
            )
            risk = entry - stop
            gain = target - entry
            passive = entry < bar.close
        else:
            body = bar.open - bar.close
            if body <= 0:
                self._skip("DISPLACEMENT_BODY_WRONG_SIGN")
                return None
            entry = bar.close + 0.50 * body
            stop = (
                episode.context.source_low + self.config.stop_buffer_atr * atr
                if episode.scenario == Scenario.AAC
                else episode.raid_extreme + self.config.stop_buffer_atr * atr
            )
            risk = stop - entry
            gain = entry - target
            passive = entry > bar.close
        if not passive or risk <= 0 or gain <= 0:
            self._skip("NON_CAUSAL_PRICE_ORDER")
            return None

        # Expected loss reserves maker entry, taker stop, an adverse fill term,
        # and funding uncertainty.  Target assumes passive execution.
        loss = (
            risk
            + entry * self.config.maker_rate
            + stop * self.config.taker_rate
            + entry * self.config.adverse_fill_reserve
            + entry * self.config.funding_reserve
        )
        net_gain = gain - entry * self.config.maker_rate - target * self.config.maker_rate
        net_r = net_gain / loss
        if net_gain <= 0 or net_r < self.config.min_net_r:
            self._skip("INSUFFICIENT_COSTED_STRUCTURAL_R")
            self._event(
                episode.symbol,
                bar.ts_ns,
                "PLAN_REJECTED",
                {
                    "scenario_id": episode.scenario_id,
                    "reason": "INSUFFICIENT_COSTED_STRUCTURAL_R",
                    "net_r": net_r,
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                },
            )
            return None
        plan = TradePlan(
            symbol=episode.symbol,
            scenario_id=episode.scenario_id,
            scenario=episode.scenario,
            direction=direction,
            observed_ts_ns=bar.ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            atr=atr,
            loss_per_unit=loss,
            gain_per_unit=net_gain,
            net_r=net_r,
            expire_ts_ns=bar.ts_ns + self.config.entry_gtd_minutes * MINUTE_NS,
            reason_code=reason,
            details={
                "source_session": episode.context.source_session,
                "target_session": episode.context.target_session,
                "side": episode.side.value,
                "raid_extreme": episode.raid_extreme,
                "causal_pivot": pivot,
            },
        )
        self._event(
            episode.symbol,
            bar.ts_ns,
            "TRADE_PLAN_CONFIRMED",
            {
                "scenario_id": episode.scenario_id,
                "scenario": episode.scenario.value,
                "direction": direction.value,
                "entry": entry,
                "stop": stop,
                "target": target,
                "net_r": net_r,
            },
        )
        return plan

    def _update_far(self, episode: _Episode, bar: BarObs, atr: float) -> TradePlan | None:
        if episode.side == BoundarySide.HIGH:
            episode.raid_extreme = max(episode.raid_extreme, bar.high)
            direction = Direction.SHORT
            if self._inside_close(bar, episode.context, episode.side):
                episode.reclaimed = True
            if episode.reclaimed:
                episode.post_reclaim.append(bar)
                pivot = self._confirmed_pivot(episode.post_reclaim, direction)
                if pivot is not None:
                    episode.pullback_pivot = pivot
                if (
                    episode.pullback_pivot is not None
                    and bar.close < episode.pullback_pivot
                    and bar.open - bar.close >= self.config.min_displacement_atr * atr
                    and bar.signed_flow <= -self.config.min_flow_alignment
                ):
                    return self._execution_plan(
                        episode,
                        bar,
                        atr=atr,
                        direction=direction,
                        target=episode.context.source_low,
                        pivot=episode.pullback_pivot,
                        reason="IDIOSYNCRATIC_RAID_RECLAIM_CAUSAL_MSS",
                    )
        else:
            episode.raid_extreme = min(episode.raid_extreme, bar.low)
            direction = Direction.LONG
            if self._inside_close(bar, episode.context, episode.side):
                episode.reclaimed = True
            if episode.reclaimed:
                episode.post_reclaim.append(bar)
                pivot = self._confirmed_pivot(episode.post_reclaim, direction)
                if pivot is not None:
                    episode.pullback_pivot = pivot
                if (
                    episode.pullback_pivot is not None
                    and bar.close > episode.pullback_pivot
                    and bar.close - bar.open >= self.config.min_displacement_atr * atr
                    and bar.signed_flow >= self.config.min_flow_alignment
                ):
                    return self._execution_plan(
                        episode,
                        bar,
                        atr=atr,
                        direction=direction,
                        target=episode.context.source_high,
                        pivot=episode.pullback_pivot,
                        reason="IDIOSYNCRATIC_RAID_RECLAIM_CAUSAL_MSS",
                    )
        return None

    def _update_aac(self, episode: _Episode, bar: BarObs, atr: float) -> TradePlan | None:
        side = episode.side
        if side == BoundarySide.HIGH:
            if self._outside_close(bar, episode.context, side):
                episode.outside_closes += 1
                if episode.pullback_pivot is None:
                    episode.impulse_extreme = max(episode.impulse_extreme or bar.high, bar.high)
            else:
                episode.outside_closes = 0
            if episode.outside_closes >= self.config.min_aac_outside_closes and not episode.pullback_bars:
                episode.state = "IMPULSE_FROZEN"
            if episode.state == "IMPULSE_FROZEN":
                assert episode.impulse_extreme is not None
                distance = max(episode.impulse_extreme - episode.context.source_high, 1e-12)
                fraction = (episode.impulse_extreme - bar.low) / distance
                if (
                    episode.context.source_high < bar.low
                    and self.config.min_pullback_fraction <= fraction <= self.config.max_pullback_fraction
                ):
                    episode.pullback_bars.append(bar)
                elif episode.pullback_bars:
                    episode.pullback_bars.append(bar)
                pivot = self._confirmed_pivot(episode.pullback_bars, Direction.SHORT)
                if pivot is not None:
                    episode.pullback_pivot = pivot
                    episode.state = "PULLBACK_CONFIRMED"
            if (
                episode.state == "PULLBACK_CONFIRMED"
                and episode.impulse_extreme is not None
                and bar.close > episode.impulse_extreme
                and bar.close - bar.open >= self.config.min_displacement_atr * atr
                and bar.signed_flow >= self.config.min_flow_alignment
            ):
                return self._execution_plan(
                    episode,
                    bar,
                    atr=atr,
                    direction=Direction.LONG,
                    target=episode.context.external_high,
                    pivot=episode.pullback_pivot or episode.context.source_high,
                    reason="BROAD_ACCEPTANCE_PULLBACK_REACCELERATION",
                )
        else:
            if self._outside_close(bar, episode.context, side):
                episode.outside_closes += 1
                if episode.pullback_pivot is None:
                    episode.impulse_extreme = min(episode.impulse_extreme or bar.low, bar.low)
            else:
                episode.outside_closes = 0
            if episode.outside_closes >= self.config.min_aac_outside_closes and not episode.pullback_bars:
                episode.state = "IMPULSE_FROZEN"
            if episode.state == "IMPULSE_FROZEN":
                assert episode.impulse_extreme is not None
                distance = max(episode.context.source_low - episode.impulse_extreme, 1e-12)
                fraction = (bar.high - episode.impulse_extreme) / distance
                if (
                    bar.high < episode.context.source_low
                    and self.config.min_pullback_fraction <= fraction <= self.config.max_pullback_fraction
                ):
                    episode.pullback_bars.append(bar)
                elif episode.pullback_bars:
                    episode.pullback_bars.append(bar)
                pivot = self._confirmed_pivot(episode.pullback_bars, Direction.LONG)
                if pivot is not None:
                    episode.pullback_pivot = pivot
                    episode.state = "PULLBACK_CONFIRMED"
            if (
                episode.state == "PULLBACK_CONFIRMED"
                and episode.impulse_extreme is not None
                and bar.close < episode.impulse_extreme
                and bar.open - bar.close >= self.config.min_displacement_atr * atr
                and bar.signed_flow <= -self.config.min_flow_alignment
            ):
                return self._execution_plan(
                    episode,
                    bar,
                    atr=atr,
                    direction=Direction.SHORT,
                    target=episode.context.external_low,
                    pivot=episode.pullback_pivot or episode.context.source_low,
                    reason="BROAD_ACCEPTANCE_PULLBACK_REACCELERATION",
                )
        return None

    def on_snapshot(
        self,
        snapshot: Mapping[str, BarObs],
        contexts: Mapping[str, AuctionContext],
    ) -> list[TradePlan]:
        if not snapshot:
            return []
        timestamps = {bar.ts_ns for bar in snapshot.values()}
        if len(timestamps) != 1:
            raise ValueError("snapshot must contain one completed timestamp")
        ts_ns = next(iter(timestamps))
        if set(snapshot) - set(SYMBOLS):
            raise ValueError("snapshot contains unsupported symbols")
        for bar in snapshot.values():
            self._update_atr(bar)

        # Contexts must be homologous across the synchronized markets.  Outside
        # target windows, no detector state is created.
        eligible = {
            symbol: context
            for symbol, context in contexts.items()
            if symbol in snapshot and ts_ns <= context.valid_until_ns
        }
        plans: list[TradePlan] = []

        # Update existing episodes first.  This makes the causal path depend only
        # on information available before evaluating a new raid at this minute.
        for symbol, episode in list(self._active.items()):
            bar = snapshot.get(symbol)
            if bar is None:
                continue
            atr = self._atr(symbol)
            if (
                atr is None
                or ts_ns > episode.context.valid_until_ns
                or ts_ns - episode.start_ts_ns > self.config.max_episode_minutes * MINUTE_NS
            ):
                self._skip("EPISODE_EXPIRED")
                self._event(symbol, ts_ns, "EPISODE_TERMINAL", {"scenario_id": episode.scenario_id, "reason": "EPISODE_EXPIRED"})
                self._active.pop(symbol, None)
                continue
            plan = self._update_far(episode, bar, atr) if episode.scenario == Scenario.FAR else self._update_aac(episode, bar, atr)
            episode.last_ts_ns = ts_ns
            if plan is not None:
                plans.append(plan)
                self._active.pop(symbol, None)

        if len(eligible) < 3 or not self._same_context(eligible):
            return plans
        observations = self._complex_observations(
            {symbol: snapshot[symbol] for symbol in eligible},
            eligible,
        )

        for symbol, context in eligible.items():
            if symbol in self._active:
                continue
            bar = snapshot[symbol]
            high = self._side_trade_through(bar, context, BoundarySide.HIGH, self.config.min_raid_fraction)
            low = self._side_trade_through(bar, context, BoundarySide.LOW, self.config.min_raid_fraction)
            if high and low:
                self._skip("AMBIGUOUS_BOTH_SIDES_RAIDED")
                continue
            for side, crossed in ((BoundarySide.HIGH, high), (BoundarySide.LOW, low)):
                if not crossed:
                    continue
                boundary_key = self._boundary_key(symbol, context, side)
                if boundary_key in self._consumed_boundaries:
                    self._skip("SOURCE_BOUNDARY_ALREADY_CONSUMED")
                    continue
                evidence = self.market_complex.evaluate(observations, symbol=symbol, side=side)
                common = {
                    "residual": str(evidence.residual),
                    "peer_median_extreme_location": str(evidence.peer_median_extreme_location),
                    "same_side_raids": evidence.same_side_raids,
                    "same_side_outside_closes": evidence.same_side_outside_closes,
                    "reason_codes": list(evidence.reason_codes),
                }
                if evidence.far_nonconfirmation:
                    self._start_episode(
                        symbol=symbol,
                        bar=bar,
                        context=context,
                        scenario=Scenario.FAR,
                        side=side,
                        evidence=common,
                    )
                elif evidence.aac_breadth_confirmation:
                    self._start_episode(
                        symbol=symbol,
                        bar=bar,
                        context=context,
                        scenario=Scenario.AAC,
                        side=side,
                        evidence=common,
                    )
                else:
                    self._skip("COMPLEX_EVIDENCE_INSUFFICIENT")
        return plans
