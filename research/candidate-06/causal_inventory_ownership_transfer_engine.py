"""Causal Inventory Ownership Transfer (CIOT) engine for candidate-06.

This state machine does not intersect two independent pattern filters. It
follows one economic episode:

* a completed extreme OI change identifies forced inventory removal or fresh
  inventory formation;
* synchronized Binance spot/perpetual bars identify which venue first owned the
  external-liquidity event;
* the old auction must be explicitly invalidated or accepted;
* a later completed OI observation must show counter-inventory rebuilding or
  retention of the fresh inventory;
* the first pullback and renewed initiative of that same ownership episode
  define the entry leg, its invalidation, and a still-live objective.

No initiating bar can trade. Every baseline excludes the current observation.
Orders, fills, positions, commissions, slippage and NAV stay in NautilusTrader.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Mapping

from causal_clock import source_bar_datetime
from futures_metrics_data import FuturesMetric
from lrb_types import (
    BarObservation,
    PrimitiveSnapshot,
    ScenarioSignal,
    ScenarioStep,
    ScenarioTransition,
)


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


@dataclass(slots=True)
class _OwnershipEpisode:
    scenario_id: str
    branch: str  # REVERSAL or CONTINUATION
    side: str  # BUY or SELL initiating impulse
    direction: str  # LONG or SHORT traded initiative
    state: str
    started_index: int
    started_ts_ns: int
    prior_auction_end_ts_ns: int
    perp_boundary: float
    spot_boundary: float
    prior_perp_high: float
    prior_perp_low: float
    event_open: float
    event_high: float
    event_low: float
    event_close: float
    event_mid: float
    event_range: float
    event_extreme: float
    atr: float
    baseline_oi: float
    event_oi: float
    oi_change: float
    oi_threshold: float
    spot_owner_ts_ns: int | None
    perp_owner_ts_ns: int | None
    high_since_event: float
    low_since_event: float
    inventory_confirmed: bool = False
    auction_confirmed: bool = False
    auction_confirmation_index: int | None = None
    auction_confirmation_high: float | None = None
    auction_confirmation_low: float | None = None
    pullback_index: int | None = None
    pullback_high: float | None = None
    pullback_low: float | None = None
    signal_index: int | None = None


class CausalInventoryOwnershipTransferEngine:
    """Trade only a completed cross-venue inventory-ownership transfer."""

    def __init__(
        self,
        params: Mapping[str, Any],
        *,
        spot_observations: Mapping[int, BarObservation],
        metrics: Mapping[int, FuturesMetric],
    ) -> None:
        self.params = dict(params)
        self._spot = dict(spot_observations)
        self._metrics = dict(metrics)
        self._period = int(self.params.get("ciot_auction_period_minutes", 15))
        if self._period < 5 or 1440 % self._period != 0:
            raise ValueError("ciot_auction_period_minutes must be >=5 and divide one UTC day")
        self._entry_window = min(
            self._period,
            max(1, int(self.params.get("ciot_entry_window_minutes", self._period - 2))),
        )
        self._current_bucket: int | None = None
        self._current: dict[str, float | int] | None = None
        self._previous: _JointAuction | None = None
        self._bars: deque[PrimitiveSnapshot] = deque(maxlen=5)
        self._spot_bars: deque[BarObservation] = deque(maxlen=5)
        self._spot_true_ranges: deque[float] = deque(maxlen=240)
        self._prior_spot_close: float | None = None
        self._last_metric: FuturesMetric | None = None
        self._increase_history: deque[tuple[int, float]] = deque()
        self._drop_history: deque[tuple[int, float]] = deque()
        self._episode: _OwnershipEpisode | None = None
        self._sequence = 0
        self._cooldown_until = -1
        self._consumed: set[tuple[int, str, str]] = set()

    @staticmethod
    def _quantile(values: list[float], q: float) -> float:
        ordered = sorted(values)
        if not ordered:
            return 0.0
        position = min(max(q, 0.0), 1.0) * (len(ordered) - 1)
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    def _threshold(self, regime: str) -> float | None:
        history = self._increase_history if regime == "BUILD" else self._drop_history
        values = [value for _, value in history]
        minimum = int(self.params.get("ciot_min_prior_oi_changes", 36))
        if len(values) < minimum:
            return None
        return self._quantile(
            values,
            float(self.params.get("ciot_oi_change_quantile", 0.85)),
        )

    def _prune_histories(self, index: int) -> None:
        cutoff = index - int(self.params.get("ciot_oi_history_minutes", 1440))
        for history in (self._increase_history, self._drop_history):
            while history and history[0][0] < cutoff:
                history.popleft()

    def _ingest_metric_history(
        self,
        snapshot: PrimitiveSnapshot,
        metric: FuturesMetric | None,
    ) -> None:
        if metric is None:
            return
        prior = self._last_metric
        if prior is not None and prior.open_interest > 0.0:
            change = (metric.open_interest - prior.open_interest) / prior.open_interest
            if change > 0.0:
                self._increase_history.append((snapshot.index, change))
            elif change < 0.0:
                self._drop_history.append((snapshot.index, -change))
        self._last_metric = metric
        self._prune_histories(snapshot.index)

    def _spot_atr(self) -> float | None:
        lookback = int(self.params.get("ciot_spot_atr_bars", 20))
        if len(self._spot_true_ranges) < lookback:
            return None
        values = list(self._spot_true_ranges)
        return sum(values[-lookback:]) / lookback

    def _append_spot_true_range(self, spot: BarObservation) -> None:
        previous = self._prior_spot_close if self._prior_spot_close is not None else spot.open
        self._spot_true_ranges.append(
            max(
                spot.high - spot.low,
                abs(spot.high - previous),
                abs(spot.low - previous),
            ),
        )
        self._prior_spot_close = spot.close

    def _bucket_position(self, ts_ns: int) -> int:
        source = source_bar_datetime(ts_ns)
        return int(source.timestamp() // 60) % self._period

    def _roll_before(
        self,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
    ) -> None:
        source = source_bar_datetime(snapshot.observation.ts_ns)
        bucket = int(source.timestamp() // 60) // self._period
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

    def _accumulate_after(
        self,
        perp: BarObservation,
        spot: BarObservation,
    ) -> None:
        source = source_bar_datetime(perp.ts_ns)
        bucket = int(source.timestamp() // 60) // self._period
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

    @staticmethod
    def _context_transition(
        episode: _OwnershipEpisode,
        previous: str,
        next_state: str,
        reason: str,
        reference: float | None,
        details: Mapping[str, Any] | None = None,
    ) -> ScenarioTransition:
        episode.state = next_state
        return ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="CIOT_CONTEXT_TRANSITION",
            previous_state=previous,
            next_state=next_state,
            reason_code=reason,
            reference_price=reference,
            details=dict(details or {}),
        )

    @staticmethod
    def _entry_scenario_id(episode: _OwnershipEpisode) -> str:
        return f"{episode.scenario_id}:ENTRY"

    @classmethod
    def _entry_transition(
        cls,
        episode: _OwnershipEpisode,
        *,
        reason: str,
        reference: float,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=cls._entry_scenario_id(episode),
            event_type="CIOT_ENTRY_TRANSITION",
            previous_state="IDLE",
            next_state="ENTRY_ARMED",
            reason_code=reason,
            reference_price=reference,
            details={
                "context_scenario_id": episode.scenario_id,
                "branch": episode.branch,
                **dict(details),
            },
        )

    def _five_minute_impulse(
        self,
    ) -> tuple[float, float, float, float] | None:
        if len(self._bars) < 5:
            return None
        bars = list(self._bars)
        return (
            bars[0].observation.open,
            max(item.observation.high for item in bars),
            min(item.observation.low for item in bars),
            bars[-1].observation.close,
        )

    def _first_crossing(
        self,
        *,
        market: str,
        side: str,
        level: float,
        distance: float,
        accepted: bool,
    ) -> int | None:
        if market == "PERP":
            observations = [item.observation for item in self._bars]
        elif market == "SPOT":
            observations = list(self._spot_bars)
        else:
            raise ValueError(f"unsupported market: {market}")
        for observation in observations:
            if side == "BUY":
                crossed = (
                    observation.close >= level + distance
                    if accepted
                    else observation.high >= level + distance
                )
            else:
                crossed = (
                    observation.close <= level - distance
                    if accepted
                    else observation.low <= level - distance
                )
            if crossed:
                return observation.ts_ns
        return None

    def _maybe_start_episode(
        self,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        metric: FuturesMetric,
    ) -> ScenarioTransition | None:
        prior_metric = self._last_metric
        previous = self._previous
        spot_atr = self._spot_atr()
        if (
            prior_metric is None
            or prior_metric.open_interest <= 0.0
            or previous is None
            or previous.perp_width <= 0.0
            or spot_atr is None
            or len(self._bars) < 5
            or self._bucket_position(snapshot.observation.ts_ns) >= self._entry_window
        ):
            return None
        change = (metric.open_interest - prior_metric.open_interest) / prior_metric.open_interest
        if change == 0.0:
            return None
        regime = "BUILD" if change > 0.0 else "UNWIND"
        if regime == "BUILD" and not bool(self.params.get("ciot_enable_continuation", True)):
            return None
        if regime == "UNWIND" and not bool(self.params.get("ciot_enable_reversal", True)):
            return None
        threshold = self._threshold(regime)
        if threshold is None or abs(change) < threshold:
            return None
        impulse = self._five_minute_impulse()
        if impulse is None:
            return None
        event_open, event_high, event_low, event_close = impulse
        move_atr = (event_close - event_open) / max(snapshot.atr, 1e-12)
        minimum_move = float(self.params.get("ciot_event_move_atr", 0.30))
        metric_flow_floor = float(self.params.get("ciot_metric_flow_floor", 0.06))
        metric_flow = metric.signed_taker_ratio
        if move_atr >= minimum_move and metric_flow >= metric_flow_floor:
            side = "BUY"
        elif move_atr <= -minimum_move and metric_flow <= -metric_flow_floor:
            side = "SELL"
        else:
            return None

        min_sweep = float(self.params.get("ciot_min_sweep_atr", 0.10))
        accept_distance = float(self.params.get("ciot_accept_close_atr", 0.05))
        perp_boundary = previous.perp_high if side == "BUY" else previous.perp_low
        spot_boundary = previous.spot_high if side == "BUY" else previous.spot_low
        perp_sweep_ts = self._first_crossing(
            market="PERP",
            side=side,
            level=perp_boundary,
            distance=min_sweep * snapshot.atr,
            accepted=False,
        )
        perp_accept_ts = self._first_crossing(
            market="PERP",
            side=side,
            level=perp_boundary,
            distance=accept_distance * snapshot.atr,
            accepted=True,
        )
        spot_sweep_ts = self._first_crossing(
            market="SPOT",
            side=side,
            level=spot_boundary,
            distance=min_sweep * spot_atr,
            accepted=False,
        )
        spot_accept_ts = self._first_crossing(
            market="SPOT",
            side=side,
            level=spot_boundary,
            distance=accept_distance * spot_atr,
            accepted=True,
        )

        require_spot_ownership = bool(
            self.params.get("ciot_require_spot_ownership", True),
        )
        if regime == "UNWIND":
            # Leveraged venue must own the sweep while the cash auction refuses
            # accepted discovery. A spot wick may occur, but no spot close may
            # accept the old external boundary.
            if perp_sweep_ts is None:
                return None
            if require_spot_ownership and spot_accept_ts is not None:
                return None
            branch = "REVERSAL"
            direction = "LONG" if side == "SELL" else "SHORT"
            owner_ts = perp_sweep_ts
            other_ts = spot_sweep_ts
            state = "FORCED_INVENTORY_REMOVAL_WITH_SPOT_REFUSAL"
            reason = "PERPETUAL_EXTERNAL_SWEEP_WITH_EXTREME_OI_CONTRACTION_AND_NO_SPOT_ACCEPTANCE"
        else:
            # Fresh inventory continuation belongs to spot only when its
            # accepted break strictly precedes perpetual acceptance.
            if require_spot_ownership:
                if spot_accept_ts is None or (
                    perp_accept_ts is not None and spot_accept_ts >= perp_accept_ts
                ):
                    return None
            elif perp_accept_ts is None:
                return None
            branch = "CONTINUATION"
            direction = "LONG" if side == "BUY" else "SHORT"
            owner_ts = spot_accept_ts
            other_ts = perp_accept_ts
            state = "SPOT_OWNED_ACCEPTANCE_WITH_FRESH_INVENTORY"
            reason = "SPOT_ACCEPTED_EXTERNAL_LIQUIDITY_BEFORE_PERPETUAL_WITH_EXTREME_OI_EXPANSION"

        key = (previous.end_ts_ns, side, branch)
        if key in self._consumed:
            return None
        self._consumed.add(key)
        self._sequence += 1
        episode = _OwnershipEpisode(
            scenario_id=f"CIOT-{snapshot.observation.ts_ns}-{self._sequence:06d}",
            branch=branch,
            side=side,
            direction=direction,
            state=state,
            started_index=snapshot.index,
            started_ts_ns=snapshot.observation.ts_ns,
            prior_auction_end_ts_ns=previous.end_ts_ns,
            perp_boundary=perp_boundary,
            spot_boundary=spot_boundary,
            prior_perp_high=previous.perp_high,
            prior_perp_low=previous.perp_low,
            event_open=event_open,
            event_high=event_high,
            event_low=event_low,
            event_close=event_close,
            event_mid=(event_open + event_close) / 2.0,
            event_range=max(event_high - event_low, snapshot.atr * 0.25),
            event_extreme=event_low if side == "SELL" else event_high,
            atr=max(snapshot.atr, 1e-12),
            baseline_oi=prior_metric.open_interest,
            event_oi=metric.open_interest,
            oi_change=change,
            oi_threshold=threshold,
            spot_owner_ts_ns=owner_ts if branch == "CONTINUATION" else None,
            perp_owner_ts_ns=owner_ts if branch == "REVERSAL" else other_ts,
            high_since_event=event_high,
            low_since_event=event_low,
        )
        self._episode = episode
        return ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="CIOT_CONTEXT_TRANSITION",
            previous_state="IDLE",
            next_state=state,
            reason_code=reason,
            reference_price=perp_boundary,
            details={
                "branch": branch,
                "side": side,
                "direction": direction,
                "oi_change_fraction": change,
                "prior_only_oi_threshold": threshold,
                "baseline_open_interest": prior_metric.open_interest,
                "event_open_interest": metric.open_interest,
                "metric_signed_taker_ratio": metric_flow,
                "perp_sweep_ts_ns": perp_sweep_ts,
                "perp_accept_ts_ns": perp_accept_ts,
                "spot_sweep_ts_ns": spot_sweep_ts,
                "spot_accept_ts_ns": spot_accept_ts,
                "prior_auction_end_ts_ns": previous.end_ts_ns,
                "perp_boundary": perp_boundary,
                "spot_boundary": spot_boundary,
            },
        )

    def _reset(
        self,
        snapshot: PrimitiveSnapshot,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        previous = episode.state
        transition = self._context_transition(
            episode,
            previous,
            "RESET",
            reason,
            snapshot.observation.close,
            details,
        )
        self._episode = None
        self._cooldown_until = snapshot.index + int(
            self.params.get("ciot_cooldown_bars", 2),
        )
        return ScenarioStep(transitions=(transition,))

    def _inventory_update(
        self,
        episode: _OwnershipEpisode,
        metric: FuturesMetric | None,
    ) -> tuple[bool, str | None, dict[str, float]]:
        if not bool(self.params.get("ciot_require_inventory_confirmation", True)):
            return True, "INVENTORY_CONFIRMATION_ABLATION_BYPASS", {}
        if metric is None or metric.ts_ns <= episode.started_ts_ns:
            return episode.inventory_confirmed, None, {}
        if episode.branch == "REVERSAL":
            removed = max(episode.baseline_oi - episode.event_oi, 0.0)
            floor = episode.event_oi + removed * float(
                self.params.get("ciot_counter_rebuild_fraction", 0.35),
            )
            confirmed = metric.open_interest >= floor
            detail = {"counter_inventory_floor": floor, "observed_open_interest": metric.open_interest}
            reason = "COUNTER_INVENTORY_REBUILT_AFTER_FORCED_REMOVAL" if confirmed else None
        else:
            added = max(episode.event_oi - episode.baseline_oi, 0.0)
            floor = episode.baseline_oi + added * float(
                self.params.get("ciot_inventory_retention_fraction", 0.35),
            )
            confirmed = metric.open_interest >= floor
            detail = {"fresh_inventory_retention_floor": floor, "observed_open_interest": metric.open_interest}
            reason = "SPOT_OWNED_FRESH_INVENTORY_RETAINED" if confirmed else None
            if episode.inventory_confirmed and metric.open_interest < episode.baseline_oi:
                return False, "FRESH_INVENTORY_OWNERSHIP_LOST", detail
        return confirmed, reason, detail

    def _spot_holds_ownership(
        self,
        episode: _OwnershipEpisode,
        spot: BarObservation,
        spot_atr: float,
    ) -> bool:
        if not bool(self.params.get("ciot_require_spot_ownership", True)):
            return True
        tolerance = float(self.params.get("ciot_spot_hold_tolerance_atr", 0.03)) * spot_atr
        if episode.branch == "REVERSAL":
            if episode.side == "SELL":
                return spot.close >= episode.spot_boundary - tolerance
            return spot.close <= episode.spot_boundary + tolerance
        if episode.side == "BUY":
            return spot.close >= episode.spot_boundary - tolerance
        return spot.close <= episode.spot_boundary + tolerance

    def _auction_confirmation(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
    ) -> bool:
        obs = snapshot.observation
        distance = float(self.params.get("ciot_perp_accept_close_atr", 0.04)) * episode.atr
        flow = float(self.params.get("ciot_response_flow_ratio", 0.05))
        location = float(self.params.get("ciot_response_close_location", 0.58))
        if episode.direction == "LONG":
            if episode.branch == "REVERSAL":
                structural = obs.close >= max(episode.perp_boundary, episode.event_mid)
            else:
                structural = obs.close >= episode.perp_boundary + distance
            return (
                structural
                and obs.close > obs.open
                and snapshot.flow_ratio >= flow
                and snapshot.close_location >= location
            )
        if episode.branch == "REVERSAL":
            structural = obs.close <= min(episode.perp_boundary, episode.event_mid)
        else:
            structural = obs.close <= episode.perp_boundary - distance
        return (
            structural
            and obs.close < obs.open
            and snapshot.flow_ratio <= -flow
            and snapshot.close_location <= 1.0 - location
        )

    def _pullback_holds(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        spot_atr: float,
    ) -> bool:
        obs = snapshot.observation
        if not self._spot_holds_ownership(episode, spot, spot_atr):
            return False
        band = float(self.params.get("ciot_retest_band_atr", 0.35)) * episode.atr
        opposing = float(self.params.get("ciot_max_opposing_flow", 0.12))
        if episode.direction == "LONG":
            reference = max(episode.perp_boundary, episode.event_mid) if episode.branch == "REVERSAL" else episode.perp_boundary
            return (
                obs.low <= reference + band
                and obs.close >= reference
                and snapshot.flow_ratio <= opposing
            )
        reference = min(episode.perp_boundary, episode.event_mid) if episode.branch == "REVERSAL" else episode.perp_boundary
        return (
            obs.high >= reference - band
            and obs.close <= reference
            and snapshot.flow_ratio >= -opposing
        )

    def _resumed(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
    ) -> bool:
        if episode.pullback_index is None or snapshot.index <= episode.pullback_index:
            return False
        obs = snapshot.observation
        extension = float(self.params.get("ciot_extension_atr", 0.05)) * episode.atr
        flow = float(self.params.get("ciot_response_flow_ratio", 0.05))
        location = float(self.params.get("ciot_response_close_location", 0.58))
        if episode.direction == "LONG":
            return (
                obs.close >= float(episode.pullback_high) + extension
                and snapshot.flow_ratio >= flow
                and snapshot.close_location >= location
            )
        return (
            obs.close <= float(episode.pullback_low) - extension
            and snapshot.flow_ratio <= -flow
            and snapshot.close_location <= 1.0 - location
        )

    def _target(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        entry: float,
        stop: float,
    ) -> tuple[float, str] | None:
        risk = abs(entry - stop)
        if risk <= 0.0:
            return None
        projection = episode.event_range * float(
            self.params.get("ciot_projection_fraction", 1.0),
        )
        if episode.direction == "LONG":
            raw = [
                (episode.event_open, "FORCED_INVENTORY_IMPULSE_ORIGIN"),
                (episode.prior_perp_high, "PRIOR_AUCTION_OPPOSITE_LIQUIDITY"),
                (snapshot.upper_fast, "LIVE_FAST_BUYSIDE_LIQUIDITY"),
                (snapshot.upper_slow, "LIVE_SLOW_BUYSIDE_LIQUIDITY"),
                (entry + projection, "OWNED_INITIATIVE_RANGE_PROJECTION"),
            ]
            consumed = episode.high_since_event
            candidates = sorted(
                (float(price), reason)
                for price, reason in raw
                if price is not None and float(price) > max(entry, consumed)
            )
        else:
            raw = [
                (episode.event_open, "FORCED_INVENTORY_IMPULSE_ORIGIN"),
                (episode.prior_perp_low, "PRIOR_AUCTION_OPPOSITE_LIQUIDITY"),
                (snapshot.lower_fast, "LIVE_FAST_SELLSIDE_LIQUIDITY"),
                (snapshot.lower_slow, "LIVE_SLOW_SELLSIDE_LIQUIDITY"),
                (entry - projection, "OWNED_INITIATIVE_RANGE_PROJECTION"),
            ]
            consumed = episode.low_since_event
            candidates = sorted(
                (
                    (float(price), reason)
                    for price, reason in raw
                    if price is not None and float(price) < min(entry, consumed)
                ),
                reverse=True,
            )
        minimum = float(self.params.get("minimum_structural_rr", 0.75))
        for price, reason in candidates:
            reward = price - entry if episode.direction == "LONG" else entry - price
            if reward > 0.0 and reward / risk >= minimum:
                return price, reason
        return None

    def _emit(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        if not allow_new:
            return self._reset(
                snapshot,
                "ENTRY_SLOT_UNAVAILABLE_AT_OWNERSHIP_RESUMPTION",
            )
        obs = snapshot.observation
        buffer_value = float(self.params.get("ciot_stop_buffer_atr", 0.08)) * episode.atr
        if episode.direction == "LONG":
            stop = float(episode.pullback_low) - buffer_value
        else:
            stop = float(episode.pullback_high) + buffer_value
        target = self._target(episode, snapshot, obs.close, stop)
        if target is None:
            return self._reset(
                snapshot,
                "NO_STILL_LIVE_OWNED_OBJECTIVE_WITH_SUFFICIENT_SPACE",
                {
                    "entry": obs.close,
                    "stop": stop,
                    "high_since_event": episode.high_since_event,
                    "low_since_event": episode.low_since_event,
                },
            )
        target_price, target_reason = target
        family = "CIOT_R" if episode.branch == "REVERSAL" else "CIOT_C"
        previous = episode.state
        episode.state = f"{family}_SIGNALLED"
        episode.signal_index = snapshot.index
        context = ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="CIOT_CONTEXT_TRANSITION",
            previous_state=previous,
            next_state=episode.state,
            reason_code="OWNERSHIP_TRANSFER_FIRST_PULLBACK_AND_RENEWED_INITIATIVE_CONFIRMED",
            reference_price=obs.close,
            details={
                "branch": episode.branch,
                "direction": episode.direction,
                "stop": stop,
                "target": target_price,
                "target_reason": target_reason,
            },
        )
        entry = self._entry_transition(
            episode,
            reason=f"{family}_ENTRY_ARMED",
            reference=obs.close,
            details={
                "direction": episode.direction,
                "stop": stop,
                "target": target_price,
                "target_reason": target_reason,
            },
        )
        invalidation_reason = (
            "COUNTER_INVENTORY_OWNERSHIP_TRANSFER_INVALIDATED"
            if episode.branch == "REVERSAL"
            else "SPOT_OWNED_FRESH_INVENTORY_CONTINUATION_INVALIDATED"
        )
        signal = ScenarioSignal(
            scenario_id=self._entry_scenario_id(episode),
            family=family,
            direction=episode.direction,
            observed_ts_ns=obs.ts_ns,
            reference_entry=obs.close,
            stop_price=stop,
            target_price=target_price,
            target_reason=target_reason,
            atr=episode.atr,
            liquidity_level=episode.perp_boundary,
            details={
                "context_scenario_id": episode.scenario_id,
                "ownership_branch": episode.branch,
                "initiating_side": episode.side,
                "oi_change_fraction": episode.oi_change,
                "prior_only_oi_threshold": episode.oi_threshold,
                "prior_auction_end_ts_ns": episode.prior_auction_end_ts_ns,
                "spot_owner_ts_ns": episode.spot_owner_ts_ns,
                "perp_owner_ts_ns": episode.perp_owner_ts_ns,
                "causal_exit_reason_codes": (invalidation_reason,),
                "causal_exit_open_position": True,
            },
        )
        return ScenarioStep(transitions=(context, entry), signal=signal)

    def _advance_signalled(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        metric: FuturesMetric | None,
        spot_atr: float,
    ) -> ScenarioStep:
        assert episode.signal_index is not None
        obs = snapshot.observation
        floor = float(self.params.get("ciot_response_flow_ratio", 0.05))
        spot_holds = self._spot_holds_ownership(episode, spot, spot_atr)
        if episode.branch == "REVERSAL":
            if episode.direction == "LONG":
                price_invalid = obs.close < episode.event_mid and snapshot.flow_ratio <= -floor
            else:
                price_invalid = obs.close > episode.event_mid and snapshot.flow_ratio >= floor
            inventory_invalid = (
                metric is not None
                and metric.ts_ns > episode.started_ts_ns
                and metric.open_interest < episode.event_oi
            )
            invalid = (not spot_holds) or price_invalid or inventory_invalid
            reason = "COUNTER_INVENTORY_OWNERSHIP_TRANSFER_INVALIDATED"
        else:
            if episode.direction == "LONG":
                price_invalid = obs.close < episode.perp_boundary and snapshot.flow_ratio <= -floor
            else:
                price_invalid = obs.close > episode.perp_boundary and snapshot.flow_ratio >= floor
            inventory_invalid = (
                metric is not None
                and metric.ts_ns > episode.started_ts_ns
                and metric.open_interest < episode.baseline_oi
            )
            invalid = (not spot_holds) or price_invalid or inventory_invalid
            reason = "SPOT_OWNED_FRESH_INVENTORY_CONTINUATION_INVALIDATED"
        if invalid:
            return self._reset(
                snapshot,
                reason,
                {
                    "spot_holds": spot_holds,
                    "price_invalid": price_invalid,
                    "inventory_invalid": inventory_invalid,
                },
            )
        if snapshot.index - episode.signal_index >= int(
            self.params.get("ciot_post_signal_context_bars", 8),
        ):
            return self._reset(
                snapshot,
                "OWNERSHIP_TRANSFER_POST_SIGNAL_CONTEXT_MATURED",
            )
        return ScenarioStep()

    def _advance_episode(
        self,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        metric: FuturesMetric | None,
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        spot_atr = self._spot_atr()
        if spot_atr is None:
            return ScenarioStep()
        if episode.state.endswith("SIGNALLED"):
            return self._advance_signalled(
                episode,
                snapshot,
                spot,
                metric,
                spot_atr,
            )
        if snapshot.index <= episode.started_index:
            return ScenarioStep()
        obs = snapshot.observation
        episode.high_since_event = max(episode.high_since_event, obs.high)
        episode.low_since_event = min(episode.low_since_event, obs.low)
        if snapshot.index - episode.started_index > int(
            self.params.get("ciot_episode_bars", 30),
        ):
            return self._reset(
                snapshot,
                "OWNERSHIP_TRANSFER_EPISODE_EXPIRED",
            )
        if not self._spot_holds_ownership(episode, spot, spot_atr):
            return self._reset(
                snapshot,
                "SPOT_OWNERSHIP_FAILED_BEFORE_ENTRY",
                {"spot_close": spot.close, "spot_boundary": episode.spot_boundary},
            )

        transitions: list[ScenarioTransition] = []
        inventory, reason, detail = self._inventory_update(episode, metric)
        if reason == "FRESH_INVENTORY_OWNERSHIP_LOST":
            return self._reset(snapshot, reason, detail)
        if inventory and not episode.inventory_confirmed:
            previous = episode.state
            episode.inventory_confirmed = True
            next_state = "INVENTORY_OWNERSHIP_CONFIRMED"
            transitions.append(
                self._context_transition(
                    episode,
                    previous,
                    next_state,
                    reason or "INVENTORY_OWNERSHIP_CONFIRMED",
                    obs.close,
                    detail,
                ),
            )

        if not episode.auction_confirmed and self._auction_confirmation(episode, snapshot):
            previous = episode.state
            episode.auction_confirmed = True
            episode.auction_confirmation_index = snapshot.index
            episode.auction_confirmation_high = obs.high
            episode.auction_confirmation_low = obs.low
            transitions.append(
                self._context_transition(
                    episode,
                    previous,
                    "OLD_AUCTION_INVALIDATED_BY_NEW_OWNER"
                    if episode.branch == "REVERSAL"
                    else "PERPETUAL_ACCEPTED_SPOT_OWNED_AUCTION",
                    "OLD_INVENTORY_AUCTION_INVALIDATED"
                    if episode.branch == "REVERSAL"
                    else "PERPETUAL_ACCEPTED_AFTER_STRICTLY_EARLIER_SPOT_DISCOVERY",
                    obs.close,
                    {
                        "auction_confirmation_index": snapshot.index,
                        "perp_boundary": episode.perp_boundary,
                        "event_mid": episode.event_mid,
                    },
                ),
            )

        if not (episode.inventory_confirmed and episode.auction_confirmed):
            return ScenarioStep(transitions=tuple(transitions))

        if episode.pullback_index is None:
            if snapshot.index <= int(episode.auction_confirmation_index):
                return ScenarioStep(transitions=tuple(transitions))
            if self._pullback_holds(episode, snapshot, spot, spot_atr):
                episode.pullback_index = snapshot.index
                episode.pullback_high = obs.high
                episode.pullback_low = obs.low
                previous = episode.state
                transitions.append(
                    self._context_transition(
                        episode,
                        previous,
                        "FIRST_OWNED_PULLBACK_HELD",
                        "FIRST_OPPOSING_FLOW_PULLBACK_HELD_NEW_OWNER_BOUNDARY",
                        obs.close,
                        {
                            "pullback_high": obs.high,
                            "pullback_low": obs.low,
                            "spot_close": spot.close,
                        },
                    ),
                )
            return ScenarioStep(transitions=tuple(transitions))

        if self._resumed(episode, snapshot):
            emitted = self._emit(episode, snapshot, allow_new=allow_new)
            return ScenarioStep(
                transitions=(*transitions, *emitted.transitions),
                signal=emitted.signal,
            )
        return ScenarioStep(transitions=tuple(transitions))

    def observe(
        self,
        snapshot: PrimitiveSnapshot,
        *,
        allow_new: bool = True,
    ) -> ScenarioStep:
        spot = self._spot.get(snapshot.observation.ts_ns)
        if spot is None:
            raise RuntimeError(
                f"missing synchronized Binance spot context for ts_ns={snapshot.observation.ts_ns}",
            )
        self._roll_before(snapshot, spot)
        self._bars.append(snapshot)
        self._spot_bars.append(spot)
        metric = self._metrics.get(snapshot.observation.ts_ns)
        transitions: list[ScenarioTransition] = []
        signal: ScenarioSignal | None = None

        if self._episode is not None:
            step = self._advance_episode(
                snapshot,
                spot,
                metric,
                allow_new=allow_new,
            )
            transitions.extend(step.transitions)
            signal = step.signal
        elif (
            allow_new
            and snapshot.ready
            and snapshot.index > self._cooldown_until
            and metric is not None
        ):
            started = self._maybe_start_episode(snapshot, spot, metric)
            if started is not None:
                transitions.append(started)

        self._ingest_metric_history(snapshot, metric)
        self._accumulate_after(snapshot.observation, spot)
        self._append_spot_true_range(spot)
        return ScenarioStep(transitions=tuple(transitions), signal=signal)

    def abort_active(
        self,
        snapshot: PrimitiveSnapshot,
        reason: str,
    ) -> ScenarioStep:
        if self._episode is None:
            return ScenarioStep()
        return self._reset(snapshot, reason)
