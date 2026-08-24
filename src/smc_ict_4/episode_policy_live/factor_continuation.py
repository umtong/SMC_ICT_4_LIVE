"""Causal flow state for the local-auction continuation family.

The later RE1 branch separates responsibilities more cleanly than the earlier
common-factor AND policy: a completed fifteen-minute liquidity break owns local
direction, a flow-validated five-minute engulf leaves the pullback location,
and the synchronized market factor vetoes an opposing opportunity.  The order
block is therefore never allowed to vote on direction by itself.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Mapping

from .domain import Bar, LiquidityBoundary, stable_id


@dataclass(frozen=True, slots=True)
class FlowObservation:
    time_ns: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    trade_count: int
    signed_taker_quote: float
    body: float
    active: bool
    directed: bool
    material_progress: bool


class CausalFlowAnalyzer:
    """Compare one closed minute only with its prior sixty closed minutes."""

    BASELINE_BARS = 60
    HISTORY_BARS = 1440

    def __init__(self, tick_size: float) -> None:
        self.tick_size = float(tick_size)
        self._raw: deque[tuple[float, float, float, float, float]] = deque(
            maxlen=self.HISTORY_BARS,
        )
        self.history: deque[FlowObservation] = deque(maxlen=self.HISTORY_BARS)
        self.last_observation: FlowObservation | None = None

    def observe(self, bar: Bar) -> FlowObservation | None:
        quote = float(bar.quote_volume)
        trades = int(bar.trade_count)
        taker_buy = float(bar.taker_buy_quote_volume)
        if (
            quote <= 0.0
            or trades <= 0
            or taker_buy < 0.0
            or taker_buy > quote * (1.0 + 1e-9)
        ):
            self.last_observation = None
            return None
        body = float(bar.close - bar.open)
        price_range = max(float(bar.high - bar.low), self.tick_size)
        signed_delta = 2.0 * taker_buy - quote
        trade_size = quote / trades
        prior = list(self._raw)[-self.BASELINE_BARS :]
        self._raw.append(
            (quote, abs(signed_delta), abs(body), price_range, trade_size),
        )
        if len(prior) < self.BASELINE_BARS:
            self.last_observation = None
            return None
        observation = FlowObservation(
            time_ns=bar.close_time_ns,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            quote_volume=quote,
            trade_count=trades,
            signed_taker_quote=signed_delta,
            body=body,
            active=quote >= median(item[0] for item in prior),
            directed=abs(signed_delta) >= median(item[1] for item in prior),
            material_progress=abs(body) >= max(
                median(item[2] for item in prior),
                self.tick_size,
            ),
        )
        self.history.append(observation)
        self.last_observation = observation
        return observation

    def between(self, after_ns: int, through_ns: int) -> list[FlowObservation]:
        return [
            item
            for item in self.history
            if after_ns < item.time_ns <= through_ns
        ]


@dataclass(frozen=True, slots=True)
class CommonFactorState:
    side: str
    event_time_ns: int
    event_midpoints: Mapping[str, float]
    agreeing_symbols: tuple[str, ...]
    sequence: int


class CommonFactorTracker:
    """BTC+ETH and three-of-four completed-minute initiative hysteresis."""

    SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")

    def __init__(self) -> None:
        self.state: CommonFactorState | None = None

    @staticmethod
    def _coherent_side(observation: FlowObservation | None) -> str | None:
        if (
            observation is None
            or not observation.active
            or not observation.directed
            or not observation.material_progress
        ):
            return None
        if observation.body > 0.0 and observation.signed_taker_quote > 0.0:
            return "LONG"
        if observation.body < 0.0 and observation.signed_taker_quote < 0.0:
            return "SHORT"
        return None

    @staticmethod
    def _beyond(side: str, close: float, midpoint: float) -> bool:
        return close > midpoint if side == "LONG" else close < midpoint

    def observe(
        self,
        bars: Mapping[str, Bar],
        observations: Mapping[str, FlowObservation | None],
    ) -> CommonFactorState | None:
        if set(bars) != set(self.SYMBOLS) or set(observations) != set(self.SYMBOLS):
            raise ValueError("common factor requires the complete four-market minute")
        sides = {
            symbol: self._coherent_side(observations[symbol])
            for symbol in self.SYMBOLS
        }
        for side in ("LONG", "SHORT"):
            agreeing = tuple(
                sorted(symbol for symbol, value in sides.items() if value == side)
            )
            if (
                sides["BTCUSDT"] == side
                and sides["ETHUSDT"] == side
                and len(agreeing) >= 3
            ):
                prior = self.state
                self.state = CommonFactorState(
                    side=side,
                    event_time_ns=next(iter(bars.values())).close_time_ns,
                    event_midpoints={
                        symbol: (bar.open + bar.close) / 2.0
                        for symbol, bar in bars.items()
                    },
                    agreeing_symbols=agreeing,
                    sequence=(
                        prior.sequence + 1
                        if prior is not None and prior.side == side
                        else 1
                    ),
                )
                return self.state
        state = self.state
        if state is None:
            return None
        held = {
            symbol
            for symbol, bar in bars.items()
            if self._beyond(
                state.side,
                bar.close,
                float(state.event_midpoints[symbol]),
            )
        }
        if "BTCUSDT" in held and "ETHUSDT" in held and len(held) >= 3:
            return state
        self.state = None
        return None


@dataclass(slots=True)
class LocalAuctionContinuationSetup:
    episode_id: str
    symbol: str
    side: str
    source: LiquidityBoundary
    source_invalidation: float
    source_strength_ratio: float
    local_direction_pivot_id: str
    local_direction_event_time_ns: int
    destination: LiquidityBoundary | None
    objective_commit_time_ns: int
    objective_revision_count: int = 0
    objective_rearm_after_ns: int | None = None
    formation_factor_side: str | None = None
    formation_factor_event_time_ns: int | None = None
    formation_factor_sequence: int | None = None
    formation_factor_agreeing_symbols: tuple[str, ...] = ()
    first_touch_time_ns: int | None = None
    touch_high: float | None = None
    touch_low: float | None = None


def five_minute_engulfing_ob(
    previous: Bar,
    current: Bar,
    *,
    tick_size: float,
) -> tuple[LiquidityBoundary, float, float] | None:
    """Return a source-defined 2x body engulf as a location, not a vote."""

    previous_low = min(previous.open, previous.close)
    previous_high = max(previous.open, previous.close)
    current_low = min(current.open, current.close)
    current_high = max(current.open, current.close)
    previous_body = previous_high - previous_low
    current_body = current_high - current_low
    if previous_body <= 0.0 or current_body <= 0.0:
        return None
    bullish = (
        previous.close < previous.open
        and current.close > current.open
        and current_low <= previous_low
        and current_high >= previous_high
    )
    bearish = (
        previous.close > previous.open
        and current.close < current.open
        and current_low <= previous_low
        and current_high >= previous_high
    )
    ratio = current_body / max(previous_body, tick_size)
    if (not bullish and not bearish) or ratio + 1e-12 < 2.0:
        return None
    side = "LOW" if bullish else "HIGH"
    location_side = "LONG" if bullish else "SHORT"
    source_id = stable_id(
        current.symbol,
        previous.open_time_ns,
        current.open_time_ns,
        location_side,
        prefix="FACTOR_OB:",
    )
    source = LiquidityBoundary(
        boundary_id=source_id,
        symbol=current.symbol,
        side=side,
        kind="FLOW_VALIDATED_5M_ORDER_BLOCK_LOCATION",
        timeframe_minutes=5,
        observed_time_ns=current.close_time_ns,
        lower=previous_low,
        upper=previous_high,
        price=(previous_low + previous_high) / 2.0,
        strength=ratio,
    )
    invalidation = (
        min(previous.low, current.low) - tick_size
        if bullish
        else max(previous.high, current.high) + tick_size
    )
    return source, invalidation, ratio


__all__ = [
    "CausalFlowAnalyzer",
    "CommonFactorState",
    "CommonFactorTracker",
    "FlowObservation",
    "LocalAuctionContinuationSetup",
    "five_minute_engulfing_ob",
]
