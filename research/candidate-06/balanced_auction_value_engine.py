"""Balanced-auction value-area excursion and reversion state machine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agg_trade_profile_data import AggMinuteStat, AuctionProfile
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(slots=True)
class _BalanceContext:
    context_id: str
    profile: AuctionProfile
    previous: AuctionProfile
    created_index: int
    expires_ts_ns: int
    upper_consumed: bool = False
    lower_consumed: bool = False


@dataclass(slots=True)
class _Excursion:
    scenario_id: str
    side: str
    direction: str
    state: str
    edge: float
    extreme: float
    started_index: int
    started_ts_ns: int
    reclaim_high: float | None = None
    reclaim_low: float | None = None
    reclaim_index: int | None = None
    outside_closes: int = 0


class BalancedAuctionValueReversionEngine:
    """Trade failed discovery only after a completed balanced value auction.

    Two adjacent completed profiles must overlap, mutually accept their POCs,
    and exhibit low directional efficiency.  The full contract additionally
    requires two-sided tails and bounded signed aggressive imbalance.  During
    the immediately following auction, a value-edge excursion can become a
    candidate.  It must reclaim value and then receive a separate directional
    response; sustained outside acceptance cancels the reversion thesis.
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
        self._period_minutes = int(self.params.get("bavr_profile_period_minutes", 15))
        self._period_ns = self._period_minutes * 60 * 1_000_000_000
        self._history: list[AuctionProfile] = []
        self._ingested_profile_ends: set[int] = set()
        self._context: _BalanceContext | None = None
        self._excursion: _Excursion | None = None
        self._sequence = 0
        self._context_sequence = 0
        self._cooldown_until = -1

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        ts_ns = snapshot.observation.ts_ns
        minute = self._minute_stats.get(ts_ns)
        if minute is None:
            raise RuntimeError(f"missing aggTrade minute context at completed timestamp {ts_ns}")
        transitions: list[ScenarioTransition] = []
        signal: ScenarioSignal | None = None

        if self._context is not None and ts_ns >= self._context.expires_ts_ns:
            transitions.extend(self._reset_context(snapshot, "BALANCED_VALUE_CONTEXT_EXPIRED").transitions)

        if self._excursion is not None:
            step = self._advance_excursion(snapshot, minute, allow_new=allow_new)
            transitions.extend(step.transitions)
            signal = step.signal

        if (
            signal is None
            and self._context is not None
            and self._excursion is None
            and allow_new
            and snapshot.index >= self._cooldown_until
            and ts_ns <= self._context.expires_ts_ns
        ):
            started = self._maybe_start_excursion(snapshot, minute)
            if started is not None:
                transitions.append(started)

        # A profile completing at this timestamp is knowable now but becomes a
        # decision context only after the current bar has been processed.
        profile = self._profiles.get(ts_ns)
        if profile is not None and ts_ns not in self._ingested_profile_ends:
            self._ingested_profile_ends.add(ts_ns)
            transitions.extend(self._ingest_completed_profile(profile, snapshot))

        return ScenarioStep(transitions=tuple(transitions), signal=signal)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        if self._excursion is not None:
            transitions.append(self._episode_transition(
                self._excursion,
                self._excursion.state,
                "RESET",
                reason,
                snapshot.observation.close,
                {"aborted": True},
            ))
            self._excursion = None
        if self._context is not None:
            transitions.append(self._context_transition(
                self._context,
                "BALANCE_ACTIVE",
                "RESET",
                reason,
                snapshot.observation.close,
                {"aborted": True},
            ))
            self._context = None
        return ScenarioStep(transitions=tuple(transitions))

    def _distribution_enabled(self) -> bool:
        return bool(self.params.get("bavr_use_trade_distribution", True))

    def _balance_enabled(self) -> bool:
        return bool(self.params.get("bavr_require_balance", True))

    def _agg_flow_enabled(self) -> bool:
        return bool(self.params.get("bavr_use_agg_trade_flow", True))

    def _profile_is_balanced(self, previous: AuctionProfile, current: AuctionProfile) -> tuple[bool, dict[str, Any]]:
        minimum_width = min(previous.width, current.width)
        overlap = max(0.0, min(previous.vah, current.vah) - max(previous.val, current.val))
        overlap_ratio = overlap / minimum_width if minimum_width > 0.0 else 0.0
        mutual_poc = (
            previous.val <= current.poc <= previous.vah
            and current.val <= previous.poc <= current.vah
        )
        efficiency_ceiling = float(self.params.get("bavr_balance_efficiency_ceiling", 0.55))
        efficiency_ok = (
            previous.directional_efficiency <= efficiency_ceiling
            and current.directional_efficiency <= efficiency_ceiling
        )
        distribution_ok = True
        if self._distribution_enabled():
            delta_ceiling = float(self.params.get("bavr_balance_delta_ceiling", 0.30))
            tail_floor = float(self.params.get("bavr_two_sided_tail_floor", 0.05))
            concentration_ceiling = float(self.params.get("bavr_poc_concentration_ceiling", 0.12))
            distribution_ok = (
                abs(previous.delta_ratio) <= delta_ceiling
                and abs(current.delta_ratio) <= delta_ceiling
                and previous.lower_tail_share >= tail_floor
                and previous.upper_tail_share >= tail_floor
                and current.lower_tail_share >= tail_floor
                and current.upper_tail_share >= tail_floor
                and previous.poc_concentration <= concentration_ceiling
                and current.poc_concentration <= concentration_ceiling
            )
        overlap_ok = overlap_ratio >= float(self.params.get("bavr_value_overlap_floor", 0.50))
        passed = (overlap_ok and mutual_poc and efficiency_ok and distribution_ok) if self._balance_enabled() else True
        return passed, {
            "overlap_ratio": overlap_ratio,
            "mutual_poc_acceptance": mutual_poc,
            "efficiency_ok": efficiency_ok,
            "distribution_ok": distribution_ok,
            "previous_efficiency": previous.directional_efficiency,
            "current_efficiency": current.directional_efficiency,
            "previous_delta_ratio": previous.delta_ratio,
            "current_delta_ratio": current.delta_ratio,
            "previous_tail_shares": [previous.lower_tail_share, previous.upper_tail_share],
            "current_tail_shares": [current.lower_tail_share, current.upper_tail_share],
            "trade_distribution_enabled": self._distribution_enabled(),
            "balance_required": self._balance_enabled(),
        }

    def _ingest_completed_profile(
        self,
        profile: AuctionProfile,
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        transitions: list[ScenarioTransition] = []
        if self._excursion is not None:
            transitions.append(self._episode_transition(
                self._excursion,
                self._excursion.state,
                "RESET",
                "NEW_PROFILE_CLOSED_BEFORE_RESPONSE",
                profile.close,
                {"profile_end_ts_ns": profile.end_ts_ns},
            ))
            self._excursion = None
        if self._context is not None:
            transitions.append(self._context_transition(
                self._context,
                "BALANCE_ACTIVE",
                "RESET",
                "BALANCE_CONTEXT_REPLACED_AT_PROFILE_CLOSE",
                profile.close,
                {"replacement_profile_end_ts_ns": profile.end_ts_ns},
            ))
            self._context = None

        if self._history:
            previous = self._history[-1]
            passed, details = self._profile_is_balanced(previous, profile)
            self._context_sequence += 1
            context_id = f"BAVR-BALANCE-{profile.end_ts_ns}-{self._context_sequence:06d}"
            if passed:
                self._context = _BalanceContext(
                    context_id=context_id,
                    profile=profile,
                    previous=previous,
                    created_index=snapshot.index,
                    expires_ts_ns=profile.end_ts_ns + self._period_ns,
                )
                transitions.append(self._context_transition(
                    self._context,
                    "IDLE",
                    "BALANCE_ACTIVE",
                    "ADJACENT_COMPLETED_AUCTIONS_ACCEPTED_SHARED_VALUE",
                    profile.poc,
                    {
                        **details,
                        "profile_end_ts_ns": profile.end_ts_ns,
                        "value_area_low": profile.val,
                        "point_of_control": profile.poc,
                        "value_area_high": profile.vah,
                        "context_expiry_ts_ns": profile.end_ts_ns + self._period_ns,
                    },
                ))
            else:
                transitions.append(ScenarioTransition(
                    scenario_id=context_id,
                    event_type="BAVR_BALANCE_TRANSITION",
                    previous_state="IDLE",
                    next_state="RESET",
                    reason_code="ADJACENT_AUCTIONS_NOT_BALANCED",
                    reference_price=profile.poc,
                    details=details,
                ))
        self._history.append(profile)
        if len(self._history) > 4:
            self._history = self._history[-4:]
        return tuple(transitions)

    def _maybe_start_excursion(
        self,
        snapshot: PrimitiveSnapshot,
        minute: AggMinuteStat,
    ) -> ScenarioTransition | None:
        context = self._context
        if context is None or snapshot.index <= context.created_index:
            return None
        obs = snapshot.observation
        depth = float(self.params.get("bavr_excursion_min_atr", 0.08)) * snapshot.atr
        flow_floor = float(self.params.get("bavr_excursion_flow_ratio", 0.05))
        flow = minute.flow_ratio if self._agg_flow_enabled() else snapshot.flow_ratio
        side: str | None = None
        direction: str | None = None
        edge = 0.0
        extreme = 0.0
        if (
            not context.upper_consumed
            and obs.high >= context.profile.vah + depth
            and flow >= flow_floor
        ):
            context.upper_consumed = True
            side, direction, edge, extreme = "UPPER", "SHORT", context.profile.vah, obs.high
        elif (
            not context.lower_consumed
            and obs.low <= context.profile.val - depth
            and flow <= -flow_floor
        ):
            context.lower_consumed = True
            side, direction, edge, extreme = "LOWER", "LONG", context.profile.val, obs.low
        if side is None or direction is None:
            return None
        self._sequence += 1
        self._excursion = _Excursion(
            scenario_id=f"BAVR-{snapshot.observation.ts_ns}-{self._sequence:06d}",
            side=side,
            direction=direction,
            state="VALUE_EDGE_EXCURSION",
            edge=edge,
            extreme=extreme,
            started_index=snapshot.index,
            started_ts_ns=obs.ts_ns,
        )
        return self._episode_transition(
            self._excursion,
            "IDLE",
            "VALUE_EDGE_EXCURSION",
            "BALANCED_VALUE_EDGE_SWEPT_BY_AGGRESSIVE_FLOW",
            edge,
            {
                "direction": direction,
                "context_id": context.context_id,
                "value_area_low": context.profile.val,
                "point_of_control": context.profile.poc,
                "value_area_high": context.profile.vah,
                "excursion_flow_ratio": flow,
                "agg_trade_flow_enabled": self._agg_flow_enabled(),
                "extreme": extreme,
            },
        )

    def _advance_excursion(
        self,
        snapshot: PrimitiveSnapshot,
        minute: AggMinuteStat,
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        episode = self._excursion
        context = self._context
        if episode is None:
            return ScenarioStep()
        if context is None:
            transition = self._episode_transition(
                episode, episode.state, "RESET", "BALANCE_CONTEXT_NOT_AVAILABLE", snapshot.observation.close, {},
            )
            self._excursion = None
            return ScenarioStep(transitions=(transition,))
        if snapshot.index <= episode.started_index:
            return ScenarioStep()
        obs = snapshot.observation
        flow = minute.flow_ratio if self._agg_flow_enabled() else snapshot.flow_ratio
        elapsed = snapshot.index - episode.started_index
        if elapsed > int(self.params.get("bavr_response_bars", 4)):
            return self._reset_episode(snapshot, "VALUE_RECLAIM_RESPONSE_EXPIRED", {"elapsed_bars": elapsed})

        accept_distance = float(self.params.get("bavr_outside_acceptance_atr", 0.05)) * snapshot.atr
        accept_flow = float(self.params.get("bavr_acceptance_flow_ratio", 0.05))
        if episode.side == "UPPER":
            episode.extreme = max(episode.extreme, obs.high)
            outside = obs.close >= episode.edge + accept_distance and flow >= accept_flow
        else:
            episode.extreme = min(episode.extreme, obs.low)
            outside = obs.close <= episode.edge - accept_distance and flow <= -accept_flow
        episode.outside_closes = episode.outside_closes + 1 if outside else 0
        if episode.outside_closes >= int(self.params.get("bavr_acceptance_closes", 2)):
            return self._reset_episode(
                snapshot,
                "OUTSIDE_VALUE_ACCEPTED_PRICE_DISCOVERY",
                {"outside_closes": episode.outside_closes, "flow_ratio": flow},
            )

        if episode.state == "VALUE_EDGE_EXCURSION":
            reclaim_tolerance = float(self.params.get("bavr_reclaim_tolerance_atr", 0.02)) * snapshot.atr
            reclaim_flow = float(self.params.get("bavr_reclaim_flow_ratio", 0.03))
            if episode.side == "UPPER":
                reclaimed = (
                    obs.close <= episode.edge + reclaim_tolerance
                    and obs.close < obs.open
                    and flow <= -reclaim_flow
                )
            else:
                reclaimed = (
                    obs.close >= episode.edge - reclaim_tolerance
                    and obs.close > obs.open
                    and flow >= reclaim_flow
                )
            if reclaimed:
                previous = episode.state
                episode.state = "VALUE_RECLAIMED"
                episode.reclaim_high = obs.high
                episode.reclaim_low = obs.low
                episode.reclaim_index = snapshot.index
                transition = self._episode_transition(
                    episode,
                    previous,
                    "VALUE_RECLAIMED",
                    "EXCURSION_REJECTED_BACK_INTO_COMPLETED_VALUE",
                    obs.close,
                    {"flow_ratio": flow, "edge": episode.edge, "extreme": episode.extreme},
                )
                return ScenarioStep(transitions=(transition,))
            return ScenarioStep()

        assert episode.reclaim_index is not None
        if snapshot.index <= episode.reclaim_index:
            return ScenarioStep()
        body_floor = float(self.params.get("bavr_response_body_atr", 0.15))
        response_flow = float(self.params.get("bavr_response_flow_ratio", 0.03))
        close_location = float(self.params.get("bavr_response_close_location", 0.62))
        if episode.direction == "SHORT":
            confirmed = (
                obs.close < obs.open
                and obs.close < float(episode.reclaim_low)
                and snapshot.body_atr >= body_floor
                and flow <= -response_flow
                and snapshot.close_location <= 1.0 - close_location
            )
        else:
            confirmed = (
                obs.close > obs.open
                and obs.close > float(episode.reclaim_high)
                and snapshot.body_atr >= body_floor
                and flow >= response_flow
                and snapshot.close_location >= close_location
            )
        if not confirmed:
            return ScenarioStep()
        if not allow_new:
            return self._reset_episode(snapshot, "ENTRY_SLOT_UNAVAILABLE_AT_VALUE_RESPONSE", {})
        return self._emit(snapshot, context, episode, flow)

    def _emit(
        self,
        snapshot: PrimitiveSnapshot,
        context: _BalanceContext,
        episode: _Excursion,
        flow: float,
    ) -> ScenarioStep:
        obs = snapshot.observation
        buffer_value = float(self.params.get("bavr_stop_buffer_atr", 0.08)) * snapshot.atr
        if episode.direction == "SHORT":
            stop = episode.extreme + buffer_value
            candidates = [
                (context.profile.poc, "BALANCED_AUCTION_POINT_OF_CONTROL")
                if obs.low > context.profile.poc else (float("nan"), "OBJECTIVE_ALREADY_TOUCHED"),
                (context.profile.val, "OPPOSITE_VALUE_AREA_EDGE")
                if obs.low > context.profile.val else (float("nan"), "OBJECTIVE_ALREADY_TOUCHED"),
            ]
        else:
            stop = episode.extreme - buffer_value
            candidates = [
                (context.profile.poc, "BALANCED_AUCTION_POINT_OF_CONTROL")
                if obs.high < context.profile.poc else (float("nan"), "OBJECTIVE_ALREADY_TOUCHED"),
                (context.profile.vah, "OPPOSITE_VALUE_AREA_EDGE")
                if obs.high < context.profile.vah else (float("nan"), "OBJECTIVE_ALREADY_TOUCHED"),
            ]
        target = self._select_target(episode.direction, obs.close, stop, candidates)
        if target is None:
            return self._reset_episode(snapshot, "NO_VALUE_OBJECTIVE_WITH_SUFFICIENT_SPACE", {})
        target_price, target_reason = target
        transition = self._episode_transition(
            episode,
            episode.state,
            "ENTRY_ARMED",
            "FAILED_VALUE_DISCOVERY_AND_SEPARATE_ROTATION_RESPONSE_CONFIRMED",
            obs.close,
            {
                "direction": episode.direction,
                "context_id": context.context_id,
                "stop_price": stop,
                "target_price": target_price,
                "target_reason": target_reason,
                "response_flow_ratio": flow,
            },
        )
        signal = ScenarioSignal(
            scenario_id=episode.scenario_id,
            family="BAVR",
            direction=episode.direction,
            observed_ts_ns=obs.ts_ns,
            reference_entry=obs.close,
            stop_price=stop,
            target_price=target_price,
            target_reason=target_reason,
            atr=snapshot.atr,
            liquidity_level=episode.edge,
            details={
                "balance_context_id": context.context_id,
                "profile_end_ts_ns": context.profile.end_ts_ns,
                "value_area_low": context.profile.val,
                "point_of_control": context.profile.poc,
                "value_area_high": context.profile.vah,
                "profile_delta_ratio": context.profile.delta_ratio,
                "profile_directional_efficiency": context.profile.directional_efficiency,
                "excursion_side": episode.side,
                "excursion_extreme": episode.extreme,
                "agg_trade_flow_enabled": self._agg_flow_enabled(),
                "trade_distribution_enabled": self._distribution_enabled(),
                "balance_required": self._balance_enabled(),
            },
        )
        self._excursion = None
        self._cooldown_until = snapshot.index + int(self.params.get("bavr_cooldown_bars", 2))
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
        for price, reason in candidates:
            if price != price:  # NaN marks an objective already touched intrabar.
                continue
            reward = price - entry if direction == "LONG" else entry - price
            if reward > 0.0 and reward / risk >= minimum_rr:
                return price, reason
        return None

    def _reset_episode(
        self,
        snapshot: PrimitiveSnapshot,
        reason: str,
        details: Mapping[str, Any],
    ) -> ScenarioStep:
        episode = self._excursion
        if episode is None:
            return ScenarioStep()
        transition = self._episode_transition(
            episode, episode.state, "RESET", reason, snapshot.observation.close, details,
        )
        self._excursion = None
        self._cooldown_until = snapshot.index + int(self.params.get("bavr_cooldown_bars", 2))
        return ScenarioStep(transitions=(transition,))

    def _reset_context(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        transitions: list[ScenarioTransition] = []
        if self._excursion is not None:
            transitions.append(self._episode_transition(
                self._excursion, self._excursion.state, "RESET", reason, snapshot.observation.close, {},
            ))
            self._excursion = None
        if self._context is not None:
            transitions.append(self._context_transition(
                self._context, "BALANCE_ACTIVE", "RESET", reason, snapshot.observation.close, {},
            ))
            self._context = None
        return ScenarioStep(transitions=tuple(transitions))

    @staticmethod
    def _context_transition(
        context: _BalanceContext,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=context.context_id,
            event_type="BAVR_BALANCE_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )

    @staticmethod
    def _episode_transition(
        episode: _Excursion,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="BAVR_SCENARIO_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )
