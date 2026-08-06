"""Causal liquidity-auction detector and scenario state machine."""

from __future__ import annotations

from collections import deque
from typing import Any

from c10_model import AuctionRange
from c10_model import BarView
from c10_model import MachineParams
from c10_model import NS_PER_MINUTE
from c10_model import Setup
from c10_model import TradePlan
from c10_model import Transition


class AuctionStateMachine:
    """Causal detector/scenario state machine independent of execution mechanics."""

    def __init__(self, params: MachineParams, *, tick_size: float, instrument_id: str):
        self.params = params
        self.tick_size = tick_size
        self.instrument_id = instrument_id
        self.bar_index = -1
        self.history: deque[BarView] = deque(maxlen=2_000)
        self.true_ranges: deque[float] = deque(maxlen=params.atr_lookback)
        self.previous_close: float | None = None
        self.current_range: AuctionRange | None = None
        self.previous_range: AuctionRange | None = None
        self.past_ranges: deque[AuctionRange] = deque(maxlen=36)
        self.active: Setup | None = None
        self.consumed_block_id: int | None = None

    @property
    def atr(self) -> float | None:
        minimum = max(20, self.params.atr_lookback // 2)
        if len(self.true_ranges) < minimum:
            return None
        ordered = sorted(self.true_ranges)
        trim = max(1, len(ordered) // 10)
        core = ordered[trim:-trim] if len(ordered) > 2 * trim else ordered
        return sum(core) / len(core)

    def reset_active(self) -> None:
        self.active = None

    def _transition(
        self,
        setup: Setup,
        bar: BarView,
        *,
        event_type: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> Transition:
        previous = setup.state
        setup.state = next_state
        return Transition(
            scenario_id=setup.scenario_id,
            event_type=event_type,
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            previous_state=previous,
            next_state=next_state,
            reason_code=reason_code,
            reference_price=reference_price,
            details=dict(details or {}),
        )

    def _new_setup(
        self,
        *,
        bar: BarView,
        scenario: str,
        direction: int,
        boundary: float,
        atr: float,
        raid_extreme: float,
        approach_level: float,
        initial_state: str,
    ) -> Setup:
        block = self.current_range.block_id if self.current_range is not None else -1
        side = "LONG" if direction > 0 else "SHORT"
        return Setup(
            scenario_id=f"{self.instrument_id}:{block}:{scenario}:{side}:{bar.ts_ns}",
            scenario=scenario,
            direction=direction,
            boundary=boundary,
            state=initial_state,
            created_index=self.bar_index,
            created_ns=bar.ts_ns,
            atr=atr,
            raid_extreme=raid_extreme,
            approach_level=approach_level,
            breakout_extreme=raid_extreme,
        )

    def _start_block(self, bar: BarView, block_id: int) -> None:
        if self.current_range is not None:
            expected = self.params.block_minutes
            if self.current_range.bars >= int(expected * 0.90):
                self.previous_range = self.current_range
                self.past_ranges.append(self.current_range)
            else:
                self.previous_range = None
        self.current_range = AuctionRange(
            block_id=block_id,
            start_ns=bar.ts_ns,
            end_ns=bar.ts_ns,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
        )
        self.active = None
        self.consumed_block_id = None

    def _execution_buffer(self, entry: float, atr: float) -> float:
        round_trip_cost = entry * (
            self.params.maker_fee + self.params.taker_fee
        ) * self.params.cost_floor_multiple
        tick_reserve = self.tick_size * self.params.execution_reserve_ticks
        return max(
            atr * self.params.stop_buffer_atr,
            round_trip_cost + tick_reserve,
        )

    def _net_rr(
        self,
        *,
        direction: int,
        entry: float,
        stop: float,
        target: float,
    ) -> float:
        gross_reward = (target - entry) * direction
        if gross_reward <= 0:
            return float("-inf")
        loss_per_unit = (
            abs(entry - stop)
            + entry * self.params.maker_fee
            + stop * self.params.taker_fee
            + self.tick_size * self.params.execution_reserve_ticks
        )
        reward_per_unit = (
            gross_reward
            - entry * self.params.maker_fee
            - target * self.params.maker_fee
            - self.tick_size * self.params.execution_reserve_ticks
        )
        if loss_per_unit <= 0 or reward_per_unit <= 0:
            return float("-inf")
        return reward_per_unit / loss_per_unit

    def _detect_setup(self, bar: BarView, atr: float) -> list[Transition]:
        transitions: list[Transition] = []
        pool = self.previous_range
        if pool is None or pool.width <= 0.0 or len(self.history) < self.params.approach_lookback:
            return transitions

        recent = list(self.history)[-self.params.approach_lookback :]
        approach_low = min(item.low for item in recent)
        approach_high = max(item.high for item in recent)
        raid_buffer = max(self.tick_size * 2.0, atr * self.params.raid_atr)
        accept_buffer = max(self.tick_size * 2.0, atr * self.params.acceptance_atr)
        high_excess = bar.high - pool.high
        low_excess = pool.low - bar.low

        # A bar which raids both sides has no directionally resolved auction result.
        if high_excess >= raid_buffer and low_excess >= raid_buffer:
            return transitions

        setup: Setup | None = None
        reason = ""
        if high_excess >= raid_buffer:
            if self.params.enable_rejection and bar.close < pool.high:
                setup = self._new_setup(
                    bar=bar,
                    scenario="REJECTION",
                    direction=-1,
                    boundary=pool.high,
                    atr=atr,
                    raid_extreme=bar.high,
                    approach_level=approach_low,
                    initial_state="POOL_ACTIVE",
                )
                reason = "HIGH_RAID_REENTERED"
            elif self.params.enable_acceptance and bar.close >= pool.high + accept_buffer:
                setup = self._new_setup(
                    bar=bar,
                    scenario="ACCEPTANCE",
                    direction=1,
                    boundary=pool.high,
                    atr=atr,
                    raid_extreme=bar.high,
                    approach_level=pool.high,
                    initial_state="POOL_ACTIVE",
                )
                reason = "HIGH_BOUNDARY_CLOSE_OUTSIDE"
        elif low_excess >= raid_buffer:
            if self.params.enable_rejection and bar.close > pool.low:
                setup = self._new_setup(
                    bar=bar,
                    scenario="REJECTION",
                    direction=1,
                    boundary=pool.low,
                    atr=atr,
                    raid_extreme=bar.low,
                    approach_level=approach_high,
                    initial_state="POOL_ACTIVE",
                )
                reason = "LOW_RAID_REENTERED"
            elif self.params.enable_acceptance and bar.close <= pool.low - accept_buffer:
                setup = self._new_setup(
                    bar=bar,
                    scenario="ACCEPTANCE",
                    direction=-1,
                    boundary=pool.low,
                    atr=atr,
                    raid_extreme=bar.low,
                    approach_level=pool.low,
                    initial_state="POOL_ACTIVE",
                )
                reason = "LOW_BOUNDARY_CLOSE_OUTSIDE"

        if setup is None:
            return transitions

        self.active = setup
        next_state = "RAIDED" if setup.scenario == "REJECTION" else "ACCEPTANCE_PROBE"
        transitions.append(
            self._transition(
                setup,
                bar,
                event_type="LIQUIDITY_EVENT",
                next_state=next_state,
                reason_code=reason,
                reference_price=setup.boundary,
                details={
                    "atr_before_event": atr,
                    "raid_extreme": setup.raid_extreme,
                    "pool_high": pool.high,
                    "pool_low": pool.low,
                    "pool_midpoint": pool.midpoint,
                    "pool_block_id": pool.block_id,
                },
            ),
        )
        return transitions

    def _select_rejection_target(
        self,
        setup: Setup,
        entry: float,
        stop: float,
    ) -> tuple[float | None, str, float]:
        pool = self.previous_range
        if pool is None:
            return None, "NONE", float("-inf")
        if setup.direction < 0:
            candidates = [
                (pool.midpoint, "PRIOR_BLOCK_MIDPOINT"),
                (pool.low, "OPPOSITE_BLOCK_LOW"),
            ]
        else:
            candidates = [
                (pool.midpoint, "PRIOR_BLOCK_MIDPOINT"),
                (pool.high, "OPPOSITE_BLOCK_HIGH"),
            ]
        for target, name in candidates:
            net_rr = self._net_rr(
                direction=setup.direction,
                entry=entry,
                stop=stop,
                target=target,
            )
            if net_rr >= self.params.min_net_rr:
                return target, name, net_rr
        return None, "NONE", float("-inf")

    def _process_rejection(
        self,
        bar: BarView,
        atr: float,
    ) -> tuple[list[Transition], TradePlan | None]:
        setup = self.active
        assert setup is not None and setup.scenario == "REJECTION"
        transitions: list[Transition] = []
        age = self.bar_index - setup.created_index
        accept_buffer = max(self.tick_size * 2.0, atr * self.params.acceptance_atr)

        if setup.direction < 0:
            setup.raid_extreme = max(setup.raid_extreme, bar.high)
            accepted = bar.close >= setup.boundary + accept_buffer
        else:
            setup.raid_extreme = min(setup.raid_extreme, bar.low)
            accepted = bar.close <= setup.boundary - accept_buffer
        if accepted:
            transitions.append(
                self._transition(
                    setup,
                    bar,
                    event_type="SCENARIO_INVALIDATED",
                    next_state="INVALIDATED",
                    reason_code="REJECTION_BECAME_ACCEPTANCE",
                    reference_price=bar.close,
                ),
            )
            self.active = None
            return transitions, None

        if age > self.params.rejection_confirm_bars:
            transitions.append(
                self._transition(
                    setup,
                    bar,
                    event_type="SCENARIO_EXPIRED",
                    next_state="EXPIRED",
                    reason_code="NO_DISPLACEMENT_AFTER_RAID",
                ),
            )
            self.active = None
            return transitions, None

        body = abs(bar.close - bar.open)
        bar_range = max(self.tick_size, bar.high - bar.low)
        close_location = (bar.close - bar.low) / bar_range
        displacement = body >= atr * self.params.displacement_atr
        if setup.direction < 0:
            confirmed = (
                displacement
                and bar.close < setup.approach_level
                and bar.close < bar.open
                and close_location <= 0.35
            )
        else:
            confirmed = (
                displacement
                and bar.close > setup.approach_level
                and bar.close > bar.open
                and close_location >= 0.65
            )
        if not confirmed:
            return transitions, None

        setup.confirmation_index = self.bar_index
        origin = setup.raid_extreme
        endpoint = bar.close
        distance = abs(origin - endpoint)
        if setup.direction < 0:
            entry = endpoint + distance * self.params.rejection_limit_fraction
            stop = origin + self._execution_buffer(entry, atr)
            setup.zone_low = endpoint + distance * 0.382
            setup.zone_high = entry
        else:
            entry = endpoint - distance * self.params.rejection_limit_fraction
            stop = origin - self._execution_buffer(entry, atr)
            setup.zone_low = entry
            setup.zone_high = endpoint - distance * 0.382
        setup.stop_price = stop
        transitions.append(
            self._transition(
                setup,
                bar,
                event_type="DISPLACEMENT_CONFIRMED",
                next_state="DISPLACED",
                reason_code="APPROACH_STRUCTURE_BROKEN",
                reference_price=bar.close,
                details={
                    "approach_level": setup.approach_level,
                    "body_atr": body / atr if atr else None,
                    "close_location": close_location,
                    "zone_low": setup.zone_low,
                    "zone_high": setup.zone_high,
                    "resting_entry": entry,
                    "stop_price": stop,
                },
            ),
        )

        target, target_name, net_rr = self._select_rejection_target(setup, entry, stop)
        if target is None:
            transitions.append(
                self._transition(
                    setup,
                    bar,
                    event_type="SCENARIO_INVALIDATED",
                    next_state="INVALIDATED",
                    reason_code="NO_COST_ADJUSTED_STRUCTURAL_TARGET",
                    reference_price=entry,
                    details={"stop": stop, "minimum_net_rr": self.params.min_net_rr},
                ),
            )
            self.active = None
            return transitions, None

        plan = TradePlan(
            scenario_id=setup.scenario_id,
            scenario=setup.scenario,
            direction=setup.direction,
            observed_ns=bar.ts_ns,
            entry_estimate=entry,
            stop_price=stop,
            target_price=target,
            boundary=setup.boundary,
            atr=atr,
            structural_target=target_name,
            entry_order_type="LIMIT",
            entry_expiry_bars=self.params.retrace_expiry_bars,
            invalidation_price=stop,
            details={
                "zone_low": setup.zone_low,
                "zone_high": setup.zone_high,
                "raid_extreme": setup.raid_extreme,
                "cost_adjusted_net_rr": net_rr,
            },
        )
        transitions.append(
            self._transition(
                setup,
                bar,
                event_type="ENTRY_READY",
                next_state="ENTRY_READY",
                reason_code="RESTING_DISPLACEMENT_RETRACE_ARMED",
                reference_price=entry,
                details={
                    "target": target,
                    "target_name": target_name,
                    "stop": stop,
                    "cost_adjusted_net_rr": net_rr,
                    "expiry_bars": self.params.retrace_expiry_bars,
                },
            ),
        )
        self.active = None
        self.consumed_block_id = self.current_range.block_id if self.current_range else None
        return transitions, plan

    def _select_acceptance_target(
        self,
        setup: Setup,
        entry: float,
        stop: float,
    ) -> tuple[float | None, str, float]:
        if setup.direction > 0:
            historical = sorted({rng.high for rng in self.past_ranges if rng.high > entry})
        else:
            historical = sorted(
                {rng.low for rng in self.past_ranges if rng.low < entry},
                reverse=True,
            )
        candidates: list[tuple[float, str]] = [
            (value, "OLDER_BLOCK_LIQUIDITY") for value in historical
        ]
        pool = self.previous_range
        if pool is not None:
            candidates.append(
                (
                    setup.boundary
                    + setup.direction
                    * pool.width
                    * self.params.acceptance_target_extension,
                    "RANGE_EXPANSION_PROJECTION",
                ),
            )
        for target, name in candidates:
            net_rr = self._net_rr(
                direction=setup.direction,
                entry=entry,
                stop=stop,
                target=target,
            )
            if net_rr >= self.params.min_net_rr:
                return target, name, net_rr
        return None, "NONE", float("-inf")

    def _process_acceptance(
        self,
        bar: BarView,
        atr: float,
    ) -> tuple[list[Transition], TradePlan | None]:
        setup = self.active
        assert setup is not None and setup.scenario == "ACCEPTANCE"
        transitions: list[Transition] = []
        age = self.bar_index - setup.created_index

        if setup.direction > 0:
            setup.breakout_extreme = max(float(setup.breakout_extreme), bar.high)
            outside = bar.close > setup.boundary
            reentered = bar.close < setup.boundary
        else:
            setup.breakout_extreme = min(float(setup.breakout_extreme), bar.low)
            outside = bar.close < setup.boundary
            reentered = bar.close > setup.boundary
        if reentered:
            transitions.append(
                self._transition(
                    setup,
                    bar,
                    event_type="SCENARIO_INVALIDATED",
                    next_state="INVALIDATED",
                    reason_code="BOUNDARY_REENTERED_BEFORE_ACCEPTANCE",
                    reference_price=bar.close,
                ),
            )
            self.active = None
            return transitions, None

        if outside:
            setup.consecutive_closes += 1
        if setup.consecutive_closes < 2:
            if age > 3:
                transitions.append(
                    self._transition(
                        setup,
                        bar,
                        event_type="SCENARIO_EXPIRED",
                        next_state="EXPIRED",
                        reason_code="NO_SECOND_OUTSIDE_CLOSE",
                    ),
                )
                self.active = None
            return transitions, None

        setup.confirmation_index = self.bar_index
        transitions.append(
            self._transition(
                setup,
                bar,
                event_type="ACCEPTANCE_CONFIRMED",
                next_state="ACCEPTED",
                reason_code="TWO_DISTINCT_CLOSES_OUTSIDE",
                reference_price=bar.close,
                details={"breakout_extreme": setup.breakout_extreme},
            ),
        )
        entry = setup.boundary
        buffer = self._execution_buffer(entry, atr)
        stop = entry - setup.direction * buffer
        target, target_name, net_rr = self._select_acceptance_target(setup, entry, stop)
        if target is None:
            transitions.append(
                self._transition(
                    setup,
                    bar,
                    event_type="SCENARIO_INVALIDATED",
                    next_state="INVALIDATED",
                    reason_code="NO_COST_ADJUSTED_STRUCTURAL_TARGET",
                    reference_price=entry,
                    details={"stop": stop, "minimum_net_rr": self.params.min_net_rr},
                ),
            )
            self.active = None
            return transitions, None

        plan = TradePlan(
            scenario_id=setup.scenario_id,
            scenario=setup.scenario,
            direction=setup.direction,
            observed_ns=bar.ts_ns,
            entry_estimate=entry,
            stop_price=stop,
            target_price=target,
            boundary=setup.boundary,
            atr=atr,
            structural_target=target_name,
            entry_order_type="LIMIT",
            entry_expiry_bars=self.params.acceptance_retest_bars,
            invalidation_price=stop,
            details={
                "breakout_extreme": setup.breakout_extreme,
                "cost_adjusted_net_rr": net_rr,
            },
        )
        transitions.append(
            self._transition(
                setup,
                bar,
                event_type="ENTRY_READY",
                next_state="ENTRY_READY",
                reason_code="RESTING_ACCEPTED_BOUNDARY_RETEST_ARMED",
                reference_price=entry,
                details={
                    "target": target,
                    "target_name": target_name,
                    "stop": stop,
                    "cost_adjusted_net_rr": net_rr,
                    "expiry_bars": self.params.acceptance_retest_bars,
                },
            ),
        )
        self.active = None
        self.consumed_block_id = self.current_range.block_id if self.current_range else None
        return transitions, plan

    def on_bar(self, bar: BarView) -> tuple[list[Transition], TradePlan | None]:
        self.bar_index += 1
        atr_before = self.atr
        block_ns = self.params.block_minutes * NS_PER_MINUTE
        # The bar is observable at ``ts_ns`` but belongs to the minute which
        # opened one minute earlier. This preserves exact UTC block membership
        # without pretending the bar was known at its open.
        bar_open_ns = bar.ts_ns - NS_PER_MINUTE
        block_id = bar_open_ns // block_ns
        transitions: list[Transition] = []
        if self.current_range is None or block_id != self.current_range.block_id:
            if self.active is not None:
                transitions.append(
                    self._transition(
                        self.active,
                        bar,
                        event_type="SCENARIO_EXPIRED",
                        next_state="EXPIRED",
                        reason_code="AUCTION_BLOCK_ROLLOVER",
                    ),
                )
            self._start_block(bar, block_id)
            is_new_block = True
        else:
            is_new_block = False

        plan: TradePlan | None = None
        if (
            atr_before is not None
            and self.previous_range is not None
            and self.consumed_block_id != block_id
        ):
            if self.active is None:
                transitions.extend(self._detect_setup(bar, atr_before))
            if self.active is not None:
                if self.active.scenario == "REJECTION":
                    more, plan = self._process_rejection(bar, atr_before)
                else:
                    more, plan = self._process_acceptance(bar, atr_before)
                transitions.extend(more)

        if not is_new_block and self.current_range is not None:
            self.current_range.update(bar)

        if self.previous_close is not None:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self.previous_close),
                abs(bar.low - self.previous_close),
            )
            self.true_ranges.append(tr)
        self.previous_close = bar.close
        self.history.append(bar)
        return transitions, plan
