"""Multi-bar event-time displacement certification for candidate 10 v2.1."""

from __future__ import annotations

from math import sqrt

from c10_model import BarView
from c10_model import Setup
from c10_model import TradePlan
from c10_model import Transition
from c10_state import AuctionStateMachine as StructuralPoolStateMachine


class AuctionStateMachine(StructuralPoolStateMachine):
    """Structural pools with path-efficient, causal displacement certification."""

    def _extend_raid_if_needed(
        self,
        setup: Setup,
        bar: BarView,
    ) -> Transition | None:
        extended = False
        if setup.direction < 0 and bar.high > setup.raid_extreme:
            setup.raid_extreme = bar.high
            extended = True
        elif setup.direction > 0 and bar.low < setup.raid_extreme:
            setup.raid_extreme = bar.low
            extended = True
        if not extended:
            return None

        # A later, deeper raid becomes the causal path origin. Earlier travel is
        # no longer displacement away from the final liquidity-taking extreme.
        setup.path_last_close = setup.raid_extreme
        setup.path_travel = 0.0
        setup.path_bars = 0
        setup.created_index = self.bar_index
        return self._setup_transition(
            setup,
            bar,
            event_type="RAID_EXTENDED",
            next_state="RAIDED",
            reason_code="SOURCE_POOL_RAID_EXTREME_EXTENDED",
            reference_price=setup.raid_extreme,
            details={"new_raid_extreme": setup.raid_extreme},
        )

    def _update_path(
        self,
        setup: Setup,
        bar: BarView,
        atr: float,
    ) -> dict[str, float | int | bool | str]:
        if setup.path_last_close == 0.0:
            setup.path_last_close = setup.raid_extreme
        prior_close = setup.path_last_close
        setup.path_travel += abs(bar.close - prior_close)
        setup.path_last_close = bar.close
        setup.path_bars += 1

        net_move = (bar.close - setup.raid_extreme) * setup.direction
        path_efficiency = (
            net_move / setup.path_travel
            if setup.path_travel > self.tick_size
            else 0.0
        )
        speed_atr = (
            net_move / (atr * sqrt(max(1, setup.path_bars)))
            if atr > 0.0
            else 0.0
        )
        structure_broken = (
            bar.close > setup.approach_level
            if setup.direction > 0
            else bar.close < setup.approach_level
        )
        directional_step = (bar.close - prior_close) * setup.direction > 0.0

        bar_range = max(self.tick_size, bar.high - bar.low)
        close_location = (bar.close - bar.low) / bar_range
        body = abs(bar.close - bar.open)
        if self.params.enable_path_displacement:
            confirmed = bool(
                net_move >= atr * self.params.displacement_atr
                and path_efficiency >= self.params.displacement_min_efficiency
                and speed_atr >= self.params.displacement_speed_atr
                and structure_broken
                and directional_step
            )
            mode = "MULTI_BAR_PATH"
        elif setup.direction < 0:
            confirmed = bool(
                body >= atr * self.params.displacement_atr
                and structure_broken
                and bar.close < bar.open
                and close_location <= 0.35
            )
            mode = "SINGLE_BAR_ABLATION"
        else:
            confirmed = bool(
                body >= atr * self.params.displacement_atr
                and structure_broken
                and bar.close > bar.open
                and close_location >= 0.65
            )
            mode = "SINGLE_BAR_ABLATION"

        return {
            "confirmed": confirmed,
            "mode": mode,
            "path_bars": setup.path_bars,
            "path_travel": setup.path_travel,
            "net_move": net_move,
            "net_move_atr": net_move / atr if atr else 0.0,
            "path_efficiency": path_efficiency,
            "speed_atr": speed_atr,
            "structure_broken": structure_broken,
            "directional_step": directional_step,
            "body_atr": body / atr if atr else 0.0,
            "close_location": close_location,
        }

    def _process_rejection(
        self,
        bar: BarView,
        atr: float,
    ) -> tuple[list[Transition], TradePlan | None]:
        setup = self.active
        assert setup is not None
        events: list[Transition] = []

        extension = self._extend_raid_if_needed(setup, bar)
        if extension is not None:
            events.append(extension)

        accept_buffer = max(self.tick_size * 2.0, atr * self.params.acceptance_atr)
        if setup.direction < 0:
            accepted = bar.close > setup.source_upper + accept_buffer
        else:
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

        path = self._update_path(setup, bar, atr)
        if not bool(path["confirmed"]):
            age = self.bar_index - setup.created_index
            if age > self.params.displacement_max_bars:
                events.append(
                    self._setup_transition(
                        setup,
                        bar,
                        event_type="SCENARIO_EXPIRED",
                        next_state="EXPIRED",
                        reason_code="NO_EFFICIENT_DISPLACEMENT_AFTER_STRUCTURAL_RAID",
                        details=path,
                    ),
                )
                self.active = None
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
                reason_code="EFFICIENT_PATH_BROKE_APPROACH_STRUCTURE",
                reference_price=bar.close,
                details={
                    **path,
                    "approach_level": setup.approach_level,
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
                        **path,
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
                **path,
            },
        )
        events.append(
            self._setup_transition(
                setup,
                bar,
                event_type="ENTRY_READY",
                next_state="ENTRY_READY",
                reason_code="STRUCTURAL_POOL_TO_POOL_PATH_RETRACE_ARMED",
                reference_price=entry,
                details={
                    "source_pool_id": setup.source_pool_id,
                    "target_pool_id": target_pool.pool_id,
                    "target": target,
                    "stop": stop,
                    "cost_adjusted_net_rr": net_rr,
                    "expiry_bars": self.params.retrace_expiry_bars,
                    **path,
                },
            ),
        )
        self.active = None
        return events, plan
