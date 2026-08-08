"""Candidate 16 v2: accepted-breakout failure on completed source auctions.

Candidate 05 continues to own causal Binance data preparation and every
execution/accounting concern: NautilusTrader event replay, orders, fills, fees,
latency, margin, liquidation, portfolio accounting, and continuous NAV.  This
module replaces only context selection and the trading decision policy.

The policy deliberately separates four roles:

* context: completed 15m/60m/daily source-auction extremes;
* state: two completed closes establish true acceptance outside the source;
* transition: a later opposite initiative bar loses the accepted boundary;
* entry: a still later initiative break or first rejected boundary retest.

A consumed source boundary cannot create another scenario, and a scenario has
one terminal outcome.  Generic wick sweeps and acceptance continuation are
explicit no-trades in this candidate.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import math
from typing import Any

from nautilus_trader.model.enums import OrderSide, TimeInForce

from accepted_failure_router import AcceptedFailureScenario
from accepted_failure_router import AuctionLevel
from accepted_failure_router import ResolutionDecision
from accepted_failure_router import ResolutionObservation
from accepted_failure_router import ResolutionState
from accepted_failure_router import RouterConfig
from accepted_failure_router import advance
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import LiquidityResponseConfig
from strategy_base import LiquidityResponseStrategy
from strategy_base import PendingSetup


_CUSTOM_PENDING_BRANCH = "C16_V2_ACCEPTED_FAILURE"


@dataclass(slots=True)
class _SourceAccumulator:
    horizon_minutes: int
    key: int
    range_start_ns: int
    range_end_ns: int
    high: float
    low: float
    count: int


class Candidate16Config(LiquidityResponseConfig, frozen=True):
    source_horizons_minutes: tuple[int, ...] = (15, 60, 1_440)
    source_min_completion_fraction: float = 0.95
    source_max_age_minutes: int = 10_080
    source_max_active_per_horizon_side: int = 96

    minimum_breach_atr: float = 0.08
    cluster_tolerance_atr: float = 0.15
    approach_period: int = 15
    minimum_approach_efficiency: float = 0.08
    minimum_approach_flow: float = 0.02
    resolution_cooldown_bars: int = 6

    router_acceptance_buffer_atr: float = 0.08
    router_acceptance_closes: int = 2
    router_acceptance_timeout_bars: int = 8
    router_minimum_acceptance_displacement_atr: float = 0.35
    router_minimum_acceptance_flow: float = 0.08
    router_minimum_acceptance_efficiency: float = 0.30
    router_minimum_participation_burst: float = 1.00

    router_failure_inside_atr: float = 0.06
    router_minimum_failure_displacement_atr: float = 0.25
    router_minimum_failure_flow: float = 0.08
    router_minimum_failure_efficiency: float = 0.20
    router_minimum_failure_close_location: float = 0.58
    router_failure_timeout_bars: int = 8

    router_trigger_timeout_bars: int = 2
    router_trigger_break_atr: float = 0.02
    router_minimum_trigger_body_atr: float = 0.12
    router_minimum_trigger_flow: float = 0.02
    router_minimum_trigger_efficiency: float = 0.15
    router_minimum_trigger_close_location: float = 0.55
    router_retest_tolerance_atr: float = 0.20
    router_retest_close_inside_atr: float = 0.02
    router_maximum_retest_counterflow: float = 0.12


class Candidate16Strategy(LiquidityResponseStrategy):
    """Accepted-auction failure router with one global order/position."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        self._source_accumulators: dict[int, _SourceAccumulator] = {}
        self._source_levels: dict[str, AuctionLevel] = {}
        self._context_history: deque[dict[str, float | int]] = deque(
            maxlen=max(240, config.approach_period + 10),
        )
        self._resolution: AcceptedFailureScenario | None = None
        self._last_resolution_index = -10**12

        self._router_config = RouterConfig(
            acceptance_buffer_atr=config.router_acceptance_buffer_atr,
            acceptance_closes=config.router_acceptance_closes,
            acceptance_timeout_bars=config.router_acceptance_timeout_bars,
            minimum_acceptance_displacement_atr=(
                config.router_minimum_acceptance_displacement_atr
            ),
            minimum_acceptance_flow=config.router_minimum_acceptance_flow,
            minimum_acceptance_efficiency=(
                config.router_minimum_acceptance_efficiency
            ),
            minimum_participation_burst=(
                config.router_minimum_participation_burst
            ),
            failure_inside_atr=config.router_failure_inside_atr,
            minimum_failure_displacement_atr=(
                config.router_minimum_failure_displacement_atr
            ),
            minimum_failure_flow=config.router_minimum_failure_flow,
            minimum_failure_efficiency=config.router_minimum_failure_efficiency,
            minimum_failure_close_location=(
                config.router_minimum_failure_close_location
            ),
            failure_timeout_bars=config.router_failure_timeout_bars,
            trigger_timeout_bars=config.router_trigger_timeout_bars,
            trigger_break_atr=config.router_trigger_break_atr,
            minimum_trigger_body_atr=config.router_minimum_trigger_body_atr,
            minimum_trigger_flow=config.router_minimum_trigger_flow,
            minimum_trigger_efficiency=config.router_minimum_trigger_efficiency,
            minimum_trigger_close_location=(
                config.router_minimum_trigger_close_location
            ),
            retest_tolerance_atr=config.router_retest_tolerance_atr,
            retest_close_inside_atr=config.router_retest_close_inside_atr,
            maximum_retest_counterflow=(
                config.router_maximum_retest_counterflow
            ),
        )
        self.diagnostics.update(
            {
                "candidate16_v2_source_levels_created": 0,
                "candidate16_v2_source_levels_expired": 0,
                "candidate16_v2_source_levels_consumed": 0,
                "candidate16_v2_incomplete_source_ranges": 0,
                "candidate16_v2_breaches": 0,
                "candidate16_v2_ambiguous_two_sided_breaches": 0,
                "candidate16_v2_approach_rejections": 0,
                "candidate16_v2_true_acceptances": 0,
                "candidate16_v2_accepted_failures": 0,
                "candidate16_v2_independent_triggers": 0,
                "candidate16_v2_geometry_rejections": 0,
                "candidate16_v2_entries": 0,
                "candidate16_v2_no_trades": 0,
                "candidate16_v2_no_trade_reasons": {},
            },
        )

    # ------------------------------------------------------------------
    # Context construction: completed source auctions only.
    # ------------------------------------------------------------------
    def _confirm_pivots(self, row: dict[str, float | int]) -> None:
        """Disable Candidate 05's two-sided micro-pivot pool generation."""
        del row

    def _roll_session(self, row: dict[str, float | int]) -> None:
        """Build completed 15m/60m/daily ranges without future information."""
        ts_event = int(row["ts"])
        self._context_history.append(
            {
                "bar_index": self.bar_index,
                "close": float(row["close"]),
                "flow_60s": self._feature("flow_60s"),
                "notional_60s": self._feature("notional_60s"),
            },
        )

        for horizon in self.config.source_horizons_minutes:
            interval_ns = int(horizon) * 60 * 1_000_000_000
            key = (ts_event - 1) // interval_ns
            accumulator = self._source_accumulators.get(int(horizon))
            if accumulator is None:
                self._source_accumulators[int(horizon)] = self._new_accumulator(
                    int(horizon),
                    int(key),
                    row,
                )
                continue
            if int(key) != accumulator.key:
                self._finalize_source_range(accumulator, observed_index=self.bar_index)
                self._source_accumulators[int(horizon)] = self._new_accumulator(
                    int(horizon),
                    int(key),
                    row,
                )
                continue
            accumulator.high = max(accumulator.high, float(row["high"]))
            accumulator.low = min(accumulator.low, float(row["low"]))
            accumulator.count += 1

    @staticmethod
    def _new_accumulator(
        horizon_minutes: int,
        key: int,
        row: dict[str, float | int],
    ) -> _SourceAccumulator:
        interval_ns = horizon_minutes * 60 * 1_000_000_000
        return _SourceAccumulator(
            horizon_minutes=horizon_minutes,
            key=key,
            range_start_ns=key * interval_ns,
            range_end_ns=(key + 1) * interval_ns - 1,
            high=float(row["high"]),
            low=float(row["low"]),
            count=1,
        )

    def _finalize_source_range(
        self,
        accumulator: _SourceAccumulator,
        *,
        observed_index: int,
    ) -> None:
        minimum_count = max(
            1,
            math.ceil(
                accumulator.horizon_minutes
                * self.config.source_min_completion_fraction
            ),
        )
        if accumulator.count < minimum_count:
            self.diagnostics["candidate16_v2_incomplete_source_ranges"] = int(
                self.diagnostics["candidate16_v2_incomplete_source_ranges"],
            ) + 1
            return
        if not (
            math.isfinite(accumulator.high)
            and math.isfinite(accumulator.low)
            and accumulator.high > accumulator.low > 0.0
        ):
            return

        midpoint = 0.5 * (accumulator.high + accumulator.low)
        prefix = f"source-{accumulator.horizon_minutes}-{accumulator.key}"
        high = AuctionLevel(
            level_id=f"{prefix}-HIGH",
            kind="HIGH",
            price=accumulator.high,
            horizon_minutes=accumulator.horizon_minutes,
            range_start_ns=accumulator.range_start_ns,
            range_end_ns=accumulator.range_end_ns,
            range_high=accumulator.high,
            range_low=accumulator.low,
            range_midpoint=midpoint,
            observed_index=observed_index,
        )
        low = AuctionLevel(
            level_id=f"{prefix}-LOW",
            kind="LOW",
            price=accumulator.low,
            horizon_minutes=accumulator.horizon_minutes,
            range_start_ns=accumulator.range_start_ns,
            range_end_ns=accumulator.range_end_ns,
            range_high=accumulator.high,
            range_low=accumulator.low,
            range_midpoint=midpoint,
            observed_index=observed_index,
        )
        self._source_levels[high.level_id] = high
        self._source_levels[low.level_id] = low
        self.diagnostics["candidate16_v2_source_levels_created"] = int(
            self.diagnostics["candidate16_v2_source_levels_created"],
        ) + 2

    def _prune_pools(self, row: dict[str, float | int]) -> None:
        """Expire completed source levels; never recreate base micro pools."""
        del row
        self.active_pools.clear()
        maximum_age = int(self.config.source_max_age_minutes)
        expired = [
            level_id
            for level_id, level in self._source_levels.items()
            if self.bar_index - level.observed_index > maximum_age
        ]
        for level_id in expired:
            self._source_levels.pop(level_id, None)
        self.diagnostics["candidate16_v2_source_levels_expired"] = int(
            self.diagnostics["candidate16_v2_source_levels_expired"],
        ) + len(expired)

        keep_ids: set[str] = set()
        limit = int(self.config.source_max_active_per_horizon_side)
        for horizon in self.config.source_horizons_minutes:
            for kind in ("HIGH", "LOW"):
                selected = sorted(
                    (
                        level
                        for level in self._source_levels.values()
                        if level.horizon_minutes == int(horizon)
                        and level.kind == kind
                    ),
                    key=lambda level: (level.observed_index, level.range_end_ns),
                    reverse=True,
                )[:limit]
                keep_ids.update(level.level_id for level in selected)
        capped = [
            level_id
            for level_id in self._source_levels
            if level_id not in keep_ids
        ]
        for level_id in capped:
            self._source_levels.pop(level_id, None)
        self.diagnostics["candidate16_v2_source_levels_expired"] = int(
            self.diagnostics["candidate16_v2_source_levels_expired"],
        ) + len(capped)

    # ------------------------------------------------------------------
    # Breach context and causal state progression.
    # ------------------------------------------------------------------
    def _resolution_observation(
        self,
        row: dict[str, float | int],
    ) -> ResolutionObservation:
        return ResolutionObservation(
            bar_index=self.bar_index,
            ts_event=int(row["ts"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            atr=self._atr(),
            flow_60s=self._feature("flow_60s"),
            notional_burst=self._feature("notional_burst"),
            efficiency_60s=self._feature("efficiency_60s"),
        )

    def _approach_pressure(self, direction: int) -> tuple[float, float]:
        history = list(self._context_history)[:-1]
        window = history[-int(self.config.approach_period) :]
        usable = [
            item
            for item in window
            if math.isfinite(float(item["close"]))
            and math.isfinite(float(item["flow_60s"]))
            and math.isfinite(float(item["notional_60s"]))
            and float(item["notional_60s"]) > 0.0
        ]
        if len(usable) < 2:
            return 0.0, 0.0
        closes = [float(item["close"]) for item in usable]
        path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
        directional_move = direction * (closes[-1] - closes[0])
        efficiency = max(0.0, directional_move) / path if path > 0.0 else 0.0
        total_notional = sum(float(item["notional_60s"]) for item in usable)
        flow = (
            sum(
                float(item["flow_60s"]) * float(item["notional_60s"])
                for item in usable
            )
            / total_notional
            if total_notional > 0.0
            else 0.0
        )
        return min(efficiency, 1.0), flow

    def _level_confluence(self, level: AuctionLevel, atr: float) -> int:
        tolerance = self.config.cluster_tolerance_atr * atr
        return sum(
            other.kind == level.kind
            and abs(other.price - level.price) <= tolerance
            for other in self._source_levels.values()
        )

    def _select_level(
        self,
        levels: list[AuctionLevel],
        *,
        atr: float,
    ) -> AuctionLevel:
        return max(
            levels,
            key=lambda level: (
                level.horizon_minutes,
                self._level_confluence(level, atr),
                level.direction * level.price,
            ),
        )

    def _consume_source_cluster(self, level: AuctionLevel, atr: float) -> int:
        tolerance = self.config.cluster_tolerance_atr * atr
        consumed = [
            level_id
            for level_id, other in self._source_levels.items()
            if other.kind == level.kind
            and abs(other.price - level.price) <= tolerance
        ]
        for level_id in consumed:
            self._source_levels.pop(level_id, None)
        self.diagnostics["candidate16_v2_source_levels_consumed"] = int(
            self.diagnostics["candidate16_v2_source_levels_consumed"],
        ) + len(consumed)
        return len(consumed)

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        if self._resolution is not None or self.pending is not None:
            return
        if (
            self.bar_index - self._last_resolution_index
            < self.config.resolution_cooldown_bars
        ):
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        penetration = self.config.minimum_breach_atr * atr
        high_levels = [
            level
            for level in self._source_levels.values()
            if level.kind == "HIGH"
            and level.observed_index < self.bar_index
            and previous_close <= level.price
            and float(row["high"]) >= level.price + penetration
        ]
        low_levels = [
            level
            for level in self._source_levels.values()
            if level.kind == "LOW"
            and level.observed_index < self.bar_index
            and previous_close >= level.price
            and float(row["low"]) <= level.price - penetration
        ]
        if high_levels and low_levels:
            seen: set[str] = set()
            for level in high_levels + low_levels:
                if level.level_id in seen:
                    continue
                consumed = [
                    other.level_id
                    for other in self._source_levels.values()
                    if other.kind == level.kind
                    and abs(other.price - level.price)
                    <= self.config.cluster_tolerance_atr * atr
                ]
                seen.update(consumed)
                self._consume_source_cluster(level, atr)
            self.diagnostics["candidate16_v2_ambiguous_two_sided_breaches"] = int(
                self.diagnostics["candidate16_v2_ambiguous_two_sided_breaches"],
            ) + 1
            self._last_resolution_index = self.bar_index
            return
        candidates = high_levels or low_levels
        if not candidates:
            return

        level = self._select_level(candidates, atr=atr)
        direction = level.direction
        confluence = self._level_confluence(level, atr)
        approach_efficiency, approach_flow = self._approach_pressure(direction)
        directional_approach_flow = direction * approach_flow
        cluster_size = self._consume_source_cluster(level, atr)

        self.scenario_counter += 1
        scenario_id = f"c16v2-{self.scenario_counter:07d}"
        base_details: dict[str, Any] = {
            "level_id": level.level_id,
            "level_kind": level.kind,
            "level_price": level.price,
            "horizon_minutes": level.horizon_minutes,
            "range_start_ns": level.range_start_ns,
            "range_end_ns": level.range_end_ns,
            "range_high": level.range_high,
            "range_low": level.range_low,
            "range_midpoint": level.range_midpoint,
            "confluence_count": confluence,
            "consumed_cluster_size": cluster_size,
            "approach_efficiency": approach_efficiency,
            "approach_flow": approach_flow,
            "directional_approach_flow": directional_approach_flow,
            "interaction_bar_index": self.bar_index,
            "interaction_ts_event": int(row["ts"]),
        }
        if (
            approach_efficiency < self.config.minimum_approach_efficiency
            or directional_approach_flow < self.config.minimum_approach_flow
        ):
            self.diagnostics["candidate16_v2_approach_rejections"] = int(
                self.diagnostics["candidate16_v2_approach_rejections"],
            ) + 1
            self._transition(
                scenario_id,
                "SOURCE_BOUNDARY_REJECTED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "NO_DIRECTIONAL_APPROACH_PRESSURE",
                level.price,
                base_details,
            )
            self._last_resolution_index = self.bar_index
            return

        observation = self._resolution_observation(row)
        scenario = AcceptedFailureScenario.start(
            scenario_id=scenario_id,
            level=level,
            observation=observation,
        )
        scenario = advance(scenario, observation, self._router_config)
        self._resolution = scenario
        maximum_age = (
            self.config.router_acceptance_timeout_bars
            + self.config.router_failure_timeout_bars
            + self.config.router_trigger_timeout_bars
            + 2
        )
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch=_CUSTOM_PENDING_BRANCH,
            side=0,
            swept_kind=level.kind,
            pool_id=level.level_id,
            pool_level=level.price,
            created_index=self.bar_index,
            expires_index=self.bar_index + maximum_age,
            sweep_extreme=(
                float(row["high"]) if direction > 0 else float(row["low"])
            ),
            structure=level.price,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details={
                **base_details,
                "router_state": self._state_details(scenario),
            },
        )
        self.diagnostics["candidate16_v2_breaches"] = int(
            self.diagnostics["candidate16_v2_breaches"],
        ) + 1
        self._transition(
            scenario_id,
            "SOURCE_AUCTION_BOUNDARY_BREACHED",
            int(row["ts"]),
            int(row["ts"]),
            scenario.state.value,
            scenario.reason,
            level.price,
            self.pending.details,
        )

    @staticmethod
    def _state_details(state: AcceptedFailureScenario) -> dict[str, Any]:
        details = asdict(state)
        details["state"] = state.state.value
        details["decision"] = state.decision.value
        return details

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None:
            return False
        if setup.branch != _CUSTOM_PENDING_BRANCH:
            return super()._process_pending(row)
        if self._resolution is None:
            self._close_resolution(row, "MISSING_ACCEPTED_FAILURE_STATE")
            return True

        previous_state = self._resolution.state
        state = advance(
            self._resolution,
            self._resolution_observation(row),
            self._router_config,
        )
        self._resolution = state
        setup.details["router_state"] = self._state_details(state)
        if previous_state is not state.state:
            if state.state is ResolutionState.ACCEPTED:
                self.diagnostics["candidate16_v2_true_acceptances"] = int(
                    self.diagnostics["candidate16_v2_true_acceptances"],
                ) + 1
            elif state.state is ResolutionState.WAIT_TRIGGER:
                self.diagnostics["candidate16_v2_accepted_failures"] = int(
                    self.diagnostics["candidate16_v2_accepted_failures"],
                ) + 1
        self._transition(
            setup.scenario_id,
            "ACCEPTED_FAILURE_STATE_OBSERVED",
            int(row["ts"]),
            int(row["ts"]),
            state.state.value if state.decision is ResolutionDecision.PENDING else state.decision.value,
            state.reason,
            float(row["close"]),
            setup.details,
        )
        if state.decision is ResolutionDecision.PENDING:
            return True
        if state.decision is ResolutionDecision.NO_TRADE:
            self._close_resolution(row, state.reason, transition_already_written=True)
            return True

        self.diagnostics["candidate16_v2_independent_triggers"] = int(
            self.diagnostics["candidate16_v2_independent_triggers"],
        ) + 1
        return self._submit_resolution_entry(state, setup, row)

    def _close_resolution(
        self,
        row: dict[str, float | int],
        reason: str,
        *,
        transition_already_written: bool = False,
    ) -> None:
        setup = self.pending
        scenario_id = (
            setup.scenario_id
            if setup is not None
            else (
                self._resolution.scenario_id
                if self._resolution is not None
                else "candidate16-v2-unmatched"
            )
        )
        details = setup.details if setup is not None else {}
        if not transition_already_written:
            self._transition(
                scenario_id,
                "ACCEPTED_FAILURE_NO_TRADE",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                reason,
                float(row["close"]),
                details,
            )
        else:
            self.scenario_states[scenario_id] = "CLOSED"
        self.diagnostics["candidate16_v2_no_trades"] = int(
            self.diagnostics["candidate16_v2_no_trades"],
        ) + 1
        reasons = self.diagnostics["candidate16_v2_no_trade_reasons"]
        assert isinstance(reasons, dict)
        reasons[reason] = int(reasons.get(reason, 0)) + 1
        self.pending = None
        self._resolution = None
        self._last_resolution_index = self.bar_index

    def _expire_pending(self, row: dict[str, float | int], reason: str) -> None:
        if self.pending is not None and self.pending.branch == _CUSTOM_PENDING_BRANCH:
            self._close_resolution(row, reason)
            return
        super()._expire_pending(row, reason)

    # ------------------------------------------------------------------
    # Same-leg entry, invalidation, objective and current-NAV risk.
    # ------------------------------------------------------------------
    def _submit_resolution_entry(
        self,
        state: AcceptedFailureScenario,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        level = state.level
        side = -level.direction
        atr = self._atr()
        entry = float(row["close"])
        if not math.isfinite(atr) or atr <= 0.0:
            self._reject_geometry(row, "INVALID_ENTRY_ATR")
            return True
        if state.failure_high is None or state.failure_low is None:
            self._reject_geometry(row, "MISSING_FAILURE_EXTREME")
            return True

        if side > 0:
            stop = min(level.price, state.failure_low, float(row["low"])) - (
                self.config.stop_buffer_atr * atr
            )
            objective_prices = [level.range_midpoint, level.range_high]
        else:
            stop = max(level.price, state.failure_high, float(row["high"])) + (
                self.config.stop_buffer_atr * atr
            )
            objective_prices = [level.range_midpoint, level.range_low]

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self._reject_geometry(row, "INVALID_STOP_GEOMETRY")
            return True

        target: float | None = None
        target_source: str | None = None
        target_net_r = -math.inf
        labels = ("SOURCE_RANGE_MIDPOINT", "SOURCE_OPPOSITE_EDGE")
        for label, price in zip(labels, objective_prices):
            if side * (price - entry) <= 0.0:
                continue
            net_r = net_r_at_price(entry, price, side, planned_loss, cost_rate)
            if net_r >= self.config.min_target_net_r:
                target = price
                target_source = label
                target_net_r = net_r
                break
        if target is None or target_source is None:
            self._reject_geometry(
                row,
                "SOURCE_AUCTION_OBJECTIVE_INSUFFICIENT_AFTER_COSTS",
            )
            return True

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        raw_quantity = risk_budget / planned_loss
        quantity_value = floor_quantity(
            raw_quantity,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            self._reject_geometry(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return True
        if side > 0 and not (stop < entry < target):
            self._reject_geometry(row, "INVALID_LONG_BRACKET")
            return True
        if side < 0 and not (target < entry < stop):
            self._reject_geometry(row, "INVALID_SHORT_BRACKET")
            return True

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            time_in_force=TimeInForce.GTC,
            tp_price=self.instrument.make_price(target),
            sl_trigger_price=self.instrument.make_price(stop),
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = "ACCEPTED_BREAKOUT_FAILURE_REVERSAL"
        self.current_pool_level = level.price
        self.pending = None
        self._resolution = None
        self._last_resolution_index = self.bar_index
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["candidate16_v2_entries"] = int(
            self.diagnostics["candidate16_v2_entries"],
        ) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            state.reason,
            entry,
            {
                **setup.details,
                "candidate16_branch": self.current_branch,
                "entry_trigger": state.trigger_kind,
                "side": side,
                "entry_estimate": entry,
                "stop": stop,
                "target": target,
                "target_source": target_source,
                "target_net_r": target_net_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit": planned_loss,
                "planned_account_loss": quantity_value * planned_loss,
            },
        )
        return True

    def _reject_geometry(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        self.diagnostics["candidate16_v2_geometry_rejections"] = int(
            self.diagnostics["candidate16_v2_geometry_rejections"],
        ) + 1
        self._close_resolution(row, reason)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._resolution = None

    def on_order_rejected(self, event: Any) -> None:
        """A rejected contingent order is a safety failure, never hidden."""
        super().on_order_rejected(event)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)


__all__ = ["Candidate16Config", "Candidate16Strategy"]
