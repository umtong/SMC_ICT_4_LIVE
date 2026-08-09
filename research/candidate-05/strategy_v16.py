#!/usr/bin/env python3
"""Candidate 05 v16: first retest of position-building balance acceptance."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from balance_acceptance_logic import BALANCE_ACCEPTANCE_HOLD_BARS
from balance_acceptance_logic import balance_retest_confirms_acceptance
from balance_acceptance_logic import closes_outside_balance
from balance_acceptance_logic import depth_migration_sponsors_acceptance
from balance_breakout_logic import BALANCE_BARS
from balance_breakout_logic import BalanceBreakoutEvidence
from balance_breakout_logic import balance_metrics
from balance_breakout_logic import breakout_side
from depth_logic import DIRECTIONAL_DEPTH_MIN
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import has_adverse_slippage_room
from logic import choose_liquidity_target
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from sponsored_choch_logic import slippage_protected_marketable_limit
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v15 import SponsoredChochFallbackStrategy


@dataclass(slots=True)
class BalanceAcceptanceWatch:
    scenario_id: str
    side: int
    balance_high: float
    balance_low: float
    breakout_high: float
    breakout_low: float
    breakout_close: float
    atr: float
    created_index: int
    expires_index: int
    hold_count: int
    phase: str
    details: dict[str, Any]


class PositionBuildingBalanceAcceptanceStrategy(SponsoredChochFallbackStrategy):
    """Add a distinct continuation auction without relaxing reversal filters.

    The deprecated v7 bought the breakout close immediately. Its first two
    events both reentered the old balance and lost. v16 retains only the useful
    observation: a low-efficiency balance followed by an OI-sponsored expansion.

    A continuation trade now requires a different causal sequence:

    1. the existing balance-expansion predicate passes;
    2. same-side depth replenishes while opposing depth withdraws and the book
       meets Candidate 05's existing 0.10 directional-depth minimum;
    3. three completed one-minute closes remain outside the balance, matching
       the already-observed three-minute flow horizon;
    4. the first retest touches the old boundary but closes back outside with
       aligned final-15-second flow, supportive depth, and a favorable close;
    5. a bounded marketable limit, structural range-reentry stop, actual live
       opposing-liquidity target, costs, slippage and 3% current-NAV sizing all
       remain valid.

    This observer runs after the unchanged v15 state machine. It never blocks a
    v15 setup and may submit only while the global entry/position slot is idle.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.balance_acceptance_watch: BalanceAcceptanceWatch | None = None
        self.diagnostics.update(
            {
                "balance_acceptance_observations": 0,
                "balance_acceptance_depth_migration_rejections": 0,
                "balance_acceptance_watches": 0,
                "balance_acceptance_early_reentries": 0,
                "balance_acceptance_holds": 0,
                "balance_acceptance_retests": 0,
                "balance_acceptance_retest_rejections": 0,
                "balance_acceptance_slot_conflicts": 0,
                "balance_acceptance_no_live_target": 0,
                "balance_acceptance_cost_geometry_rejections": 0,
                "balance_acceptance_submissions": 0,
                "balance_acceptance_expired": 0,
            },
        )

    def on_bar(self, bar: Any) -> None:
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        ts = int(row["ts"])
        if not self._in_evaluation(ts) or not self._features_ready(ts):
            return
        if self._funding_blackout(ts):
            if self.balance_acceptance_watch is not None:
                self._expire_balance_watch(row, "FUNDING_BLACKOUT_DURING_BALANCE_ACCEPTANCE")
            return

        had_watch = self.balance_acceptance_watch is not None
        if had_watch:
            self._advance_balance_acceptance(row)
            return
        self._detect_position_building_balance(row)

    def _metrics_ready_for_balance(self) -> bool:
        if self.current_feature is None:
            return False
        value = self.current_feature.get("metrics_ready", False)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes"}

    def _detect_position_building_balance(self, row: dict[str, float | int]) -> None:
        if not self._metrics_ready_for_balance():
            return
        if len(self.bars) < max(self.config.atr_period + 1, BALANCE_BARS + 1):
            return
        if self.bar_index - self.last_entry_index < self.config.cooldown_bars:
            return

        rows = list(self.bars)
        balance = rows[-(BALANCE_BARS + 1) : -1]
        if len(balance) != BALANCE_BARS:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        balance_high = max(float(item["high"]) for item in balance)
        balance_low = min(float(item["low"]) for item in balance)
        balance_closes = tuple(float(item["close"]) for item in balance)
        evidence = BalanceBreakoutEvidence(
            open_price=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            atr=atr,
            balance_high=balance_high,
            balance_low=balance_low,
            balance_closes=balance_closes,
            flow_15s=self._feature("flow_15s"),
            flow_60s=self._feature("flow_60s"),
            flow_3m=self._feature("flow_3m"),
            efficiency_60s=self._feature("efficiency_60s"),
            notional_burst=self._feature("notional_burst"),
            depth_imbalance_1=self._feature("depth_imbalance_1"),
            bid_depth_change_5m=self._feature("bid_depth_change_1_5m"),
            ask_depth_change_5m=self._feature("ask_depth_change_1_5m"),
            oi_change_15m=self._feature("oi_change_15m"),
            metrics_ready=True,
        )
        self.diagnostics["balance_acceptance_observations"] += 1
        side = breakout_side(evidence)
        if side == 0:
            return
        if not depth_migration_sponsors_acceptance(
            side=side,
            depth_imbalance=evidence.depth_imbalance_1,
            bid_depth_change_5m=evidence.bid_depth_change_5m,
            ask_depth_change_5m=evidence.ask_depth_change_5m,
            minimum_directional_depth=DIRECTIONAL_DEPTH_MIN,
        ):
            self.diagnostics["balance_acceptance_depth_migration_rejections"] += 1
            return

        self.scenario_counter += 1
        scenario_id = f"pba-{self.scenario_counter:07d}"
        metrics = balance_metrics(
            balance_high=balance_high,
            balance_low=balance_low,
            atr=atr,
            closes=balance_closes,
        )
        details = {
            "side": side,
            "balance_start_ns": int(balance[0]["ts"]),
            "balance_end_ns": int(balance[-1]["ts"]),
            "balance_high": balance_high,
            "balance_low": balance_low,
            "balance_range_atr": metrics["range_atr"],
            "balance_efficiency": metrics["efficiency"],
            "breakout_open": float(row["open"]),
            "breakout_high": float(row["high"]),
            "breakout_low": float(row["low"]),
            "breakout_close": float(row["close"]),
            "flow_15s": evidence.flow_15s,
            "flow_60s": evidence.flow_60s,
            "flow_3m": evidence.flow_3m,
            "efficiency_60s": evidence.efficiency_60s,
            "notional_burst": evidence.notional_burst,
            "depth_imbalance_1": evidence.depth_imbalance_1,
            "bid_depth_change_1_5m": evidence.bid_depth_change_5m,
            "ask_depth_change_1_5m": evidence.ask_depth_change_5m,
            "oi_change_15m": evidence.oi_change_15m,
            "metrics_age_seconds": self._feature("metrics_age_seconds"),
        }
        self.balance_acceptance_watch = BalanceAcceptanceWatch(
            scenario_id=scenario_id,
            side=side,
            balance_high=balance_high,
            balance_low=balance_low,
            breakout_high=float(row["high"]),
            breakout_low=float(row["low"]),
            breakout_close=float(row["close"]),
            atr=atr,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.acceptance_retrace_bars,
            hold_count=0,
            phase="HOLD_OUTSIDE",
            details=details,
        )
        self.diagnostics["balance_acceptance_watches"] += 1
        self._transition(
            scenario_id,
            "POSITION_BUILDING_BALANCE_EXPANSION_OBSERVED",
            int(row["ts"]),
            int(row["ts"]),
            "HOLD_OUTSIDE",
            "OI_FLOW_AND_DEPTH_MIGRATION_SPONSORED_BALANCE_BREAK",
            float(row["close"]),
            details,
        )

    def _advance_balance_acceptance(self, row: dict[str, float | int]) -> None:
        watch = self.balance_acceptance_watch
        if watch is None or self.bar_index <= watch.created_index:
            return
        if self.bar_index > watch.expires_index:
            self._expire_balance_watch(row, "BALANCE_ACCEPTANCE_RETEST_WINDOW_EXPIRED")
            return

        outside = closes_outside_balance(
            side=watch.side,
            close=float(row["close"]),
            balance_high=watch.balance_high,
            balance_low=watch.balance_low,
        )
        if watch.phase == "HOLD_OUTSIDE":
            if not outside:
                self.diagnostics["balance_acceptance_early_reentries"] += 1
                self._expire_balance_watch(
                    row,
                    "BALANCE_REENTERED_BEFORE_THREE_MINUTE_ACCEPTANCE",
                )
                return
            watch.hold_count += 1
            if watch.hold_count >= BALANCE_ACCEPTANCE_HOLD_BARS:
                watch.phase = "FIRST_RETEST"
                self.diagnostics["balance_acceptance_holds"] += 1
                self._transition(
                    watch.scenario_id,
                    "THREE_MINUTE_BALANCE_ACCEPTANCE_CONFIRMED",
                    int(row["ts"]),
                    int(row["ts"]),
                    "FIRST_RETEST",
                    "THREE_COMPLETED_CLOSES_HELD_OUTSIDE_POSITION_BUILDING_BALANCE",
                    float(row["close"]),
                    {**watch.details, "hold_count": watch.hold_count},
                )
            return

        boundary = watch.balance_high if watch.side > 0 else watch.balance_low
        touched = (
            float(row["low"]) <= boundary + self.config.acceptance_retrace_tolerance_atr * watch.atr
            if watch.side > 0
            else float(row["high"]) >= boundary - self.config.acceptance_retrace_tolerance_atr * watch.atr
        )
        if not touched:
            outside_distance = watch.side * (float(row["close"]) - boundary) / watch.atr
            if outside_distance < -self.config.acceptance_hold_tolerance_atr:
                self._expire_balance_watch(row, "ACCEPTED_BALANCE_BROKE_BACK_INSIDE")
            return

        self.diagnostics["balance_acceptance_retests"] += 1
        ready = balance_retest_confirms_acceptance(
            side=watch.side,
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            balance_high=watch.balance_high,
            balance_low=watch.balance_low,
            atr=watch.atr,
            flow_15s=self._feature("flow_15s"),
            depth_imbalance=self._feature("depth_imbalance_1"),
            retrace_tolerance_atr=self.config.acceptance_retrace_tolerance_atr,
            minimum_close_location=self.config.acceptance_retest_close_location,
            minimum_directional_depth=DIRECTIONAL_DEPTH_MIN,
        )
        if not ready:
            self.diagnostics["balance_acceptance_retest_rejections"] += 1
            self._expire_balance_watch(
                row,
                "FIRST_RETEST_DID_NOT_RECLAIM_WITH_FLOW_AND_DEPTH",
            )
            return
        if not self._balance_entry_slot_idle():
            self.diagnostics["balance_acceptance_slot_conflicts"] += 1
            self._expire_balance_watch(row, "GLOBAL_ENTRY_SLOT_OCCUPIED_AT_CONFIRMED_RETEST")
            return
        self._submit_balance_acceptance(watch, row)

    def _balance_entry_slot_idle(self) -> bool:
        return (
            self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and not self.exit_pending
            and self.pending is None
            and self.armed_entry_path is None
        )

    def _submit_balance_acceptance(
        self,
        watch: BalanceAcceptanceWatch,
        row: dict[str, float | int],
    ) -> bool:
        side = watch.side
        observed = float(row["close"])
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        raw_limit = slippage_protected_marketable_limit(
            observed_price=observed,
            side=side,
            adverse_slippage_rate=slippage_rate,
            price_increment=_as_float(self.instrument.price_increment),
        )
        entry_price = self.instrument.make_price(raw_limit)
        entry = _as_float(entry_price)
        boundary = watch.balance_high if side > 0 else watch.balance_low
        stop_price = self.instrument.make_price(
            boundary - side * self.config.stop_buffer_atr * watch.atr,
        )
        stop = _as_float(stop_price)
        if (
            not math.isfinite(entry)
            or not has_adverse_slippage_room(
                observed_price=observed,
                limit_price=entry,
                side=side,
                adverse_slippage_rate=slippage_rate,
            )
            or (side > 0 and not stop < entry)
            or (side < 0 and not entry < stop)
        ):
            self.diagnostics["balance_acceptance_cost_geometry_rejections"] += 1
            self._expire_balance_watch(row, "INVALID_BALANCE_ACCEPTANCE_ENTRY_GEOMETRY")
            return False

        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["balance_acceptance_cost_geometry_rejections"] += 1
            self._expire_balance_watch(row, "INVALID_BALANCE_ACCEPTANCE_PLANNED_LOSS")
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
            self.diagnostics["balance_acceptance_no_live_target"] += 1
            self._expire_balance_watch(row, "NO_LIVE_LIQUIDITY_TARGET_AFTER_BALANCE_ACCEPTANCE")
            return False
        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        rounded_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if (
            rounded_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R
            or (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["balance_acceptance_cost_geometry_rejections"] += 1
            self._expire_balance_watch(row, "INVALID_BALANCE_ACCEPTANCE_TARGET_GEOMETRY")
            return False

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            self.diagnostics["balance_acceptance_cost_geometry_rejections"] += 1
            self._expire_balance_watch(row, "BALANCE_ACCEPTANCE_QUANTITY_BELOW_MINIMUM")
            return False

        details = {
            **watch.details,
            "hold_count": watch.hold_count,
            "retest_open": float(row["open"]),
            "retest_high": float(row["high"]),
            "retest_low": float(row["low"]),
            "retest_close": float(row["close"]),
            "retest_flow_15s": self._feature("flow_15s"),
            "retest_flow_3m": self._feature("flow_3m"),
            "retest_depth_imbalance_1": self._feature("depth_imbalance_1"),
            "position_building_balance_acceptance": True,
        }
        setup = PendingSetup(
            scenario_id=watch.scenario_id,
            branch="POSITION_BUILDING_BALANCE_ACCEPTANCE",
            side=side,
            swept_kind="LOW" if side > 0 else "HIGH",
            pool_id=target_source.split(":", 1)[1],
            pool_level=boundary,
            created_index=self.bar_index,
            expires_index=self.bar_index + 2,
            sweep_extreme=watch.breakout_low if side > 0 else watch.breakout_high,
            structure=boundary,
            atr=watch.atr,
            hold_count=watch.hold_count,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="POSITION_BUILDING_BALANCE_ACCEPTANCE",
            choch_close=observed,
            stop=stop,
            atr=watch.atr,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            details=details,
        )
        self.armed_entry_path = armed
        self.diagnostics["entry_path_armed"] += 1
        self._transition(
            watch.scenario_id,
            "POSITION_BUILDING_BALANCE_RETEST_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "BALANCE_ACCEPTANCE_ENTRY",
            "FIRST_RETEST_CLOSED_OUTSIDE_WITH_ALIGNED_FLOW_AND_DEPTH",
            observed,
            {
                **details,
                "slippage_protected_limit": entry,
                "stop": stop,
                "target": target,
                "target_source": target_source,
                "target_net_r": rounded_r,
                "preflight_quantity": quantity_value,
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
            target_r=target_r,
            branch="POSITION_BUILDING_BALANCE_ACCEPTANCE",
            event_type="BALANCE_ACCEPTANCE_MARKETABLE_LIMIT_SUBMITTED",
            reason="THREE_MINUTE_ACCEPTANCE_AND_FIRST_RETEST_WITH_LIVE_LIQUIDITY_TARGET",
            expires_index=self.bar_index + 2,
            entry_tag="POSITION_BUILDING_BALANCE_ACCEPTANCE_ENTRY",
            extra={
                "observed_retest_price": observed,
                "slippage_protected_limit": entry,
                "rounded_target_net_r": rounded_r,
            },
        )
        self.balance_acceptance_watch = None
        if submitted:
            self.diagnostics["balance_acceptance_submissions"] += 1
        return submitted

    def _expire_balance_watch(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        watch = self.balance_acceptance_watch
        if watch is None:
            return
        self.diagnostics["balance_acceptance_expired"] += 1
        self._transition(
            watch.scenario_id,
            "BALANCE_ACCEPTANCE_WATCH_CLOSED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            {
                **watch.details,
                "phase": watch.phase,
                "hold_count": watch.hold_count,
            },
        )
        self.balance_acceptance_watch = None

    def on_stop(self) -> None:
        if self.balance_acceptance_watch is not None and self.bars:
            self._expire_balance_watch(
                self.bars[-1],
                "BACKTEST_ENDED_WITH_BALANCE_ACCEPTANCE_WATCH_OPEN",
            )
        super().on_stop()


__all__ = ["PositionBuildingBalanceAcceptanceStrategy"]
