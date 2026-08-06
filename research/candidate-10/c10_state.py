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

        # A bar that raids both sides of the prior block is an unresolved auction,
        # not a directional signal.
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
            if bar.close >= setup.boundary + accept_buffer:
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
        else:
            setup.raid_extreme = min(setup.raid_extreme, bar.low)
            if bar.close <= setup.boundary - accept_buffer:
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

        if setup.state == "RAIDED":
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

            if confirmed:
                setup.confirmation_index = self.bar_index
                origin = setup.raid_extreme
                endpoint = bar.close
                lower = min(origin, endpoint)
                upper = max(origin, endpoint)
                distance = upper - lower
                if setup.direction < 0:
                    setup.zone_low = endpoint + distance * 0.382
                    setup.zone_high = endpoint + distance * 0.618
                    setup.stop_price = origin + atr * self.params.stop_buffer_atr
                else:
                    setup.zone_low = endpoint - distance * 0.618
                    setup.zone_high = endpoint - distance * 0.382
                    setup.stop_price = origin - atr * self.params.stop_buffer_atr
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
                            "stop_price": setup.stop_price,
                        },
                    ),
                )
            return transitions, None

        if setup.state == "DISPLACED":
            assert setup.confirmation_index is not None
            assert setup.zone_low is not None and setup.zone_high is not None
            assert setup.stop_price is not None
            since_confirmation = self.bar_index - setup.confirmation_index
            if since_confirmation > self.params.retrace_expiry_bars:
                transitions.append(
                    self._transition(
                        setup,
                        bar,
                        event_type="SCENARIO_EXPIRED",
                        next_state="EXPIRED",
                        reason_code="NO_FIRST_RETRACE",
                    ),
                )
                self.active = None
                return transitions, None

            if setup.direction < 0 and bar.high >= setup.stop_price:
                transitions.append(
                    self._transition(
                        setup,
                        bar,
                        event_type="SCENARIO_INVALIDATED",
                        next_state="INVALIDATED",
                        reason_code="RAID_EXTREME_BROKEN_BEFORE_ENTRY",
                        reference_price=bar.high,
                    ),
                )
                self.active = None
                return transitions, None
            if setup.direction > 0 and bar.low <= setup.stop_price:
                transitions.append(
                    self._transition(
                        setup,
                        bar,
                        event_type="SCENARIO_INVALIDATED",
                        next_state="INVALIDATED",
                        reason_code="RAID_EXTREME_BROKEN_BEFORE_ENTRY",
                        reference_price=bar.low,
                    ),
                )
                self.active = None
                return transitions, None

            touched = bar.high >= setup.zone_low and bar.low <= setup.zone_high
            directional_rejection = bar.close < bar.open if setup.direction < 0 else bar.close > bar.open
            if since_confirmation >= 1 and touched and directional_rejection:
                target, target_name = self._select_rejection_target(setup, bar.close)
                if target is None:
                    transitions.append(
                        self._transition(
                            setup,
                            bar,
                            event_type="SCENARIO_INVALIDATED",
                            next_state="INVALIDATED",
                            reason_code="NO_STRUCTURAL_TARGET_WITH_ROOM",
                            reference_price=bar.close,
                        ),
                    )
                    self.active = None
                    return transitions, None
                plan = TradePlan(
                    scenario_id=setup.scenario_id,
                    scenario=setup.scenario,
                    direction=setup.direction,
                    observed_ns=bar.ts_ns,
                    entry_estimate=bar.close,
                    stop_price=setup.stop_price,
                    target_price=target,
                    boundary=setup.boundary,
                    atr=atr,
                    structural_target=target_name,
                    details={
                        "zone_low": setup.zone_low,
                        "zone_high": setup.zone_high,
                        "raid_extreme": setup.raid_extreme,
                    },
                )
                transitions.append(
                    self._transition(
                        setup,
                        bar,
                        event_type="ENTRY_READY",
                        next_state="ENTRY_READY",
                        reason_code="FIRST_CORRIDOR_RETEST_REJECTED",
                        reference_price=bar.close,
                        details={
                            "target": target,
                            "target_name": target_name,
                            "stop": setup.stop_price,
                        },
                    ),
                )
                self.active = None
                self.consumed_block_id = self.current_range.block_id if self.current_range else None
                return transitions, plan

        return transitions, None

    def _select_rejection_target(self, setup: Setup, entry: float) -> tuple[float | None, str]:
        pool = self.previous_range
        if pool is None:
            return None, "NONE"
        candidates: list[tuple[float, str]]
        if setup.direction < 0:
            candidates = [(pool.midpoint, "PRIOR_BLOCK_MIDPOINT"), (pool.low, "OPPOSITE_BLOCK_LOW")]
        else:
            candidates = [(pool.midpoint, "PRIOR_BLOCK_MIDPOINT"), (pool.high, "OPPOSITE_BLOCK_HIGH")]
        risk = abs(entry - float(setup.stop_price))
        if risk <= self.tick_size:
            return None, "NONE"
        for target, name in candidates:
            reward = (target - entry) * setup.direction
            if reward > 0 and reward / risk >= self.params.min_net_rr:
                return target, name
        return None, "NONE"

    def _process_acceptance(
        self,
        bar: BarView,
        atr: float,
    ) -> tuple[list[Transition], TradePlan | None]:
        setup = self.active
        assert setup is not None and setup.scenario == "ACCEPTANCE"
        transitions: list[Transition] = []
        age = self.bar_index - setup.created_index
        tolerance = max(self.tick_size * 2.0, atr * self.params.retest_tolerance_atr)

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
                    reason_code="BOUNDARY_REENTERED",
                    reference_price=bar.close,
                ),
            )
            self.active = None
            return transitions, None

        if setup.state == "ACCEPTANCE_PROBE":
            if outside:
                setup.consecutive_closes += 1
            if setup.consecutive_closes >= 2:
                setup.confirmation_index = self.bar_index
                transitions.append(
                    self._transition(
                        setup,
                        bar,
                        event_type="ACCEPTANCE_CONFIRMED",
                        next_state="ACCEPTED",
                        reason_code="TWO_CLOSES_OUTSIDE_AND_EXTENSION",
                        reference_price=bar.close,
                        details={"breakout_extreme": setup.breakout_extreme},
                    ),
                )
            elif age > 3:
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

        if setup.state == "ACCEPTED":
            assert setup.confirmation_index is not None
            since_confirmation = self.bar_index - setup.confirmation_index
            if since_confirmation > self.params.acceptance_retest_bars:
                transitions.append(
                    self._transition(
                        setup,
                        bar,
                        event_type="SCENARIO_EXPIRED",
                        next_state="EXPIRED",
                        reason_code="NO_BOUNDARY_RETEST",
                    ),
                )
                self.active = None
                return transitions, None

            if setup.direction > 0:
                touched = bar.low <= setup.boundary + tolerance
                held = bar.close > setup.boundary and bar.close > bar.open
                stop = min(bar.low, setup.boundary - atr * self.params.stop_buffer_atr)
            else:
                touched = bar.high >= setup.boundary - tolerance
                held = bar.close < setup.boundary and bar.close < bar.open
                stop = max(bar.high, setup.boundary + atr * self.params.stop_buffer_atr)

            if since_confirmation >= 1 and touched and held:
                target, target_name = self._select_acceptance_target(setup, bar.close)
                risk = abs(bar.close - stop)
                reward = (target - bar.close) * setup.direction if target is not None else -1.0
                if target is None or risk <= self.tick_size or reward / risk < self.params.min_net_rr:
                    transitions.append(
                        self._transition(
                            setup,
                            bar,
                            event_type="SCENARIO_INVALIDATED",
                            next_state="INVALIDATED",
                            reason_code="NO_STRUCTURAL_TARGET_WITH_ROOM",
                            reference_price=bar.close,
                        ),
                    )
                    self.active = None
                    return transitions, None
                plan = TradePlan(
                    scenario_id=setup.scenario_id,
                    scenario=setup.scenario,
                    direction=setup.direction,
                    observed_ns=bar.ts_ns,
                    entry_estimate=bar.close,
                    stop_price=stop,
                    target_price=target,
                    boundary=setup.boundary,
                    atr=atr,
                    structural_target=target_name,
                    details={"breakout_extreme": setup.breakout_extreme, "retest_tolerance": tolerance},
                )
                transitions.append(
                    self._transition(
                        setup,
                        bar,
                        event_type="ENTRY_READY",
                        next_state="ENTRY_READY",
                        reason_code="ACCEPTED_BOUNDARY_RETEST_HELD",
                        reference_price=bar.close,
                        details={"target": target, "target_name": target_name, "stop": stop},
                    ),
                )
                self.active = None
                self.consumed_block_id = self.current_range.block_id if self.current_range else None
                return transitions, plan

        return transitions, None

    def _select_acceptance_target(self, setup: Setup, entry: float) -> tuple[float | None, str]:
        historical: list[float] = []
        if setup.direction > 0:
            historical = sorted({rng.high for rng in self.past_ranges if rng.high > entry})
        else:
            historical = sorted({rng.low for rng in self.past_ranges if rng.low < entry}, reverse=True)
        for value in historical:
            if (value - entry) * setup.direction > 0:
                return value, "OLDER_BLOCK_LIQUIDITY"

        pool = self.previous_range
        if pool is None:
            return None, "NONE"
        projection = setup.boundary + setup.direction * pool.width * self.params.acceptance_target_extension
        if (projection - entry) * setup.direction > 0:
            return projection, "RANGE_EXPANSION_PROJECTION"
        return None, "NONE"

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
