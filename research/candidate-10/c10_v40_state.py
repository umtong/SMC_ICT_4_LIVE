"""v40 source-equilibrium detector/scenario separation."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from logic import Auction, BarObs, Direction, Scenario, Side, TradePlan

from c10_v38_state import ConfirmedMicroPivotProtectionEngine
from c10_v40_overlay import source_equilibrium
from c10_v40_overlay import source_equilibrium_detector_enabled


class SourceEquilibriumFailedAuctionEngine(
    ConfirmedMicroPivotProtectionEngine,
):
    """Confirm source-range failed auctions without an external-draw prerequisite."""

    def _detect_sweep(
        self,
        bar: BarObs,
        prev: BarObs,
        atr: float,
        rel_volume: float,
        auxiliary_high: list[Any] | None = None,
        auxiliary_low: list[Any] | None = None,
    ) -> None:
        if not source_equilibrium_detector_enabled():
            return super()._detect_sweep(
                bar,
                prev,
                atr,
                rel_volume,
                auxiliary_high=auxiliary_high,
                auxiliary_low=auxiliary_low,
            )
        if self.active is not None or self.active_trade_id is not None:
            return

        crossed_high = [
            pool
            for pool in self.pools
            if not pool.consumed
            and pool.external
            and pool.triggerable
            and pool.confirmed_index < self._index
            and bar.ts_ns <= pool.trigger_end_ts_ns
            and pool.side == Side.HIGH
            and prev.close <= pool.level < bar.high
        ]
        crossed_low = [
            pool
            for pool in self.pools
            if not pool.consumed
            and pool.external
            and pool.triggerable
            and pool.confirmed_index < self._index
            and bar.ts_ns <= pool.trigger_end_ts_ns
            and pool.side == Side.LOW
            and prev.close >= pool.level > bar.low
        ]
        auxiliary_high = auxiliary_high or []
        auxiliary_low = auxiliary_low or []
        if (
            crossed_high
            and (crossed_low or auxiliary_low)
        ) or (
            crossed_low and auxiliary_high
        ):
            for pool in [*crossed_high, *crossed_low]:
                pool.consumed = True
            self.skips["AMBIGUOUS_BOTH_SIDES_SWEPT"] += 1
            self._event(
                "AMBIGUOUS",
                "AMBIGUOUS_SWEEP",
                bar.ts_ns,
                bar.ts_ns,
                "ARMED",
                "TERMINAL",
                "BAR_PATH_UNRESOLVABLE",
                bar.close,
                {
                    "high_pool_count": len(crossed_high) + len(auxiliary_high),
                    "low_pool_count": len(crossed_low) + len(auxiliary_low),
                },
            )
            return

        crossed = crossed_high or crossed_low
        if not crossed:
            return
        side = Side.HIGH if crossed_high else Side.LOW
        auxiliary = auxiliary_high if side == Side.HIGH else auxiliary_low
        all_crossed = [*crossed, *auxiliary]
        for pool in crossed:
            pool.consumed = True

        if side == Side.HIGH:
            source = max(crossed, key=lambda item: (item.strength, item.level))
            extreme = bar.high
            internal = self._latest_internal(Side.LOW, bar.ts_ns)
            penetration = (bar.high - source.level) / atr
        else:
            source = max(crossed, key=lambda item: (item.strength, -item.level))
            extreme = bar.low
            internal = self._latest_internal(Side.HIGH, bar.ts_ns)
            penetration = (source.level - bar.low) / atr
        if internal is None:
            self.skips["NO_CAUSAL_INTERNAL_STRUCTURE"] += 1
            return
        if (
            rel_volume < self.config.min_relative_volume
            or not self.config.sweep_min_atr
            <= penetration
            <= self.config.sweep_max_atr
        ):
            self.skips["SWEEP_ACTIVITY_OR_PENETRATION"] += 1
            return
        sweep_flow_ok = (
            bar.signed_flow >= self.config.absorption_flow_min
            if side == Side.HIGH
            else bar.signed_flow <= -self.config.absorption_flow_min
        )
        if not sweep_flow_ok:
            self.skips["NO_AGGRESSOR_FLOW_AT_SWEEP"] += 1
            return
        try:
            equilibrium = source_equilibrium(source)
        except ValueError:
            self.skips["SOURCE_RANGE_ENDPOINTS_UNAVAILABLE"] += 1
            self._event(
                source.scenario_id,
                "SWEEP_UNFRAMED",
                bar.ts_ns,
                bar.ts_ns,
                "ARMED",
                "TERMINAL",
                "SOURCE_RANGE_ENDPOINTS_UNAVAILABLE",
                source.level,
            )
            return

        self.active = Auction(
            pool=source,
            sweep=bar,
            sweep_index=self._index,
            atr=atr,
            initial_sweep_ts_ns=bar.ts_ns,
            internal_level=internal,
            sweep_extreme=extreme,
            rejection_seed=True,
            acceptance_seed=False,
            state="OBSERVE",
            framed_draw_side=None,
            framed_target_pool_id=None,
            framed_target_level=None,
            continuation_target_pool_id=None,
            continuation_target_level=None,
            reversal_target_pool_id=None,
            reversal_target_level=None,
            source_draw_side=None,
            source_draw_score=0.0,
            framed_draw_score=0.0,
            framed_draw_method="SOURCE_EQUILIBRIUM_PRIMARY_DECOUPLED",
            framed_high_hazard=0.0,
            framed_low_hazard=0.0,
            crossed_pool_ids=[item.scenario_id for item in all_crossed],
            last_crossed_level=(
                max(item.level for item in all_crossed)
                if side == Side.HIGH
                else min(item.level for item in all_crossed)
            ),
            cascade_count=sum(
                item.source != "ROUND_NUMBER" for item in all_crossed
            ),
        )
        self._event(
            source.scenario_id,
            "SOURCE_RANGE_LIQUIDITY_SWEEP",
            bar.ts_ns,
            bar.ts_ns,
            "ARMED",
            "OBSERVE",
            "SESSION_SOURCE_BOUNDARY_TRADED_THROUGH",
            source.level,
            {
                "side": side.value,
                "penetration_atr": penetration,
                "relative_volume": rel_volume,
                "aggregate_aggressor_flow": bar.signed_flow,
                "crossed_pool_count": len(all_crossed),
                "source_range_id": source.range_id,
                "source_level": source.level,
                "source_opposite_level": source.opposite_level,
                "source_equilibrium": equilibrium,
                "external_draw_required": False,
                "competing_acceptance_scenario_enabled": False,
            },
        )

    def _update_cascade_map(self, auction: Auction, bar: BarObs) -> None:
        if not source_equilibrium_detector_enabled():
            return super()._update_cascade_map(auction, bar)
        if auction.state != "OBSERVE":
            return
        if auction.pool.side == Side.HIGH:
            newly = [
                pool
                for pool in self.pools
                if not pool.consumed
                and pool.external
                and pool.side == Side.HIGH
                and pool.confirmed_index < self._index
                and auction.sweep_extreme < pool.level < bar.high
            ]
            newly.sort(key=lambda item: item.level)
        else:
            newly = [
                pool
                for pool in self.pools
                if not pool.consumed
                and pool.external
                and pool.side == Side.LOW
                and pool.confirmed_index < self._index
                and bar.low < pool.level < auction.sweep_extreme
            ]
            newly.sort(key=lambda item: item.level, reverse=True)
        for pool in newly:
            pool.consumed = True
            auction.crossed_pool_ids.append(pool.scenario_id)
            auction.last_crossed_level = pool.level
            if pool.source != "ROUND_NUMBER":
                auction.cascade_count += 1
            self._event(
                auction.pool.scenario_id,
                "CASCADE_LIQUIDITY_CONSUMED",
                auction.sweep.ts_ns,
                bar.ts_ns,
                "OBSERVE",
                "OBSERVE",
                "SAME_SIDE_EXTERNAL_LEVEL_CONSUMED_WITHOUT_TARGET_REFRAME",
                pool.level,
                {
                    "consumed_pool": pool.scenario_id,
                    "cascade_count": auction.cascade_count,
                    "external_draw_required": False,
                },
            )

    def _confirm_far(
        self,
        auction: Auction,
        bar: BarObs,
    ) -> TradePlan | None:
        if not source_equilibrium_detector_enabled():
            return super()._confirm_far(auction, bar)
        if auction.state != "OBSERVE":
            return super()._confirm_far(auction, bar)
        if not auction.rejection_seed:
            return None
        if bar.ts_ns < auction.pool.trigger_start_ts_ns:
            return None
        if bar.ts_ns > auction.pool.trigger_end_ts_ns:
            self._terminal(
                auction,
                bar,
                "SESSION_DECISION_WINDOW_EXPIRED",
            )
            return None

        side = auction.pool.side
        reclaimed = (
            bar.close < auction.pool.level
            if side == Side.HIGH
            else bar.close > auction.pool.level
        )
        if reclaimed and not auction.reclaim_seen:
            auction.reclaim_seen = True
            post_sweep_side = Side.LOW if side == Side.HIGH else Side.HIGH
            internal = self._latest_internal(
                post_sweep_side,
                bar.ts_ns,
                after_ts_ns=auction.sweep.ts_ns,
            )
            if internal is not None:
                auction.internal_level = internal
        if not auction.reclaim_seen:
            return None

        if side == Side.HIGH:
            structure_break = bar.close < auction.internal_level
            flow = bar.signed_flow <= -self.config.displacement_flow_min
            direction = Direction.SHORT
            draw_side = Side.LOW
            stop = (
                auction.sweep_extreme
                + self.config.stop_buffer_atr * auction.atr
            )
        else:
            structure_break = bar.close > auction.internal_level
            flow = bar.signed_flow >= self.config.displacement_flow_min
            direction = Direction.LONG
            draw_side = Side.HIGH
            stop = (
                auction.sweep_extreme
                - self.config.stop_buffer_atr * auction.atr
            )
        body = bar.body >= self.config.displacement_body_atr * auction.atr
        if not (structure_break and flow and body):
            return None

        try:
            target = source_equilibrium(auction.pool)
        except ValueError:
            self._terminal(
                auction,
                bar,
                "SOURCE_RANGE_ENDPOINTS_UNAVAILABLE",
            )
            return None

        auction.state = "FAR_CONFIRMED"
        auction.scenario = Scenario.FAR
        auction.direction = direction
        auction.stop_price = stop
        auction.target_price = target
        auction.draw_side = draw_side
        auction.draw_score = 1.0
        auction.framed_draw_method = "SOURCE_EQUILIBRIUM_PRIMARY_DECOUPLED"
        auction.displacement_index = self._index
        auction.zone_low, auction.zone_high = self._zone_from_displacement(
            self.bars,
            self._index,
            direction,
        )
        auction.elapsed = 0
        self._event(
            auction.pool.scenario_id,
            "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED",
            auction.sweep.ts_ns,
            bar.ts_ns,
            "OBSERVE",
            "FAR_CONFIRMED",
            "RECLAIM_MSS_DISPLACEMENT_TO_SOURCE_EQUILIBRIUM",
            auction.pool.level,
            {
                "internal_level": auction.internal_level,
                "direction": direction.value,
                "source_equilibrium": target,
                "zone_low": auction.zone_low,
                "zone_high": auction.zone_high,
                "initial_stop": stop,
                "external_draw_required": False,
                "detector_evidence": [
                    "source boundary sweep",
                    "source boundary reclaim",
                    "post-sweep internal structure break",
                    "directional displacement body and aggressor flow",
                ],
            },
        )
        return self._costed_limit_plan(
            auction,
            bar,
            "SOURCE_EQUILIBRIUM_FAILED_AUCTION_FIRST_DISPLACEMENT",
        )

    def _confirm_aac(
        self,
        auction: Auction,
        bar: BarObs,
    ) -> TradePlan | None:
        if source_equilibrium_detector_enabled() and auction.state == "OBSERVE":
            return None
        return super()._confirm_aac(auction, bar)

    def _decorate_plan(
        self,
        plan: TradePlan,
        *,
        state: dict[str, Any],
        entry_process: str,
    ) -> TradePlan:
        decorated = super()._decorate_plan(
            plan,
            state=state,
            entry_process=entry_process,
        )
        if not source_equilibrium_detector_enabled():
            return decorated
        details = dict(decorated.details)
        ce = dict(details.get("ce_rejection_primary", {}))
        ce["original_independent_external_draw"] = None
        ce["external_draw_required"] = False
        ce["detector_contract"] = (
            "SESSION_SOURCE_SWEEP_RECLAIM_MSS_DISPLACEMENT"
        )
        ce["target_selected_after_detector"] = (
            "SOURCE_DEALING_RANGE_EQUILIBRIUM"
        )
        ce["detector_scenario_separation"] = True
        details["ce_rejection_primary"] = ce
        details["source_equilibrium_primary_detector"] = {
            "schema": "candidate-10-v40-source-equilibrium-detector-v1",
            "external_draw_required": False,
            "source_range_id": getattr(
                next(
                    (
                        pool
                        for pool in self.pools
                        if pool.scenario_id == plan.scenario_id
                    ),
                    None,
                ),
                "range_id",
                None,
            ),
            "state_sequence": [
                "SOURCE_RANGE_LIQUIDITY_SWEEP",
                "SOURCE_BOUNDARY_RECLAIMED",
                "SOURCE_EQUILIBRIUM_FAILED_AUCTION_CONFIRMED",
                "CE_RETEST_ARMED",
                "CE_RETEST_TOUCHED",
                "CE_REJECTION_DISPLACEMENT_CONFIRMED",
                "SECOND_DISPLACEMENT_RETRACE_PENDING",
            ],
        }
        return replace(decorated, details=details)


__all__ = ["SourceEquilibriumFailedAuctionEngine"]
