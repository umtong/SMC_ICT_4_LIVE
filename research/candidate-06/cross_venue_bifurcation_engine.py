"""Cross-venue spot/perpetual price-discovery bifurcation for candidate-06.

The engine trades BTCUSDT perpetuals only.  Binance BTCUSDT spot bars are a
synchronized context feed observed at the same completed one-minute timestamp.
A completed prior auction defines venue-specific liquidity boundaries.  The
engine then distinguishes two causal mechanisms:

* a perpetual-only boundary sweep with an extreme basis residual which spot
  does not confirm, followed by a separate perpetual reclaim response;
* a spot-led accepted boundary break while perpetuals lag, followed by a
  separate perpetual catch-up response.

The initiating bar cannot emit a trade.  Basis and activity baselines are
computed from observations completed strictly before the current decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import log
from statistics import median
from typing import Any, Mapping

from causal_clock import source_bar_datetime
from lrb_types import BarObservation, PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


@dataclass(frozen=True, slots=True)
class _JointAuction:
    bucket: int
    start_ts_ns: int
    end_ts_ns: int
    perp_open: float
    perp_high: float
    perp_low: float
    perp_close: float
    spot_open: float
    spot_high: float
    spot_low: float
    spot_close: float

    @property
    def perp_width(self) -> float:
        return max(self.perp_high - self.perp_low, 0.0)

    @property
    def spot_width(self) -> float:
        return max(self.spot_high - self.spot_low, 0.0)


@dataclass(slots=True)
class _Episode:
    scenario_id: str
    family: str
    side: str
    direction: str
    state: str
    prior_auction_end_ts_ns: int
    perp_level: float
    spot_level: float
    perp_range_high: float
    perp_range_low: float
    spot_range_high: float
    spot_range_low: float
    started_index: int
    started_ts_ns: int
    basis_z_at_start: float | None
    basis_residual_at_start: float
    event_spot_close: float
    event_perp_close: float
    high: float
    low: float


class CrossVenuePriceDiscoveryBifurcationEngine:
    """Trade only cross-venue divergence followed by a distinct response."""

    def __init__(
        self,
        params: Mapping[str, Any],
        *,
        spot_observations: Mapping[int, BarObservation],
    ) -> None:
        self.params = dict(params)
        self._spot = dict(spot_observations)
        self._period = int(self.params.get("cvpd_period_minutes", 15))
        if self._period < 5 or 1440 % self._period != 0:
            raise ValueError("cvpd_period_minutes must be at least 5 and divide one UTC day")
        self._entry_window = min(
            self._period,
            max(1, int(self.params.get("cvpd_entry_window_minutes", self._period - 2))),
        )
        self._current: dict[str, float | int] | None = None
        self._current_bucket: int | None = None
        self._previous: _JointAuction | None = None
        self._prior_perp_close: float | None = None
        self._prior_spot_close: float | None = None
        self._basis_history: list[float] = []
        self._spot_true_ranges: list[float] = []
        self._spot_volumes: list[float] = []
        self._episode: _Episode | None = None
        self._sequence = 0
        self._cooldown_until = -1
        self._consumed: set[tuple[int, str]] = set()

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        spot = self._spot.get(snapshot.observation.ts_ns)
        if spot is None:
            raise RuntimeError(
                f"missing synchronized Binance spot context for ts_ns={snapshot.observation.ts_ns}",
            )
        self._roll_before(snapshot, spot)
        basis_residual = self._basis_residual(snapshot.observation.close, spot.close)
        basis_z = self._basis_z_prior_only(basis_residual)
        spot_atr = self._spot_atr()
        spot_rel_volume = self._spot_relative_volume(spot.volume)

        transitions: list[ScenarioTransition] = []
        signal: ScenarioSignal | None = None
        if self._episode is not None:
            advanced = self._advance_episode(
                snapshot,
                spot,
                basis_residual=basis_residual,
                basis_z=basis_z,
                spot_atr=spot_atr,
                allow_new=allow_new,
            )
            transitions.extend(advanced.transitions)
            signal = advanced.signal

        if (
            signal is None
            and self._episode is None
            and allow_new
            and snapshot.index >= self._cooldown_until
        ):
            started = self._maybe_start_episode(
                snapshot,
                spot,
                basis_residual=basis_residual,
                basis_z=basis_z,
                spot_atr=spot_atr,
                spot_rel_volume=spot_rel_volume,
            )
            if started is not None:
                transitions.append(started)

        self._accumulate_after(snapshot.observation, spot)
        self._append_prior_only_histories(snapshot.observation, spot, basis_residual)
        self._prior_perp_close = snapshot.observation.close
        self._prior_spot_close = spot.close
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

    @staticmethod
    def _basis_residual(perp: float, spot: float) -> float:
        if perp <= 0.0 or spot <= 0.0:
            raise ValueError("spot and perpetual closes must be positive")
        return log(perp / spot)

    def _basis_z_prior_only(self, current: float) -> float | None:
        lookback = int(self.params.get("cvpd_basis_lookback", 120))
        minimum = int(self.params.get("cvpd_basis_min_history", 60))
        history = self._basis_history[-lookback:]
        if len(history) < minimum:
            return None
        center = median(history)
        mad = median(abs(value - center) for value in history)
        scale = 1.4826 * mad
        if scale <= 1e-12:
            return 0.0
        return (current - center) / scale

    def _spot_atr(self) -> float | None:
        lookback = int(self.params.get("cvpd_spot_atr_bars", 20))
        if len(self._spot_true_ranges) < lookback:
            return None
        return sum(self._spot_true_ranges[-lookback:]) / lookback

    def _spot_relative_volume(self, current: float) -> float | None:
        lookback = int(self.params.get("cvpd_spot_volume_bars", 60))
        if len(self._spot_volumes) < lookback:
            return None
        baseline = median(self._spot_volumes[-lookback:])
        return current / baseline if baseline > 0.0 else None

    @staticmethod
    def _spot_flow(spot: BarObservation) -> float:
        return spot.flow_ratio

    def _roll_before(self, snapshot: PrimitiveSnapshot, spot: BarObservation) -> None:
        source = source_bar_datetime(snapshot.observation.ts_ns)
        source_minute = int(source.timestamp() // 60)
        bucket = source_minute // self._period
        if self._current_bucket is None:
            self._current_bucket = bucket
            return
        if bucket == self._current_bucket:
            return
        current = self._current
        if current is not None:
            self._previous = _JointAuction(
                bucket=int(current["bucket"]),
                start_ts_ns=int(current["start_ts_ns"]),
                end_ts_ns=int(current["end_ts_ns"]),
                perp_open=float(current["perp_open"]),
                perp_high=float(current["perp_high"]),
                perp_low=float(current["perp_low"]),
                perp_close=float(current["perp_close"]),
                spot_open=float(current["spot_open"]),
                spot_high=float(current["spot_high"]),
                spot_low=float(current["spot_low"]),
                spot_close=float(current["spot_close"]),
            )
        self._current_bucket = bucket
        self._current = None
        self._consumed.clear()

    def _accumulate_after(self, perp: BarObservation, spot: BarObservation) -> None:
        source = source_bar_datetime(perp.ts_ns)
        source_minute = int(source.timestamp() // 60)
        bucket = source_minute // self._period
        if self._current is None:
            self._current = {
                "bucket": bucket,
                "start_ts_ns": perp.ts_ns,
                "end_ts_ns": perp.ts_ns,
                "perp_open": perp.open,
                "perp_high": perp.high,
                "perp_low": perp.low,
                "perp_close": perp.close,
                "spot_open": spot.open,
                "spot_high": spot.high,
                "spot_low": spot.low,
                "spot_close": spot.close,
            }
            return
        current = self._current
        current["end_ts_ns"] = perp.ts_ns
        current["perp_high"] = max(float(current["perp_high"]), perp.high)
        current["perp_low"] = min(float(current["perp_low"]), perp.low)
        current["perp_close"] = perp.close
        current["spot_high"] = max(float(current["spot_high"]), spot.high)
        current["spot_low"] = min(float(current["spot_low"]), spot.low)
        current["spot_close"] = spot.close

    def _append_prior_only_histories(
        self,
        perp: BarObservation,
        spot: BarObservation,
        basis_residual: float,
    ) -> None:
        previous_spot_close = self._prior_spot_close if self._prior_spot_close is not None else spot.open
        true_range = max(
            spot.high - spot.low,
            abs(spot.high - previous_spot_close),
            abs(spot.low - previous_spot_close),
        )
        self._basis_history.append(basis_residual)
        self._spot_true_ranges.append(true_range)
        self._spot_volumes.append(spot.volume)
        capacity = max(
            360,
            int(self.params.get("cvpd_basis_lookback", 120)) + 32,
            int(self.params.get("cvpd_spot_volume_bars", 60)) + 32,
        )
        self._basis_history = self._basis_history[-capacity:]
        self._spot_true_ranges = self._spot_true_ranges[-capacity:]
        self._spot_volumes = self._spot_volumes[-capacity:]

    def _bucket_position(self, ts_ns: int) -> int:
        source = source_bar_datetime(ts_ns)
        return int(source.timestamp() // 60) % self._period

    def _maybe_start_episode(
        self,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        *,
        basis_residual: float,
        basis_z: float | None,
        spot_atr: float | None,
        spot_rel_volume: float | None,
    ) -> ScenarioTransition | None:
        previous = self._previous
        if (
            previous is None
            or spot_atr is None
            or basis_z is None
            or self._prior_perp_close is None
            or self._prior_spot_close is None
            or self._bucket_position(snapshot.observation.ts_ns) >= self._entry_window
        ):
            return None
        if previous.perp_width <= 0.0 or previous.spot_width <= 0.0:
            return None

        obs = snapshot.observation
        min_sweep = float(self.params.get("cvpd_min_sweep_atr", 0.10))
        confirm_tolerance = float(self.params.get("cvpd_confirm_tolerance_atr", 0.03))
        spot_accept = float(self.params.get("cvpd_spot_accept_close_atr", 0.05))
        perp_upper = (
            self._prior_perp_close <= previous.perp_high
            and obs.high >= previous.perp_high + min_sweep * snapshot.atr
        )
        perp_lower = (
            self._prior_perp_close >= previous.perp_low
            and obs.low <= previous.perp_low - min_sweep * snapshot.atr
        )
        spot_upper = (
            self._prior_spot_close <= previous.spot_high
            and spot.high >= previous.spot_high + min_sweep * spot_atr
        )
        spot_lower = (
            self._prior_spot_close >= previous.spot_low
            and spot.low <= previous.spot_low - min_sweep * spot_atr
        )
        spot_upper_accepted = (
            spot_upper
            and spot.close >= previous.spot_high + spot_accept * spot_atr
            and spot.close > spot.open
        )
        spot_lower_accepted = (
            spot_lower
            and spot.close <= previous.spot_low - spot_accept * spot_atr
            and spot.close < spot.open
        )
        perp_upper_confirmed = obs.high >= previous.perp_high + confirm_tolerance * snapshot.atr
        perp_lower_confirmed = obs.low <= previous.perp_low - confirm_tolerance * snapshot.atr

        # Simultaneous same-side discovery is not a divergence event.
        if (perp_upper and spot_upper) or (perp_lower and spot_lower):
            side = "UPPER" if perp_upper and spot_upper else "LOWER"
            key = (previous.end_ts_ns, side)
            if key not in self._consumed:
                self._consumed.add(key)
                self._sequence += 1
                episode = _Episode(
                    scenario_id=f"CVPD-AMB-{obs.ts_ns}-{self._sequence:06d}",
                    family="AMBIGUOUS",
                    side=side,
                    direction="NONE",
                    state="RESET",
                    prior_auction_end_ts_ns=previous.end_ts_ns,
                    perp_level=previous.perp_high if side == "UPPER" else previous.perp_low,
                    spot_level=previous.spot_high if side == "UPPER" else previous.spot_low,
                    perp_range_high=previous.perp_high,
                    perp_range_low=previous.perp_low,
                    spot_range_high=previous.spot_high,
                    spot_range_low=previous.spot_low,
                    started_index=snapshot.index,
                    started_ts_ns=obs.ts_ns,
                    basis_z_at_start=basis_z,
                    basis_residual_at_start=basis_residual,
                    event_spot_close=spot.close,
                    event_perp_close=obs.close,
                    high=obs.high,
                    low=obs.low,
                )
                return self._transition(
                    episode,
                    "IDLE",
                    "RESET",
                    "SPOT_AND_PERPETUAL_CONFIRMED_SAME_LIQUIDITY_EVENT",
                    episode.perp_level,
                    {"basis_z": basis_z, "side": side},
                )
            return None

        use_basis = bool(self.params.get("cvpd_use_basis_filter", True))
        basis_threshold = float(self.params.get("cvpd_basis_z_threshold", 1.50))
        lag_ceiling = float(self.params.get("cvpd_lag_basis_z_ceiling", 0.50))
        perp_shock_flow = float(self.params.get("cvpd_perp_shock_flow_ratio", 0.03))
        spot_flow_floor = float(self.params.get("cvpd_spot_flow_ratio", 0.04))
        spot_body_floor = float(self.params.get("cvpd_spot_body_atr", 0.25))
        spot_volume_floor = float(self.params.get("cvpd_spot_relative_volume", 0.90))
        spot_body_atr = abs(spot.close - spot.open) / spot_atr if spot_atr > 0.0 else 0.0
        spot_flow = self._spot_flow(spot)
        spot_volume_ok = spot_rel_volume is not None and spot_rel_volume >= spot_volume_floor

        candidates: list[tuple[str, str, str, float, float]] = []
        if bool(self.params.get("cvpd_enable_perp_reversion", True)):
            if (
                perp_upper
                and not spot_upper
                and obs.flow_ratio >= perp_shock_flow
                and ((basis_z >= basis_threshold) if use_basis else True)
            ):
                candidates.append(("PERP_ONLY_REVERSION", "UPPER", "SHORT", previous.perp_high, previous.spot_high))
            if (
                perp_lower
                and not spot_lower
                and obs.flow_ratio <= -perp_shock_flow
                and ((basis_z <= -basis_threshold) if use_basis else True)
            ):
                candidates.append(("PERP_ONLY_REVERSION", "LOWER", "LONG", previous.perp_low, previous.spot_low))

        if bool(self.params.get("cvpd_enable_spot_relay", True)):
            if (
                spot_upper_accepted
                and not perp_upper_confirmed
                and spot_body_atr >= spot_body_floor
                and spot_flow >= spot_flow_floor
                and spot_volume_ok
                and ((basis_z <= lag_ceiling) if use_basis else True)
            ):
                candidates.append(("SPOT_LED_RELAY", "UPPER", "LONG", previous.perp_high, previous.spot_high))
            if (
                spot_lower_accepted
                and not perp_lower_confirmed
                and spot_body_atr >= spot_body_floor
                and spot_flow <= -spot_flow_floor
                and spot_volume_ok
                and ((basis_z >= -lag_ceiling) if use_basis else True)
            ):
                candidates.append(("SPOT_LED_RELAY", "LOWER", "SHORT", previous.perp_low, previous.spot_low))

        if len(candidates) != 1:
            return None
        family, side, direction, perp_level, spot_level = candidates[0]
        key = (previous.end_ts_ns, side)
        if key in self._consumed:
            return None
        self._consumed.add(key)
        self._sequence += 1
        state = f"{family}_{side}_RESPONSE_OBSERVATION"
        self._episode = _Episode(
            scenario_id=f"CVPD-{obs.ts_ns}-{self._sequence:06d}",
            family=family,
            side=side,
            direction=direction,
            state=state,
            prior_auction_end_ts_ns=previous.end_ts_ns,
            perp_level=perp_level,
            spot_level=spot_level,
            perp_range_high=previous.perp_high,
            perp_range_low=previous.perp_low,
            spot_range_high=previous.spot_high,
            spot_range_low=previous.spot_low,
            started_index=snapshot.index,
            started_ts_ns=obs.ts_ns,
            basis_z_at_start=basis_z,
            basis_residual_at_start=basis_residual,
            event_spot_close=spot.close,
            event_perp_close=obs.close,
            high=obs.high,
            low=obs.low,
        )
        return self._transition(
            self._episode,
            "IDLE",
            state,
            (
                "PERPETUAL_ONLY_LIQUIDITY_SWEEP_WITH_UNCONFIRMED_SPOT_AND_EXTREME_BASIS"
                if family == "PERP_ONLY_REVERSION"
                else "SPOT_ACCEPTED_LIQUIDITY_BREAK_WHILE_PERPETUAL_LAGGED"
            ),
            perp_level,
            {
                "family": family,
                "side": side,
                "direction": direction,
                "basis_residual": basis_residual,
                "basis_z": basis_z,
                "spot_flow_ratio": spot_flow,
                "spot_body_atr": spot_body_atr,
                "spot_relative_volume": spot_rel_volume,
                "perp_flow_ratio": obs.flow_ratio,
                "prior_auction_end_ts_ns": previous.end_ts_ns,
            },
        )

    def _advance_episode(
        self,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        *,
        basis_residual: float,
        basis_z: float | None,
        spot_atr: float | None,
        allow_new: bool,
    ) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        if snapshot.index <= episode.started_index:
            return ScenarioStep()
        obs = snapshot.observation
        episode.high = max(episode.high, obs.high)
        episode.low = min(episode.low, obs.low)
        elapsed = snapshot.index - episode.started_index
        if elapsed > int(self.params.get("cvpd_response_bars", 3)):
            return self._reset_episode(snapshot, "CROSS_VENUE_RESPONSE_EXPIRED", {"elapsed_bars": elapsed})
        if spot_atr is None:
            return ScenarioStep()

        confirm_tolerance = float(self.params.get("cvpd_confirm_tolerance_atr", 0.03))
        response_body = float(self.params.get("cvpd_response_body_atr", 0.15))
        response_flow = float(self.params.get("cvpd_response_flow_ratio", 0.02))
        close_location = float(self.params.get("cvpd_response_close_location", 0.60))
        accept_distance = float(self.params.get("cvpd_perp_accept_close_atr", 0.04)) * snapshot.atr

        if episode.family == "PERP_ONLY_REVERSION":
            if episode.side == "UPPER":
                spot_confirmed = (
                    spot.high >= episode.spot_level + confirm_tolerance * spot_atr
                    or spot.close > episode.spot_level
                )
                confirmed = (
                    obs.close < episode.perp_level
                    and obs.close < obs.open
                    and snapshot.body_atr >= response_body
                    and snapshot.flow_ratio <= -response_flow
                    and snapshot.close_location <= 1.0 - close_location
                )
            else:
                spot_confirmed = (
                    spot.low <= episode.spot_level - confirm_tolerance * spot_atr
                    or spot.close < episode.spot_level
                )
                confirmed = (
                    obs.close > episode.perp_level
                    and obs.close > obs.open
                    and snapshot.body_atr >= response_body
                    and snapshot.flow_ratio >= response_flow
                    and snapshot.close_location >= close_location
                )
            if spot_confirmed:
                return self._reset_episode(
                    snapshot,
                    "SPOT_CONFIRMATION_INVALIDATED_PERPETUAL_FALSE_BREAK",
                    {"spot_close": spot.close, "basis_z": basis_z},
                )
        elif episode.family == "SPOT_LED_RELAY":
            if episode.side == "UPPER":
                spot_failed = spot.close < episode.spot_level - confirm_tolerance * spot_atr
                confirmed = (
                    obs.close >= episode.perp_level + accept_distance
                    and obs.close > obs.open
                    and snapshot.body_atr >= response_body
                    and snapshot.flow_ratio >= response_flow
                    and snapshot.close_location >= close_location
                )
            else:
                spot_failed = spot.close > episode.spot_level + confirm_tolerance * spot_atr
                confirmed = (
                    obs.close <= episode.perp_level - accept_distance
                    and obs.close < obs.open
                    and snapshot.body_atr >= response_body
                    and snapshot.flow_ratio <= -response_flow
                    and snapshot.close_location <= 1.0 - close_location
                )
            if spot_failed:
                return self._reset_episode(
                    snapshot,
                    "SPOT_ACCEPTANCE_FAILED_BEFORE_PERPETUAL_RELAY",
                    {"spot_close": spot.close, "basis_z": basis_z},
                )
        else:
            return self._reset_episode(snapshot, "UNSUPPORTED_CROSS_VENUE_EPISODE", {})

        if not confirmed:
            return ScenarioStep()
        if not allow_new:
            return self._reset_episode(snapshot, "ENTRY_SLOT_UNAVAILABLE_AT_CROSS_VENUE_RESPONSE", {})
        return self._emit(snapshot, spot, basis_residual=basis_residual, basis_z=basis_z)

    def _emit(
        self,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        *,
        basis_residual: float,
        basis_z: float | None,
    ) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        obs = snapshot.observation
        buffer_value = float(self.params.get("cvpd_stop_buffer_atr", 0.08)) * snapshot.atr
        projection = float(self.params.get("cvpd_projection_fraction", 0.50)) * (
            episode.perp_range_high - episode.perp_range_low
        )
        if episode.direction == "LONG":
            if episode.family == "PERP_ONLY_REVERSION":
                stop = episode.low - buffer_value
                candidates = [
                    ((episode.perp_range_high + episode.perp_range_low) / 2.0, "PRIOR_AUCTION_EQUILIBRIUM"),
                    (episode.perp_range_high, "PRIOR_AUCTION_OPPOSITE_LIQUIDITY"),
                    (snapshot.upper_fast, "PRIOR_FAST_BUYSIDE_LIQUIDITY"),
                ]
            else:
                stop = min(episode.low, episode.perp_level - buffer_value)
                spot_implied = episode.perp_level * (spot.close / episode.spot_level)
                candidates = [
                    (spot_implied, "SPOT_IMPLIED_PERPETUAL_FAIR_VALUE"),
                    (episode.perp_level + projection, "ACCEPTED_AUCTION_PROJECTION"),
                    (snapshot.upper_fast, "PRIOR_FAST_BUYSIDE_LIQUIDITY"),
                    (snapshot.upper_slow, "PRIOR_SLOW_BUYSIDE_LIQUIDITY"),
                ]
        else:
            if episode.family == "PERP_ONLY_REVERSION":
                stop = episode.high + buffer_value
                candidates = [
                    ((episode.perp_range_high + episode.perp_range_low) / 2.0, "PRIOR_AUCTION_EQUILIBRIUM"),
                    (episode.perp_range_low, "PRIOR_AUCTION_OPPOSITE_LIQUIDITY"),
                    (snapshot.lower_fast, "PRIOR_FAST_SELLSIDE_LIQUIDITY"),
                ]
            else:
                stop = max(episode.high, episode.perp_level + buffer_value)
                spot_implied = episode.perp_level * (spot.close / episode.spot_level)
                candidates = [
                    (spot_implied, "SPOT_IMPLIED_PERPETUAL_FAIR_VALUE"),
                    (episode.perp_level - projection, "ACCEPTED_AUCTION_PROJECTION"),
                    (snapshot.lower_fast, "PRIOR_FAST_SELLSIDE_LIQUIDITY"),
                    (snapshot.lower_slow, "PRIOR_SLOW_SELLSIDE_LIQUIDITY"),
                ]
        target = self._select_target(episode.direction, obs.close, stop, candidates)
        if target is None:
            return self._reset_episode(
                snapshot,
                "NO_CROSS_VENUE_OBJECTIVE_WITH_SUFFICIENT_SPACE",
                {"stop_price": stop, "candidate_count": len(candidates)},
            )
        target_price, target_reason = target
        transition = self._transition(
            episode,
            episode.state,
            "ENTRY_ARMED",
            "CROSS_VENUE_BIFURCATION_AND_SEPARATE_RESPONSE_CONFIRMED",
            obs.close,
            {
                "direction": episode.direction,
                "family": episode.family,
                "stop_price": stop,
                "target_price": target_price,
                "target_reason": target_reason,
                "basis_residual_at_start": episode.basis_residual_at_start,
                "basis_z_at_start": episode.basis_z_at_start,
                "basis_residual_at_response": basis_residual,
                "basis_z_at_response": basis_z,
            },
        )
        signal = ScenarioSignal(
            scenario_id=episode.scenario_id,
            family=("CVPD_R" if episode.family == "PERP_ONLY_REVERSION" else "CVPD_C"),
            direction=episode.direction,
            observed_ts_ns=obs.ts_ns,
            reference_entry=obs.close,
            stop_price=stop,
            target_price=target_price,
            target_reason=target_reason,
            atr=snapshot.atr,
            liquidity_level=episode.perp_level,
            details={
                "cross_venue_family": episode.family,
                "side": episode.side,
                "prior_auction_end_ts_ns": episode.prior_auction_end_ts_ns,
                "perp_level": episode.perp_level,
                "spot_level": episode.spot_level,
                "event_perp_close": episode.event_perp_close,
                "event_spot_close": episode.event_spot_close,
                "response_perp_close": obs.close,
                "response_spot_close": spot.close,
                "basis_z_at_start": episode.basis_z_at_start,
                "basis_z_at_response": basis_z,
                "synchronized_context_contract": "same completed one-minute timestamp on Binance spot and USDT-M perpetual",
            },
        )
        self._episode = None
        self._cooldown_until = snapshot.index + int(self.params.get("cvpd_cooldown_bars", 2))
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
        minimum_rr = float(self.params.get("minimum_structural_rr", 0.75))
        valid: list[tuple[float, str]] = []
        for price, reason in candidates:
            if price is None:
                continue
            reward = float(price) - entry if direction == "LONG" else entry - float(price)
            if reward > 0.0 and reward / risk >= minimum_rr:
                valid.append((float(price), reason))
        valid.sort(key=lambda value: abs(value[0] - entry))
        return valid[0] if valid else None

    def _reset_episode(
        self,
        snapshot: PrimitiveSnapshot,
        reason: str,
        details: Mapping[str, Any],
    ) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        transition = self._transition(
            episode,
            episode.state,
            "RESET",
            reason,
            snapshot.observation.close,
            details,
        )
        self._episode = None
        self._cooldown_until = snapshot.index + int(self.params.get("cvpd_cooldown_bars", 2))
        return ScenarioStep(transitions=(transition,))

    @staticmethod
    def _transition(
        episode: _Episode,
        previous_state: str,
        next_state: str,
        reason: str,
        reference_price: float | None,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="CVPD_TRANSITION",
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference_price,
            details=dict(details),
        )
