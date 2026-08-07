#!/usr/bin/env python3
"""Candidate 05 v29b: contract-only repair for v29 ArmedEntryPath creation."""
from __future__ import annotations

import math

from external_displacement_fvg_logic import structural_gap_stop
from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from logic import choose_liquidity_target
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v29_external_displacement_fvg import ExternalDisplacementFvgStrategy
from strategy_v29_external_displacement_fvg import ExternalFvgWatch
from strategy_v29_external_displacement_fvg import ObservedBar


class ExternalDisplacementFvgStrategyV2(ExternalDisplacementFvgStrategy):
    """Keep v29 market logic unchanged and satisfy ArmedEntryPath's contract."""

    def _submit_external_fvg(
        self,
        watch: ExternalFvgWatch,
        bar: ObservedBar,
    ) -> None:
        gap = watch.gap
        if gap is None:
            self._close_external_watch(watch.scenario_id, bar, "MISSING_GAP_STATE")
            return
        side = watch.side
        atr = self._atr()
        entry_price = self.instrument.make_price(gap.midpoint)
        entry = _as_float(entry_price)
        raw_stop = structural_gap_stop(
            side=side,
            external_level=watch.external_level,
            gap=gap,
            atr=atr,
            stop_buffer_atr=self.config.stop_buffer_atr,
        )
        stop_price = self.instrument.make_price(raw_stop)
        stop = _as_float(stop_price)
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(entry, stop, side, cost_rate, slippage_rate)
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["external_fvg_geometry_rejections"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "INVALID_EXTERNAL_FVG_STOP_GEOMETRY",
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
            self.diagnostics["external_fvg_no_live_target"] += 1
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
            self.diagnostics["external_fvg_geometry_rejections"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "ROUNDED_EXTERNAL_FVG_BRACKET_INVALID",
            )
            return

        details = {
            **watch.details,
            "implementation_revision": "v29b_armed_entry_path_created_ts_contract",
            "retest_index": self.bar_index,
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
            flow_state="EXTERNAL_ACCEPTANCE_DISPLACEMENT_FVG",
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
            event_type="EXTERNAL_FVG_MIDPOINT_LIMIT_SUBMITTED",
            reason="FIRST_DEFENDED_RETURN_TO_EXTERNAL_DISPLACEMENT_IMBALANCE",
            expires_index=watch.retest_expires_index,
            entry_tag="EXTERNAL_DISPLACEMENT_FVG_ENTRY",
            extra=details,
        )
        if submitted:
            self.external_fvg_watches.pop(watch.scenario_id, None)
            self.diagnostics["external_fvg_submissions"] += 1
        else:
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "EXTERNAL_FVG_BRACKET_SUBMISSION_FAILED",
            )


__all__ = ["ExternalDisplacementFvgStrategyV2"]
