"""Causal volume-clock aggressive-flow impact bifurcation.

Buckets close after a volume budget fixed from prior completed one-minute
volumes. Two same-direction buckets are interpreted by marginal price impact:
persistent efficient impact supports continuation; collapsing impact without
extension supports exhaustion. A later completed response is still required.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(slots=True)
class _Bucket:
    start_index: int
    end_index: int
    start_ts_ns: int
    end_ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    signed_volume: float
    atr: float
    close_location: float
    direction: str
    flow_ratio: float
    displacement_atr: float
    efficiency: float


@dataclass(slots=True)
class _Episode:
    scenario_id: str
    state: str
    direction: str
    first: _Bucket
    second: _Bucket | None
    created_index: int
    retest_index: int | None = None
    retest_extreme: float | None = None


class VolumeClockImpactBifurcationEngine:
    def __init__(self, params: Mapping[str, Any]):
        self.params = dict(params)
        self._minute_volumes: list[float] = []
        self._bucket_start_index: int | None = None
        self._bucket_start_ts_ns: int | None = None
        self._bucket_open: float | None = None
        self._bucket_high: float | None = None
        self._bucket_low: float | None = None
        self._bucket_close: float | None = None
        self._bucket_volume = 0.0
        self._bucket_signed_volume = 0.0
        self._bucket_budget: float | None = None
        self._efficiency_history: list[float] = []
        self._episode: _Episode | None = None
        self._sequence = 0
        self._cooldown_until = -1

    @staticmethod
    def _quantile(values: list[float], fraction: float) -> float:
        ordered = sorted(values)
        if not ordered:
            return 0.0
        position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    def _transition(
        self,
        episode: _Episode,
        previous: str,
        next_state: str,
        reason: str,
        snapshot: PrimitiveSnapshot,
        details: Mapping[str, Any] | None = None,
    ) -> ScenarioTransition:
        second = episode.second
        return ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="VCIB_TRANSITION",
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=snapshot.observation.close,
            details={
                "direction": episode.direction,
                "first_flow_ratio": episode.first.flow_ratio,
                "first_efficiency": episode.first.efficiency,
                "second_flow_ratio": None if second is None else second.flow_ratio,
                "second_efficiency": None if second is None else second.efficiency,
                **dict(details or {}),
            },
        )

    def _start_bucket(self, snapshot: PrimitiveSnapshot) -> bool:
        lookback = int(self.params.get("vcib_volume_lookback", 60))
        minimum = int(self.params.get("vcib_minimum_volume_history", 30))
        history = self._minute_volumes[-lookback:]
        if len(history) < minimum:
            return False
        baseline = median(history)
        if baseline <= 0.0:
            return False
        self._bucket_budget = baseline * float(self.params.get("vcib_target_minutes", 3.0))
        self._bucket_start_index = snapshot.index
        self._bucket_start_ts_ns = snapshot.observation.ts_ns
        self._bucket_open = snapshot.observation.open
        self._bucket_high = snapshot.observation.high
        self._bucket_low = snapshot.observation.low
        self._bucket_close = snapshot.observation.close
        self._bucket_volume = 0.0
        self._bucket_signed_volume = 0.0
        return True

    def _finish_bucket(self, snapshot: PrimitiveSnapshot) -> _Bucket:
        assert self._bucket_start_index is not None
        assert self._bucket_start_ts_ns is not None
        assert self._bucket_open is not None
        assert self._bucket_high is not None
        assert self._bucket_low is not None
        assert self._bucket_close is not None
        flow_ratio = self._bucket_signed_volume / self._bucket_volume if self._bucket_volume > 0.0 else 0.0
        displacement_atr = abs(self._bucket_close - self._bucket_open) / snapshot.atr if snapshot.atr > 0.0 else 0.0
        efficiency = displacement_atr / max(abs(flow_ratio), 1e-9)
        direction = "UP" if self._bucket_close > self._bucket_open else "DOWN"
        price_range = self._bucket_high - self._bucket_low
        close_location = 0.5 if price_range <= 0.0 else (self._bucket_close - self._bucket_low) / price_range
        bucket = _Bucket(
            start_index=self._bucket_start_index,
            end_index=snapshot.index,
            start_ts_ns=self._bucket_start_ts_ns,
            end_ts_ns=snapshot.observation.ts_ns,
            open=self._bucket_open,
            high=self._bucket_high,
            low=self._bucket_low,
            close=self._bucket_close,
            volume=self._bucket_volume,
            signed_volume=self._bucket_signed_volume,
            atr=snapshot.atr,
            close_location=close_location,
            direction=direction,
            flow_ratio=flow_ratio,
            displacement_atr=displacement_atr,
            efficiency=efficiency,
        )
        self._bucket_start_index = None
        self._bucket_budget = None
        return bucket

    def _ingest(self, snapshot: PrimitiveSnapshot) -> _Bucket | None:
        if self._bucket_start_index is None and not self._start_bucket(snapshot):
            self._minute_volumes.append(snapshot.observation.volume)
            return None
        observation = snapshot.observation
        self._bucket_high = max(float(self._bucket_high), observation.high)
        self._bucket_low = min(float(self._bucket_low), observation.low)
        self._bucket_close = observation.close
        self._bucket_volume += observation.volume
        self._bucket_signed_volume += snapshot.flow_ratio * observation.volume
        self._minute_volumes.append(observation.volume)
        if self._bucket_volume < float(self._bucket_budget):
            return None
        return self._finish_bucket(snapshot)

    def _is_impulse(self, bucket: _Bucket) -> bool:
        flow_floor = float(self.params.get("vcib_flow_floor", 0.10))
        displacement_floor = float(self.params.get("vcib_displacement_atr", 0.25))
        location = float(self.params.get("vcib_close_location", 0.65))
        directional_location = bucket.close_location >= location if bucket.direction == "UP" else bucket.close_location <= 1.0 - location
        aligned = bucket.flow_ratio >= flow_floor if bucket.direction == "UP" else bucket.flow_ratio <= -flow_floor
        return aligned and directional_location and bucket.displacement_atr >= displacement_floor

    def _classify_second(self, episode: _Episode, bucket: _Bucket, snapshot: PrimitiveSnapshot) -> tuple[ScenarioTransition, ...]:
        if not self._is_impulse(bucket) or bucket.direction != episode.direction:
            transition = self._transition(episode, episode.state, "RESET", "SECOND_VOLUME_BUCKET_DID_NOT_CONFIRM_DIRECTIONAL_FLOW", snapshot)
            self._episode = None
            return (transition,)
        history = self._efficiency_history[-int(self.params.get("vcib_efficiency_history", 40)):]
        minimum = int(self.params.get("vcib_minimum_efficiency_history", 20))
        if len(history) < minimum:
            self._efficiency_history.append(bucket.efficiency)
            return ()
        use_impact = bool(self.params.get("vcib_use_impact_efficiency", True))
        continuation_floor = self._quantile(history, float(self.params.get("vcib_continuation_quantile", 0.50)))
        exhaustion_ceiling = self._quantile(history, float(self.params.get("vcib_exhaustion_quantile", 0.25)))
        extended = bucket.high > episode.first.high if episode.direction == "UP" else bucket.low < episode.first.low
        continuation = extended and (bucket.efficiency >= continuation_floor if use_impact else True)
        exhaustion = (not extended) and (bucket.efficiency <= exhaustion_ceiling if use_impact else True)
        self._efficiency_history.append(bucket.efficiency)
        episode.second = bucket
        episode.created_index = snapshot.index
        if continuation:
            episode.state = "CONTINUATION_CONTEXT"
            return (
                self._transition(
                    episode,
                    "IMPULSE_BUCKET",
                    "CONTINUATION_CONTEXT",
                    "SEQUENTIAL_VOLUME_BUCKETS_RETAINED_MARGINAL_PRICE_IMPACT",
                    snapshot,
                    {"continuation_floor": continuation_floor},
                ),
            )
        if exhaustion:
            episode.state = "EXHAUSTION_CONTEXT"
            return (
                self._transition(
                    episode,
                    "IMPULSE_BUCKET",
                    "EXHAUSTION_CONTEXT",
                    "SEQUENTIAL_AGGRESSIVE_FLOW_FAILED_TO_EXTEND_PRICE",
                    snapshot,
                    {"exhaustion_ceiling": exhaustion_ceiling},
                ),
            )
        transition = self._transition(episode, "IMPULSE_BUCKET", "RESET", "SECOND_VOLUME_BUCKET_RESPONSE_AMBIGUOUS", snapshot)
        self._episode = None
        return (transition,)

    def _signal(self, episode: _Episode, snapshot: PrimitiveSnapshot, *, branch: str) -> ScenarioSignal | None:
        assert episode.second is not None
        first, second = episode.first, episode.second
        close = snapshot.observation.close
        atr = max(second.atr, 1e-9)
        buffer = float(self.params.get("vcib_stop_buffer_atr", 0.08)) * atr
        combined_high = max(first.high, second.high)
        combined_low = min(first.low, second.low)
        if branch == "EXHAUSTION":
            if episode.direction == "UP":
                direction, stop, target = "SHORT", combined_high + buffer, first.open
            else:
                direction, stop, target = "LONG", combined_low - buffer, first.open
            family, reason = "VCIB_E", "FIRST_VOLUME_BUCKET_ORIGIN"
        else:
            projection = float(self.params.get("vcib_projection_fraction", 0.75))
            combined_range = max(combined_high - combined_low, atr)
            if episode.direction == "UP":
                direction = "LONG"
                stop = min((combined_high + combined_low) / 2.0, episode.retest_extreme or combined_low) - buffer
                target = combined_high + projection * combined_range
            else:
                direction = "SHORT"
                stop = max((combined_high + combined_low) / 2.0, episode.retest_extreme or combined_high) + buffer
                target = combined_low - projection * combined_range
            family, reason = "VCIB_C", "SEQUENTIAL_BUCKET_RANGE_EXTENSION"
        risk = abs(close - stop)
        reward = target - close if direction == "LONG" else close - target
        if risk <= 0.0 or reward <= 0.0 or reward / risk < float(self.params.get("minimum_structural_rr", 0.75)):
            return None
        return ScenarioSignal(
            scenario_id=episode.scenario_id,
            family=family,
            direction=direction,
            observed_ts_ns=snapshot.observation.ts_ns,
            reference_entry=close,
            stop_price=stop,
            target_price=target,
            target_reason=reason,
            atr=atr,
            liquidity_level=(combined_high + combined_low) / 2.0,
            details={
                "first_bucket_end_ts_ns": first.end_ts_ns,
                "second_bucket_end_ts_ns": second.end_ts_ns,
                "first_efficiency": first.efficiency,
                "second_efficiency": second.efficiency,
            },
        )

    def _advance_episode(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        episode = self._episode
        if episode is None or episode.second is None or snapshot.index <= episode.created_index:
            return ScenarioStep()
        if snapshot.index - episode.created_index > int(self.params.get("vcib_response_bars", 10)):
            transition = self._transition(episode, episode.state, "RESET", "VOLUME_CLOCK_RESPONSE_EXPIRED", snapshot)
            self._episode = None
            self._cooldown_until = snapshot.index + int(self.params.get("vcib_cooldown_bars", 2))
            return ScenarioStep(transitions=(transition,))
        observation = snapshot.observation
        midpoint = (min(episode.first.low, episode.second.low) + max(episode.first.high, episode.second.high)) / 2.0
        body_atr = abs(observation.close - observation.open) / max(episode.second.atr, 1e-9)
        body_floor = float(self.params.get("vcib_response_body_atr", 0.12))
        flow_floor = float(self.params.get("vcib_response_flow_ratio", 0.03))
        location = float(self.params.get("vcib_response_close_location", 0.62))
        if episode.state == "EXHAUSTION_CONTEXT":
            if episode.direction == "UP":
                confirmed = observation.close < midpoint and observation.close < observation.open and body_atr >= body_floor and snapshot.flow_ratio <= -flow_floor and snapshot.close_location <= 1.0 - location
            else:
                confirmed = observation.close > midpoint and observation.close > observation.open and body_atr >= body_floor and snapshot.flow_ratio >= flow_floor and snapshot.close_location >= location
            if confirmed:
                transition = self._transition(episode, "EXHAUSTION_CONTEXT", "EXHAUSTION_CONFIRMED", "MARGINAL_IMPACT_COLLAPSE_CONFIRMED_BY_OPPOSITE_RESPONSE", snapshot)
                signal = self._signal(episode, snapshot, branch="EXHAUSTION") if allow_new else None
                self._episode = None
                self._cooldown_until = snapshot.index + int(self.params.get("vcib_cooldown_bars", 2))
                return ScenarioStep(transitions=(transition,), signal=signal)
            return ScenarioStep()
        if episode.direction == "UP":
            held = observation.low <= episode.second.close and observation.close > midpoint
            resumed = episode.state == "CONTINUATION_RETEST" and observation.close > observation.open and body_atr >= body_floor and snapshot.flow_ratio >= flow_floor and snapshot.close_location >= location
        else:
            held = observation.high >= episode.second.close and observation.close < midpoint
            resumed = episode.state == "CONTINUATION_RETEST" and observation.close < observation.open and body_atr >= body_floor and snapshot.flow_ratio <= -flow_floor and snapshot.close_location <= 1.0 - location
        if episode.state == "CONTINUATION_CONTEXT" and held:
            episode.state = "CONTINUATION_RETEST"
            episode.retest_index = snapshot.index
            episode.retest_extreme = observation.low if episode.direction == "UP" else observation.high
            return ScenarioStep(
                transitions=(
                    self._transition(episode, "CONTINUATION_CONTEXT", "CONTINUATION_RETEST", "SEQUENTIAL_IMPACT_RETEST_HELD_COMBINED_MIDPOINT", snapshot),
                ),
            )
        if resumed and episode.retest_index is not None and snapshot.index > episode.retest_index:
            transition = self._transition(episode, "CONTINUATION_RETEST", "CONTINUATION_CONFIRMED", "SEQUENTIAL_IMPACT_RETEST_HELD_AND_SEPARATE_RESPONSE_RESUMED", snapshot)
            signal = self._signal(episode, snapshot, branch="CONTINUATION") if allow_new else None
            self._episode = None
            self._cooldown_until = snapshot.index + int(self.params.get("vcib_cooldown_bars", 2))
            return ScenarioStep(transitions=(transition,), signal=signal)
        if episode.state == "CONTINUATION_RETEST" and episode.retest_extreme is not None:
            episode.retest_extreme = min(episode.retest_extreme, observation.low) if episode.direction == "UP" else max(episode.retest_extreme, observation.high)
        return ScenarioStep()

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool = True) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        advanced = self._advance_episode(snapshot, allow_new=allow_new)
        transitions.extend(advanced.transitions)
        if advanced.signal is not None:
            self._minute_volumes.append(snapshot.observation.volume)
            return ScenarioStep(transitions=tuple(transitions), signal=advanced.signal)
        bucket = self._ingest(snapshot)
        if bucket is None:
            return ScenarioStep(transitions=tuple(transitions))
        history_before = list(self._efficiency_history)
        if self._episode is None and allow_new and snapshot.index >= self._cooldown_until and self._is_impulse(bucket):
            self._sequence += 1
            episode = _Episode(
                scenario_id=f"VCIB-{bucket.end_ts_ns}-{self._sequence:06d}",
                state="IMPULSE_BUCKET",
                direction=bucket.direction,
                first=bucket,
                second=None,
                created_index=snapshot.index,
            )
            self._episode = episode
            transitions.append(self._transition(episode, "IDLE", "IMPULSE_BUCKET", "COMPLETED_VOLUME_BUCKET_WITH_DIRECTIONAL_AGGRESSIVE_IMPACT", snapshot))
        elif self._episode is not None and self._episode.state == "IMPULSE_BUCKET":
            transitions.extend(self._classify_second(self._episode, bucket, snapshot))
        if not (self._episode is not None and self._episode.second is bucket):
            self._efficiency_history.append(bucket.efficiency)
        if len(history_before) == 0 and len(self._efficiency_history) > int(self.params.get("vcib_efficiency_history", 40)) * 3:
            self._efficiency_history = self._efficiency_history[-int(self.params.get("vcib_efficiency_history", 40)) * 2:]
        return ScenarioStep(transitions=tuple(transitions))

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        episode = self._episode
        if episode is None:
            return ScenarioStep()
        transition = self._transition(episode, episode.state, "RESET", reason, snapshot)
        self._episode = None
        return ScenarioStep(transitions=(transition,))
