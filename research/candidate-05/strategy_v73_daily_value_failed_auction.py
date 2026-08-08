#!/usr/bin/env python3
"""Candidate 05 v73: previous-day value failed auction (Turtle Soup family).

This is not another five-minute sweep filter.  A fully completed UTC day defines
an external range and accepted traded-value VWAP.  The next day may trade the
first material failure of either boundary only when v45's strict inventory
transfer is present.  Entry rests back at the failed daily boundary, stop stays
beyond the same raid extreme, and target is the prior day's accepted VWAP (or,
when VWAP is already consumed, the opposite daily extreme).

The family is additive over unchanged v46.  NautilusTrader, costs, slippage,
3% current-NAV sizing, funding flattening and the one executable intent/position
contract remain inherited.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from daily_value_failed_auction_logic import CompletedDailyValue
from daily_value_failed_auction_logic import daily_value_target_candidates
from daily_value_failed_auction_logic import failed_auction_side
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from inventory_repricing_logic import inventory_trap_confirmed
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy


BRANCH = "DAILY_VALUE_FAILED_AUCTION"


class DailyValueFailedAuctionStrategy(NoPostRetraceBreakawayStrategy):
    """Trade one first failed auction per daily boundary and side."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self._daily_key: str | None = None
        self._daily_high = -math.inf
        self._daily_low = math.inf
        self._daily_value_numerator = 0.0
        self._daily_value_denominator = 0.0
        self.previous_daily_value: CompletedDailyValue | None = None
        self.daily_failed_sides_consumed: set[int] = set()
        self.daily_scenario_counter = 0
        self.diagnostics.update(
            {
                "daily_value_days_completed": 0,
                "daily_value_boundary_failures": 0,
                "daily_value_inventory_confirmations": 0,
                "daily_value_inventory_rejections": 0,
                "daily_value_slot_conflicts": 0,
                "daily_value_invalid_geometry": 0,
                "daily_value_insufficient_target_r": 0,
                "daily_value_submissions": 0,
                "daily_value_vwap_targets": 0,
                "daily_value_opposite_extreme_targets": 0,
            },
        )

    def on_bar(self, bar: Any) -> None:
        super().on_bar(bar)
        if not self.bars or self.current_feature is None:
            return
        row = self.bars[-1]
        self._advance_daily_value(row)
        if self.previous_daily_value is None or len(self.bars) < 2:
            return
        ts = int(row["ts"])
        if (
            not self._in_evaluation(ts)
            or self._funding_blackout(ts)
            or not self._features_ready(ts)
        ):
            return
        self._consider_daily_failure(row, float(list(self.bars)[-2]["close"]))

    @staticmethod
    def _day_key(ts_event: int) -> str:
        return datetime.fromtimestamp(
            int(ts_event) / 1_000_000_000,
            tz=timezone.utc,
        ).date().isoformat()

    def _advance_daily_value(self, row: dict[str, float | int]) -> None:
        key = self._day_key(int(row["ts"]))
        if self._daily_key is None:
            self._daily_key = key
        elif key != self._daily_key:
            if (
                math.isfinite(self._daily_high)
                and math.isfinite(self._daily_low)
                and self._daily_high > self._daily_low > 0.0
                and self._daily_value_denominator > 0.0
            ):
                vwap = self._daily_value_numerator / self._daily_value_denominator
                self.previous_daily_value = CompletedDailyValue(
                    day=self._daily_key,
                    high=self._daily_high,
                    low=self._daily_low,
                    vwap=min(max(vwap, self._daily_low), self._daily_high),
                )
                self.diagnostics["daily_value_days_completed"] += 1
            self._daily_key = key
            self._daily_high = -math.inf
            self._daily_low = math.inf
            self._daily_value_numerator = 0.0
            self._daily_value_denominator = 0.0
            self.daily_failed_sides_consumed.clear()

        self._daily_high = max(self._daily_high, float(row["high"]))
        self._daily_low = min(self._daily_low, float(row["low"]))
        minute_vwap = self._feature("trade_vwap_60s")
        notional = self._feature("notional_60s")
        if math.isfinite(minute_vwap) and math.isfinite(notional) and notional > 0.0:
            self._daily_value_numerator += minute_vwap * notional
            self._daily_value_denominator += notional

    def _entry_slot_idle(self) -> bool:
        return (
            self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and not bool(getattr(self, "exit_pending", False))
            and self.pending is None
            and self.armed_entry_path is None
            and not bool(getattr(self, "counter_context_parent_lock_active", False))
            and self.bar_index - self.last_entry_index >= self.config.cooldown_bars
        )

    def _consider_daily_failure(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        reference = self.previous_daily_value
        if reference is None:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        side = failed_auction_side(
            previous_close=previous_close,
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            reference=reference,
            atr=atr,
        )
        if side == 0 or side in self.daily_failed_sides_consumed:
            return

        # The first materially failed boundary interaction owns this side for
        # the day.  A later prettier raid cannot be selected after observing the
        # first outcome.
        self.daily_failed_sides_consumed.add(side)
        self.diagnostics["daily_value_boundary_failures"] += 1
        penetration = (
            (reference.low - float(row["low"])) / atr
            if side > 0
            else (float(row["high"]) - reference.high) / atr
        )
        if not inventory_trap_confirmed(
            side=side,
            penetration_atr=penetration,
            flow_15s=self._feature("flow_15s"),
            flow_60s=self._feature("flow_60s"),
            depth_imbalance=self._feature("depth_imbalance_1"),
            close=float(row["close"]),
            trade_vwap=self._feature("trade_vwap_60s"),
            external_or_clustered=True,
        ):
            self.diagnostics["daily_value_inventory_rejections"] += 1
            return
        self.diagnostics["daily_value_inventory_confirmations"] += 1
        if not self._entry_slot_idle():
            self.diagnostics["daily_value_slot_conflicts"] += 1
            return
        self._submit_daily_value_failure(
            row=row,
            side=side,
            penetration_atr=penetration,
            reference=reference,
            atr=atr,
        )

    def _submit_daily_value_failure(
        self,
        *,
        row: dict[str, float | int],
        side: int,
        penetration_atr: float,
        reference: CompletedDailyValue,
        atr: float,
    ) -> bool:
        # Passive return to the failed boundary preserves the same new auction
        # leg and avoids paying for a late close-confirmation chase.
        raw_entry = reference.low if side > 0 else reference.high
        entry_price = self.instrument.make_price(raw_entry)
        entry = _as_float(entry_price)
        raw_stop = (
            float(row["low"]) - self.config.stop_buffer_atr * atr
            if side > 0
            else float(row["high"]) + self.config.stop_buffer_atr * atr
        )
        stop_price = self.instrument.make_price(raw_stop)
        stop = _as_float(stop_price)
        if (side > 0 and not stop < entry) or (side < 0 and not entry < stop):
            self.diagnostics["daily_value_invalid_geometry"] += 1
            return False

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
            self.diagnostics["daily_value_invalid_geometry"] += 1
            return False

        chosen: tuple[str, float, float] | None = None
        for target_source, raw_target in daily_value_target_candidates(
            side=side,
            entry=entry,
            reference=reference,
        ):
            target_price = self.instrument.make_price(raw_target)
            target = _as_float(target_price)
            target_r = net_r_at_price(
                entry,
                target,
                side,
                planned_loss,
                cost_rate,
            )
            if (
                math.isfinite(target_r)
                and target_r + 1e-9 >= MIN_LIQUIDITY_TARGET_NET_R
                and (
                    stop < entry < target
                    if side > 0
                    else target < entry < stop
                )
            ):
                chosen = (target_source, target, target_r)
                break
        if chosen is None:
            self.diagnostics["daily_value_insufficient_target_r"] += 1
            return False
        target_source, target, target_r = chosen
        target_price = self.instrument.make_price(target)

        self.daily_scenario_counter += 1
        scenario_id = f"daily-value-{self.daily_scenario_counter:07d}"
        details = {
            "branch": BRANCH,
            "side": side,
            "previous_day": reference.day,
            "previous_day_high": reference.high,
            "previous_day_low": reference.low,
            "previous_day_vwap": reference.vwap,
            "penetration_atr": penetration_atr,
            "flow_15s": self._feature("flow_15s"),
            "flow_60s": self._feature("flow_60s"),
            "depth_imbalance_1": self._feature("depth_imbalance_1"),
            "trade_vwap_60s": self._feature("trade_vwap_60s"),
            "entry_limit": entry,
            "stop": stop,
            "target": target,
            "target_source": target_source,
            "target_net_r": target_r,
        }
        setup = PendingSetup(
            scenario_id=scenario_id,
            branch=BRANCH,
            side=side,
            swept_kind="PREVIOUS_DAY_LOW" if side > 0 else "PREVIOUS_DAY_HIGH",
            pool_id=f"daily-boundary-{reference.day}-{side}",
            pool_level=entry,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.acceptance_retrace_bars,
            sweep_extreme=float(row["low"]) if side > 0 else float(row["high"]),
            structure=entry,
            atr=atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="DAILY_VALUE_FAILED_AUCTION",
            choch_close=entry,
            stop=stop,
            atr=atr,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            details=details,
        )
        self.armed_entry_path = armed
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
            branch=BRANCH,
            event_type="DAILY_VALUE_BOUNDARY_LIMIT_SUBMITTED",
            reason="PREVIOUS_DAY_BOUNDARY_FAILED_WITH_STRICT_INVENTORY_TRANSFER",
            expires_index=self.bar_index + self.config.acceptance_retrace_bars,
            entry_tag="DAILY_VALUE_FAILED_AUCTION_ENTRY",
            extra=details,
        )
        if submitted:
            self.diagnostics["daily_value_submissions"] += 1
            if target_source.startswith("PREVIOUS_DAY_VWAP"):
                self.diagnostics["daily_value_vwap_targets"] += 1
            else:
                self.diagnostics["daily_value_opposite_extreme_targets"] += 1
        elif self.armed_entry_path is armed:
            self.armed_entry_path = None
        return submitted


LiquidityResponseStrategy = DailyValueFailedAuctionStrategy

__all__ = [
    "BRANCH",
    "DailyValueFailedAuctionStrategy",
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
]
