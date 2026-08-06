"""Accepted higher-timeframe expansion and delayed boundary-retest continuation."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

from causal_clock import source_bar_datetime
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(frozen=True, slots=True)
class _AuctionBar:
    start_ts_ns: int
    end_ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    trades: int

    @property
    def candle_range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def flow_ratio(self) -> float:
        return (2.0 * self.taker_buy_volume - self.volume) / self.volume if self.volume > 0.0 else 0.0


@dataclass(slots=True)
class _AcceptedExpansion:
    scenario_id: str
    direction: str
    state: str
    boundary: float
    expansion_open: float
    expansion_high: float
    expansion_low: float
    expansion_close: float
    expansion_range: float
    atr_htf: float
    created_index: int
    created_ts_ns: int
    expires_index: int
    compressed_source: bool
    source_range_ratio: float
    expansion_range_atr: float
    expansion_body_fraction: float
    expansion_flow_ratio: float
    expansion_relative_volume: float
    touch_index: int | None = None
    touch_extreme: float | None = None
    touch_opposite_extreme: float | None = None


class AcceptedExpansionPullbackEngine:
    """Trade an accepted auction breakout only after a later causal retest.

    A completed 30/60-minute auction must close beyond the preceding completed
    auction with aligned range expansion, body, volume and taker flow.  The
    breakout boundary is then frozen.  A later one-minute bar may touch it, but
    only a separate subsequent response bar can arm an entry.
    """

    def __init__(self, params: Mapping[str, Any]):
        self.params = dict(params)
        self._period = int(self.params.get("aepr_period_minutes", 60))
        if self._period < 15 or 1440 % self._period != 0:
            raise ValueError("aepr_period_minutes must be at least 15 and divide one UTC day")
        self._history: list[_AuctionBar] = []
        self._true_ranges: list[float] = []
        self._ranges: list[float] = []
        self._volumes: list[float] = []
        self._current: dict[str, Any] | None = None
        self._episode: _AcceptedExpansion | None = None
        self._sequence = 0

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        signal: ScenarioSignal | None = None
        if self._episode is not None:
            advanced = self._advance(snapshot, allow_new=allow_new)
            transitions.extend(advanced.transitions)
            signal = advanced.signal

        completed = self._accumulate(snapshot)
        if completed is not None:
            if self._episode is None and signal is None and allow_new:
                started = self._start_expansion(completed, snapshot)
                if started is not None:
                    transitions.append(started)
            self._append_history(completed)
        return ScenarioStep(transitions=tuple(transitions), signal=signal)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        episode = self._episode
        if episode is None:
            return ScenarioStep()
        transition = self._transition(
            episode,
            episode.state,
            "RESET",
            reason,
            snapshot.observation.close,
            {"aborted": True},
        )
        self._episode = None
        return ScenarioStep(transitions=(transition,))

    def _accumulate(self, snapshot: PrimitiveSnapshot) -> _AuctionBar | None:
        observation = snapshot.observation
        source = source_bar_datetime(observation.ts_ns)
        source_minute = int(source.timestamp() // 60)
        bucket = source_minute // self._period
        position = source_minute % self._period
        if self._current is None or int(self._current["bucket"]) != bucket:
            self._current = {
                "bucket": bucket,
                "start_ts_ns": observation.ts_ns,
                "open": observation.open,
                "high": observation.high,
                "low": observation.low,
                "close": observation.close,
                "volume": observation.volume,
                "taker_buy_volume": observation.taker_buy_volume,
                "trades": observation.trades,
            }
        else:
            current = self._current
            current["high"] = max(float(current["high"]), observation.high)
            current["low"] = min(float(current["low"]), observation.low)
            current["close"] = observation.close
            current["volume"] = float(current["volume"]) + observation.volume
            current["taker_buy_volume"] = float(current["taker_buy_volume"]) + observation.taker_buy_volume
            current["trades"] = int(current["trades"]) + observation.trades
        if position != self._period - 1:
            return None
        assert self._current is not None
        current = self._current
        completed = _AuctionBar(
            start_ts_ns=int(current["start_ts_ns"]),
            end_ts_ns=observation.ts_ns,
            open=float(current["open"]),
            high=float(current["high"]),
            low=float(current["low"]),
            close=float(current["close"]),
            volume=float(current["volume"]),
            taker_buy_volume=float(current["taker_buy_volume"]),
            trades=int(current["trades"]),
        )
        self._current = None
        return completed

    def _append_history(self, bar: _AuctionBar) -> None:
        previous_close = self._history[-1].close if self._history else bar.close
        true_range = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
        self._history.append(bar)
        self._true_ranges.append(true_range)
        self._ranges.append(bar.candle_range)
        self._volumes.append(bar.volume)
        capacity = max(
            32,
            int(self.params.get("aepr_atr_bars", 12)) + 4,
            int(self.params.get("aepr_volume_bars", 12)) + 4,
            int(self.params.get("aepr_compression_bars", 12)) + 4,
        )
        if len(self._history) > capacity:
            self._history = self._history[-capacity:]
            self._true_ranges = self._true_ranges[-capacity:]
            self._ranges = self._ranges[-capacity:]
            self._volumes = self._volumes[-capacity:]

    def _start_expansion(
        self,
        bar: _AuctionBar,
        snapshot: PrimitiveSnapshot,
    ) -> ScenarioTransition | None:
        atr_bars = int(self.params.get("aepr_atr_bars", 12))
        volume_bars = int(self.params.get("aepr_volume_bars", 12))
        compression_bars = int(self.params.get("aepr_compression_bars", 12))
        required = max(2, atr_bars, volume_bars, compression_bars)
        if len(self._history) < required:
            return None
        previous = self._history[-1]
        atr_htf = sum(self._true_ranges[-atr_bars:]) / atr_bars
        median_volume = median(self._volumes[-volume_bars:])
        median_range = median(self._ranges[-compression_bars:])
        if atr_htf <= 0.0 or median_volume <= 0.0 or median_range <= 0.0 or bar.candle_range <= 0.0:
            return None

        range_atr = bar.candle_range / atr_htf
        body_fraction = bar.body / bar.candle_range
        relative_volume = bar.volume / median_volume
        close_location = (bar.close - bar.low) / bar.candle_range
        flow = bar.flow_ratio
        source_range_ratio = previous.candle_range / median_range
        compression_required = bool(self.params.get("aepr_require_source_compression", False))
        compressed_source = source_range_ratio <= float(self.params.get("aepr_source_compression_ratio", 0.85))
        if compression_required and not compressed_source:
            return None

        minimum_range_atr = float(self.params.get("aepr_expansion_range_atr", 0.90))
        minimum_body_fraction = float(self.params.get("aepr_expansion_body_fraction", 0.55))
        minimum_relative_volume = float(self.params.get("aepr_expansion_relative_volume", 1.0))
        minimum_flow = float(self.params.get("aepr_expansion_flow_ratio", 0.04))
        outer_close = float(self.params.get("aepr_expansion_close_location", 0.72))
        acceptance = float(self.params.get("aepr_acceptance_close_atr", 0.03)) * atr_htf
        direction: str | None = None
        boundary = 0.0
        if (
            bar.close > previous.high + acceptance
            and bar.close > bar.open
            and range_atr >= minimum_range_atr
            and body_fraction >= minimum_body_fraction
            and relative_volume >= minimum_relative_volume
            and flow >= minimum_flow
            and close_location >= outer_close
        ):
            direction = "LONG"
            boundary = previous.high
        elif (
            bar.close < previous.low - acceptance
            and bar.close < bar.open
            and range_atr >= minimum_range_atr
            and body_fraction >= minimum_body_fraction
            and relative_volume >= minimum_relative_volume
            and flow <= -minimum_flow
            and close_location <= 1.0 - outer_close
        ):
            direction = "SHORT"
            boundary = previous.low
        if direction is None:
            return None

        self._sequence += 1
        scenario_id = f"AEPR-{bar.end_ts_ns}-{self._sequence:06d}"
        lifetime_periods = float(self.params.get("aepr_bias_lifetime_periods", 3.0))
        self._episode = _AcceptedExpansion(
            scenario_id=scenario_id,
            direction=direction,
            state="ACCEPTED_EXPANSION_RETEST_WAIT",
            boundary=boundary,
            expansion_open=bar.open,
            expansion_high=bar.high,
            expansion_low=bar.low,
            expansion_close=bar.close,
            expansion_range=bar.candle_range,
            atr_htf=atr_htf,
            created_index=snapshot.index,
            created_ts_ns=bar.end_ts_ns,
            expires_index=snapshot.index + max(1, int(self._period * lifetime_periods)),
            compressed_source=compressed_source,
            source_range_ratio=source_range_ratio,
            expansion_range_atr=range_atr,
            expansion_body_fraction=body_fraction,
            expansion_flow_ratio=flow,
            expansion_relative_volume=relative_volume,
        )
        return self._transition(
            self._episode,
            "IDLE",
            "ACCEPTED_EXPANSION_RETEST_WAIT",
            "COMPLETED_AUCTION_ACCEPTED_PRIOR_RANGE_BREAK",
            bar.close,
            {
                "period_minutes": self._period,
                "direction": direction,
                "boundary": boundary,
                "expansion_open": bar.open,
                "expansion_high": bar.high,
                "expansion_low": bar.low,
                "expansion_close": bar.close,
                "atr_htf": atr_htf,
                "range_atr": range_atr,
                "body_fraction": body_fraction,
                "flow_ratio": flow,
                "relative_volume": relative_volume,
                "compressed_source": compressed_source,
                "source_range_ratio": source_range_ratio,
            },
        )

    def _advance(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        observation = snapshot.observation
        if snapshot.index > episode.expires_index:
            return self._reset(snapshot, episode, "ACCEPTED_EXPANSION_BIAS_EXPIRED")
        invalidation_fraction = float(self.params.get("aepr_bias_invalidation_fraction", 0.50))
        if episode.direction == "LONG":
            invalidation = episode.expansion_open + invalidation_fraction * max(
                episode.boundary - episode.expansion_open,
                0.0,
            )
            if observation.close < invalidation:
                return self._reset(snapshot, episode, "BULLISH_ACCEPTED_EXPANSION_INVALIDATED")
        else:
            invalidation = episode.expansion_open - invalidation_fraction * max(
                episode.expansion_open - episode.boundary,
                0.0,
            )
            if observation.close > invalidation:
                return self._reset(snapshot, episode, "BEARISH_ACCEPTED_EXPANSION_INVALIDATED")

        band = float(self.params.get("aepr_retest_band_atr", 0.12)) * episode.atr_htf
        if episode.state == "ACCEPTED_EXPANSION_RETEST_WAIT":
            touched = observation.low <= episode.boundary + band and observation.high >= episode.boundary - band
            if touched:
                episode.state = "ACCEPTED_BOUNDARY_RETEST_TOUCHED"
                episode.touch_index = snapshot.index
                episode.touch_extreme = observation.low if episode.direction == "LONG" else observation.high
                episode.touch_opposite_extreme = observation.high if episode.direction == "LONG" else observation.low
                return ScenarioStep(
                    transitions=(
                        self._transition(
                            episode,
                            "ACCEPTED_EXPANSION_RETEST_WAIT",
                            "ACCEPTED_BOUNDARY_RETEST_TOUCHED",
                            "ACCEPTED_AUCTION_BOUNDARY_RETESTED",
                            observation.close,
                            {
                                "boundary": episode.boundary,
                                "band": band,
                                "touch_low": observation.low,
                                "touch_high": observation.high,
                            },
                        ),
                    ),
                )
            return ScenarioStep()

        assert episode.touch_index is not None
        if episode.direction == "LONG":
            episode.touch_extreme = min(float(episode.touch_extreme), observation.low)
            episode.touch_opposite_extreme = max(float(episode.touch_opposite_extreme), observation.high)
            if observation.close < episode.expansion_open:
                return self._reset(snapshot, episode, "BULLISH_RETEST_LOST_EXPANSION_ORIGIN")
            confirmed = (
                snapshot.index > episode.touch_index
                and observation.close > episode.boundary
                and observation.close > observation.open
                and snapshot.body_atr >= float(self.params.get("aepr_response_body_atr_1m", 0.12))
                and snapshot.flow_ratio >= float(self.params.get("aepr_response_flow_ratio", 0.0))
                and snapshot.close_location >= float(self.params.get("aepr_response_close_location", 0.55))
            )
            if confirmed and str(self.params.get("aepr_response_mode", "BODY_FLOW")).upper() == "BREAK_TOUCH_EXTREME":
                confirmed = observation.close > float(episode.touch_opposite_extreme)
        else:
            episode.touch_extreme = max(float(episode.touch_extreme), observation.high)
            episode.touch_opposite_extreme = min(float(episode.touch_opposite_extreme), observation.low)
            if observation.close > episode.expansion_open:
                return self._reset(snapshot, episode, "BEARISH_RETEST_LOST_EXPANSION_ORIGIN")
            confirmed = (
                snapshot.index > episode.touch_index
                and observation.close < episode.boundary
                and observation.close < observation.open
                and snapshot.body_atr >= float(self.params.get("aepr_response_body_atr_1m", 0.12))
                and snapshot.flow_ratio <= -float(self.params.get("aepr_response_flow_ratio", 0.0))
                and snapshot.close_location <= 1.0 - float(self.params.get("aepr_response_close_location", 0.55))
            )
            if confirmed and str(self.params.get("aepr_response_mode", "BODY_FLOW")).upper() == "BREAK_TOUCH_EXTREME":
                confirmed = observation.close < float(episode.touch_opposite_extreme)
        if not confirmed:
            return ScenarioStep()
        if not allow_new:
            return self._reset(snapshot, episode, "ENTRY_SLOT_UNAVAILABLE_AT_RETEST_RESPONSE")
        return self._emit(snapshot, episode)

    def _emit(self, snapshot: PrimitiveSnapshot, episode: _AcceptedExpansion) -> ScenarioStep:
        observation = snapshot.observation
        buffer_value = float(self.params.get("aepr_stop_buffer_atr", 0.05)) * episode.atr_htf
        extension = float(self.params.get("aepr_extension_fraction", 0.75)) * episode.expansion_range
        if episode.direction == "LONG":
            stop = min(float(episode.touch_extreme), episode.boundary - buffer_value) - buffer_value
            candidates = [
                (episode.expansion_high, "ACCEPTED_EXPANSION_HIGH"),
                (snapshot.upper_fast, "PRIOR_FAST_BUYSIDE_LIQUIDITY"),
                (snapshot.upper_slow, "PRIOR_SLOW_BUYSIDE_LIQUIDITY"),
                (episode.expansion_high + extension, "ACCEPTED_EXPANSION_PROJECTION"),
            ]
        else:
            stop = max(float(episode.touch_extreme), episode.boundary + buffer_value) + buffer_value
            candidates = [
                (episode.expansion_low, "ACCEPTED_EXPANSION_LOW"),
                (snapshot.lower_fast, "PRIOR_FAST_SELLSIDE_LIQUIDITY"),
                (snapshot.lower_slow, "PRIOR_SLOW_SELLSIDE_LIQUIDITY"),
                (episode.expansion_low - extension, "ACCEPTED_EXPANSION_PROJECTION"),
            ]
        target = self._select_target(episode.direction, observation.close, stop, candidates)
        if target is None:
            return self._reset(snapshot, episode, "NO_ACCEPTED_EXPANSION_OBJECTIVE_WITH_SUFFICIENT_SPACE")
        target_price, target_reason = target
        transition = self._transition(
            episode,
            "ACCEPTED_BOUNDARY_RETEST_TOUCHED",
            "ENTRY_ARMED",
            "ACCEPTED_BOUNDARY_RETEST_RESPONSE_CONFIRMED",
            observation.close,
            {
                "direction": episode.direction,
                "stop_price": stop,
                "target_price": target_price,
                "target_reason": target_reason,
                "boundary": episode.boundary,
                "touch_extreme": episode.touch_extreme,
                "response_mode": self.params.get("aepr_response_mode", "BODY_FLOW"),
            },
        )
        signal = ScenarioSignal(
            scenario_id=episode.scenario_id,
            family="AEPR",
            direction=episode.direction,
            observed_ts_ns=observation.ts_ns,
            reference_entry=observation.close,
            stop_price=stop,
            target_price=target_price,
            target_reason=target_reason,
            atr=snapshot.atr,
            liquidity_level=episode.boundary,
            details={
                "period_minutes": self._period,
                "boundary": episode.boundary,
                "expansion_open": episode.expansion_open,
                "expansion_high": episode.expansion_high,
                "expansion_low": episode.expansion_low,
                "expansion_close": episode.expansion_close,
                "atr_htf": episode.atr_htf,
                "compressed_source": episode.compressed_source,
                "source_range_ratio": episode.source_range_ratio,
                "expansion_range_atr": episode.expansion_range_atr,
                "expansion_body_fraction": episode.expansion_body_fraction,
                "expansion_flow_ratio": episode.expansion_flow_ratio,
                "expansion_relative_volume": episode.expansion_relative_volume,
                "touch_extreme": episode.touch_extreme,
            },
        )
        self._episode = None
        return ScenarioStep(transitions=(transition,), signal=signal)

    def _select_target(
        self,
        direction: str,
        entry: float,
        stop: float,
        candidates: list[tuple[float | None, str]],
    ) -> tuple[float, str] | None:
        risk = abs(entry - stop)
        if risk <= 0.0:
            return None
        minimum_rr = float(self.params.get("minimum_structural_rr", 1.10))
        valid: list[tuple[float, str]] = []
        for price, reason in candidates:
            if price is None:
                continue
            reward = float(price) - entry if direction == "LONG" else entry - float(price)
            if reward > 0.0 and reward / risk >= minimum_rr:
                valid.append((float(price), reason))
        valid.sort(key=lambda item: abs(item[0] - entry))
        return valid[0] if valid else None

    def _reset(
        self,
        snapshot: PrimitiveSnapshot,
        episode: _AcceptedExpansion,
        reason: str,
    ) -> ScenarioStep:
        transition = self._transition(
            episode,
            episode.state,
            "RESET",
            reason,
            snapshot.observation.close,
            {},
        )
        self._episode = None
        return ScenarioStep(transitions=(transition,))

    @staticmethod
    def _transition(
        episode: _AcceptedExpansion,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="AEPR_STATE_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )
