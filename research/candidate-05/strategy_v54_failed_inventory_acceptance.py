#!/usr/bin/env python3
"""Candidate 05 v54: failed strict inventory reversal becomes true acceptance.

v46 remains authoritative for genuine failed-auction reversals.  v54 adds the
mutually exclusive complement: only when a strict external inventory trap is
structurally invalidated before CHoCH may the original sweep direction become a
continuation candidate.  Price, aggressor flow, price efficiency and withdrawal
of liquidity ahead must confirm acceptance, followed by the first defended
retest.  Ambiguous states remain no-trade.

The branch inherits v46 order handling, fees, slippage, live-liquidity targets,
three-percent current-NAV sizing and NautilusTrader execution/accounting.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from external_acceptance_retest_logic import external_level_structural_stop
from external_acceptance_retest_logic import first_accepted_level_retest_response
from failed_inventory_acceptance_logic import accepted_level_closed_back_inside
from failed_inventory_acceptance_logic import accepted_level_retest_touched
from failed_inventory_acceptance_logic import failed_inventory_acceptance_ready
from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import has_adverse_slippage_room
from logic import choose_liquidity_target
from logic import net_r_at_price
from logic import planned_loss_per_unit
from sponsored_choch_logic import slippage_protected_marketable_limit
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy


_BRANCH = "FAILED_INVENTORY_ACCEPTANCE_CONTINUATION"
_REVERSAL_FAILURE_REASONS = {
    "SWEEP_EXTREME_INVALIDATED_BEFORE_CHOCH",
    "REJECTION_EXTREME_ACCEPTED",
}


@dataclass(slots=True)
class FailedInventoryAcceptanceWatch:
    scenario_id: str
    parent_scenario_id: str
    side: int
    level: float
    atr: float
    created_index: int
    created_ts: int
    expires_index: int
    phase: str
    accepted_index: int
    details: dict[str, Any]


class FailedInventoryAcceptanceStrategy(NoPostRetraceBreakawayStrategy):
    """Route a failed strict reversal to one causal acceptance/retest attempt."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.failed_inventory_acceptance_watches: dict[
            str,
            FailedInventoryAcceptanceWatch,
        ] = {}
        self.failed_inventory_acceptance_counter = 0
        self.diagnostics.update(
            {
                "failed_inventory_acceptance_watches": 0,
                "failed_inventory_acceptance_confirmations": 0,
                "failed_inventory_acceptance_first_retests": 0,
                "failed_inventory_acceptance_retest_rejections": 0,
                "failed_inventory_acceptance_range_reentries": 0,
                "failed_inventory_acceptance_expiries": 0,
                "failed_inventory_acceptance_slot_conflicts": 0,
                "failed_inventory_acceptance_no_live_target": 0,
                "failed_inventory_acceptance_geometry_rejections": 0,
                "failed_inventory_acceptance_submissions": 0,
            },
        )

    def on_bar(self, bar: Any) -> None:
        super().on_bar(bar)
        if not self.bars or not self.failed_inventory_acceptance_watches:
            return
        row = self.bars[-1]
        self._advance_failed_inventory_acceptance_watches(row)

    def _expire_pending(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        setup = self.pending
        if (
            setup is not None
            and reason in _REVERSAL_FAILURE_REASONS
            and bool(setup.details.get("strict_external_inventory_confirmed", False))
        ):
            self._arm_failed_inventory_acceptance_watch(setup, row, reason)
        super()._expire_pending(row, reason)

    def _arm_failed_inventory_acceptance_watch(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        self.failed_inventory_acceptance_counter += 1
        scenario_id = f"fia-{self.failed_inventory_acceptance_counter:07d}"
        continuation_side = -int(setup.side)
        level = float(setup.sweep_extreme)
        details = {
            **setup.details,
            "branch": _BRANCH,
            "parent_scenario_id": setup.scenario_id,
            "parent_reversal_side": int(setup.side),
            "continuation_side": continuation_side,
            "failed_reversal_reason": reason,
            "accepted_level": level,
            "causal_origin": "STRICT_EXTERNAL_INVENTORY_TRAP_FAILED_BEFORE_CHOCH",
        }
        watch = FailedInventoryAcceptanceWatch(
            scenario_id=scenario_id,
            parent_scenario_id=setup.scenario_id,
            side=continuation_side,
            level=level,
            atr=float(setup.atr),
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            expires_index=self.bar_index + self.config.acceptance_retrace_bars,
            phase="AWAIT_ACCEPTANCE",
            accepted_index=-1,
            details=details,
        )
        self.failed_inventory_acceptance_watches[scenario_id] = watch
        self.diagnostics["failed_inventory_acceptance_watches"] += 1
        self._transition(
            scenario_id,
            "FAILED_INVENTORY_ACCEPTANCE_WATCH_ARMED",
            int(row["ts"]),
            int(row["ts"]),
            "AWAIT_ACCEPTANCE",
            "STRICT_INVENTORY_REVERSAL_FAILED_BEFORE_CHOCH",
            level,
            details,
        )

    def _advance_failed_inventory_acceptance_watches(
        self,
        row: dict[str, float | int],
    ) -> None:
        for watch in sorted(
            tuple(self.failed_inventory_acceptance_watches.values()),
            key=lambda item: (item.created_index, item.scenario_id),
        ):
            if watch.scenario_id not in self.failed_inventory_acceptance_watches:
                continue
            if self.bar_index > watch.expires_index:
                self.diagnostics["failed_inventory_acceptance_expiries"] += 1
                self._close_failed_inventory_watch(
                    watch,
                    row,
                    "FAILED_INVENTORY_ACCEPTANCE_WINDOW_EXPIRED",
                )
                continue
            if not self._in_evaluation(int(row["ts"])):
                self._close_failed_inventory_watch(
                    watch,
                    row,
                    "EVALUATION_ENDED_WITH_ACCEPTANCE_UNRESOLVED",
                )
                continue
            if self._funding_blackout(int(row["ts"])):
                self._close_failed_inventory_watch(
                    watch,
                    row,
                    "FUNDING_BLACKOUT_WITH_ACCEPTANCE_UNRESOLVED",
                )
                continue

            if watch.phase == "AWAIT_ACCEPTANCE":
                # Price-only observation may continue through a depth gap, but
                # no acceptance can be confirmed without the full feature state.
                if not self._features_ready(int(row["ts"])):
                    continue
                ready = failed_inventory_acceptance_ready(
                    side=watch.side,
                    level=watch.level,
                    open_price=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    atr=watch.atr,
                    flow_15s=self._feature("flow_15s"),
                    flow_60s=self._feature("flow_60s"),
                    efficiency_60s=self._feature("efficiency_60s"),
                    bid_depth_change_1m=self._feature("bid_depth_change_1_1m"),
                    ask_depth_change_1m=self._feature("ask_depth_change_1_1m"),
                    minimum_close_atr=self.config.acceptance_close_atr,
                    minimum_flow=self.config.acceptance_flow_min,
                    minimum_efficiency=self.config.acceptance_efficiency_min,
                    minimum_depth_withdrawal=self.config.acceptance_depth_withdrawal_min,
                    minimum_close_location=self.config.acceptance_close_location,
                )
                if not ready:
                    continue
                watch.phase = "FIRST_RETEST"
                watch.accepted_index = self.bar_index
                watch.details.update(
                    {
                        "acceptance_ts": int(row["ts"]),
                        "acceptance_close": float(row["close"]),
                        "acceptance_flow_15s": self._feature("flow_15s"),
                        "acceptance_flow_60s": self._feature("flow_60s"),
                        "acceptance_efficiency_60s": self._feature("efficiency_60s"),
                        "acceptance_bid_depth_change_1m": self._feature(
                            "bid_depth_change_1_1m",
                        ),
                        "acceptance_ask_depth_change_1m": self._feature(
                            "ask_depth_change_1_1m",
                        ),
                    },
                )
                self.diagnostics["failed_inventory_acceptance_confirmations"] += 1
                self._transition(
                    watch.scenario_id,
                    "FAILED_INVENTORY_TRUE_ACCEPTANCE_CONFIRMED",
                    int(row["ts"]),
                    int(row["ts"]),
                    "FIRST_RETEST",
                    "PRICE_FLOW_EFFICIENCY_AND_AHEAD_LIQUIDITY_WITHDRAWAL_AGREED",
                    watch.level,
                    watch.details,
                )
                continue

            if self.bar_index <= watch.accepted_index:
                continue
            touched = accepted_level_retest_touched(
                side=watch.side,
                level=watch.level,
                high=float(row["high"]),
                low=float(row["low"]),
            )
            if not touched:
                if accepted_level_closed_back_inside(
                    side=watch.side,
                    level=watch.level,
                    close=float(row["close"]),
                ):
                    self.diagnostics["failed_inventory_acceptance_range_reentries"] += 1
                    self._close_failed_inventory_watch(
                        watch,
                        row,
                        "ACCEPTED_LEVEL_REENTERED_BEFORE_FIRST_DEFENDED_RETEST",
                    )
                continue

            self.diagnostics["failed_inventory_acceptance_first_retests"] += 1
            response = (
                self._features_ready(int(row["ts"]))
                and first_accepted_level_retest_response(
                    side=watch.side,
                    level=watch.level,
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    flow_15s=self._feature("flow_15s"),
                    depth_imbalance=self._feature("depth_imbalance_1"),
                    maximum_counterflow=self.config.acceptance_max_counterflow,
                )
            )
            if not response:
                self.diagnostics["failed_inventory_acceptance_retest_rejections"] += 1
                self._close_failed_inventory_watch(
                    watch,
                    row,
                    "FIRST_ACCEPTED_LEVEL_RETEST_LACKED_FLOW_AND_DEPTH_DEFENSE",
                )
                continue
            if not self._failed_inventory_entry_slot_idle():
                self.diagnostics["failed_inventory_acceptance_slot_conflicts"] += 1
                self._close_failed_inventory_watch(
                    watch,
                    row,
                    "GLOBAL_ENTRY_SLOT_OCCUPIED_AT_FIRST_DEFENDED_RETEST",
                )
                continue
            self._submit_failed_inventory_acceptance(watch, row)

    def _failed_inventory_entry_slot_idle(self) -> bool:
        return (
            self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and not self.exit_pending
            and self.pending is None
            and self.armed_entry_path is None
            and not bool(getattr(self, "counter_context_parent_lock_active", False))
        )

    def _submit_failed_inventory_acceptance(
        self,
        watch: FailedInventoryAcceptanceWatch,
        row: dict[str, float | int],
    ) -> bool:
        side = watch.side
        observed = float(row["close"])
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        raw_entry = slippage_protected_marketable_limit(
            observed_price=observed,
            side=side,
            adverse_slippage_rate=slippage_rate,
            price_increment=_as_float(self.instrument.price_increment),
        )
        entry_price = self.instrument.make_price(raw_entry)
        entry = _as_float(entry_price)
        if not has_adverse_slippage_room(
            observed_price=observed,
            limit_price=entry,
            side=side,
            adverse_slippage_rate=slippage_rate,
        ):
            self.diagnostics["failed_inventory_acceptance_geometry_rejections"] += 1
            self._close_failed_inventory_watch(
                watch,
                row,
                "MARKETABLE_LIMIT_DID_NOT_PRESERVE_ADVERSE_SLIPPAGE_ROOM",
            )
            return False

        raw_stop = external_level_structural_stop(
            side=side,
            level=watch.level,
            atr=watch.atr,
            stop_buffer_atr=self.config.stop_buffer_atr,
        )
        stop_price = self.instrument.make_price(raw_stop)
        stop = _as_float(stop_price)
        if (side > 0 and not stop < entry) or (side < 0 and not entry < stop):
            self.diagnostics["failed_inventory_acceptance_geometry_rejections"] += 1
            self._close_failed_inventory_watch(
                watch,
                row,
                "INVALID_ACCEPTED_LEVEL_STOP_GEOMETRY",
            )
            return False
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["failed_inventory_acceptance_geometry_rejections"] += 1
            self._close_failed_inventory_watch(
                watch,
                row,
                "INVALID_ACCEPTED_LEVEL_PLANNED_LOSS",
            )
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
            self.diagnostics["failed_inventory_acceptance_no_live_target"] += 1
            self._close_failed_inventory_watch(
                watch,
                row,
                "NO_STILL_LIVE_OPPOSING_LIQUIDITY_TARGET",
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
            self.diagnostics["failed_inventory_acceptance_geometry_rejections"] += 1
            self._close_failed_inventory_watch(
                watch,
                row,
                "ROUNDED_ACCEPTANCE_CONTINUATION_BRACKET_INVALID",
            )
            return False

        details = {
            **watch.details,
            "retest_ts": int(row["ts"]),
            "retest_close": observed,
            "retest_flow_15s": self._feature("flow_15s"),
            "retest_depth_imbalance_1": self._feature("depth_imbalance_1"),
            "entry_limit": entry,
            "stop": stop,
            "target": target,
            "target_source": target_source,
            "target_net_r": rounded_r,
        }
        setup = PendingSetup(
            scenario_id=watch.scenario_id,
            branch=_BRANCH,
            side=side,
            swept_kind="HIGH" if side > 0 else "LOW",
            pool_id=f"failed-inventory-level-{watch.parent_scenario_id}",
            pool_level=watch.level,
            created_index=watch.accepted_index,
            expires_index=self.bar_index + 2,
            sweep_extreme=watch.level,
            structure=watch.level,
            atr=watch.atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="FAILED_INVENTORY_TRUE_ACCEPTANCE",
            choch_close=entry,
            stop=stop,
            atr=watch.atr,
            created_index=watch.accepted_index,
            created_ts=int(row["ts"]),
            details=details,
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
            branch=_BRANCH,
            event_type="FAILED_INVENTORY_ACCEPTANCE_LIMIT_SUBMITTED",
            reason="FIRST_DEFENDED_RETEST_AFTER_FAILED_REVERSAL_BECAME_TRUE_ACCEPTANCE",
            expires_index=self.bar_index + 2,
            entry_tag="FAILED_INVENTORY_ACCEPTANCE_ENTRY",
            extra=details,
        )
        if submitted:
            self.failed_inventory_acceptance_watches.pop(watch.scenario_id, None)
            self.diagnostics["failed_inventory_acceptance_submissions"] += 1
            return True
        self._close_failed_inventory_watch(
            watch,
            row,
            "FAILED_INVENTORY_ACCEPTANCE_BRACKET_SUBMISSION_FAILED",
        )
        return False

    def _close_failed_inventory_watch(
        self,
        watch: FailedInventoryAcceptanceWatch,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        if watch.scenario_id not in self.failed_inventory_acceptance_watches:
            return
        self._transition(
            watch.scenario_id,
            "FAILED_INVENTORY_ACCEPTANCE_CLOSED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            watch.details,
        )
        self.failed_inventory_acceptance_watches.pop(watch.scenario_id, None)


LiquidityResponseStrategy = FailedInventoryAcceptanceStrategy

__all__ = [
    "FailedInventoryAcceptanceStrategy",
    "FailedInventoryAcceptanceWatch",
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
]
