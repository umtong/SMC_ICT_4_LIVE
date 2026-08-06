"""Causal structural-liquidity detector and sweep-rejection state machine."""

from __future__ import annotations

from collections import Counter, deque
from typing import Any

from c10_model import BarView
from c10_model import LiquidityPool
from c10_model import MachineParams
from c10_model import NS_PER_MINUTE
from c10_model import Setup
from c10_model import StructuralBar
from c10_model import TradePlan
from c10_model import Transition


class AuctionStateMachine:
    """Build confirmed liquidity pools and trade their causal sweep rejection."""

    def __init__(self, params: MachineParams, *, tick_size: float, instrument_id: str):
        self.params = params
        self.tick_size = tick_size
        self.instrument_id = instrument_id
        self.bar_index = -1
        self.history: deque[BarView] = deque(maxlen=2_000)
        self.true_ranges: deque[float] = deque(maxlen=params.atr_lookback)
        self.previous_close: float | None = None

        self.current_structural: StructuralBar | None = None
        self.structural_history: deque[StructuralBar] = deque(maxlen=2_000)
        self.structural_true_ranges: deque[float] = deque(
            maxlen=params.structural_atr_lookback,
        )
        self.previous_structural_close: float | None = None

        self.pools: list[LiquidityPool] = []
        self.pool_sequence = 0
        self.active: Setup | None = None

    @staticmethod
    def _robust_average(values: deque[float], minimum: int) -> float | None:
        if len(values) < minimum:
            return None
        ordered = sorted(values)
        trim = max(1, len(ordered) // 10)
        core = ordered[trim:-trim] if len(ordered) > 2 * trim else ordered
        return sum(core) / len(core)

    @property
    def atr(self) -> float | None:
        return self._robust_average(
            self.true_ranges,
            max(20, self.params.atr_lookback // 2),
        )

    @property
    def structural_atr(self) -> float | None:
        return self._robust_average(
            self.structural_true_ranges,
            max(8, self.params.structural_atr_lookback // 2),
        )

    def reset_active(self) -> None:
        self.active = None

    def pool_diagnostics(self) -> dict[str, Any]:
        status_counts = Counter(pool.status for pool in self.pools)
        active = [pool for pool in self.pools if pool.status == "ACTIVE"]
        return {
            "total_pools": len(self.pools),
            "status_counts": dict(status_counts),
            "active_high_pools": sum(pool.side == "HIGH" for pool in active),
            "active_low_pools": sum(pool.side == "LOW" for pool in active),
            "clustered_pools": sum(pool.source_count >= 2 for pool in self.pools),
            "prominent_single_pools": sum(
                pool.source_count == 1
                and pool.max_prominence_atr
                >= self.params.single_swing_prominence_atr
                for pool in self.pools
            ),
        }

    def _transition(
        self,
        *,
        scenario_id: str,
        event_time_ns: int,
        observed_time_ns: int,
        event_type: str,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> Transition:
        return Transition(
            scenario_id=scenario_id,
            event_type=event_type,
            event_time_ns=event_time_ns,
            observed_time_ns=observed_time_ns,
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason_code,
            reference_price=reference_price,
            details=dict(details or {}),
        )

    def _setup_transition(
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
        return self._transition(
            scenario_id=setup.scenario_id,
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            event_type=event_type,
            previous_state=previous,
            next_state=next_state,
            reason_code=reason_code,
            reference_price=reference_price,
            details=details,
        )

    def _pool_is_active(self, pool: LiquidityPool) -> bool:
        return pool.status == "ACTIVE"

    def _eligible_after_source_update(self, pool: LiquidityPool) -> bool:
        prominent = (
            pool.max_prominence_atr
            >= self.params.single_swing_prominence_atr
        )
        clustered = (
            self.params.enable_pool_clustering
            and pool.source_count >= self.params.cluster_min_sources
        )
        return prominent or clustered

    def _new_structural_bar(self, bar: BarView, bucket_id: int) -> StructuralBar:
        interval_ns = self.params.structure_minutes * NS_PER_MINUTE
        return StructuralBar(
            bucket_id=bucket_id,
            start_ns=bucket_id * interval_ns,
            end_ns=bar.ts_ns,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        )

    def _update_structure(self, bar: BarView) -> list[Transition]:
        events: list[Transition] = []
        interval_ns = self.params.structure_minutes * NS_PER_MINUTE
        bar_open_ns = bar.ts_ns - NS_PER_MINUTE
        bucket_id = bar_open_ns // interval_ns

        if self.current_structural is None:
            self.current_structural = self._new_structural_bar(bar, bucket_id)
        elif self.current_structural.bucket_id != bucket_id:
            # Defensive path for an incomplete prior structural bucket. The data
            # quality gate normally prevents this branch from losing minutes.
            events.extend(
                self._finalize_structural(
                    self.current_structural,
                    observed_time_ns=bar.ts_ns,
                ),
            )
            self.current_structural = self._new_structural_bar(bar, bucket_id)
        else:
            self.current_structural.update(bar)

        if bar.ts_ns % interval_ns == 0 and self.current_structural is not None:
            events.extend(
                self._finalize_structural(
                    self.current_structural,
                    observed_time_ns=bar.ts_ns,
                ),
            )
            self.current_structural = None
        return events

    def _finalize_structural(
        self,
        structural: StructuralBar,
        *,
        observed_time_ns: int,
    ) -> list[Transition]:
        minimum_minutes = max(1, int(self.params.structure_minutes * 0.90))
        if structural.minute_count < minimum_minutes:
            return []
        if self.previous_structural_close is not None:
            true_range = max(
                structural.high - structural.low,
                abs(structural.high - self.previous_structural_close),
                abs(structural.low - self.previous_structural_close),
            )
            self.structural_true_ranges.append(true_range)
        self.previous_structural_close = structural.close
        self.structural_history.append(structural)
        return self._confirm_new_pivots(observed_time_ns)

    def _confirm_new_pivots(self, observed_time_ns: int) -> list[Transition]:
        left_count = self.params.pivot_left
        right_count = self.params.pivot_right
        required = left_count + right_count + 1
        if len(self.structural_history) < required:
            return []
        atr = self.structural_atr
        if atr is None or atr <= 0:
            return []

        history = list(self.structural_history)
        pivot_index = len(history) - right_count - 1
        pivot = history[pivot_index]
        left = history[pivot_index - left_count : pivot_index]
        right = history[pivot_index + 1 : pivot_index + 1 + right_count]
        if len(left) != left_count or len(right) != right_count:
            return []

        events: list[Transition] = []
        is_high = all(pivot.high > item.high for item in (*left, *right))
        is_low = all(pivot.low < item.low for item in (*left, *right))
        if is_high:
            left_valley = min(item.low for item in left)
            right_valley = min(item.low for item in right)
            prominence = pivot.high - max(left_valley, right_valley)
            events.append(
                self._upsert_pool(
                    side="HIGH",
                    price=pivot.high,
                    prominence_atr=prominence / atr,
                    event_time_ns=pivot.end_ns,
                    observed_time_ns=observed_time_ns,
                    structural_atr=atr,
                ),
            )
        if is_low:
            left_peak = max(item.high for item in left)
            right_peak = max(item.high for item in right)
            prominence = min(left_peak, right_peak) - pivot.low
            events.append(
                self._upsert_pool(
                    side="LOW",
                    price=pivot.low,
                    prominence_atr=prominence / atr,
                    event_time_ns=pivot.end_ns,
                    observed_time_ns=observed_time_ns,
                    structural_atr=atr,
                ),
            )
        return events

    def _upsert_pool(
        self,
        *,
        side: str,
        price: float,
        prominence_atr: float,
        event_time_ns: int,
        observed_time_ns: int,
        structural_atr: float,
    ) -> Transition:
        merge_tolerance = max(
            self.tick_size * 4.0,
            structural_atr * self.params.pool_merge_atr,
        )
        zone_half = max(
            self.tick_size * 2.0,
            structural_atr * self.params.pool_zone_atr,
        )
        candidates = [
            pool
            for pool in self.pools
            if pool.side == side
            and pool.status in {"LATENT", "ACTIVE"}
            and abs(pool.center - price) <= merge_tolerance
        ]
        if candidates:
            pool = min(candidates, key=lambda item: abs(item.center - price))
            previous_state = pool.status
            old_count = pool.source_count
            pool.center = (pool.center * old_count + price) / (old_count + 1)
            pool.lower = min(pool.lower, price - zone_half)
            pool.upper = max(pool.upper, price + zone_half)
            pool.source_count += 1
            pool.max_prominence_atr = max(
                pool.max_prominence_atr,
                prominence_atr,
            )
            pool.last_source_time_ns = event_time_ns
            pool.status = (
                "ACTIVE" if self._eligible_after_source_update(pool) else "LATENT"
            )
            return self._transition(
                scenario_id=pool.pool_id,
                event_time_ns=event_time_ns,
                observed_time_ns=observed_time_ns,
                event_type="POOL_CLUSTERED",
                previous_state=previous_state,
                next_state=pool.status,
                reason_code="CONFIRMED_PIVOT_MERGED",
                reference_price=pool.center,
                details={
                    "side": side,
                    "source_count": pool.source_count,
                    "new_source_price": price,
                    "lower": pool.lower,
                    "upper": pool.upper,
                    "max_prominence_atr": pool.max_prominence_atr,
                    "cluster_activation_enabled": self.params.enable_pool_clustering,
                },
            )

        self.pool_sequence += 1
        pool_id = f"{self.instrument_id}:POOL:{side}:{self.pool_sequence:05d}"
        pool = LiquidityPool(
            pool_id=pool_id,
            side=side,
            center=price,
            lower=price - zone_half,
            upper=price + zone_half,
            event_time_ns=event_time_ns,
            observed_time_ns=observed_time_ns,
            last_source_time_ns=event_time_ns,
            source_count=1,
            max_prominence_atr=prominence_atr,
            status="LATENT",
        )
        pool.status = "ACTIVE" if self._eligible_after_source_update(pool) else "LATENT"
        self.pools.append(pool)
        return self._transition(
            scenario_id=pool.pool_id,
            event_time_ns=event_time_ns,
            observed_time_ns=observed_time_ns,
            event_type="POOL_CONFIRMED",
            previous_state="UNSEEN",
            next_state=pool.status,
            reason_code="RIGHT_CONFIRMED_STRUCTURAL_PIVOT",
            reference_price=price,
            details={
                "side": side,
                "source_count": 1,
                "lower": pool.lower,
                "upper": pool.upper,
                "prominence_atr": prominence_atr,
                "single_prominence_required": self.params.single_swing_prominence_atr,
            },
        )

    def _expire_and_accept_pools(
        self,
        bar: BarView,
        atr: float,
    ) -> list[Transition]:
        events: list[Transition] = []
        max_age_ns = self.params.pool_max_age_minutes * NS_PER_MINUTE
        accept_buffer = max(self.tick_size * 2.0, atr * self.params.acceptance_atr)
        for pool in self.pools:
            if pool.status not in {"LATENT", "ACTIVE"}:
                continue
            if bar.ts_ns - pool.observed_time_ns > max_age_ns:
                previous = pool.status
                pool.status = "EXPIRED"
                pool.consumed_time_ns = bar.ts_ns
                pool.consumed_reason = "MAX_AGE"
                events.append(
                    self._transition(
                        scenario_id=pool.pool_id,
                        event_time_ns=bar.ts_ns,
                        observed_time_ns=bar.ts_ns,
                        event_type="POOL_EXPIRED",
                        previous_state=previous,
                        next_state="EXPIRED",
                        reason_code="MAX_POOL_AGE",
                        reference_price=pool.center,
                    ),
                )
                continue
            if pool.status != "ACTIVE" or pool.observed_time_ns >= bar.ts_ns:
                continue
            if pool.side == "HIGH":
                outside = bar.close > pool.upper + accept_buffer
            else:
                outside = bar.close < pool.lower - accept_buffer
            pool.outside_closes = pool.outside_closes + 1 if outside else 0
            if pool.outside_closes >= 2:
                pool.status = "CONSUMED"
                pool.consumed_time_ns = bar.ts_ns
                pool.consumed_reason = "TWO_CLOSE_ACCEPTANCE"
                events.append(
                    self._transition(
                        scenario_id=pool.pool_id,
                        event_time_ns=bar.ts_ns,
                        observed_time_ns=bar.ts_ns,
                        event_type="POOL_CONSUMED",
                        previous_state="ACTIVE",
                        next_state="CONSUMED",
                        reason_code=(
                            "TWO_CLOSES_ABOVE_POOL"
                            if pool.side == "HIGH"
                            else "TWO_CLOSES_BELOW_POOL"
                        ),
                        reference_price=bar.close,
                    ),
                )
        return events

    def _consume_pool(
        self,
        pool: LiquidityPool,
        bar: BarView,
        *,
        reason: str,
    ) -> Transition:
        previous = pool.status
        pool.status = "CONSUMED"
        pool.consumed_time_ns = bar.ts_ns
        pool.consumed_reason = reason
        pool.touch_count += 1
        return self._transition(
            scenario_id=pool.pool_id,
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            event_type="POOL_CONSUMED",
            previous_state=previous,
            next_state="CONSUMED",
            reason_code=reason,
            reference_price=pool.center,
            details={
                "side": pool.side,
                "source_count": pool.source_count,
                "max_prominence_atr": pool.max_prominence_atr,
                "lower": pool.lower,
                "upper": pool.upper,
            },
        )

    def _detect_sweep(
        self,
        bar: BarView,
        atr: float,
    ) -> list[Transition]:
        if self.previous_close is None or len(self.history) < self.params.approach_lookback:
            return []
        raid_buffer = max(self.tick_size * 2.0, atr * self.params.raid_atr)
        active = [
            pool
            for pool in self.pools
            if self._pool_is_active(pool) and pool.observed_time_ns < bar.ts_ns
        ]
        high_crossed = [
            pool
            for pool in active
            if pool.side == "HIGH"
            and self.previous_close < pool.lower
            and bar.high >= pool.upper + raid_buffer
            and bar.close < pool.lower
        ]
        low_crossed = [
            pool
            for pool in active
            if pool.side == "LOW"
            and self.previous_close > pool.upper
            and bar.low <= pool.lower - raid_buffer
            and bar.close > pool.upper
        ]
        if high_crossed and low_crossed:
            return []
        if not high_crossed and not low_crossed:
            return []

        events: list[Transition] = []
        recent = list(self.history)[-self.params.approach_lookback :]
        if high_crossed:
            source = max(high_crossed, key=lambda pool: pool.center)
            direction = -1
            raid_extreme = bar.high
            approach_level = min(item.low for item in recent)
            reason = "CONFIRMED_HIGH_POOL_RAID_REENTERED"
            crossed = high_crossed
        else:
            source = min(low_crossed, key=lambda pool: pool.center)
            direction = 1
            raid_extreme = bar.low
            approach_level = max(item.high for item in recent)
            reason = "CONFIRMED_LOW_POOL_RAID_REENTERED"
            crossed = low_crossed

        for pool in crossed:
            events.append(self._consume_pool(pool, bar, reason="RAID_REENTERED"))

        side = "LONG" if direction > 0 else "SHORT"
        scenario_id = (
            f"{self.instrument_id}:STRUCTURAL_SWEEP_REJECTION:"
            f"{source.pool_id.rsplit(':', 1)[-1]}:{side}:{bar.ts_ns}"
        )
        setup = Setup(
            scenario_id=scenario_id,
            scenario="STRUCTURAL_SWEEP_REJECTION",
            direction=direction,
            source_pool_id=source.pool_id,
            source_pool_side=source.side,
            source_lower=source.lower,
            source_upper=source.upper,
            state="POOL_ACTIVE",
            created_index=self.bar_index,
            created_ns=bar.ts_ns,
            atr=atr,
            raid_extreme=raid_extreme,
            approach_level=approach_level,
        )
        self.active = setup
        events.append(
            self._setup_transition(
                setup,
                bar,
                event_type="LIQUIDITY_EVENT",
                next_state="RAIDED",
                reason_code=reason,
                reference_price=source.center,
                details={
                    "source_pool_id": source.pool_id,
                    "source_side": source.side,
                    "source_count": source.source_count,
                    "source_prominence_atr": source.max_prominence_atr,
                    "source_lower": source.lower,
                    "source_upper": source.upper,
                    "raid_extreme": raid_extreme,
                    "approach_level": approach_level,
                    "crossed_pool_ids": [pool.pool_id for pool in crossed],
                },
            ),
        )
        return events

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

    def _select_opposing_target(
        self,
        setup: Setup,
        *,
        entry: float,
        stop: float,
    ) -> tuple[LiquidityPool | None, float | None, float]:
        candidates = [
            pool
            for pool in self.pools
            if pool.status == "ACTIVE"
            and pool.observed_time_ns < setup.created_ns
            and (
                (setup.direction > 0 and pool.side == "HIGH" and pool.lower > entry)
                or (setup.direction < 0 and pool.side == "LOW" and pool.upper < entry)
            )
        ]
        if setup.direction > 0:
            candidates.sort(key=lambda pool: pool.lower)
        else:
            candidates.sort(key=lambda pool: pool.upper, reverse=True)
        if not candidates:
            return None, None, float("-inf")
        nearest = candidates[0]
        target = nearest.lower if setup.direction > 0 else nearest.upper
        net_rr = self._net_rr(
            direction=setup.direction,
            entry=entry,
            stop=stop,
            target=target,
        )
        if net_rr < self.params.min_net_rr:
            return None, target, net_rr
        return nearest, target, net_rr

    def _process_rejection(
        self,
        bar: BarView,
        atr: float,
    ) -> tuple[list[Transition], TradePlan | None]:
        setup = self.active
        assert setup is not None
        events: list[Transition] = []
        age = self.bar_index - setup.created_index
        accept_buffer = max(self.tick_size * 2.0, atr * self.params.acceptance_atr)

        if setup.direction < 0:
            setup.raid_extreme = max(setup.raid_extreme, bar.high)
            accepted = bar.close > setup.source_upper + accept_buffer
        else:
            setup.raid_extreme = min(setup.raid_extreme, bar.low)
            accepted = bar.close < setup.source_lower - accept_buffer
        if accepted:
            events.append(
                self._setup_transition(
                    setup,
                    bar,
                    event_type="SCENARIO_INVALIDATED",
                    next_state="INVALIDATED",
                    reason_code="SOURCE_POOL_ACCEPTED_AFTER_RAID",
                    reference_price=bar.close,
                ),
            )
            self.active = None
            return events, None

        if age > self.params.rejection_confirm_bars:
            events.append(
                self._setup_transition(
                    setup,
                    bar,
                    event_type="SCENARIO_EXPIRED",
                    next_state="EXPIRED",
                    reason_code="NO_DISPLACEMENT_AFTER_STRUCTURAL_RAID",
                ),
            )
            self.active = None
            return events, None

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
            return events, None

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
        events.append(
            self._setup_transition(
                setup,
                bar,
                event_type="DISPLACEMENT_CONFIRMED",
                next_state="DISPLACED",
                reason_code="APPROACH_STRUCTURE_BROKEN_AFTER_POOL_RAID",
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

        target_pool, target, net_rr = self._select_opposing_target(
            setup,
            entry=entry,
            stop=stop,
        )
        if target_pool is None or target is None:
            events.append(
                self._setup_transition(
                    setup,
                    bar,
                    event_type="SCENARIO_INVALIDATED",
                    next_state="INVALIDATED",
                    reason_code=(
                        "NO_OPPOSING_CONFIRMED_POOL"
                        if target is None
                        else "NEAREST_OPPOSING_POOL_FAILS_NET_RR"
                    ),
                    reference_price=entry,
                    details={
                        "nearest_target_price": target,
                        "cost_adjusted_net_rr": net_rr,
                        "minimum_net_rr": self.params.min_net_rr,
                    },
                ),
            )
            self.active = None
            return events, None

        plan = TradePlan(
            scenario_id=setup.scenario_id,
            scenario=setup.scenario,
            direction=setup.direction,
            observed_ns=bar.ts_ns,
            entry_estimate=entry,
            stop_price=stop,
            target_price=target,
            boundary=(setup.source_upper + setup.source_lower) / 2.0,
            atr=atr,
            structural_target=(
                "CONFIRMED_HIGH_LIQUIDITY_POOL"
                if target_pool.side == "HIGH"
                else "CONFIRMED_LOW_LIQUIDITY_POOL"
            ),
            entry_order_type="LIMIT",
            entry_expiry_bars=self.params.retrace_expiry_bars,
            invalidation_price=stop,
            details={
                "source_pool_id": setup.source_pool_id,
                "source_pool_side": setup.source_pool_side,
                "source_lower": setup.source_lower,
                "source_upper": setup.source_upper,
                "target_pool_id": target_pool.pool_id,
                "target_pool_side": target_pool.side,
                "target_pool_sources": target_pool.source_count,
                "target_pool_prominence_atr": target_pool.max_prominence_atr,
                "zone_low": setup.zone_low,
                "zone_high": setup.zone_high,
                "raid_extreme": setup.raid_extreme,
                "cost_adjusted_net_rr": net_rr,
            },
        )
        events.append(
            self._setup_transition(
                setup,
                bar,
                event_type="ENTRY_READY",
                next_state="ENTRY_READY",
                reason_code="STRUCTURAL_POOL_TO_POOL_RETRACE_ARMED",
                reference_price=entry,
                details={
                    "source_pool_id": setup.source_pool_id,
                    "target_pool_id": target_pool.pool_id,
                    "target": target,
                    "stop": stop,
                    "cost_adjusted_net_rr": net_rr,
                    "expiry_bars": self.params.retrace_expiry_bars,
                },
            ),
        )
        self.active = None
        return events, plan

    def on_bar(
        self,
        bar: BarView,
        *,
        allow_new_setup: bool = True,
    ) -> tuple[list[Transition], TradePlan | None]:
        self.bar_index += 1
        atr_before = self.atr
        events = self._update_structure(bar)
        plan: TradePlan | None = None

        if atr_before is not None:
            events.extend(self._expire_and_accept_pools(bar, atr_before))
            if self.active is not None:
                more, plan = self._process_rejection(bar, atr_before)
                events.extend(more)
            elif allow_new_setup:
                events.extend(self._detect_sweep(bar, atr_before))
                if self.active is not None:
                    more, plan = self._process_rejection(bar, atr_before)
                    events.extend(more)

        if self.previous_close is not None:
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - self.previous_close),
                abs(bar.low - self.previous_close),
            )
            self.true_ranges.append(true_range)
        self.previous_close = bar.close
        self.history.append(bar)
        return events, plan
