#!/usr/bin/env python3
"""Candidate 05 v21: preserve the CHoCH-time liquidity destination."""
from __future__ import annotations

import math
from typing import Any

from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import choose_protectable_liquidity_milestone
from flow_inflection_logic import has_adverse_slippage_room
from flow_inflection_logic import worst_entry_preserving_net_r
from logic import choose_liquidity_target
from logic import net_r_at_price
from logic import planned_loss_per_unit
from scenario_target_logic import frozen_target_reached_before_entry
from scenario_target_logic import revalidate_frozen_milestone
from sponsored_choch_logic import slippage_protected_marketable_limit
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v9 import ObservedEntryPathStrategy
from strategy_v20 import ConfirmedRetestResponseStrategy


class FrozenScenarioTargetStrategy(ConfirmedRetestResponseStrategy):
    """Freeze destination and intermediate protection when CHoCH confirms.

    v20 correctly delayed entry until the executable retest showed current price,
    final-15-second flow and resting-depth support. It nevertheless re-ran target
    selection at that later time. Newly confirmed pools could silently replace
    the destination which made the original CHoCH scenario monetizable. In two
    first-week paths this moved both the final target and the first defendable
    liquidity milestone farther away after the original move was already defined.

    v21 treats target selection as scenario state rather than execution state:

    * the nearest monetizable opposing pool, capped target price and first
      protectable intermediate pool are frozen at CHoCH/path-arm time;
    * if the frozen target is reached before entry, the missed opportunity closes
      without trading;
    * if its source pool expires before entry, the scenario closes rather than
      substituting a later pool;
    * current retest flow/depth and current adverse-slippage-capped entry remain
      mandatory;
    * the current entry must still preserve at least the existing 0.40 post-cost R
      to the frozen target;
    * the frozen milestone is revalidated against the current entry, but is never
      replaced by a later-created pool;
    * true breakaway execution uses the same frozen destination.

    No detector threshold, risk fraction, fee, slippage, stop, target-R boundary,
    execution model or account constraint is changed.
    """

    _FROZEN_BRANCHES = {
        "TAIL_FLOW_CONFIRMED_RETRACE",
        "TAIL_FLOW_LIQUIDITY_BREAKAWAY",
    }

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "frozen_scenario_targets": 0,
                "frozen_target_no_live_pool_at_arm": 0,
                "frozen_target_invalid_geometry_at_arm": 0,
                "frozen_target_reached_before_entry": 0,
                "frozen_target_source_expired_before_entry": 0,
                "frozen_target_retest_geometry_rejections": 0,
                "frozen_target_breakaway_geometry_rejections": 0,
                "frozen_milestones_at_arm": 0,
                "frozen_milestones_revalidated": 0,
                "frozen_milestones_no_longer_protectable": 0,
            },
        )

    def _submit_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        handled = super()._submit_entry(setup, row)
        armed = self.armed_entry_path
        if armed is None or armed.setup.scenario_id != setup.scenario_id:
            return handled
        if "frozen_target_price" in armed.details:
            return handled
        if self._freeze_scenario_destination(armed, row):
            return handled

        # The CHoCH was processed, but there was no coherent live destination.
        # Return True so the same completed bar cannot immediately open a new
        # overlapping scenario after this one is explicitly closed.
        return True

    def _freeze_scenario_destination(
        self,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
    ) -> bool:
        side = armed.setup.side
        entry = armed.choch_close
        stop = armed.stop
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
            self.diagnostics["frozen_target_invalid_geometry_at_arm"] += 1
            self._expire_armed_entry(row, "INVALID_FROZEN_TARGET_ARM_GEOMETRY")
            return False

        target, target_source, target_r = choose_liquidity_target(
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
            self.diagnostics["frozen_target_no_live_pool_at_arm"] += 1
            self._expire_armed_entry(
                row,
                "NO_LIVE_OPPOSING_LIQUIDITY_AT_CHOCH_TARGET_FREEZE",
            )
            return False

        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        rounded_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if (
            rounded_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R
            or (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["frozen_target_invalid_geometry_at_arm"] += 1
            self._expire_armed_entry(row, "INVALID_FROZEN_TARGET_AT_CHOCH")
            return False

        milestone = choose_protectable_liquidity_milestone(
            entry=entry,
            target=target,
            side=side,
            pools=list(self.active_pools.values()),
            atr=armed.atr,
            stop_buffer_atr=self.config.stop_buffer_atr,
            cost_rate=cost_rate,
            adverse_slippage_rate=slippage_rate,
        )
        target_pool_id = target_source.split(":", 1)[1]
        armed.details.update(
            {
                "scenario_target_policy": "FROZEN_AT_CHOCH",
                "frozen_target_price": target,
                "frozen_target_source": target_source,
                "frozen_target_pool_id": target_pool_id,
                "frozen_target_net_r_at_choch": rounded_r,
                "frozen_target_planned_loss_at_choch": planned_loss,
                "frozen_milestone_pool_id": None if milestone is None else milestone[0],
                "frozen_milestone_level": None if milestone is None else milestone[1],
                "frozen_milestone_raw_stop": None if milestone is None else milestone[2],
                "frozen_milestone_expected_net_at_choch": (
                    None if milestone is None else milestone[3]
                ),
            },
        )
        self.diagnostics["frozen_scenario_targets"] += 1
        if milestone is not None:
            self.diagnostics["frozen_milestones_at_arm"] += 1
        self._transition(
            armed.setup.scenario_id,
            "SCENARIO_LIQUIDITY_DESTINATION_FROZEN",
            int(row["ts"]),
            int(row["ts"]),
            "PATH_OBSERVATION_WITH_FROZEN_TARGET",
            "CHOCH_TIME_OPPOSING_LIQUIDITY_DEFINES_DESTINATION",
            target,
            dict(armed.details),
        )
        return True

    def _resolve_entry_path(self, row: dict[str, float | int]) -> None:
        armed = self.armed_entry_path
        if armed is None or self.bar_index <= armed.created_index:
            return
        target = self._frozen_target_price(armed)
        if not math.isfinite(target):
            self.diagnostics["frozen_target_invalid_geometry_at_arm"] += 1
            self._expire_armed_entry(row, "FROZEN_TARGET_STATE_MISSING")
            return
        if frozen_target_reached_before_entry(
            side=armed.setup.side,
            target=target,
            high=float(row["high"]),
            low=float(row["low"]),
        ):
            self.diagnostics["frozen_target_reached_before_entry"] += 1
            self._expire_armed_entry(
                row,
                "FROZEN_TARGET_REACHED_BEFORE_EXECUTABLE_ENTRY",
            )
            return
        if not self._frozen_target_pool_is_active(armed):
            self.diagnostics["frozen_target_source_expired_before_entry"] += 1
            self._expire_armed_entry(
                row,
                "FROZEN_TARGET_SOURCE_NO_LONGER_ACTIVE_BEFORE_ENTRY",
            )
            return
        super()._resolve_entry_path(row)

    def _submit_retest_response(
        self,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
        *,
        flow_15s: float,
        depth_imbalance: float,
    ) -> bool:
        side = armed.setup.side
        observed_price = self.instrument.make_price(float(row["close"]))
        observed = _as_float(observed_price)
        stop_price = self.instrument.make_price(armed.stop)
        stop = _as_float(stop_price)
        if (side > 0 and not stop < observed) or (side < 0 and not observed < stop):
            self.diagnostics["frozen_target_retest_geometry_rejections"] += 1
            self._expire_armed_entry(row, "INVALID_RETEST_RESPONSE_STOP_GEOMETRY")
            return False

        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        raw_limit = slippage_protected_marketable_limit(
            observed_price=observed,
            side=side,
            adverse_slippage_rate=slippage_rate,
            price_increment=_as_float(self.instrument.price_increment),
        )
        if not math.isfinite(raw_limit):
            self.diagnostics["frozen_target_retest_geometry_rejections"] += 1
            self._expire_armed_entry(row, "INVALID_RETEST_RESPONSE_LIMIT")
            return False

        entry_price = self.instrument.make_price(raw_limit)
        entry = _as_float(entry_price)
        if not has_adverse_slippage_room(
            observed_price=observed,
            limit_price=entry,
            side=side,
            adverse_slippage_rate=slippage_rate,
        ):
            self.diagnostics["frozen_target_retest_geometry_rejections"] += 1
            self._expire_armed_entry(row, "RETEST_RESPONSE_LIMIT_LACKS_SLIPPAGE_ROOM")
            return False

        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        target = self._frozen_target_price(armed)
        target_source = str(armed.details.get("frozen_target_source", ""))
        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        rounded_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if (
            not math.isfinite(planned_loss)
            or planned_loss <= 0.0
            or rounded_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R
            or (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["frozen_target_retest_geometry_rejections"] += 1
            self._expire_armed_entry(
                row,
                "FROZEN_TARGET_NO_LONGER_MONETIZABLE_AT_RETEST_RESPONSE",
            )
            return False

        armed.details.update(
            {
                "retest_response_close": observed,
                "retest_response_flow_15s": flow_15s,
                "retest_response_depth_imbalance_1": depth_imbalance,
                "retest_response_directional_flow_15s": side * flow_15s,
                "retest_response_directional_depth": side * depth_imbalance,
                "frozen_target_net_r_at_response": rounded_r,
            },
        )
        self._transition(
            armed.setup.scenario_id,
            "CHOCH_RETEST_RESPONSE_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "RETEST_RESPONSE_EXECUTION",
            "RETEST_RECLAIMED_AND_ORIGINAL_LIQUIDITY_DESTINATION_REMAINS_VALID",
            observed,
            {
                **armed.details,
                "raw_slippage_protected_limit": raw_limit,
                "rounded_slippage_protected_limit": entry,
                "target": target,
                "target_source": target_source,
                "rounded_target_net_r": rounded_r,
            },
        )
        submitted = self._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=rounded_r,
            branch="TAIL_FLOW_CONFIRMED_RETRACE",
            event_type="RETEST_RESPONSE_MARKETABLE_LIMIT_SUBMITTED",
            reason="CURRENT_RETEST_RESPONSE_TO_FROZEN_CHOCH_TARGET",
            expires_index=self.bar_index + 2,
            entry_tag="CONFIRMED_CHOCH_RETEST_RESPONSE_ENTRY",
            extra={
                "observed_retest_response_price": observed,
                "raw_slippage_protected_limit": raw_limit,
                "rounded_slippage_protected_limit": entry,
                "rounded_target_net_r": rounded_r,
            },
        )
        if submitted:
            self.diagnostics["retest_response_submissions"] += 1
        return submitted

    def _submit_breakaway_limit(
        self,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
    ) -> bool:
        side = armed.setup.side
        observed_entry = _as_float(self.instrument.make_price(float(row["close"])))
        stop = armed.stop
        target = self._frozen_target_price(armed)
        target_source = str(armed.details.get("frozen_target_source", ""))
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        raw_bound = worst_entry_preserving_net_r(
            stop=stop,
            target=target,
            side=side,
            minimum_net_r=MIN_LIQUIDITY_TARGET_NET_R,
            cost_rate=cost_rate,
            adverse_slippage_rate=slippage_rate,
        )
        if not math.isfinite(raw_bound):
            self.diagnostics["frozen_target_breakaway_geometry_rejections"] += 1
            self._expire_armed_entry(row, "FROZEN_TARGET_BREAKAWAY_HAS_NO_VALID_ENTRY")
            return False

        price_increment = _as_float(self.instrument.price_increment)
        entry_price = self.instrument.make_price(raw_bound)
        entry = _as_float(entry_price)
        if side > 0 and entry > raw_bound:
            entry_price = self.instrument.make_price(raw_bound - price_increment)
            entry = _as_float(entry_price)
        elif side < 0 and entry < raw_bound:
            entry_price = self.instrument.make_price(raw_bound + price_increment)
            entry = _as_float(entry_price)
        if not has_adverse_slippage_room(
            observed_price=observed_entry,
            limit_price=entry,
            side=side,
            adverse_slippage_rate=slippage_rate,
        ):
            self.diagnostics["frozen_target_breakaway_geometry_rejections"] += 1
            self._expire_armed_entry(
                row,
                "BREAKAWAY_PRICE_EXCEEDED_FROZEN_TARGET_ENTRY_BOUND",
            )
            return False

        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        rounded_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if (
            not math.isfinite(planned_loss)
            or planned_loss <= 0.0
            or rounded_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R
            or (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["frozen_target_breakaway_geometry_rejections"] += 1
            self._expire_armed_entry(
                row,
                "FROZEN_TARGET_INVALID_AFTER_BREAKAWAY_PRICE_ROUNDING",
            )
            return False

        stop_price = self.instrument.make_price(stop)
        self.diagnostics["breakaway_limit_submissions"] += 1
        return self._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=rounded_r,
            branch="TAIL_FLOW_LIQUIDITY_BREAKAWAY",
            event_type="PRICE_PROTECTED_BREAKAWAY_LIMIT_SUBMITTED",
            reason="NO_RETRACE_EXTENSION_TO_FROZEN_CHOCH_TARGET",
            expires_index=self.bar_index + 2,
            entry_tag="PRICE_PROTECTED_BREAKAWAY_ENTRY",
            extra={
                "observed_breakaway_price": observed_entry,
                "raw_maximum_acceptable_entry": raw_bound,
                "rounded_maximum_acceptable_entry": entry,
                "rounded_target_net_r": rounded_r,
                "breakaway_extension_atr": (
                    side * (float(row["close"]) - armed.choch_close) / armed.atr
                ),
                "breakaway_flow_3m": self._feature("flow_3m"),
                "breakaway_depth_imbalance_1": self._feature("depth_imbalance_1"),
            },
        )

    def _submit_price_capped_bracket(
        self,
        *,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
        entry_price: Any,
        stop_price: Any,
        target_price: Any,
        sizing_entry: float,
        planned_loss: float,
        target_source: str,
        target_r: float,
        branch: str,
        event_type: str,
        reason: str,
        expires_index: int,
        entry_tag: str,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        if (
            branch not in self._FROZEN_BRANCHES
            or "frozen_target_price" not in armed.details
        ):
            return super()._submit_price_capped_bracket(
                armed=armed,
                row=row,
                entry_price=entry_price,
                stop_price=stop_price,
                target_price=target_price,
                sizing_entry=sizing_entry,
                planned_loss=planned_loss,
                target_source=target_source,
                target_r=target_r,
                branch=branch,
                event_type=event_type,
                reason=reason,
                expires_index=expires_index,
                entry_tag=entry_tag,
                extra=extra,
            )

        entry = _as_float(entry_price)
        target = _as_float(target_price)
        milestone = self._revalidated_frozen_milestone(
            armed=armed,
            entry=entry,
            target=target,
        )
        enriched_extra = {
            **(extra or {}),
            "scenario_target_policy": "FROZEN_AT_CHOCH",
            "frozen_target_price": target,
            "frozen_target_source": target_source,
            "frozen_target_net_r_at_execution": target_r,
            "protection_mode": "SOFTWARE_COMPLETED_BAR_INVALIDATION",
            "protection_pool_id": None if milestone is None else milestone[0],
            "protection_milestone": None if milestone is None else milestone[1],
            "protection_stop": None if milestone is None else milestone[2],
            "protection_expected_net_per_unit": None if milestone is None else milestone[3],
        }

        # Bypass v12's dynamic milestone selection only for the two frozen-target
        # observation branches. The base v9 submitter still owns the exact order,
        # quantity, bracket, diagnostics and state transitions. All other branches
        # continue through the unchanged v15/v12 chain above.
        submitted = ObservedEntryPathStrategy._submit_price_capped_bracket(
            self,
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=sizing_entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=target_r,
            branch=branch,
            event_type=event_type,
            reason=reason,
            expires_index=expires_index,
            entry_tag=entry_tag,
            extra=enriched_extra,
        )
        if not submitted:
            return False

        self._clear_protection_state()
        if milestone is None:
            return True
        pool_id, level, protected_stop, expected_net = milestone
        self.protection_pool_id = pool_id
        self.protection_milestone = level
        self.protection_stop = protected_stop
        self.protection_expected_net = expected_net
        self.protection_armed = False
        self.protection_armed_index = -1
        self.diagnostics["protectable_milestones"] += 1
        self.diagnostics["frozen_milestones_revalidated"] += 1
        return True

    def _revalidated_frozen_milestone(
        self,
        *,
        armed: ArmedEntryPath,
        entry: float,
        target: float,
    ) -> tuple[str, float, float, float] | None:
        pool_id = armed.details.get("frozen_milestone_pool_id")
        level_value = armed.details.get("frozen_milestone_level")
        if pool_id is None or level_value is None:
            return None
        if str(pool_id) not in self.active_pools:
            self.diagnostics["frozen_milestones_no_longer_protectable"] += 1
            return None

        level = float(level_value)
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        result = revalidate_frozen_milestone(
            side=armed.setup.side,
            entry=entry,
            target=target,
            milestone=level,
            atr=armed.atr,
            stop_buffer_atr=self.config.stop_buffer_atr,
            cost_rate=cost_rate,
            adverse_slippage_rate=slippage_rate,
        )
        if result is None:
            self.diagnostics["frozen_milestones_no_longer_protectable"] += 1
            return None
        raw_stop, _raw_expected_net = result
        protected_stop = _as_float(self.instrument.make_price(raw_stop))
        expected_exit = protected_stop * (
            1.0 - armed.setup.side * slippage_rate
        )
        expected_net = armed.setup.side * (expected_exit - entry) - cost_rate * (
            entry + expected_exit
        )
        if expected_net <= 0.0:
            self.diagnostics["frozen_milestones_no_longer_protectable"] += 1
            return None
        return str(pool_id), level, protected_stop, expected_net

    def _frozen_target_price(self, armed: ArmedEntryPath) -> float:
        try:
            value = float(armed.details.get("frozen_target_price", float("nan")))
        except (TypeError, ValueError):
            return float("nan")
        return value if math.isfinite(value) else float("nan")

    def _frozen_target_pool_is_active(self, armed: ArmedEntryPath) -> bool:
        pool_id = armed.details.get("frozen_target_pool_id")
        return pool_id is not None and str(pool_id) in self.active_pools


__all__ = ["FrozenScenarioTargetStrategy"]
