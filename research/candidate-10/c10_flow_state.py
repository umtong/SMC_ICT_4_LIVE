"""Causal event-notional absorption/repricing state machine for candidate 10 v3."""

from __future__ import annotations

from collections import Counter, deque
from statistics import median
from typing import Iterable

from c10_flow_model import FlowBar
from c10_flow_model import FlowParams
from c10_flow_model import FlowRaidProbe
from c10_flow_model import FlowTickView
from c10_flow_model import FlowTradePlan
from c10_flow_model import FlowTransition
from c10_flow_model import NS_PER_MINUTE


def _quantile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    probability = min(1.0, max(0.0, probability))
    location = (len(ordered) - 1) * probability
    lower = int(location)
    upper = min(len(ordered) - 1, lower + 1)
    weight = location - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


class FlowAuctionStateMachine:
    """Separate pattern detection from the executable trading scenario.

    Detection identifies a causal local dealing-range raid. The trading scenario
    is created only when outward aggressive flow fails to create efficient price
    progress and the following event bars efficiently reprice in the opposite
    direction. ``enable_order_flow=False`` is the exact price-only ablation.
    """

    def __init__(
        self,
        params: FlowParams,
        *,
        tick_size: float,
        instrument_id: str,
    ) -> None:
        self.params = params
        self.tick_size = tick_size
        self.instrument_id = instrument_id

        self.minute_bucket: int | None = None
        self.minute_notional = 0.0
        self.minute_history: deque[float] = deque(
            maxlen=params.minute_notional_lookback,
        )

        self.current_bar: FlowBar | None = None
        self.next_sequence = 0
        self.completed_bars: deque[FlowBar] = deque(
            maxlen=max(params.feature_lookback, params.range_event_bars) + 16,
        )
        self.true_ranges: deque[float] = deque(maxlen=params.atr_event_bars)
        self.abs_delta_history: deque[float] = deque(maxlen=params.feature_lookback)
        self.efficiency_history: deque[float] = deque(maxlen=params.feature_lookback)
        self.previous_flow_close: float | None = None

        self.active_probe: FlowRaidProbe | None = None
        self.scenario_sequence = 0
        self.counters: Counter[str] = Counter()

    @property
    def completed_sequence(self) -> int:
        return self.next_sequence - 1

    def diagnostics(self) -> dict[str, object]:
        return {
            "completed_flow_bars": self.completed_sequence + 1,
            "completed_minutes": len(self.minute_history),
            "active_probe": self.active_probe is not None,
            "counts": dict(self.counters),
            "current_event_threshold": self._event_threshold(),
        }

    def _event_threshold(self) -> float | None:
        minimum = self.params.minimum_minute_history
        if len(self.minute_history) < minimum:
            return None
        values = list(self.minute_history)[-self.params.minute_notional_lookback :]
        threshold = median(values) * self.params.event_notional_fraction
        return threshold if threshold > 0.0 else None

    def _roll_minute(self, tick: FlowTickView) -> None:
        bucket = tick.ts_ns // NS_PER_MINUTE
        if self.minute_bucket is None:
            self.minute_bucket = bucket
        elif bucket != self.minute_bucket:
            # BTC is continuously traded. Explicit zeros preserve the causal
            # meaning if a data gap or truly inactive minute appears.
            self.minute_history.append(self.minute_notional)
            gap = max(0, bucket - self.minute_bucket - 1)
            for _ in range(min(gap, self.params.minute_notional_lookback)):
                self.minute_history.append(0.0)
            self.minute_bucket = bucket
            self.minute_notional = 0.0
        self.minute_notional += tick.notional

    def on_tick(
        self,
        tick: FlowTickView,
    ) -> tuple[list[FlowTransition], FlowTradePlan | None, FlowBar | None]:
        if tick.quantity <= 0.0 or tick.price <= 0.0:
            raise ValueError("flow tick price and quantity must be positive")
        if tick.aggressor not in {-1, 1}:
            raise ValueError("flow tick aggressor must be +1 or -1")

        self._roll_minute(tick)
        if self.current_bar is None:
            threshold = self._event_threshold()
            if threshold is None:
                return [], None, None
            self.current_bar = FlowBar.from_tick(
                sequence=self.next_sequence,
                threshold_notional=threshold,
                tick=tick,
            )
        else:
            self.current_bar.update(tick)

        if self.current_bar.notional < self.current_bar.threshold_notional:
            return [], None, None

        completed = self.current_bar
        self.current_bar = None
        self.next_sequence += 1
        transitions, plan = self._on_completed_bar(completed)
        return transitions, plan, completed

    def _feature_snapshot(self) -> dict[str, float] | None:
        if len(self.true_ranges) < self.params.minimum_atr_history:
            return None
        if len(self.abs_delta_history) < self.params.minimum_feature_history:
            return None
        if len(self.efficiency_history) < self.params.minimum_feature_history:
            return None
        if len(self.completed_bars) < self.params.range_event_bars:
            return None

        atr_values = list(self.true_ranges)[-self.params.atr_event_bars :]
        atr = median(atr_values)
        if atr <= 0.0:
            return None
        delta_extreme = _quantile(
            self.abs_delta_history,
            self.params.flow_extreme_quantile,
        )
        delta_reversal = _quantile(
            self.abs_delta_history,
            self.params.flow_reversal_quantile,
        )
        absorption_efficiency = _quantile(
            self.efficiency_history,
            self.params.absorption_efficiency_quantile,
        )
        repricing_efficiency = _quantile(
            self.efficiency_history,
            self.params.repricing_efficiency_quantile,
        )
        if None in {
            delta_extreme,
            delta_reversal,
            absorption_efficiency,
            repricing_efficiency,
        }:
            return None
        range_bars = list(self.completed_bars)[-self.params.range_event_bars :]
        return {
            "atr": atr,
            "delta_extreme": max(
                self.params.minimum_delta_ratio,
                float(delta_extreme),
            ),
            "delta_reversal": max(
                self.params.minimum_delta_ratio,
                float(delta_reversal),
            ),
            "absorption_efficiency": max(
                self.params.minimum_efficiency,
                float(absorption_efficiency),
            ),
            "repricing_efficiency": max(
                self.params.minimum_efficiency,
                float(repricing_efficiency),
            ),
            "range_high": max(item.high for item in range_bars),
            "range_low": min(item.low for item in range_bars),
        }

    def _transition(
        self,
        *,
        scenario_id: str,
        bar: FlowBar,
        event_type: str,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None = None,
        details: dict[str, object] | None = None,
    ) -> FlowTransition:
        return FlowTransition(
            scenario_id=scenario_id,
            event_type=event_type,
            event_time_ns=bar.end_ns,
            observed_time_ns=bar.end_ns,
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason_code,
            reference_price=reference_price,
            details=dict(details or {}),
        )

    def _on_completed_bar(
        self,
        bar: FlowBar,
    ) -> tuple[list[FlowTransition], FlowTradePlan | None]:
        features = self._feature_snapshot()
        transitions: list[FlowTransition] = []
        plan: FlowTradePlan | None = None

        if features is not None and self.active_probe is not None:
            probe_events, plan = self._process_probe(bar, features)
            transitions.extend(probe_events)

        if features is not None and plan is None and self.active_probe is None:
            transitions.extend(self._detect_absorption_raid(bar, features))

        true_range = bar.true_range(self.previous_flow_close)
        self.previous_flow_close = bar.close
        self.true_ranges.append(true_range)
        self.abs_delta_history.append(abs(bar.delta_ratio))
        self.efficiency_history.append(bar.efficiency)
        self.completed_bars.append(bar)
        self.counters["FLOW_BAR_COMPLETED"] += 1
        return transitions, plan

    def _detect_absorption_raid(
        self,
        bar: FlowBar,
        features: dict[str, float],
    ) -> list[FlowTransition]:
        atr = features["atr"]
        high = features["range_high"]
        low = features["range_low"]
        extension = self.params.raid_atr * atr

        high_raid = bar.high >= high + extension and bar.close < high
        low_raid = bar.low <= low - extension and bar.close > low
        if high_raid and low_raid:
            self.counters["AMBIGUOUS_TWO_SIDED_RAID"] += 1
            return []
        if not high_raid and not low_raid:
            return []

        self.counters["PRICE_RAID_REENTERED"] += 1
        low_efficiency = bar.efficiency <= features["absorption_efficiency"]
        if high_raid:
            close_rejected = bar.close_location <= 0.45
            outward_flow = bar.delta_ratio >= features["delta_extreme"]
            direction = -1
            side = "HIGH"
            boundary = high
            opposite = low
            extreme = bar.high
        else:
            close_rejected = bar.close_location >= 0.55
            outward_flow = bar.delta_ratio <= -features["delta_extreme"]
            direction = 1
            side = "LOW"
            boundary = low
            opposite = high
            extreme = bar.low

        if not low_efficiency or not close_rejected:
            self.counters["PRICE_RESPONSE_NOT_ABSORPTIVE"] += 1
            return []
        if self.params.enable_order_flow and not outward_flow:
            self.counters["FLOW_DIRECTION_OR_MAGNITUDE_REJECTED"] += 1
            return []

        self.scenario_sequence += 1
        scenario_id = (
            f"{self.instrument_id}:FLOW:{self.scenario_sequence:06d}"
        )
        self.active_probe = FlowRaidProbe(
            scenario_id=scenario_id,
            direction=direction,
            source_side=side,
            boundary=boundary,
            opposite_boundary=opposite,
            raid_extreme=extreme,
            initiated_sequence=bar.sequence,
            initiated_ns=bar.end_ns,
            initial_delta_ratio=bar.delta_ratio,
            initial_efficiency=bar.efficiency,
            initial_flow_threshold=features["delta_extreme"],
            initial_bar_open=bar.open,
            initial_bar_close=bar.close,
        )
        self.counters["ABSORPTION_PROBE_CREATED"] += 1
        return [
            self._transition(
                scenario_id=scenario_id,
                bar=bar,
                event_type="ABSORPTION_PROBED",
                previous_state="LOCAL_RANGE_ACTIVE",
                next_state="ABSORPTION_PROBE",
                reason_code=(
                    "OUTWARD_AGGRESSOR_FLOW_FAILED_TO_REPRICE"
                    if self.params.enable_order_flow
                    else "PRICE_ONLY_RAID_REENTRY_ABLATION"
                ),
                reference_price=boundary,
                details={
                    "source_side": side,
                    "direction": direction,
                    "boundary": boundary,
                    "opposite_boundary": opposite,
                    "raid_extreme": extreme,
                    "event_atr": atr,
                    "delta_ratio": bar.delta_ratio,
                    "delta_threshold": features["delta_extreme"],
                    "price_efficiency": bar.efficiency,
                    "absorption_efficiency_threshold": features[
                        "absorption_efficiency"
                    ],
                    "close_location": bar.close_location,
                    "order_flow_enabled": self.params.enable_order_flow,
                },
            ),
        ]

    def _process_probe(
        self,
        bar: FlowBar,
        features: dict[str, float],
    ) -> tuple[list[FlowTransition], FlowTradePlan | None]:
        probe = self.active_probe
        assert probe is not None
        atr = features["atr"]
        age = bar.sequence - probe.initiated_sequence
        events: list[FlowTransition] = []

        if probe.direction < 0:
            if bar.high > probe.raid_extreme and bar.close < probe.boundary:
                probe.raid_extreme = bar.high
                events.append(
                    self._transition(
                        scenario_id=probe.scenario_id,
                        bar=bar,
                        event_type="RAID_EXTENDED",
                        previous_state="ABSORPTION_PROBE",
                        next_state="ABSORPTION_PROBE",
                        reason_code="HIGH_RAID_EXTENDED_BUT_REENTERED",
                        reference_price=bar.high,
                    ),
                )
            accepted_outside = bar.close >= probe.boundary + self.params.raid_atr * atr
            price_repriced = (
                bar.net_move < 0.0
                and bar.close
                <= probe.initial_bar_close - self.params.repricing_atr * atr
                and bar.close < probe.boundary
            )
            opposite_flow = bar.delta_ratio <= -features["delta_reversal"]
        else:
            if bar.low < probe.raid_extreme and bar.close > probe.boundary:
                probe.raid_extreme = bar.low
                events.append(
                    self._transition(
                        scenario_id=probe.scenario_id,
                        bar=bar,
                        event_type="RAID_EXTENDED",
                        previous_state="ABSORPTION_PROBE",
                        next_state="ABSORPTION_PROBE",
                        reason_code="LOW_RAID_EXTENDED_BUT_REENTERED",
                        reference_price=bar.low,
                    ),
                )
            accepted_outside = bar.close <= probe.boundary - self.params.raid_atr * atr
            price_repriced = (
                bar.net_move > 0.0
                and bar.close
                >= probe.initial_bar_close + self.params.repricing_atr * atr
                and bar.close > probe.boundary
            )
            opposite_flow = bar.delta_ratio >= features["delta_reversal"]

        if accepted_outside:
            self.counters["PROBE_INVALIDATED_BY_ACCEPTANCE"] += 1
            events.append(
                self._transition(
                    scenario_id=probe.scenario_id,
                    bar=bar,
                    event_type="SCENARIO_INVALIDATED",
                    previous_state="ABSORPTION_PROBE",
                    next_state="INVALIDATED",
                    reason_code="SOURCE_BOUNDARY_ACCEPTED_AFTER_RAID",
                    reference_price=bar.close,
                ),
            )
            self.active_probe = None
            return events, None

        efficient = bar.efficiency >= features["repricing_efficiency"]
        flow_confirmed = (
            opposite_flow if self.params.enable_order_flow else True
        )
        if price_repriced and efficient and flow_confirmed:
            plan = self._build_plan(bar, probe, features)
            if plan is None:
                self.counters["COST_OR_STRUCTURE_REJECTED"] += 1
                events.append(
                    self._transition(
                        scenario_id=probe.scenario_id,
                        bar=bar,
                        event_type="SCENARIO_INVALIDATED",
                        previous_state="ABSORPTION_PROBE",
                        next_state="INVALIDATED",
                        reason_code="OPPOSITE_BOUNDARY_FAILS_COST_ADJUSTED_RR",
                        reference_price=bar.close,
                        details={
                            "minimum_net_rr": self.params.min_net_rr,
                            "target": probe.opposite_boundary,
                        },
                    ),
                )
                self.active_probe = None
                return events, None
            self.counters["REPRICING_CONFIRMED"] += 1
            self.counters["TRADE_PLAN_CREATED"] += 1
            events.append(
                self._transition(
                    scenario_id=probe.scenario_id,
                    bar=bar,
                    event_type="REPRICING_CONFIRMED",
                    previous_state="ABSORPTION_PROBE",
                    next_state="ENTRY_READY",
                    reason_code=(
                        "OPPOSITE_AGGRESSOR_FLOW_EFFICIENTLY_REPRICED"
                        if self.params.enable_order_flow
                        else "PRICE_ONLY_EFFICIENT_REPRICING_ABLATION"
                    ),
                    reference_price=bar.close,
                    details={
                        "delta_ratio": bar.delta_ratio,
                        "delta_reversal_threshold": features["delta_reversal"],
                        "price_efficiency": bar.efficiency,
                        "repricing_efficiency_threshold": features[
                            "repricing_efficiency"
                        ],
                        "net_move_atr": bar.net_move / atr,
                        "entry": plan.entry_price,
                        "stop": plan.stop_price,
                        "target": plan.target_price,
                        "net_rr": plan.details["cost_adjusted_net_rr"],
                        "order_flow_enabled": self.params.enable_order_flow,
                    },
                ),
            )
            self.active_probe = None
            return events, plan

        if age >= self.params.probe_max_bars:
            self.counters["PROBE_EXPIRED"] += 1
            events.append(
                self._transition(
                    scenario_id=probe.scenario_id,
                    bar=bar,
                    event_type="SCENARIO_EXPIRED",
                    previous_state="ABSORPTION_PROBE",
                    next_state="EXPIRED",
                    reason_code="NO_EFFICIENT_OPPOSITE_REPRICING",
                    reference_price=bar.close,
                    details={
                        "age_event_bars": age,
                        "price_repriced": price_repriced,
                        "price_efficiency": bar.efficiency,
                        "flow_confirmed": flow_confirmed,
                    },
                ),
            )
            self.active_probe = None
        return events, None

    def _execution_buffer(self, entry: float, atr: float) -> float:
        volatility_floor = atr * self.params.stop_buffer_atr
        cost_floor = (
            entry
            * (self.params.maker_fee + self.params.taker_fee)
            * self.params.cost_floor_multiple
            + self.tick_size * self.params.execution_reserve_ticks
        )
        return max(volatility_floor, cost_floor)

    def _net_rr(
        self,
        *,
        direction: int,
        entry: float,
        stop: float,
        target: float,
    ) -> float:
        if direction > 0:
            gross_reward = target - entry
            gross_loss = entry - stop
        else:
            gross_reward = entry - target
            gross_loss = stop - entry
        if gross_reward <= 0.0 or gross_loss <= 0.0:
            return float("-inf")
        entry_cost = entry * self.params.maker_fee
        target_cost = target * self.params.maker_fee
        stop_cost = stop * self.params.taker_fee
        reserve = self.tick_size * self.params.execution_reserve_ticks
        net_reward = gross_reward - entry_cost - target_cost - reserve
        net_loss = gross_loss + entry_cost + stop_cost + reserve
        return net_reward / net_loss if net_loss > 0.0 else float("-inf")

    def _build_plan(
        self,
        bar: FlowBar,
        probe: FlowRaidProbe,
        features: dict[str, float],
    ) -> FlowTradePlan | None:
        atr = features["atr"]
        body = abs(bar.close - bar.open)
        if body <= self.tick_size:
            return None
        if probe.direction < 0:
            if bar.close >= bar.open:
                return None
            entry = bar.close + body * self.params.retrace_fraction
            stop = probe.raid_extreme + self._execution_buffer(entry, atr)
            target = probe.opposite_boundary
            valid = target < entry < stop
        else:
            if bar.close <= bar.open:
                return None
            entry = bar.close - body * self.params.retrace_fraction
            stop = probe.raid_extreme - self._execution_buffer(entry, atr)
            target = probe.opposite_boundary
            valid = stop < entry < target
        if not valid:
            return None
        net_rr = self._net_rr(
            direction=probe.direction,
            entry=entry,
            stop=stop,
            target=target,
        )
        if net_rr < self.params.min_net_rr:
            return None
        return FlowTradePlan(
            scenario_id=probe.scenario_id,
            scenario="FLOW_ABSORPTION_REPRICING",
            direction=probe.direction,
            observed_ns=bar.end_ns,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            source_boundary=probe.boundary,
            opposite_boundary=probe.opposite_boundary,
            event_atr=atr,
            entry_expiry_bars=self.params.entry_expiry_bars,
            invalidation_price=stop,
            details={
                "cost_adjusted_net_rr": net_rr,
                "raid_delta_ratio": probe.initial_delta_ratio,
                "raid_efficiency": probe.initial_efficiency,
                "repricing_delta_ratio": bar.delta_ratio,
                "repricing_efficiency": bar.efficiency,
                "source_side": probe.source_side,
                "entry_retrace_fraction": self.params.retrace_fraction,
                "source_boundary": probe.boundary,
                "opposite_boundary": probe.opposite_boundary,
                "raid_extreme": probe.raid_extreme,
            },
        )
