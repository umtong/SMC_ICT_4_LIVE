"""Session Liquidity Transfer (SLT) engine for candidate-06 v0.3.

The engine treats completed Asia and previous-UTC-day ranges as pre-existing
liquidity, then observes how London/NY activity responds after taking a boundary.
It emits the same ScenarioStep/ScenarioSignal contract as the LRB engine and has
no execution or PnL implementation of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass
class _SessionEpisode:
    scenario_id: str
    state: str
    side: str
    level_name: str
    level: float
    family: str
    direction: str
    extreme: float
    sweep_midpoint: float
    started_index: int
    started_ts_ns: int
    atr_at_start: float
    window: str
    range_high: float | None
    range_low: float | None


class SessionLiquidityTransferEngine:
    """Causal session-range sweep/reaction state machine."""

    def __init__(self, params: Mapping[str, Any]):
        self.params = dict(params)
        self._episode: _SessionEpisode | None = None
        self._sequence = 0
        self._cooldown_until = -1
        self._current_day: str | None = None
        self._day_high: float | None = None
        self._day_low: float | None = None
        self._previous_day_high: float | None = None
        self._previous_day_low: float | None = None
        self._asia_high: float | None = None
        self._asia_low: float | None = None
        self._prior_close: float | None = None
        self._consumed: set[tuple[str, str, str]] = set()

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        if not snapshot.ready:
            self._finish_observation(snapshot)
            return ScenarioStep()
        dt = self._datetime(snapshot.observation.ts_ns)
        self._roll_day(dt.date().isoformat())
        try:
            if self._episode is not None:
                return self._advance(snapshot)
            if not allow_new or snapshot.index < self._cooldown_until:
                return ScenarioStep()
            window = self._active_window(dt.hour * 60 + dt.minute)
            if window is None or self._prior_close is None:
                return ScenarioStep()
            candidates = self._sweep_candidates(snapshot, window)
            if not candidates:
                return ScenarioStep()
            sides = {value[0] for value in candidates}
            if len(sides) > 1:
                for side, name, _, _, _ in candidates:
                    self._consumed.add((self._current_day or "", name, side))
                return self._ambiguous(snapshot, "BOTH_SESSION_BOUNDARIES_SWEPT", window, candidates)
            side, level_name, level, range_high, range_low = min(
                candidates,
                key=lambda value: abs(value[2] - float(self._prior_close)),
            )
            self._consumed.add((self._current_day or "", level_name, side))
            return self._start(snapshot, window, side, level_name, level, range_high, range_low)
        finally:
            self._finish_observation(snapshot)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        if self._episode is None:
            return ScenarioStep()
        episode = self._episode
        self._episode = None
        self._cooldown_until = snapshot.index + int(self.params.get("cooldown_bars", 3))
        return ScenarioStep(
            transitions=(self._transition(episode, "RESET", reason, snapshot.observation.close, {}),),
        )

    def _start(
        self,
        snapshot: PrimitiveSnapshot,
        window: str,
        side: str,
        level_name: str,
        level: float,
        range_high: float | None,
        range_low: float | None,
    ) -> ScenarioStep:
        self._sequence += 1
        scenario_id = f"SLT-{snapshot.observation.ts_ns}-{self._sequence:06d}"
        state = f"{side}_SESSION_SWEEP_RESPONSE_OBSERVATION"
        episode = _SessionEpisode(
            scenario_id=scenario_id,
            state=state,
            side=side,
            level_name=level_name,
            level=level,
            family="PENDING",
            direction="NONE",
            extreme=snapshot.observation.high if side == "UPPER" else snapshot.observation.low,
            sweep_midpoint=(snapshot.observation.high + snapshot.observation.low) / 2.0,
            started_index=snapshot.index,
            started_ts_ns=snapshot.observation.ts_ns,
            atr_at_start=snapshot.atr,
            window=window,
            range_high=range_high,
            range_low=range_low,
        )
        self._episode = episode
        depth = (
            (snapshot.observation.high - level) / snapshot.atr
            if side == "UPPER"
            else (level - snapshot.observation.low) / snapshot.atr
        )
        return ScenarioStep(
            transitions=(
                ScenarioTransition(
                    scenario_id=scenario_id,
                    event_type="SCENARIO_TRANSITION",
                    previous_state="IDLE",
                    next_state=state,
                    reason_code=f"{level_name}_LIQUIDITY_SWEEP_DURING_{window}",
                    reference_price=level,
                    details={
                        "engine": "SESSION_LIQUIDITY_TRANSFER",
                        "window": window,
                        "level_name": level_name,
                        "level": level,
                        "sweep_depth_atr": depth,
                        "flow_ratio": snapshot.flow_ratio,
                        "relative_volume": snapshot.rel_volume,
                    },
                ),
            ),
        )

    def _advance(self, snapshot: PrimitiveSnapshot) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        if episode.family == "PENDING":
            return self._classify_response(snapshot, episode)
        return self._advance_retest(snapshot, episode)

    def _classify_response(self, snapshot: PrimitiveSnapshot, episode: _SessionEpisode) -> ScenarioStep:
        elapsed = snapshot.index - episode.started_index
        maximum = int(self.params.get("session_response_bars", 3))
        if elapsed > maximum:
            return self._reset(snapshot, episode, "SESSION_RESPONSE_NOT_IDENTIFIABLE")
        observation = snapshot.observation
        tolerance = float(self.params.get("session_reclaim_tolerance_atr", 0.05)) * snapshot.atr
        body_floor = float(self.params.get("session_response_body_atr", 0.30))
        flow_floor = float(self.params.get("session_response_flow_ratio", 0.05))
        accept_distance = float(self.params.get("session_acceptance_close_atr", 0.12)) * snapshot.atr
        accept_body = float(self.params.get("session_acceptance_body_atr", 0.45))
        accept_flow = float(self.params.get("session_acceptance_flow_ratio", 0.08))
        use_flow = bool(self.params.get("session_use_flow_proxy", True))

        if episode.side == "UPPER":
            reject = (
                observation.close <= episode.level + tolerance
                and observation.close < observation.open
                and snapshot.body_atr >= body_floor
                and ((snapshot.flow_ratio <= -flow_floor) if use_flow else observation.close < episode.sweep_midpoint)
            )
            accept = (
                observation.close >= episode.level + accept_distance
                and observation.close > observation.open
                and snapshot.body_atr >= accept_body
                and ((snapshot.flow_ratio >= accept_flow) if use_flow else snapshot.close_location >= 0.65)
            )
            reject_direction, accept_direction = "SHORT", "LONG"
        else:
            reject = (
                observation.close >= episode.level - tolerance
                and observation.close > observation.open
                and snapshot.body_atr >= body_floor
                and ((snapshot.flow_ratio >= flow_floor) if use_flow else observation.close > episode.sweep_midpoint)
            )
            accept = (
                observation.close <= episode.level - accept_distance
                and observation.close < observation.open
                and snapshot.body_atr >= accept_body
                and ((snapshot.flow_ratio <= -accept_flow) if use_flow else snapshot.close_location <= 0.35)
            )
            reject_direction, accept_direction = "LONG", "SHORT"

        if reject and accept:
            return self._reset(snapshot, episode, "SESSION_REJECTION_ACCEPTANCE_CONFLICT")
        if reject:
            previous = episode.state
            episode.family = "SRR"
            episode.direction = reject_direction
            episode.state = f"{episode.side}_SESSION_SRR_CONFIRMED"
            classification = self._transition(
                episode,
                episode.state,
                "SESSION_SWEEP_RECLAIMED_WITH_OPPOSITE_RESPONSE",
                observation.close,
                {"previous_state_override": previous, "elapsed_bars": elapsed},
                previous_state=previous,
            )
            armed = self._arm_reversal(snapshot, episode)
            return ScenarioStep(transitions=(classification, *armed.transitions), signal=armed.signal)
        if accept:
            previous = episode.state
            episode.family = "SAC"
            episode.direction = accept_direction
            episode.state = f"{episode.side}_SESSION_SAC_RETEST"
            return ScenarioStep(
                transitions=(
                    self._transition(
                        episode,
                        episode.state,
                        "SESSION_BOUNDARY_ACCEPTED_WITH_DISPLACEMENT",
                        observation.close,
                        {"elapsed_bars": elapsed},
                        previous_state=previous,
                    ),
                ),
            )
        return ScenarioStep()

    def _advance_retest(self, snapshot: PrimitiveSnapshot, episode: _SessionEpisode) -> ScenarioStep:
        elapsed = snapshot.index - episode.started_index
        if elapsed > int(self.params.get("session_retest_bars", 7)):
            return self._reset(snapshot, episode, "SESSION_ACCEPTANCE_RETEST_EXPIRED")
        observation = snapshot.observation
        band = float(self.params.get("session_retest_band_atr", 0.18)) * snapshot.atr
        reclaim = float(self.params.get("session_acceptance_reclaim_atr", 0.08)) * snapshot.atr
        opposing = float(self.params.get("session_retest_max_opposing_flow", 0.20))
        if episode.direction == "LONG":
            episode.extreme = max(episode.extreme, observation.high)
            if observation.close < episode.level - reclaim:
                return self._reset(snapshot, episode, "SESSION_UPPER_ACCEPTANCE_RECLAIMED")
            retested = observation.low <= episode.level + band and observation.close > episode.level
            response_ok = snapshot.flow_ratio >= -opposing or not bool(self.params.get("session_use_flow_proxy", True))
        else:
            episode.extreme = min(episode.extreme, observation.low)
            if observation.close > episode.level + reclaim:
                return self._reset(snapshot, episode, "SESSION_LOWER_ACCEPTANCE_RECLAIMED")
            retested = observation.high >= episode.level - band and observation.close < episode.level
            response_ok = snapshot.flow_ratio <= opposing or not bool(self.params.get("session_use_flow_proxy", True))
        if retested and response_ok:
            return self._arm_continuation(snapshot, episode)
        return ScenarioStep()

    def _arm_reversal(self, snapshot: PrimitiveSnapshot, episode: _SessionEpisode) -> ScenarioStep:
        buffer_value = float(self.params.get("stop_buffer_atr", 0.10)) * snapshot.atr
        if episode.direction == "SHORT":
            stop = episode.extreme + buffer_value
            candidates = [
                (episode.range_low, "OPPOSITE_SESSION_RANGE_LIQUIDITY"),
                (self._asia_low, "ASIA_LOW_LIQUIDITY"),
                (self._previous_day_low, "PREVIOUS_DAY_LOW_LIQUIDITY"),
            ]
        else:
            stop = episode.extreme - buffer_value
            candidates = [
                (episode.range_high, "OPPOSITE_SESSION_RANGE_LIQUIDITY"),
                (self._asia_high, "ASIA_HIGH_LIQUIDITY"),
                (self._previous_day_high, "PREVIOUS_DAY_HIGH_LIQUIDITY"),
            ]
        target = self._select_target(episode.direction, snapshot.observation.close, stop, candidates)
        if target is None:
            return self._reset(snapshot, episode, "NO_OPPOSING_SESSION_LIQUIDITY_WITH_SUFFICIENT_SPACE")
        return self._emit(snapshot, episode, stop, target[0], target[1], "SESSION_REJECTION_CONFIRMED")

    def _arm_continuation(self, snapshot: PrimitiveSnapshot, episode: _SessionEpisode) -> ScenarioStep:
        buffer_value = float(self.params.get("stop_buffer_atr", 0.10)) * snapshot.atr
        observation = snapshot.observation
        width = None
        if episode.range_high is not None and episode.range_low is not None:
            width = episode.range_high - episode.range_low
        if width is None or width <= 0.0:
            width = snapshot.atr * float(self.params.get("session_projection_atr", 3.0))
        projection = width * float(self.params.get("session_projection_fraction", 1.0))
        if episode.direction == "LONG":
            stop = min(observation.low, episode.level - buffer_value)
            candidates = [
                (self._previous_day_high if self._previous_day_high is not None and self._previous_day_high > observation.close else None, "NEXT_PREVIOUS_DAY_HIGH"),
                (episode.level + projection, "ACCEPTED_SESSION_RANGE_PROJECTION"),
            ]
        else:
            stop = max(observation.high, episode.level + buffer_value)
            candidates = [
                (self._previous_day_low if self._previous_day_low is not None and self._previous_day_low < observation.close else None, "NEXT_PREVIOUS_DAY_LOW"),
                (episode.level - projection, "ACCEPTED_SESSION_RANGE_PROJECTION"),
            ]
        target = self._select_target(episode.direction, observation.close, stop, candidates)
        if target is None:
            return self._reset(snapshot, episode, "NO_SESSION_CONTINUATION_OBJECTIVE_WITH_SUFFICIENT_SPACE")
        return self._emit(snapshot, episode, stop, target[0], target[1], "SESSION_ACCEPTANCE_RETEST_HELD")

    def _emit(
        self,
        snapshot: PrimitiveSnapshot,
        episode: _SessionEpisode,
        stop: float,
        target: float,
        target_reason: str,
        reason: str,
    ) -> ScenarioStep:
        signal = ScenarioSignal(
            scenario_id=episode.scenario_id,
            family=episode.family,
            direction=episode.direction,
            observed_ts_ns=snapshot.observation.ts_ns,
            reference_entry=snapshot.observation.close,
            stop_price=stop,
            target_price=target,
            target_reason=target_reason,
            atr=snapshot.atr,
            liquidity_level=episode.level,
            details={
                "engine": "SESSION_LIQUIDITY_TRANSFER",
                "window": episode.window,
                "level_name": episode.level_name,
                "target_reason": target_reason,
                "elapsed_bars": snapshot.index - episode.started_index,
                "flow_ratio": snapshot.flow_ratio,
                "relative_volume": snapshot.rel_volume,
            },
        )
        transition = self._transition(
            episode,
            "ENTRY_ARMED",
            reason,
            snapshot.observation.close,
            {
                "direction": episode.direction,
                "family": episode.family,
                "stop_price": stop,
                "target_price": target,
                "target_reason": target_reason,
            },
        )
        self._episode = None
        self._cooldown_until = snapshot.index + int(self.params.get("cooldown_bars", 3))
        return ScenarioStep(transitions=(transition,), signal=signal)

    def _reset(self, snapshot: PrimitiveSnapshot, episode: _SessionEpisode, reason: str) -> ScenarioStep:
        transition = self._transition(episode, "RESET", reason, snapshot.observation.close, {})
        self._episode = None
        self._cooldown_until = snapshot.index + int(self.params.get("cooldown_bars", 3))
        return ScenarioStep(transitions=(transition,))

    def _ambiguous(self, snapshot: PrimitiveSnapshot, reason: str, window: str, candidates: list[tuple[str, str, float, float | None, float | None]]) -> ScenarioStep:
        self._sequence += 1
        scenario_id = f"SLT-{snapshot.observation.ts_ns}-{self._sequence:06d}"
        self._cooldown_until = snapshot.index + int(self.params.get("ambiguous_cooldown_bars", 2))
        details = {"window": window, "candidates": [{"side": side, "name": name, "level": level} for side, name, level, _, _ in candidates]}
        return ScenarioStep(
            transitions=(
                ScenarioTransition(scenario_id, "SCENARIO_TRANSITION", "IDLE", "AMBIGUOUS", reason, snapshot.observation.close, details),
                ScenarioTransition(scenario_id, "SCENARIO_TRANSITION", "AMBIGUOUS", "RESET", "SELECTIVE_ABSTENTION", snapshot.observation.close, {}),
            ),
        )

    def _transition(
        self,
        episode: _SessionEpisode,
        next_state: str,
        reason: str,
        reference_price: float,
        details: dict[str, Any],
        *,
        previous_state: str | None = None,
    ) -> ScenarioTransition:
        before = episode.state if previous_state is None else previous_state
        transition = ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="SCENARIO_TRANSITION",
            previous_state=before,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details={"engine": "SESSION_LIQUIDITY_TRANSFER", "window": episode.window, "level_name": episode.level_name, **details},
        )
        episode.state = next_state
        return transition

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
            reward = price - entry if direction == "LONG" else entry - price
            if reward > 0.0:
                valid.append((float(price), reason))
        valid.sort(key=lambda value: abs(value[0] - entry))
        return next((value for value in valid if abs(value[0] - entry) / risk >= minimum_rr), None)

    def _sweep_candidates(self, snapshot: PrimitiveSnapshot, window: str) -> list[tuple[str, str, float, float | None, float | None]]:
        observation = snapshot.observation
        minimum = float(self.params.get("session_sweep_min_atr", self.params.get("sweep_min_atr", 0.10))) * snapshot.atr
        levels: list[tuple[str, float, str, float | None, float | None]] = []
        if bool(self.params.get("session_use_asia_levels", True)) and self._asia_high is not None and self._asia_low is not None:
            levels.extend([
                ("ASIA_HIGH", self._asia_high, "UPPER", self._asia_high, self._asia_low),
                ("ASIA_LOW", self._asia_low, "LOWER", self._asia_high, self._asia_low),
            ])
        if bool(self.params.get("session_use_previous_day_levels", True)) and self._previous_day_high is not None and self._previous_day_low is not None:
            levels.extend([
                ("PREVIOUS_DAY_HIGH", self._previous_day_high, "UPPER", self._previous_day_high, self._previous_day_low),
                ("PREVIOUS_DAY_LOW", self._previous_day_low, "LOWER", self._previous_day_high, self._previous_day_low),
            ])
        result: list[tuple[str, str, float, float | None, float | None]] = []
        for name, level, side, range_high, range_low in levels:
            if (self._current_day or "", name, side) in self._consumed:
                continue
            if side == "UPPER" and self._prior_close <= level and observation.high >= level + minimum:
                result.append((side, name, level, range_high, range_low))
            elif side == "LOWER" and self._prior_close >= level and observation.low <= level - minimum:
                result.append((side, name, level, range_high, range_low))
        return result

    def _roll_day(self, day: str) -> None:
        if self._current_day is None:
            self._current_day = day
            return
        if day == self._current_day:
            return
        self._previous_day_high = self._day_high
        self._previous_day_low = self._day_low
        self._current_day = day
        self._day_high = None
        self._day_low = None
        self._asia_high = None
        self._asia_low = None
        self._consumed.clear()
        self._episode = None
        self._cooldown_until = -1

    def _finish_observation(self, snapshot: PrimitiveSnapshot) -> None:
        observation = snapshot.observation
        dt = self._datetime(observation.ts_ns)
        day = dt.date().isoformat()
        self._roll_day(day)
        self._day_high = observation.high if self._day_high is None else max(self._day_high, observation.high)
        self._day_low = observation.low if self._day_low is None else min(self._day_low, observation.low)
        minute = dt.hour * 60 + dt.minute
        asia_start = int(self.params.get("asia_start_minute_utc", 0))
        asia_end = int(self.params.get("asia_end_minute_utc", 360))
        if asia_start <= minute < asia_end:
            self._asia_high = observation.high if self._asia_high is None else max(self._asia_high, observation.high)
            self._asia_low = observation.low if self._asia_low is None else min(self._asia_low, observation.low)
        self._prior_close = observation.close

    def _active_window(self, minute: int) -> str | None:
        london_start = int(self.params.get("london_start_minute_utc", 420))
        london_end = int(self.params.get("london_end_minute_utc", 660))
        new_york_start = int(self.params.get("new_york_start_minute_utc", 780))
        new_york_end = int(self.params.get("new_york_end_minute_utc", 1020))
        if london_start <= minute < london_end:
            return "LONDON_EXPANSION"
        if new_york_start <= minute < new_york_end:
            return "NEW_YORK_EXPANSION"
        return None

    @staticmethod
    def _datetime(ts_ns: int) -> datetime:
        return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
