#!/usr/bin/env python3
"""Candidate 05 v35c: remove only current depth from first retest."""
from __future__ import annotations

from retrace_logic import pending_limit_invalidated
from sequential_retest_ablation_logic import first_sequential_boundary_retest_without_depth
from strategy_v35b_directional_sequential_flow import DirectionalSequentialFlowRegimeStrategy


class SequentialRetestDepthAblationStrategy(DirectionalSequentialFlowRegimeStrategy):
    """Test whether displayed depth blocked otherwise valid first retests.

    Directional likelihood windows, release structure, activity, efficiency,
    breakout book withdrawal, first-touch finality, stop, target, fees,
    slippage, 3% current-NAV sizing and Nautilus execution are unchanged. Only
    current displayed-depth support at the later first retest is removed.
    """

    def __init__(self, config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "sequential_retest_depth_requirement_ablated": 0,
                "sequential_retest_flow_only_confirmations": 0,
            },
        )

    def _advance_sequential_watch(self, row: dict[str, float | int]) -> None:
        watch = self.sequential_watch
        if watch is None or self.bar_index <= watch.breakout_index:
            return
        self.diagnostics["sequential_flow_retest_observations"] += 1
        if pending_limit_invalidated(
            side=watch.side,
            stop=watch.stop,
            high=float(row["high"]),
            low=float(row["low"]),
        ):
            self.diagnostics["sequential_flow_structural_invalidations"] += 1
            self._close_sequential_watch(row, "OPPOSITE_EVIDENCE_RANGE_INVALIDATED_BEFORE_ENTRY")
            return
        if (watch.side > 0 and float(row["high"]) >= watch.target) or (
            watch.side < 0 and float(row["low"]) <= watch.target
        ):
            self.diagnostics["sequential_flow_target_reached_before_entry"] += 1
            self._close_sequential_watch(row, "FROZEN_TARGET_REACHED_BEFORE_FIRST_RETEST_ENTRY")
            return
        if watch.target_pool_id not in self.active_pools:
            self.diagnostics["sequential_flow_target_source_expired"] += 1
            self._close_sequential_watch(row, "FROZEN_TARGET_SOURCE_EXPIRED_BEFORE_ENTRY")
            return
        if self.bar_index > watch.expires_index:
            self.diagnostics["sequential_flow_retest_expiries"] += 1
            self._close_sequential_watch(row, "EXISTING_FIRST_RETEST_WINDOW_EXPIRED")
            return

        touched = (
            float(row["low"]) <= watch.boundary
            if watch.side > 0
            else float(row["high"]) >= watch.boundary
        )
        if not touched:
            return
        self.diagnostics["sequential_retest_depth_requirement_ablated"] += 1
        if not first_sequential_boundary_retest_without_depth(
            side=watch.side,
            boundary=watch.boundary,
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            flow_15s=self._feature("flow_15s"),
            maximum_counterflow=self.config.acceptance_max_counterflow,
        ):
            self.diagnostics["sequential_flow_first_touch_failures"] += 1
            self._close_sequential_watch(row, "FIRST_BOUNDARY_TOUCH_NOT_DEFENDED_BY_PRICE_AND_FLOW")
            return
        self.diagnostics["sequential_flow_retest_confirmations"] += 1
        self.diagnostics["sequential_retest_flow_only_confirmations"] += 1
        if not self._sequential_entry_slot_idle():
            self.diagnostics["sequential_flow_slot_conflicts"] += 1
            self._close_sequential_watch(row, "LOCAL_ENTRY_SLOT_OCCUPIED_AT_FIRST_RETEST")
            return
        self._submit_sequential_release(watch, row)


__all__ = ["SequentialRetestDepthAblationStrategy"]
