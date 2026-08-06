"""Five-minute displacement, imbalance rebalance, and continuation engine."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

from causal_clock import source_bar_datetime
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(frozen=True, slots=True)
class _AggregateBar:
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
    def flow_ratio(self) -> float:
        return (2.0 * self.taker_buy_volume - self.volume) / self.volume if self.volume > 0.0 else 0.0


@dataclass(slots=True)
class _DisplacementEpisode:
    scenario_id: str
    direction: str
    state: str
    created_index: int
    created_ts_ns: int
    expires_index: int
    zone_low: float
    zone_high: float
    impulse_low: float
    impulse_high: float
    impulse_origin: float
    projection_target: float
    atr5: float
    displacement_body_atr: float
    displacement_body_fraction: float
    displacement_flow_ratio: float
    displacement_relative_volume: float
    zone_mode: str
    touch_index: int | None = None
    rebalance_extreme: float | None = None


class FiveMinuteDisplacementRebalanceEngine:
    """Trade a causal displacement-rebalance-response sequence.

    Non-overlapping completed five-minute auctions identify aggressive
    displacement.  The engine then waits on one-minute completed bars for a
    rebalance into either a strict FVG or the displacement-body origin, followed
    by a separate response bar.  The displacement bar can never trigger its own
    entry.
    """

    def __init__(self, params: Mapping[str, Any]):
        self.params = dict(params)
        self._period = int(self.params.get("dirc_aggregate_minutes", 5))
        if self._period <= 1 or 60 % self._period != 0:
            raise ValueError("dirc_aggregate_minutes must divide one hour and exceed one minute")
        self._history: list[_AggregateBar] = []
        self._true_ranges: list[float] = []
        self._volumes: list[float] = []
        self._current: dict[str, Any] | None = None
        self._episode: _DisplacementEpisode | None = None
        self._sequence = 0

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        signal: ScenarioSignal | None = None
        if self._episode is not None:
            advanced = self._advance_episode(snapshot, allow_new=allow_new)
            transitions.extend(advanced.transitions)
            signal = advanced.signal

        completed = self._accumulate(snapshot)
        if completed is not None:
            if self._episode is None and signal is None and allow_new:
                started = self._start_episode(completed, snapshot)
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

    def _accumulate(self, snapshot: PrimitiveSnapshot) -> _AggregateBar | None:
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
        completed = _AggregateBar(
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

    def _append_history(self, bar: _AggregateBar) -> None:
        previous_close = self._history[-1].close if self._history else bar.close
        true_range = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
        self._history.append(bar)
        self._true_ranges.append(true_range)
        self._volumes.append(bar.volume)
        capacity = max(
            40,
            int(self.params.get("dirc_atr_bars", 12)) + 4,
            int(self.params.get("dirc_volume_bars", 12)) + 4,
        )
        if len(self._history) > capacity:
            self._history = self._history[-capacity:]
            self._true_ranges = self._true_ranges[-capacity:]
            self._volumes = self._volumes[-capacity:]

    def _start_episode(
        self,
        bar: _AggregateBar,
        snapshot: PrimitiveSnapshot,
    ) -> ScenarioTransition | None:
        atr_bars = int(self.params.get("dirc_atr_bars", 12))
        volume_bars = int(self.params.get("dirc_volume_bars", 12))
        required = max(2, atr_bars, volume_bars)
        if len(self._history) < required:
            return None
        atr5 = sum(self._true_ranges[-atr_bars:]) / atr_bars
        baseline_volume = median(self._volumes[-volume_bars:])
        if atr5 <= 0.0 or baseline_volume <= 0.0:
            return None
        candle_range = bar.high - bar.low
        body = abs(bar.close - bar.open)
        if candle_range <= 0.0:
            return None
        body_atr = body / atr5
        body_fraction = body / candle_range
        relative_volume = bar.volume / baseline_volume
        close_location = (bar.close - bar.low) / candle_range
        flow = bar.flow_ratio
        minimum_body_atr = float(self.params.get("dirc_displacement_body_atr", 0.80))
        minimum_body_fraction = float(self.params.get("dirc_displacement_body_fraction", 0.65))
        minimum_relative_volume = float(self.params.get("dirc_displacement_relative_volume", 1.15))
        minimum_flow = float(self.params.get("dirc_displacement_flow_ratio", 0.08))
        outer_close = float(self.params.get("dirc_displacement_close_location", 0.75))
        direction: str | None = None
        if (
            bar.close > bar.open
            and body_atr >= minimum_body_atr
            and body_fraction >= minimum_body_fraction
            and relative_volume >= minimum_relative_volume
            and flow >= minimum_flow
            and close_location >= outer_close
        ):
            direction = "LONG"
        elif (
            bar.close < bar.open
            and body_atr >= minimum_body_atr
            and body_fraction >= minimum_body_fraction
            and relative_volume >= minimum_relative_volume
            and flow <= -minimum_flow
            and close_location <= 1.0 - outer_close
        ):
            direction = "SHORT"
        if direction is None:
            return None

        previous = self._history[-1]
        two_back = self._history[-2]
        zone_mode = str(self.params.get("dirc_zone_mode", "STRICT_FVG")).upper()
        if zone_mode == "STRICT_FVG":
            if direction == "LONG":
                if bar.low <= two_back.high:
                    return None
                zone_low, zone_high = two_back.high, bar.low
            else:
                if bar.high >= two_back.low:
                    return None
                zone_low, zone_high = bar.high, two_back.low
        elif zone_mode == "DISPLACEMENT_BODY_ORIGIN":
            midpoint = (bar.open + bar.close) / 2.0
            zone_low, zone_high = sorted((bar.open, midpoint))
        else:
            raise ValueError(f"unsupported dirc_zone_mode: {zone_mode}")

        impulse_low = min(two_back.low, previous.low, bar.low)
        impulse_high = max(two_back.high, previous.high, bar.high)
        impulse_range = impulse_high - impulse_low
        if impulse_range <= 0.0:
            return None
        projection_fraction = float(self.params.get("dirc_projection_fraction", 1.0))
        if direction == "LONG":
            impulse_origin = impulse_low
            projection_target = bar.high + projection_fraction * impulse_range
        else:
            impulse_origin = impulse_high
            projection_target = bar.low - projection_fraction * impulse_range
        self._sequence += 1
        scenario_id = f"DIRC-{bar.end_ts_ns}-{self._sequence:06d}"
        self._episode = _DisplacementEpisode(
            scenario_id=scenario_id,
            direction=direction,
            state="IMBALANCE_REBALANCE_WAIT",
            created_index=snapshot.index,
            created_ts_ns=bar.end_ts_ns,
            expires_index=snapshot.index + int(self.params.get("dirc_rebalance_bars", 24)),
            zone_low=zone_low,
            zone_high=zone_high,
            impulse_low=impulse_low,
            impulse_high=impulse_high,
            impulse_origin=impulse_origin,
            projection_target=projection_target,
            atr5=atr5,
            displacement_body_atr=body_atr,
            displacement_body_fraction=body_fraction,
            displacement_flow_ratio=flow,
            displacement_relative_volume=relative_volume,
            zone_mode=zone_mode,
        )
        return self._transition(
            self._episode,
            "IDLE",
            "IMBALANCE_REBALANCE_WAIT",
            "FIVE_MINUTE_DISPLACEMENT_CONFIRMED",
            bar.close,
            {
                "direction": direction,
                "zone_mode": zone_mode,
                "zone_low": zone_low,
                "zone_high": zone_high,
                "impulse_low": impulse_low,
                "impulse_high": impulse_high,
                "atr5": atr5,
                "body_atr5": body_atr,
                "body_fraction": body_fraction,
                "flow_ratio": flow,
                "relative_volume": relative_volume,
                "projection_target": projection_target,
            },
        )

    def _advance_episode(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        observation = snapshot.observation
        if snapshot.index > episode.expires_index:
            return self._reset(snapshot, episode, "REBALANCE_WINDOW_EXPIRED")
        if episode.direction == "LONG":
            if observation.close < episode.impulse_origin:
                return self._reset(snapshot, episode, "BULLISH_IMPULSE_ORIGIN_INVALIDATED")
            if episode.state == "IMBALANCE_REBALANCE_WAIT" and observation.high >= episode.projection_target:
                return self._reset(snapshot, episode, "DISPLACEMENT_OBJECTIVE_REACHED_WITHOUT_REBALANCE")
            touched = observation.low <= episode.zone_high and observation.high >= episode.zone_low
            if episode.state == "IMBALANCE_REBALANCE_WAIT" and touched:
                episode.state = "REBALANCE_TOUCHED"
                episode.touch_index = snapshot.index
                episode.rebalance_extreme = observation.low
                return ScenarioStep(
                    transitions=(
                        self._transition(
                            episode,
                            "IMBALANCE_REBALANCE_WAIT",
                            "REBALANCE_TOUCHED",
                            "BULLISH_IMBALANCE_ZONE_REBALANCED",
                            observation.close,
                            {"touch_low": observation.low},
                        ),
                    ),
                )
            if episode.state == "REBALANCE_TOUCHED":
                episode.rebalance_extreme = min(float(episode.rebalance_extreme), observation.low)
                if snapshot.index <= int(episode.touch_index):
                    return ScenarioStep()
                confirmed = (
                    observation.close > observation.open
                    and observation.close > episode.zone_high
                    and snapshot.body_atr >= float(self.params.get("dirc_response_body_atr_1m", 0.12))
                    and snapshot.flow_ratio >= float(self.params.get("dirc_response_flow_ratio", 0.0))
                    and snapshot.close_location >= float(self.params.get("dirc_response_close_location", 0.55))
                )
                if confirmed:
                    if not allow_new:
                        return self._reset(snapshot, episode, "ENTRY_SLOT_UNAVAILABLE_AT_RESPONSE")
                    return self._emit_signal(snapshot, episode)
        else:
            if observation.close > episode.impulse_origin:
                return self._reset(snapshot, episode, "BEARISH_IMPULSE_ORIGIN_INVALIDATED")
            if episode.state == "IMBALANCE_REBALANCE_WAIT" and observation.low <= episode.projection_target:
                return self._reset(snapshot, episode, "DISPLACEMENT_OBJECTIVE_REACHED_WITHOUT_REBALANCE")
            touched = observation.high >= episode.zone_low and observation.low <= episode.zone_high
            if episode.state == "IMBALANCE_REBALANCE_WAIT" and touched:
                episode.state = "REBALANCE_TOUCHED"
                episode.touch_index = snapshot.index
                episode.rebalance_extreme = observation.high
                return ScenarioStep(
                    transitions=(
                        self._transition(
                            episode,
                            "IMBALANCE_REBALANCE_WAIT",
                            "REBALANCE_TOUCHED",
                            "BEARISH_IMBALANCE_ZONE_REBALANCED",
                            observation.close,
                            {"touch_high": observation.high},
                        ),
                    ),
                )
            if episode.state == "REBALANCE_TOUCHED":
                episode.rebalance_extreme = max(float(episode.rebalance_extreme), observation.high)
                if snapshot.index <= int(episode.touch_index):
                    return ScenarioStep()
                confirmed = (
                    observation.close < observation.open
                    and observation.close < episode.zone_low
                    and snapshot.body_atr >= float(self.params.get("dirc_response_body_atr_1m", 0.12))
                    and snapshot.flow_ratio <= -float(self.params.get("dirc_response_flow_ratio", 0.0))
                    and snapshot.close_location <= 1.0 - float(self.params.get("dirc_response_close_location", 0.55))
                )
                if confirmed:
                    if not allow_new:
                        return self._reset(snapshot, episode, "ENTRY_SLOT_UNAVAILABLE_AT_RESPONSE")
                    return self._emit_signal(snapshot, episode)
        return ScenarioStep()

    def _emit_signal(self, snapshot: PrimitiveSnapshot, episode: _DisplacementEpisode) -> ScenarioStep:
        observation = snapshot.observation
        buffer_value = float(self.params.get("dirc_stop_buffer_atr5", 0.05)) * episode.atr5
        if episode.direction == "LONG":
            stop = episode.impulse_origin - buffer_value
            candidates = [
                (snapshot.upper_fast, "PRIOR_FAST_BUYSIDE_LIQUIDITY"),
                (snapshot.upper_slow, "PRIOR_SLOW_BUYSIDE_LIQUIDITY"),
                (episode.projection_target, "DISPLACEMENT_RANGE_PROJECTION"),
            ]
        else:
            stop = episode.impulse_origin + buffer_value
            candidates = [
                (snapshot.lower_fast, "PRIOR_FAST_SELLSIDE_LIQUIDITY"),
                (snapshot.lower_slow, "PRIOR_SLOW_SELLSIDE_LIQUIDITY"),
                (episode.projection_target, "DISPLACEMENT_RANGE_PROJECTION"),
            ]
        target = self._select_target(episode.direction, observation.close, stop, candidates)
        if target is None:
            return self._reset(snapshot, episode, "NO_STRUCTURAL_OBJECTIVE_WITH_SUFFICIENT_SPACE")
        target_price, target_reason = target
        transition = self._transition(
            episode,
            "REBALANCE_TOUCHED",
            "ENTRY_ARMED",
            "IMBALANCE_REBALANCE_RESPONSE_CONFIRMED",
            observation.close,
            {
                "direction": episode.direction,
                "stop_price": stop,
                "target_price": target_price,
                "target_reason": target_reason,
                "rebalance_extreme": episode.rebalance_extreme,
                "zone_low": episode.zone_low,
                "zone_high": episode.zone_high,
            },
        )
        signal = ScenarioSignal(
            scenario_id=episode.scenario_id,
            family="DIRC",
            direction=episode.direction,
            observed_ts_ns=observation.ts_ns,
            reference_entry=observation.close,
            stop_price=stop,
            target_price=target_price,
            target_reason=target_reason,
            atr=snapshot.atr,
            liquidity_level=(episode.zone_low + episode.zone_high) / 2.0,
            details={
                "zone_mode": episode.zone_mode,
                "zone_low": episode.zone_low,
                "zone_high": episode.zone_high,
                "impulse_low": episode.impulse_low,
                "impulse_high": episode.impulse_high,
                "atr5": episode.atr5,
                "displacement_body_atr": episode.displacement_body_atr,
                "displacement_body_fraction": episode.displacement_body_fraction,
                "displacement_flow_ratio": episode.displacement_flow_ratio,
                "displacement_relative_volume": episode.displacement_relative_volume,
                "rebalance_extreme": episode.rebalance_extreme,
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
        minimum_rr = float(self.params.get("minimum_structural_rr", 1.25))
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
        episode: _DisplacementEpisode,
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
        episode: _DisplacementEpisode,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="DIRC_STATE_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )
