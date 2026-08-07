"""Auction-value migration and continuation state machine for candidate-06."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

from agg_trade_profile_data import AggMinuteStat, AuctionProfile
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(slots=True)
class _MigrationContext:
    context_id: str
    direction: str
    previous: AuctionProfile
    current: AuctionProfile
    defended_edge: float
    poc_shift: float
    created_index: int
    expires_ts_ns: int


@dataclass(slots=True)
class _RetestEpisode:
    scenario_id: str
    direction: str
    state: str
    defended_edge: float
    retest_high: float
    retest_low: float
    started_index: int
    started_ts_ns: int


class AuctionImbalanceMigrationDiscoveryEngine:
    """Trade continuation only after completed value migrates and survives retest.

    A completed profile may establish price discovery when its POC and value
    area migrate beyond the previous completed profile, the close accepts the
    migrated side, and realized direction is efficient.  The full contract also
    requires aligned aggregate-trade delta.  During the immediately following
    profile, the first opposing-flow retest must hold the migrated value edge;
    a later completed minute must resume in the discovery direction before a
    signal is emitted.  Re-entry into old value invalidates the context.
    """

    def __init__(
        self,
        params: Mapping[str, Any],
        *,
        profiles: Mapping[int, AuctionProfile],
        minute_stats: Mapping[int, AggMinuteStat],
    ) -> None:
        self.params = dict(params)
        self._profiles = dict(profiles)
        self._minute_stats = dict(minute_stats)
        self._period_minutes = int(self.params.get("aimd_profile_period_minutes", 15))
        self._period_ns = self._period_minutes * 60 * 1_000_000_000
        self._history: list[AuctionProfile] = []
        self._ingested_profile_ends: set[int] = set()
        self._context: _MigrationContext | None = None
        self._episode: _RetestEpisode | None = None
        self._context_sequence = 0
        self._episode_sequence = 0
        self._cooldown_until = -1

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        ts_ns = snapshot.observation.ts_ns
        minute = self._minute_stats.get(ts_ns)
        if minute is None:
            raise RuntimeError(f"missing aggTrade minute context at completed timestamp {ts_ns}")
        transitions: list[ScenarioTransition] = []
        signal: ScenarioSignal | None = None

        if self._context is not None and ts_ns >= self._context.expires_ts_ns:
            transitions.extend(self._reset_context(snapshot, "MIGRATION_CONTEXT_EXPIRED").transitions)

        if self._episode is not None:
            step = self._advance_episode(snapshot, minute, allow_new=allow_new)
            transitions.extend(step.transitions)
            signal = step.signal

        if (
            signal is None
            and self._context is not None
            and self._episode is None
            and allow_new
            and snapshot.index >= self._cooldown_until
            and ts_ns < self._context.expires_ts_ns
        ):
            started = self._maybe_start_retest(snapshot, minute)
            if started is not None:
                transitions.append(started)

        # A profile closing now becomes decision information only after this
        # completed minute has already been processed.
        profile = self._profiles.get(ts_ns)
        if profile is not None and ts_ns not in self._ingested_profile_ends:
            self._ingested_profile_ends.add(ts_ns)
            transitions.extend(self._ingest_completed_profile(profile, snapshot))

        return ScenarioStep(transitions=tuple(transitions), signal=signal)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        if self._episode is not None:
            transitions.append(self._episode_transition(
                self._episode,
                self._episode.state,
                "RESET",
                reason,
                snapshot.observation.close,
                {"aborted": True},
            ))
            self._episode = None
        if self._context is not None:
            transitions.append(self._context_transition(
                self._context,
                "MIGRATION_ACTIVE",
                "RESET",
                reason,
                snapshot.observation.close,
                {"aborted": True},
            ))
            self._context = None
        return ScenarioStep(transitions=tuple(transitions))

    def _delta_enabled(self) -> bool:
        return bool(self.params.get("aimd_use_profile_delta", True))

    def _poc_migration_enabled(self) -> bool:
        return bool(self.params.get("aimd_require_poc_migration", True))

    def _agg_flow_enabled(self) -> bool:
        return bool(self.params.get("aimd_use_agg_trade_flow", True))

    def _migration_direction(
        self,
        previous: AuctionProfile,
        current: AuctionProfile,
        atr: float,
    ) -> tuple[str | None, dict[str, Any]]:
        if atr <= 0.0 or previous.width <= 0.0 or current.width <= 0.0:
            return None, {"ready": False}
        overlap = max(0.0, min(previous.vah, current.vah) - max(previous.val, current.val))
        overlap_ratio = overlap / min(previous.width, current.width)
        poc_shift = current.poc - previous.poc
        poc_floor = float(self.params.get("aimd_poc_shift_atr", 0.15)) * atr
        value_shift = float(self.params.get("aimd_value_shift_atr", 0.05)) * atr
        acceptance = float(self.params.get("aimd_close_acceptance_atr", 0.03)) * atr
        efficiency_floor = float(self.params.get("aimd_efficiency_floor", 0.50))
        delta_floor = float(self.params.get("aimd_delta_floor", 0.08))
        overlap_ceiling = float(self.params.get("aimd_value_overlap_ceiling", 0.65))
        efficiency_ok = current.directional_efficiency >= efficiency_floor
        overlap_ok = overlap_ratio <= overlap_ceiling

        long_poc = poc_shift >= poc_floor if self._poc_migration_enabled() else True
        short_poc = poc_shift <= -poc_floor if self._poc_migration_enabled() else True
        long_delta = current.delta_ratio >= delta_floor if self._delta_enabled() else True
        short_delta = current.delta_ratio <= -delta_floor if self._delta_enabled() else True
        long_value = current.val >= previous.val + value_shift and current.vah > previous.vah
        short_value = current.vah <= previous.vah - value_shift and current.val < previous.val
        long_accept = current.close >= previous.vah + acceptance and current.close >= current.poc
        short_accept = current.close <= previous.val - acceptance and current.close <= current.poc

        direction: str | None = None
        if long_poc and long_delta and long_value and long_accept and efficiency_ok and overlap_ok:
            direction = "LONG"
        elif short_poc and short_delta and short_value and short_accept and efficiency_ok and overlap_ok:
            direction = "SHORT"
        return direction, {
            "ready": True,
            "direction": direction,
            "poc_shift": poc_shift,
            "poc_shift_atr": poc_shift / atr,
            "value_overlap_ratio": overlap_ratio,
            "previous_value": [previous.val, previous.poc, previous.vah],
            "current_value": [current.val, current.poc, current.vah],
            "current_delta_ratio": current.delta_ratio,
            "current_directional_efficiency": current.directional_efficiency,
            "poc_migration_required": self._poc_migration_enabled(),
            "profile_delta_enabled": self._delta_enabled(),
            "efficiency_ok": efficiency_ok,
            "overlap_ok": overlap_ok,
            "long_components": {
                "poc": long_poc,
                "delta": long_delta,
                "value": long_value,
                "acceptance": long_accept,
            },
            "short_components": {
                "poc": short_poc,
                "delta": short_delta,
                "value": short_value,
                "acceptance": short_accept,
            },
        }

    def _ingest_completed_profile(
        self,
        profile: AuctionProfile,
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        transitions: list[ScenarioTransition] = []
        if self._episode is not None:
            transitions.append(self._episode_transition(
                self._episode,
                self._episode.state,
                "RESET",
                "NEW_PROFILE_CLOSED_BEFORE_MIGRATION_RESPONSE",
                profile.close,
                {"profile_end_ts_ns": profile.end_ts_ns},
            ))
            self._episode = None
        if self._context is not None:
            transitions.append(self._context_transition(
                self._context,
                "MIGRATION_ACTIVE",
                "RESET",
                "MIGRATION_CONTEXT_REPLACED_AT_PROFILE_CLOSE",
                profile.close,
                {"replacement_profile_end_ts_ns": profile.end_ts_ns},
            ))
            self._context = None

        if self._history:
            previous = self._history[-1]
            direction, details = self._migration_direction(previous, profile, snapshot.atr)
            self._context_sequence += 1
            context_id = f"AIMD-MIGRATION-{profile.end_ts_ns}-{self._context_sequence:06d}"
            if direction is not None:
                defended_edge = max(previous.vah, profile.val) if direction == "LONG" else min(previous.val, profile.vah)
                self._context = _MigrationContext(
                    context_id=context_id,
                    direction=direction,
                    previous=previous,
                    current=profile,
                    defended_edge=defended_edge,
                    poc_shift=profile.poc - previous.poc,
                    created_index=snapshot.index,
                    expires_ts_ns=profile.end_ts_ns + self._period_ns,
                )
                transitions.append(self._context_transition(
                    self._context,
                    "IDLE",
                    "MIGRATION_ACTIVE",
                    "COMPLETED_AUCTION_VALUE_AND_IMBALANCE_MIGRATED",
                    profile.poc,
                    {
                        **details,
                        "defended_edge": defended_edge,
                        "profile_end_ts_ns": profile.end_ts_ns,
                        "context_expiry_ts_ns": profile.end_ts_ns + self._period_ns,
                    },
                ))
            else:
                transitions.append(ScenarioTransition(
                    scenario_id=context_id,
                    event_type="AIMD_MIGRATION_TRANSITION",
                    previous_state="IDLE",
                    next_state="RESET",
                    reason_code="COMPLETED_AUCTION_DID_NOT_ESTABLISH_VALUE_MIGRATION",
                    reference_price=profile.poc,
                    details=details,
                ))
        self._history.append(profile)
        if len(self._history) > 4:
            self._history = self._history[-4:]
        return tuple(transitions)

    def _flow(self, snapshot: PrimitiveSnapshot, minute: AggMinuteStat) -> float:
        return minute.flow_ratio if self._agg_flow_enabled() else snapshot.flow_ratio

    def _maybe_start_retest(
        self,
        snapshot: PrimitiveSnapshot,
        minute: AggMinuteStat,
    ) -> ScenarioTransition | None:
        context = self._context
        if context is None or snapshot.index <= context.created_index:
            return None
        obs = snapshot.observation
        flow = self._flow(snapshot, minute)
        band = float(self.params.get("aimd_retest_band_atr", 0.12)) * snapshot.atr
        tolerance = float(self.params.get("aimd_old_value_tolerance_atr", 0.03)) * snapshot.atr
        opposing_flow = float(self.params.get("aimd_retest_opposing_flow", 0.02))
        if context.direction == "LONG":
            if obs.close < context.previous.vah - tolerance:
                self._cooldown_until = snapshot.index + int(self.params.get("aimd_cooldown_bars", 2))
                return self._invalidate_context(snapshot, "OLD_VALUE_REACCEPTED_BEFORE_RETEST")
            touched = obs.low <= context.defended_edge + band
            held = obs.close >= context.defended_edge - tolerance
            flow_ok = flow <= -opposing_flow
        else:
            if obs.close > context.previous.val + tolerance:
                self._cooldown_until = snapshot.index + int(self.params.get("aimd_cooldown_bars", 2))
                return self._invalidate_context(snapshot, "OLD_VALUE_REACCEPTED_BEFORE_RETEST")
            touched = obs.high >= context.defended_edge - band
            held = obs.close <= context.defended_edge + tolerance
            flow_ok = flow >= opposing_flow
        if not (touched and held and flow_ok):
            return None
        self._episode_sequence += 1
        self._episode = _RetestEpisode(
            scenario_id=f"AIMD-{obs.ts_ns}-{self._episode_sequence:06d}",
            direction=context.direction,
            state="MIGRATED_VALUE_RETEST_HELD",
            defended_edge=context.defended_edge,
            retest_high=obs.high,
            retest_low=obs.low,
            started_index=snapshot.index,
            started_ts_ns=obs.ts_ns,
        )
        return self._episode_transition(
            self._episode,
            "IDLE",
            "MIGRATED_VALUE_RETEST_HELD",
            "OPPOSING_AGGRESSION_FAILED_TO_REENTER_OLD_VALUE",
            context.defended_edge,
            {
                "direction": context.direction,
                "context_id": context.context_id,
                "flow_ratio": flow,
                "defended_edge": context.defended_edge,
                "previous_value": [context.previous.val, context.previous.poc, context.previous.vah],
                "current_value": [context.current.val, context.current.poc, context.current.vah],
            },
        )

    def _advance_episode(
        self,
        snapshot: PrimitiveSnapshot,
        minute: AggMinuteStat,
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        episode = self._episode
        context = self._context
        if episode is None:
            return ScenarioStep()
        if context is None:
            return self._reset_episode(snapshot, "MIGRATION_CONTEXT_NOT_AVAILABLE", {})
        if snapshot.index <= episode.started_index:
            return ScenarioStep()
        obs = snapshot.observation
        flow = self._flow(snapshot, minute)
        tolerance = float(self.params.get("aimd_old_value_tolerance_atr", 0.03)) * snapshot.atr
        if episode.direction == "LONG":
            episode.retest_low = min(episode.retest_low, obs.low)
            if obs.close < context.previous.vah - tolerance:
                return self._reset_context(snapshot, "OLD_VALUE_REACCEPTED_AFTER_RETEST")
        else:
            episode.retest_high = max(episode.retest_high, obs.high)
            if obs.close > context.previous.val + tolerance:
                return self._reset_context(snapshot, "OLD_VALUE_REACCEPTED_AFTER_RETEST")

        elapsed = snapshot.index - episode.started_index
        if elapsed > int(self.params.get("aimd_response_bars", 4)):
            return self._reset_episode(snapshot, "MIGRATION_RESUMPTION_RESPONSE_EXPIRED", {"elapsed_bars": elapsed})
        body_floor = float(self.params.get("aimd_response_body_atr", 0.15))
        response_flow = float(self.params.get("aimd_response_flow_ratio", 0.03))
        close_location = float(self.params.get("aimd_response_close_location", 0.62))
        if episode.direction == "LONG":
            confirmed = (
                obs.close > obs.open
                and obs.close > episode.retest_high
                and snapshot.body_atr >= body_floor
                and flow >= response_flow
                and snapshot.close_location >= close_location
            )
        else:
            confirmed = (
                obs.close < obs.open
                and obs.close < episode.retest_low
                and snapshot.body_atr >= body_floor
                and flow <= -response_flow
                and snapshot.close_location <= 1.0 - close_location
            )
        if not confirmed:
            return ScenarioStep()
        if not allow_new:
            return self._reset_episode(snapshot, "ENTRY_SLOT_UNAVAILABLE_AT_MIGRATION_RESPONSE", {})
        return self._emit(snapshot, context, episode, flow)

    def _emit(
        self,
        snapshot: PrimitiveSnapshot,
        context: _MigrationContext,
        episode: _RetestEpisode,
        flow: float,
    ) -> ScenarioStep:
        obs = snapshot.observation
        buffer_value = float(self.params.get("aimd_stop_buffer_atr", 0.08)) * snapshot.atr
        projection = max(
            abs(context.poc_shift),
            context.current.width * float(self.params.get("aimd_projection_fraction", 0.50)),
        )
        if episode.direction == "LONG":
            stop = min(episode.retest_low, context.previous.vah - buffer_value)
            candidates = [
                (context.current.high, "MIGRATION_AUCTION_HIGH") if obs.high < context.current.high else (float("nan"), "OBJECTIVE_ALREADY_TOUCHED"),
                (context.current.high + projection, "MIGRATED_VALUE_EXTENSION"),
            ]
        else:
            stop = max(episode.retest_high, context.previous.val + buffer_value)
            candidates = [
                (context.current.low, "MIGRATION_AUCTION_LOW") if obs.low > context.current.low else (float("nan"), "OBJECTIVE_ALREADY_TOUCHED"),
                (context.current.low - projection, "MIGRATED_VALUE_EXTENSION"),
            ]
        target = self._select_target(episode.direction, obs.close, stop, candidates)
        if target is None:
            return self._reset_episode(snapshot, "NO_MIGRATION_OBJECTIVE_WITH_SUFFICIENT_SPACE", {})
        target_price, target_reason = target
        transition = self._episode_transition(
            episode,
            episode.state,
            "ENTRY_ARMED",
            "MIGRATED_VALUE_RETEST_AND_SEPARATE_RESUMPTION_CONFIRMED",
            obs.close,
            {
                "direction": episode.direction,
                "context_id": context.context_id,
                "flow_ratio": flow,
                "stop_price": stop,
                "target_price": target_price,
                "target_reason": target_reason,
                "defended_edge": context.defended_edge,
                "poc_shift": context.poc_shift,
            },
        )
        signal = ScenarioSignal(
            scenario_id=episode.scenario_id,
            family="AIMD",
            direction=episode.direction,
            observed_ts_ns=obs.ts_ns,
            reference_entry=obs.close,
            stop_price=stop,
            target_price=target_price,
            target_reason=target_reason,
            atr=snapshot.atr,
            liquidity_level=context.defended_edge,
            details={
                "context_id": context.context_id,
                "previous_profile_end_ts_ns": context.previous.end_ts_ns,
                "migration_profile_end_ts_ns": context.current.end_ts_ns,
                "previous_value": [context.previous.val, context.previous.poc, context.previous.vah],
                "current_value": [context.current.val, context.current.poc, context.current.vah],
                "profile_delta_ratio": context.current.delta_ratio,
                "profile_directional_efficiency": context.current.directional_efficiency,
                "poc_shift": context.poc_shift,
                "defended_edge": context.defended_edge,
                "retest_high": episode.retest_high,
                "retest_low": episode.retest_low,
                "profile_delta_enabled": self._delta_enabled(),
                "poc_migration_required": self._poc_migration_enabled(),
                "agg_trade_flow_enabled": self._agg_flow_enabled(),
            },
        )
        self._episode = None
        self._context = None
        self._cooldown_until = snapshot.index + int(self.params.get("aimd_cooldown_bars", 2))
        return ScenarioStep(transitions=(transition,), signal=signal)

    def _select_target(
        self,
        direction: str,
        entry: float,
        stop: float,
        candidates: list[tuple[float, str]],
    ) -> tuple[float, str] | None:
        risk = abs(entry - stop)
        if risk <= 0.0:
            return None
        minimum_rr = float(self.params.get("minimum_structural_rr", 0.75))
        valid: list[tuple[float, str]] = []
        for price, reason in candidates:
            if not isfinite(price):
                continue
            reward = price - entry if direction == "LONG" else entry - price
            if reward > 0.0 and reward / risk >= minimum_rr:
                valid.append((price, reason))
        valid.sort(key=lambda value: abs(value[0] - entry))
        return valid[0] if valid else None

    def _invalidate_context(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioTransition:
        context = self._context
        assert context is not None
        transition = self._context_transition(
            context,
            "MIGRATION_ACTIVE",
            "RESET",
            reason,
            snapshot.observation.close,
            {},
        )
        self._context = None
        self._episode = None
        return transition

    def _reset_episode(self, snapshot: PrimitiveSnapshot, reason: str, details: Mapping[str, Any]) -> ScenarioStep:
        episode = self._episode
        if episode is None:
            return ScenarioStep()
        transition = self._episode_transition(
            episode,
            episode.state,
            "RESET",
            reason,
            snapshot.observation.close,
            details,
        )
        self._episode = None
        self._cooldown_until = snapshot.index + int(self.params.get("aimd_cooldown_bars", 2))
        return ScenarioStep(transitions=(transition,))

    def _reset_context(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        if self._episode is not None:
            transitions.append(self._episode_transition(
                self._episode,
                self._episode.state,
                "RESET",
                reason,
                snapshot.observation.close,
                {},
            ))
            self._episode = None
        if self._context is not None:
            transitions.append(self._context_transition(
                self._context,
                "MIGRATION_ACTIVE",
                "RESET",
                reason,
                snapshot.observation.close,
                {},
            ))
            self._context = None
        self._cooldown_until = snapshot.index + int(self.params.get("aimd_cooldown_bars", 2))
        return ScenarioStep(transitions=tuple(transitions))

    @staticmethod
    def _context_transition(
        context: _MigrationContext,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=context.context_id,
            event_type="AIMD_MIGRATION_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )

    @staticmethod
    def _episode_transition(
        episode: _RetestEpisode,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="AIMD_RETEST_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )
