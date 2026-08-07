"""Spot-perpetual auction reconciliation state machine for candidate 10 v24.

The detector and trading scenario are deliberately separated. Every decision
uses completed aligned spot/perpetual aggregate-trade bars and baselines formed
strictly before the current observation. A dislocation creates one event. It
can produce at most one plan and cannot be reused until basis/returns normalize.
"""
from __future__ import annotations

from collections import Counter, deque
from math import exp, log
from statistics import median
from typing import Iterable

from c10_v24_model import (
    CrossMarketBar,
    CrossMarketParams,
    CrossMarketPlan,
    CrossMarketProbe,
    CrossMarketTransition,
)


def _sign(value: float) -> int:
    return 1 if value > 0.0 else -1 if value < 0.0 else 0


def _quantile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    p = min(1.0, max(0.0, probability))
    location = (len(ordered) - 1) * p
    lower = int(location)
    upper = min(len(ordered) - 1, lower + 1)
    weight = location - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _robust_location_scale(
    values: Iterable[float],
    *,
    scale_floor: float,
) -> tuple[float, float]:
    data = [float(value) for value in values]
    if not data:
        return 0.0, scale_floor
    location = median(data)
    mad = median(abs(value - location) for value in data)
    return location, max(scale_floor, 1.4826 * mad)


class CrossMarketReconciliationStateMachine:
    """Classify transient cross-market price-discovery disagreement."""

    def __init__(
        self,
        params: CrossMarketParams,
        *,
        tick_size: float,
        instrument_id: str,
    ) -> None:
        if tick_size <= 0.0:
            raise ValueError("tick_size must be positive")
        if params.return_horizon_bars < 1:
            raise ValueError("return_horizon_bars must be positive")
        self.params = params
        self.tick_size = float(tick_size)
        self.instrument_id = str(instrument_id)
        history_size = params.feature_lookback + params.return_horizon_bars + 16
        self.spot_closes: deque[float] = deque(maxlen=history_size)
        self.perp_closes: deque[float] = deque(maxlen=history_size)
        self.basis_history: deque[float] = deque(maxlen=params.feature_lookback)
        self.spot_return_history: deque[float] = deque(maxlen=params.feature_lookback)
        self.perp_return_history: deque[float] = deque(maxlen=params.feature_lookback)
        self.spot_abs_flow_history: deque[float] = deque(maxlen=params.feature_lookback)
        self.perp_abs_flow_history: deque[float] = deque(maxlen=params.feature_lookback)
        self.perp_range_history: deque[float] = deque(maxlen=params.feature_lookback)
        self.perp_notional_history: deque[float] = deque(maxlen=params.feature_lookback)
        self.sequence = 0
        self.event_sequence = 0
        self.active_probe: CrossMarketProbe | None = None
        self.cooldown_active = False
        self.cooldown_normal_count = 0
        self.counters: Counter[str] = Counter()

    def diagnostics(self) -> dict[str, object]:
        return {
            "completed_rows": self.sequence,
            "active_probe": self.active_probe is not None,
            "cooldown_active": self.cooldown_active,
            "counts": dict(self.counters),
        }

    def _transition(
        self,
        *,
        scenario_id: str,
        bar: CrossMarketBar,
        event_type: str,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None = None,
        details: dict[str, object] | None = None,
    ) -> CrossMarketTransition:
        return CrossMarketTransition(
            scenario_id=scenario_id,
            event_type=event_type,
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason_code,
            reference_price=reference_price,
            details=dict(details or {}),
        )

    def _snapshot(self, bar: CrossMarketBar) -> dict[str, float] | None:
        p = self.params
        minimum = max(p.minimum_feature_history, p.return_horizon_bars + 2)
        if (
            len(self.basis_history) < minimum
            or len(self.spot_closes) < p.return_horizon_bars
            or len(self.spot_return_history) < minimum - p.return_horizon_bars
            or len(self.perp_return_history) < minimum - p.return_horizon_bars
        ):
            return None

        spot_past = self.spot_closes[-p.return_horizon_bars]
        perp_past = self.perp_closes[-p.return_horizon_bars]
        if min(spot_past, perp_past, bar.spot_close, bar.perp_close) <= 0.0:
            return None
        spot_return = log(bar.spot_close / spot_past)
        perp_return = log(bar.perp_close / perp_past)

        fair_basis, basis_scale = _robust_location_scale(
            self.basis_history,
            scale_floor=1e-6,
        )
        spot_return_location, spot_return_scale = _robust_location_scale(
            self.spot_return_history,
            scale_floor=1e-6,
        )
        perp_return_location, perp_return_scale = _robust_location_scale(
            self.perp_return_history,
            scale_floor=1e-6,
        )
        current_basis = bar.basis_log
        basis_deviation = current_basis - fair_basis
        spot_flow_floor = median(self.spot_abs_flow_history)
        perp_flow_floor = median(self.perp_abs_flow_history)
        perp_range_median = max(self.tick_size, median(self.perp_range_history))
        causal_liquidity_notional = max(1.0, median(self.perp_notional_history))
        return {
            "fair_basis": fair_basis,
            "basis_scale": basis_scale,
            "basis_deviation": basis_deviation,
            "basis_z": basis_deviation / basis_scale,
            "spot_return": spot_return,
            "perp_return": perp_return,
            "spot_return_scale": spot_return_scale,
            "perp_return_scale": perp_return_scale,
            "spot_return_z": (
                spot_return - spot_return_location
            ) / spot_return_scale,
            "perp_return_z": (
                perp_return - perp_return_location
            ) / perp_return_scale,
            "spot_flow_floor": max(0.0, spot_flow_floor),
            "perp_flow_floor": max(0.0, perp_flow_floor),
            "perp_range_median": perp_range_median,
            "causal_liquidity_notional": causal_liquidity_notional,
        }

    def _append_history(
        self,
        bar: CrossMarketBar,
        features: dict[str, float] | None,
    ) -> None:
        # Return histories start before a complete feature snapshot exists;
        # otherwise the minimum-history gate would be circular. The past close
        # is already completed and the current close is known only at bar.ts_ns.
        horizon = self.params.return_horizon_bars
        if len(self.spot_closes) >= horizon:
            spot_past = self.spot_closes[-horizon]
            perp_past = self.perp_closes[-horizon]
            if min(spot_past, perp_past, bar.spot_close, bar.perp_close) > 0.0:
                self.spot_return_history.append(
                    log(bar.spot_close / spot_past),
                )
                self.perp_return_history.append(
                    log(bar.perp_close / perp_past),
                )
        self.spot_closes.append(bar.spot_close)
        self.perp_closes.append(bar.perp_close)
        self.basis_history.append(bar.basis_log)
        self.spot_abs_flow_history.append(abs(bar.spot_flow))
        self.perp_abs_flow_history.append(abs(bar.perp_flow))
        self.perp_range_history.append(max(self.tick_size, bar.perp_range))
        self.perp_notional_history.append(max(0.0, bar.perp_quote_volume))

    def _release_event(self) -> None:
        self.active_probe = None
        self.cooldown_active = True
        self.cooldown_normal_count = 0

    def _update_cooldown(self, features: dict[str, float]) -> None:
        normal = (
            abs(features["basis_z"]) <= self.params.cooldown_basis_z
            and abs(features["spot_return_z"]) <= self.params.cooldown_return_z
            and abs(features["perp_return_z"]) <= self.params.cooldown_return_z
        )
        if normal:
            self.cooldown_normal_count += 1
        else:
            self.cooldown_normal_count = 0
        if self.cooldown_normal_count >= self.params.cooldown_normal_bars:
            self.cooldown_active = False
            self.cooldown_normal_count = 0
            self.counters["EVENT_COOLDOWN_RELEASED"] += 1

    def _spot_flow_confirms(self, bar: CrossMarketBar, direction: int, floor: float) -> bool:
        if not self.params.use_spot_flow:
            return True
        return direction * bar.spot_flow >= floor

    def _spot_flow_does_not_confirm(
        self,
        bar: CrossMarketBar,
        move_direction: int,
        floor: float,
    ) -> bool:
        if not self.params.use_spot_flow:
            return True
        return move_direction * bar.spot_flow < floor

    def _detect_candidates(
        self,
        bar: CrossMarketBar,
        features: dict[str, float],
    ) -> list[tuple[float, str, int, int]]:
        p = self.params
        spot_return = features["spot_return"]
        perp_return = features["perp_return"]
        spot_direction = _sign(spot_return)
        perp_direction = _sign(perp_return)
        candidates: list[tuple[float, str, int, int]] = []

        if spot_direction:
            spot_magnitude = spot_direction * spot_return
            perp_same_move = spot_direction * perp_return
            spot_lead = (
                abs(features["spot_return_z"]) >= p.dislocation_z
                and spot_direction * features["basis_z"] <= -p.basis_z
                and perp_same_move >= -0.25 * spot_magnitude
                and perp_same_move <= p.lag_ratio * spot_magnitude
                and self._spot_flow_confirms(
                    bar,
                    spot_direction,
                    features["spot_flow_floor"],
                )
            )
            if spot_lead:
                score = abs(features["spot_return_z"]) + abs(features["basis_z"])
                candidates.append(
                    (score, "SPOT_LEAD_CATCHUP", spot_direction, spot_direction),
                )

        if perp_direction:
            perp_magnitude = perp_direction * perp_return
            spot_same_move = perp_direction * spot_return
            perp_overshoot = (
                abs(features["perp_return_z"]) >= p.dislocation_z
                and perp_direction * features["basis_z"] >= p.basis_z
                and spot_same_move <= p.lag_ratio * perp_magnitude
                and perp_direction * bar.perp_flow >= features["perp_flow_floor"]
                and self._spot_flow_does_not_confirm(
                    bar,
                    perp_direction,
                    features["spot_flow_floor"],
                )
            )
            if perp_overshoot:
                score = abs(features["perp_return_z"]) + abs(features["basis_z"])
                candidates.append(
                    (score, "PERP_OVERSHOOT_REVERSION", -perp_direction, perp_direction),
                )
        return candidates

    def _start_probe(
        self,
        bar: CrossMarketBar,
        features: dict[str, float],
        *,
        mode: str,
        trade_direction: int,
        move_direction: int,
    ) -> CrossMarketTransition:
        self.event_sequence += 1
        scenario_id = f"{self.instrument_id}:XMR:{self.event_sequence:08d}"
        fair_target = bar.spot_close * exp(features["fair_basis"])
        self.active_probe = CrossMarketProbe(
            scenario_id=scenario_id,
            mode=mode,
            source_id=f"{mode}@{bar.ts_ns}",
            target_id=f"PRE_EVENT_FAIR_BASIS@{bar.ts_ns}",
            initiated_sequence=self.sequence,
            initiated_ns=bar.ts_ns,
            trade_direction=trade_direction,
            move_direction=move_direction,
            fair_basis=features["fair_basis"],
            fair_target=fair_target,
            initial_basis_deviation=features["basis_deviation"],
            initial_basis_abs_z=abs(features["basis_z"]),
            spot_event_price=bar.spot_close,
            perp_event_price=bar.perp_close,
            spot_event_return=features["spot_return"],
            perp_event_return=features["perp_return"],
            perp_extreme_high=bar.perp_high,
            perp_extreme_low=bar.perp_low,
        )
        self.counters[f"{mode}_PROBE_CREATED"] += 1
        return self._transition(
            scenario_id=scenario_id,
            bar=bar,
            event_type="CROSS_MARKET_DISLOCATION_DETECTED",
            previous_state="NEUTRAL",
            next_state=f"{mode}_WAIT",
            reason_code=(
                "SPOT_DISPLACEMENT_WITH_PERP_LAG"
                if mode == "SPOT_LEAD_CATCHUP"
                else "PERP_DISPLACEMENT_WITHOUT_SPOT_CONFIRMATION"
            ),
            reference_price=bar.perp_close,
            details={
                "mode": mode,
                "trade_direction": trade_direction,
                "move_direction": move_direction,
                "fair_basis": features["fair_basis"],
                "fair_target_fixed_at_detection": fair_target,
                "basis_z": features["basis_z"],
                "spot_return_z": features["spot_return_z"],
                "perp_return_z": features["perp_return_z"],
                "spot_flow": bar.spot_flow,
                "perp_flow": bar.perp_flow,
                "use_spot_flow": self.params.use_spot_flow,
            },
        )

    def _cost_adjusted_plan(
        self,
        *,
        bar: CrossMarketBar,
        probe: CrossMarketProbe,
        features: dict[str, float],
    ) -> CrossMarketPlan | None:
        direction = probe.trade_direction
        entry = bar.perp_close
        target = probe.fair_target
        buffer = max(
            self.tick_size * self.params.execution_reserve_ticks,
            self.params.stop_range_multiple * features["perp_range_median"],
        )
        stop = (
            probe.perp_extreme_low - buffer
            if direction > 0
            else probe.perp_extreme_high + buffer
        )
        valid = stop < entry < target if direction > 0 else target < entry < stop
        if not valid:
            self.counters["PLAN_INVALID_GEOMETRY"] += 1
            return None
        impact = max(
            self.tick_size * self.params.execution_reserve_ticks,
            self.params.impact_range_fraction * features["perp_range_median"],
            self.params.current_range_impact_fraction * bar.perp_range,
        )
        fee = self.params.taker_fee
        loss = abs(entry - stop) + fee * (entry + stop) + 2.0 * impact
        gross_reward = direction * (target - entry)
        net_reward = gross_reward - fee * (entry + target) - 2.0 * impact
        rr = net_reward / loss if loss > 0.0 else float("-inf")
        if net_reward <= 0.0 or rr < self.params.min_net_rr:
            self.counters["COST_ADJUSTED_RR_REJECTED"] += 1
            return None
        return CrossMarketPlan(
            scenario_id=probe.scenario_id,
            scenario=probe.mode,
            direction=direction,
            observed_ns=bar.ts_ns,
            entry_estimate=entry,
            stop_price=stop,
            target_price=target,
            source_pool_id=probe.source_id,
            target_pool_id=probe.target_id,
            expected_entry_impact=impact,
            expected_stop_impact=impact,
            cost_adjusted_net_rr=rr,
            details={
                "mode": probe.mode,
                "fair_basis": probe.fair_basis,
                "fair_target_fixed_at_detection": probe.fair_target,
                "initial_basis_deviation": probe.initial_basis_deviation,
                "confirmation_basis_deviation": features["basis_deviation"],
                "basis_z": features["basis_z"],
                "spot_return": features["spot_return"],
                "perp_return": features["perp_return"],
                "spot_flow": bar.spot_flow,
                "perp_flow": bar.perp_flow,
                "atr": features["perp_range_median"],
                "causal_liquidity_notional": features[
                    "causal_liquidity_notional"
                ],
                "base_impact_per_side": impact,
                "event_perp_extreme_high": probe.perp_extreme_high,
                "event_perp_extreme_low": probe.perp_extreme_low,
                "use_spot_flow": self.params.use_spot_flow,
            },
        )

    def _process_probe(
        self,
        bar: CrossMarketBar,
        features: dict[str, float],
    ) -> tuple[list[CrossMarketTransition], CrossMarketPlan | None]:
        probe = self.active_probe
        assert probe is not None
        probe.perp_extreme_high = max(probe.perp_extreme_high, bar.perp_high)
        probe.perp_extreme_low = min(probe.perp_extreme_low, bar.perp_low)
        age = self.sequence - probe.initiated_sequence
        events: list[CrossMarketTransition] = []

        target_reached = (
            bar.perp_high >= probe.fair_target
            if probe.trade_direction > 0
            else bar.perp_low <= probe.fair_target
        )
        if target_reached:
            self.counters[f"{probe.mode}_CONVERGED_BEFORE_ENTRY"] += 1
            events.append(
                self._transition(
                    scenario_id=probe.scenario_id,
                    bar=bar,
                    event_type="SCENARIO_EXPIRED",
                    previous_state=f"{probe.mode}_WAIT",
                    next_state="CONVERGED_WITHOUT_ENTRY",
                    reason_code="FAIR_BASIS_TARGET_REACHED_BEFORE_CONFIRMATION",
                    reference_price=bar.perp_close,
                    details={"age_bars": age, "fair_target": probe.fair_target},
                ),
            )
            self._release_event()
            return events, None

        initial_abs = max(1e-12, abs(probe.initial_basis_deviation))
        contracted = abs(features["basis_deviation"]) <= (
            initial_abs * (1.0 - self.params.basis_contraction_fraction)
        )
        if probe.mode == "SPOT_LEAD_CATCHUP":
            direction = probe.trade_direction
            spot_since_event = log(bar.spot_close / probe.spot_event_price)
            spot_holds = (
                direction * spot_since_event
                >= -0.5 * features["spot_return_scale"]
            )
            perp_catches = (
                direction * bar.perp_flow >= features["perp_flow_floor"]
            )
            spot_flow_holds = (
                not self.params.use_spot_flow
                or direction * bar.spot_flow >= 0.0
            )
            confirmed = contracted and spot_holds and perp_catches and spot_flow_holds
            invalidated = direction * spot_since_event < -features["spot_return_scale"]
            confirm_reason = "SPOT_LEAD_HELD_WHILE_PERP_FLOW_CAUGHT_UP"
        else:
            direction = probe.trade_direction
            move_direction = probe.move_direction
            spot_since_event = log(bar.spot_close / probe.spot_event_price)
            perp_reverses = (
                direction * bar.perp_flow >= features["perp_flow_floor"]
            )
            spot_still_does_not_confirm = (
                move_direction * spot_since_event
                <= self.params.lag_ratio * abs(probe.perp_event_return)
                and self._spot_flow_does_not_confirm(
                    bar,
                    move_direction,
                    features["spot_flow_floor"],
                )
            )
            confirmed = contracted and perp_reverses and spot_still_does_not_confirm
            invalidated = (
                move_direction * spot_since_event
                > abs(probe.perp_event_return)
            )
            confirm_reason = "PERP_OVERSHOOT_REVERSED_WITHOUT_SPOT_CONFIRMATION"

        if confirmed:
            plan = self._cost_adjusted_plan(
                bar=bar,
                probe=probe,
                features=features,
            )
            if plan is None:
                events.append(
                    self._transition(
                        scenario_id=probe.scenario_id,
                        bar=bar,
                        event_type="SCENARIO_INVALIDATED",
                        previous_state=f"{probe.mode}_WAIT",
                        next_state="NO_EXECUTABLE_PLAN",
                        reason_code="CROSS_MARKET_TARGET_NOT_COST_QUALIFIED",
                        reference_price=bar.perp_close,
                        details={"age_bars": age, "fair_target": probe.fair_target},
                    ),
                )
                self._release_event()
                return events, None
            self.counters[f"{probe.mode}_CONFIRMED"] += 1
            self.counters["TRADE_PLAN_CREATED"] += 1
            events.append(
                self._transition(
                    scenario_id=probe.scenario_id,
                    bar=bar,
                    event_type="AUCTION_RECONCILIATION_CONFIRMED",
                    previous_state=f"{probe.mode}_WAIT",
                    next_state="ENTRY_READY",
                    reason_code=confirm_reason,
                    reference_price=bar.perp_close,
                    details={
                        "age_bars": age,
                        "direction": plan.direction,
                        "entry": plan.entry_estimate,
                        "stop": plan.stop_price,
                        "target": plan.target_price,
                        "cost_adjusted_net_rr": plan.cost_adjusted_net_rr,
                        "basis_deviation": features["basis_deviation"],
                    },
                ),
            )
            self._release_event()
            return events, plan

        if invalidated:
            self.counters[f"{probe.mode}_INVALIDATED"] += 1
            events.append(
                self._transition(
                    scenario_id=probe.scenario_id,
                    bar=bar,
                    event_type="SCENARIO_INVALIDATED",
                    previous_state=f"{probe.mode}_WAIT",
                    next_state="INVALIDATED",
                    reason_code="LEADER_FOLLOWER_INTERPRETATION_INVALIDATED",
                    reference_price=bar.perp_close,
                    details={"age_bars": age, "spot_since_event": spot_since_event},
                ),
            )
            self._release_event()
        elif age >= self.params.probe_max_bars:
            self.counters[f"{probe.mode}_EXPIRED"] += 1
            events.append(
                self._transition(
                    scenario_id=probe.scenario_id,
                    bar=bar,
                    event_type="SCENARIO_EXPIRED",
                    previous_state=f"{probe.mode}_WAIT",
                    next_state="EXPIRED",
                    reason_code="NO_CROSS_MARKET_RECONCILIATION_WITHIN_ONE_MINUTE",
                    reference_price=bar.perp_close,
                    details={"age_bars": age, "contracted": contracted},
                ),
            )
            self._release_event()
        return events, None

    def on_bar(
        self,
        bar: CrossMarketBar,
    ) -> tuple[list[CrossMarketTransition], CrossMarketPlan | None]:
        self.sequence += 1
        self.counters["BAR_COMPLETED"] += 1
        features = self._snapshot(bar)
        events: list[CrossMarketTransition] = []
        plan: CrossMarketPlan | None = None

        if features is not None:
            if self.active_probe is not None:
                events, plan = self._process_probe(bar, features)
            elif self.cooldown_active:
                self._update_cooldown(features)
            else:
                candidates = self._detect_candidates(bar, features)
                if candidates:
                    if len(candidates) > 1:
                        self.counters["AMBIGUOUS_CROSS_MARKET_DISLOCATION"] += 1
                    _, mode, trade_direction, move_direction = max(
                        candidates,
                        key=lambda item: item[0],
                    )
                    events.append(
                        self._start_probe(
                            bar,
                            features,
                            mode=mode,
                            trade_direction=trade_direction,
                            move_direction=move_direction,
                        ),
                    )
        self._append_history(bar, features)
        return events, plan


__all__ = [
    "CrossMarketReconciliationStateMachine",
    "_quantile",
    "_robust_location_scale",
]
