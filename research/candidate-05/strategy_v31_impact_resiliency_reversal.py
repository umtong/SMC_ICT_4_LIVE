#!/usr/bin/env python3
"""Candidate 05 v31: reverse an accepted shock only after book resiliency."""
from __future__ import annotations

import math

from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from impact_resiliency_reversal_logic import failed_break_reaccepted
from impact_resiliency_reversal_logic import failed_break_structural_stop
from impact_resiliency_reversal_logic import first_failed_break_retest_response
from impact_resiliency_reversal_logic import impact_failure_ready
from logic import choose_liquidity_target
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v29_external_displacement_fvg import ExternalFvgWatch
from strategy_v29_external_displacement_fvg import ObservedBar
from strategy_v29b_external_displacement_fvg import ExternalDisplacementFvgStrategyV2


class ImpactResiliencyReversalStrategy(ExternalDisplacementFvgStrategyV2):
    """Add an impact-decay reversal after a causally accepted external shock.

    A completed four-hour external high or low must first be broken with the
    unchanged Candidate 05 acceptance contract.  The strategy then observes,
    without occupying the execution slot, whether the shock is absorbed:

    * price closes back through the broken level and the shock midpoint;
    * marginal one-minute price efficiency falls below the existing rejection
      efficiency ceiling;
    * final-fifteen-second flow turns against the original shock by at least the
      existing rejection-flow threshold;
    * the side of the book depleted by the shock replenishes by at least the
      existing depth-refill threshold.

    No order is submitted on that failure bar.  The strategy waits for the first
    later return to the failed external level, requires current reversal flow and
    depth, and places a passive limit at the level.  The stop is beyond the
    original shock extreme; the target must be a still-live opposing liquidity
    pool.  Costs, 3% current-NAV risk sizing, pending-order validity and all
    execution/accounting remain inherited from v26 and NautilusTrader.
    """

    BRANCH = "EXTERNAL_IMPACT_RESILIENCY_REVERSAL"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "impact_resiliency_observation_bars": 0,
                "impact_resiliency_failures_confirmed": 0,
                "impact_resiliency_failure_window_expired": 0,
                "impact_resiliency_reaccepted_before_failure": 0,
                "impact_resiliency_retest_observations": 0,
                "impact_resiliency_retest_confirmations": 0,
                "impact_resiliency_retest_expired": 0,
                "impact_resiliency_reaccepted_after_failure": 0,
                "impact_resiliency_slot_conflicts": 0,
                "impact_resiliency_no_live_target": 0,
                "impact_resiliency_geometry_rejections": 0,
                "impact_resiliency_submissions": 0,
            },
        )

    def _advance_gap_formation(
        self,
        watch: ExternalFvgWatch,
        bar: ObservedBar,
    ) -> None:
        """Observe post-shock impact decay instead of displacement continuation."""
        if self.bar_index <= watch.breakout_index:
            return
        self.diagnostics["impact_resiliency_observation_bars"] += 1

        # A new close farther outside the level before failure confirmation means
        # the accepted shock is still being discovered rather than absorbed.
        if (
            self.bar_index > watch.breakout_index + 1
            and failed_break_reaccepted(
                shock_side=watch.side,
                external_level=watch.external_level,
                close=bar.close,
            )
        ):
            # Continue observing within the existing four-bar confirmation
            # horizon; do not call this a reversal merely because one bar paused.
            if self.bar_index > watch.formation_expires_index:
                self.diagnostics["impact_resiliency_reaccepted_before_failure"] += 1
                self._close_external_watch(
                    watch.scenario_id,
                    bar,
                    "EXTERNAL_SHOCK_REMAINED_ACCEPTED_THROUGH_FAILURE_WINDOW",
                )
            return

        if self.bar_index > watch.formation_expires_index:
            self.diagnostics["impact_resiliency_failure_window_expired"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "EXISTING_FOUR_BAR_IMPACT_FAILURE_WINDOW_EXPIRED",
            )
            return

        if not impact_failure_ready(
            shock_side=watch.side,
            external_level=watch.external_level,
            shock_high=float(watch.details["breakout_high"]),
            shock_low=float(watch.details["breakout_low"]),
            close=bar.close,
            flow_15s=bar.flow_15s,
            efficiency_60s=bar.efficiency_60s,
            bid_depth_change_1m=self._feature("bid_depth_change_1_1m"),
            ask_depth_change_1m=self._feature("ask_depth_change_1_1m"),
            maximum_efficiency=self.config.rejection_efficiency_max,
            minimum_reversal_flow=self.config.rejection_flow_min,
            minimum_depth_refill=self.config.rejection_depth_refill_min,
        ):
            return

        watch.phase = "WAIT_FAILED_EXTERNAL_LEVEL_RETEST"
        watch.gap_formed_index = self.bar_index
        watch.retest_expires_index = self.bar_index + self.config.acceptance_retrace_bars
        watch.details.update(
            {
                "impact_failure_index": self.bar_index,
                "impact_failure_ts": bar.ts,
                "impact_failure_close": bar.close,
                "impact_failure_flow_15s": bar.flow_15s,
                "impact_failure_efficiency_60s": bar.efficiency_60s,
                "impact_failure_bid_depth_change_1m": self._feature("bid_depth_change_1_1m"),
                "impact_failure_ask_depth_change_1m": self._feature("ask_depth_change_1_1m"),
                "trade_side": -watch.side,
            },
        )
        self.diagnostics["impact_resiliency_failures_confirmed"] += 1
        self._transition(
            watch.scenario_id,
            "EXTERNAL_IMPACT_FAILURE_CONFIRMED",
            bar.ts,
            bar.ts,
            "WAIT_FAILED_EXTERNAL_LEVEL_RETEST",
            "PRICE_IMPACT_DECAYED_WHILE_DEPLETED_BOOK_SIDE_REPLENISHED",
            watch.external_level,
            dict(watch.details),
        )

    def _advance_gap_retest(
        self,
        watch: ExternalFvgWatch,
        bar: ObservedBar,
    ) -> None:
        """Wait for the first later defended retest of the failed level."""
        if self.bar_index <= watch.gap_formed_index:
            return
        self.diagnostics["impact_resiliency_retest_observations"] += 1

        if failed_break_reaccepted(
            shock_side=watch.side,
            external_level=watch.external_level,
            close=bar.close,
        ):
            self.diagnostics["impact_resiliency_reaccepted_after_failure"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "ORIGINAL_SHOCK_DIRECTION_REACCEPTED_BEFORE_REVERSAL_ENTRY",
            )
            return
        if self.bar_index > watch.retest_expires_index:
            self.diagnostics["impact_resiliency_retest_expired"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "EXISTING_FIRST_FAILED_LEVEL_RETEST_WINDOW_EXPIRED",
            )
            return

        trade_side = -watch.side
        if not first_failed_break_retest_response(
            trade_side=trade_side,
            external_level=watch.external_level,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            flow_15s=bar.flow_15s,
            depth_imbalance=bar.depth_imbalance,
            maximum_counterflow=self.config.acceptance_max_counterflow,
        ):
            return
        self.diagnostics["impact_resiliency_retest_confirmations"] += 1
        if not self._external_entry_slot_idle():
            self.diagnostics["impact_resiliency_slot_conflicts"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "GLOBAL_ENTRY_SLOT_OCCUPIED_AT_FAILED_LEVEL_RETEST",
            )
            return
        self._submit_impact_resiliency_reversal(watch, bar)

    def _submit_impact_resiliency_reversal(
        self,
        watch: ExternalFvgWatch,
        bar: ObservedBar,
    ) -> None:
        trade_side = -watch.side
        atr = self._atr()
        entry_price = self.instrument.make_price(watch.external_level)
        entry = _as_float(entry_price)
        raw_stop = failed_break_structural_stop(
            trade_side=trade_side,
            shock_high=float(watch.details["breakout_high"]),
            shock_low=float(watch.details["breakout_low"]),
            atr=atr,
            stop_buffer_atr=self.config.stop_buffer_atr,
        )
        stop_price = self.instrument.make_price(raw_stop)
        stop = _as_float(stop_price)
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            trade_side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["impact_resiliency_geometry_rejections"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "INVALID_IMPACT_RESILIENCY_STOP_GEOMETRY",
            )
            return

        target, target_source, _ = choose_liquidity_target(
            entry=entry,
            side=trade_side,
            pools=list(self.active_pools.values()),
            planned_loss=planned_loss,
            cost_rate=cost_rate,
            min_net_r=MIN_LIQUIDITY_TARGET_NET_R,
            max_net_r=MAX_LIQUIDITY_TARGET_NET_R,
            fallback_net_r=FALLBACK_TARGET_NET_R,
        )
        if not target_source.startswith("POOL:"):
            self.diagnostics["impact_resiliency_no_live_target"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "NO_STILL_LIVE_OPPOSING_LIQUIDITY_TARGET",
            )
            return
        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        rounded_r = net_r_at_price(
            entry,
            target,
            trade_side,
            planned_loss,
            cost_rate,
        )
        if (
            rounded_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R
            or (trade_side > 0 and not stop < entry < target)
            or (trade_side < 0 and not target < entry < stop)
        ):
            self.diagnostics["impact_resiliency_geometry_rejections"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "ROUNDED_IMPACT_RESILIENCY_BRACKET_INVALID",
            )
            return

        details = {
            **watch.details,
            "retest_index": self.bar_index,
            "retest_ts": bar.ts,
            "retest_close": bar.close,
            "retest_flow_15s": bar.flow_15s,
            "retest_depth_imbalance": bar.depth_imbalance,
            "entry_limit": entry,
            "stop": stop,
            "target": target,
            "target_source": target_source,
            "target_net_r": rounded_r,
        }
        setup = PendingSetup(
            scenario_id=watch.scenario_id,
            branch=self.BRANCH,
            side=trade_side,
            swept_kind=watch.level_kind,
            pool_id=watch.level_id,
            pool_level=watch.external_level,
            created_index=watch.breakout_index,
            expires_index=watch.retest_expires_index,
            sweep_extreme=watch.breakout_extreme,
            structure=watch.external_level,
            atr=watch.atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="EXTERNAL_IMPACT_RESILIENCY_REVERSAL",
            choch_close=entry,
            stop=stop,
            atr=atr,
            created_index=watch.breakout_index,
            expires_index=watch.retest_expires_index,
            details=details,
        )
        submitted = self._submit_price_capped_bracket(
            armed=armed,
            row={
                "ts": bar.ts,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
            },
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=rounded_r,
            branch=self.BRANCH,
            event_type="IMPACT_RESILIENCY_REVERSAL_LIMIT_SUBMITTED",
            reason="FIRST_DEFENDED_RETEST_AFTER_ACCEPTED_SHOCK_DECAYED",
            expires_index=watch.retest_expires_index,
            entry_tag="IMPACT_RESILIENCY_REVERSAL_ENTRY",
            extra=details,
        )
        if submitted:
            self.external_fvg_watches.pop(watch.scenario_id, None)
            self.diagnostics["impact_resiliency_submissions"] += 1
        else:
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "IMPACT_RESILIENCY_BRACKET_SUBMISSION_FAILED",
            )


__all__ = ["ImpactResiliencyReversalStrategy"]
