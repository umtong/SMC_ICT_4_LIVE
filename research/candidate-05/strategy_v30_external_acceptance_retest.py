#!/usr/bin/env python3
"""Candidate 05 v30: ablate only the FVG requirement from v29b."""
from __future__ import annotations

import math

from external_acceptance_retest_logic import accepted_level_invalidated
from external_acceptance_retest_logic import external_level_structural_stop
from external_acceptance_retest_logic import first_accepted_level_retest_response
from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from logic import choose_liquidity_target
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v29_external_displacement_fvg import ExternalFvgWatch
from strategy_v29_external_displacement_fvg import ObservedBar
from strategy_v29b_external_displacement_fvg import ExternalDisplacementFvgStrategyV2


class ExternalAcceptanceFirstRetestStrategy(ExternalDisplacementFvgStrategyV2):
    """Keep external acceptance but remove the three-bar imbalance condition.

    This is a one-variable structural ablation of v29b. Completed four-hour
    external levels, the unchanged acceptance classifier, first-retest horizon,
    current tail flow and depth, structural invalidation, real liquidity target,
    costs, 3% NAV risk sizing and Nautilus execution remain identical in role.
    The passive entry reference becomes the accepted external level itself.
    """

    BRANCH = "EXTERNAL_ACCEPTANCE_FIRST_RETEST"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "external_level_retest_observations": 0,
                "external_level_retest_confirmations": 0,
                "external_level_retest_invalidations": 0,
                "external_level_retest_expiries": 0,
                "external_level_retest_slot_conflicts": 0,
                "external_level_retest_no_live_target": 0,
                "external_level_retest_geometry_rejections": 0,
                "external_level_retest_submissions": 0,
            },
        )

    def _advance_gap_formation(
        self,
        watch: ExternalFvgWatch,
        bar: ObservedBar,
    ) -> None:
        """Observe only the first defended retest of the accepted level."""
        if self.bar_index <= watch.breakout_index:
            return
        self.diagnostics["external_level_retest_observations"] += 1
        if accepted_level_invalidated(
            side=watch.side,
            level=watch.external_level,
            close=bar.close,
        ):
            self.diagnostics["external_level_retest_invalidations"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "EXTERNAL_ACCEPTANCE_FAILED_BEFORE_DEFENDED_RETEST",
            )
            return
        expiry = watch.breakout_index + self.config.acceptance_retrace_bars
        if self.bar_index > expiry:
            self.diagnostics["external_level_retest_expiries"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "EXISTING_FIRST_EXTERNAL_LEVEL_RETEST_WINDOW_EXPIRED",
            )
            return
        if not first_accepted_level_retest_response(
            side=watch.side,
            level=watch.external_level,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            flow_15s=bar.flow_15s,
            depth_imbalance=bar.depth_imbalance,
            maximum_counterflow=self.config.acceptance_max_counterflow,
        ):
            return
        self.diagnostics["external_level_retest_confirmations"] += 1
        if not self._external_entry_slot_idle():
            self.diagnostics["external_level_retest_slot_conflicts"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "GLOBAL_ENTRY_SLOT_OCCUPIED_AT_FIRST_EXTERNAL_LEVEL_RETEST",
            )
            return
        self._submit_external_level_retest(watch, bar, expiry)

    def _submit_external_level_retest(
        self,
        watch: ExternalFvgWatch,
        bar: ObservedBar,
        expiry: int,
    ) -> None:
        side = watch.side
        atr = self._atr()
        entry_price = self.instrument.make_price(watch.external_level)
        entry = _as_float(entry_price)
        raw_stop = external_level_structural_stop(
            side=side,
            level=watch.external_level,
            atr=atr,
            stop_buffer_atr=self.config.stop_buffer_atr,
        )
        stop_price = self.instrument.make_price(raw_stop)
        stop = _as_float(stop_price)
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(entry, stop, side, cost_rate, slippage_rate)
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["external_level_retest_geometry_rejections"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "INVALID_EXTERNAL_LEVEL_RETEST_STOP_GEOMETRY",
            )
            return
        target, target_source, _ = choose_liquidity_target(
            entry=entry,
            side=side,
            pools=list(self.active_pools.values()),
            planned_loss=planned_loss,
            cost_rate=cost_rate,
            min_net_r=MIN_LIQUIDITY_TARGET_NET_R,
            max_net_r=MAX_LIQUIDITY_TARGET_NET_R,
            fallback_net_r=FALLBACK_TARGET_NET_R,
        )
        if not target_source.startswith("POOL:"):
            self.diagnostics["external_level_retest_no_live_target"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "NO_STILL_LIVE_OPPOSING_LIQUIDITY_TARGET",
            )
            return
        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        rounded_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if (
            rounded_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R
            or (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["external_level_retest_geometry_rejections"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "ROUNDED_EXTERNAL_LEVEL_RETEST_BRACKET_INVALID",
            )
            return
        details = {
            **watch.details,
            "ablation": "REMOVE_DISPLACEMENT_FVG_REQUIREMENT",
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
            side=side,
            swept_kind=watch.level_kind,
            pool_id=watch.level_id,
            pool_level=watch.external_level,
            created_index=watch.breakout_index,
            expires_index=expiry,
            sweep_extreme=watch.breakout_extreme,
            structure=watch.external_level,
            atr=watch.atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="EXTERNAL_ACCEPTANCE_FIRST_RETEST",
            choch_close=entry,
            stop=stop,
            atr=atr,
            created_index=watch.breakout_index,
            created_ts=bar.ts,
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
            event_type="EXTERNAL_LEVEL_RETEST_LIMIT_SUBMITTED",
            reason="FIRST_DEFENDED_RETEST_OF_ACCEPTED_EXTERNAL_LEVEL",
            expires_index=expiry,
            entry_tag="EXTERNAL_ACCEPTANCE_RETEST_ENTRY",
            extra=details,
        )
        if submitted:
            self.external_fvg_watches.pop(watch.scenario_id, None)
            self.diagnostics["external_level_retest_submissions"] += 1
        else:
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "EXTERNAL_LEVEL_RETEST_BRACKET_SUBMISSION_FAILED",
            )


__all__ = ["ExternalAcceptanceFirstRetestStrategy"]
