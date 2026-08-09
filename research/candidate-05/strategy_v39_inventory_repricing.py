#!/usr/bin/env python3
"""Candidate 05 v39: external inventory traps plus repricing pullbacks."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

from nautilus_trader.model.data import Bar

from inventory_repricing_logic import inventory_trap_confirmed
from inventory_repricing_logic import quarter_context_accepted
from inventory_repricing_logic import quarter_context_invalidated
from inventory_repricing_logic import quarter_hour_repricing_direction
from inventory_repricing_logic import quarter_internal_sweep_eligible
from logic import is_confirmed_pivot
from retrace_logic import aggregate_completed_bar
from strategy_base import LiquidityResponseConfig
from strategy_v26 import ScenarioValidEntryStrategy


MINUTE_NS = 60_000_000_000


@dataclass(slots=True)
class QuarterHourContext:
    direction: int
    created_index: int
    created_ts: int
    boundary_open: float
    boundary_high: float
    boundary_low: float
    boundary_close: float
    atr: float
    favorable_extreme: float
    accepted: bool = False


class InventoryRepricingStrategy(ScenarioValidEntryStrategy):
    """Trade two complementary causal states through the v26 execution chain.

    A. External/clustered inventory trap
       A 5m or multi-level liquidity raid must penetrate at least one-third ATR,
       turn final flow by at least 0.50, retain a 3:2 reversal-side displayed
       depth edge, and close through its own trade VWAP before CHoCH.

    B. Quarter-hour repricing pullback
       The first ten seconds of a quarter-hour minute must show at least 2:1
       directional aggressor flow, 1.5x activity, efficient aligned movement,
       and later achieve a half-ATR directional acceptance. Only after fifteen
       completed minutes may a 1m/3m counter-liquidity raid be traded back in the
       context direction, with its own tail-flow turn, depth support and VWAP
       reclaim.

    Every accepted setup then uses the unchanged v26 CHoCH, path observation,
    live-liquidity target, structural stop, cost model, 3% current-NAV sizing,
    contingent orders and cancel-race lifecycle. No standalone execution or
    accounting is introduced.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.three_bars: deque[dict[str, float | int]] = deque(maxlen=1_500)
        self.three_rows: list[dict[str, float | int]] = []
        self.three_bucket: int | None = None
        self.quarter_context: QuarterHourContext | None = None
        self.diagnostics.update(
            {
                "one_minute_internal_pools": 0,
                "three_minute_internal_pools": 0,
                "quarter_hour_contexts": 0,
                "quarter_hour_contexts_accepted": 0,
                "quarter_hour_contexts_invalidated": 0,
                "quarter_hour_contexts_expired": 0,
                "external_inventory_traps": 0,
                "quarter_internal_inventory_traps": 0,
                "inventory_quality_rejections": 0,
                "quarter_context_rejections": 0,
                "legacy_balance_branch_disabled": 0,
                "legacy_target_handoff_disabled": 0,
            },
        )

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        self._update_internal_liquidity(row)
        self._update_quarter_hour_context(row)

    def _update_internal_liquidity(self, row: dict[str, float | int]) -> None:
        self._confirm_one_minute_pivot(int(row["ts"]))
        self._update_three_minute(row)

    def _confirm_one_minute_pivot(self, observed_ns: int) -> None:
        span = 2
        rows = list(self.bars)
        if len(rows) < 2 * span + 1:
            return
        window = rows[-(2 * span + 1) :]
        center = window[span]
        highs = [float(item["high"]) for item in window]
        lows = [float(item["low"]) for item in window]
        if is_confirmed_pivot(highs, span=span, kind="HIGH"):
            self._add_pool(
                "HIGH",
                float(center["high"]),
                int(center["ts"]),
                observed_ns,
                "CONFIRMED_1M_INTERNAL",
                strength=1,
            )
            self.diagnostics["one_minute_internal_pools"] += 1
        if is_confirmed_pivot(lows, span=span, kind="LOW"):
            self._add_pool(
                "LOW",
                float(center["low"]),
                int(center["ts"]),
                observed_ns,
                "CONFIRMED_1M_INTERNAL",
                strength=1,
            )
            self.diagnostics["one_minute_internal_pools"] += 1

    def _update_three_minute(self, row: dict[str, float | int]) -> None:
        minute = int(row["ts"]) // MINUTE_NS
        bucket = minute // 3
        if self.three_bucket is None:
            self.three_bucket = bucket
        elif bucket != self.three_bucket:
            self.three_rows = []
            self.three_bucket = bucket
        self.three_rows.append(row.copy())
        if minute % 3 != 2:
            return
        if len(self.three_rows) == 3:
            self.three_bars.append(aggregate_completed_bar(self.three_rows))
            self._confirm_three_minute_pivot(int(row["ts"]))
        self.three_rows = []
        self.three_bucket = None

    def _confirm_three_minute_pivot(self, observed_ns: int) -> None:
        span = 2
        rows = list(self.three_bars)
        if len(rows) < 2 * span + 1:
            return
        window = rows[-(2 * span + 1) :]
        center = window[span]
        highs = [float(item["high"]) for item in window]
        lows = [float(item["low"]) for item in window]
        if is_confirmed_pivot(highs, span=span, kind="HIGH"):
            self._add_pool(
                "HIGH",
                float(center["high"]),
                int(center["ts"]),
                observed_ns,
                "CONFIRMED_3M_INTERNAL",
                strength=2,
            )
            self.diagnostics["three_minute_internal_pools"] += 1
        if is_confirmed_pivot(lows, span=span, kind="LOW"):
            self._add_pool(
                "LOW",
                float(center["low"]),
                int(center["ts"]),
                observed_ns,
                "CONFIRMED_3M_INTERNAL",
                strength=2,
            )
            self.diagnostics["three_minute_internal_pools"] += 1

    def _update_quarter_hour_context(self, row: dict[str, float | int]) -> None:
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        context = self.quarter_context
        if context is not None:
            age = self.bar_index - context.created_index
            if quarter_context_invalidated(
                direction=context.direction,
                boundary_low=context.boundary_low,
                boundary_high=context.boundary_high,
                current_close=float(row["close"]),
                atr=context.atr,
            ):
                self.diagnostics["quarter_hour_contexts_invalidated"] += 1
                self.quarter_context = None
                context = None
            elif age > 75:
                self.diagnostics["quarter_hour_contexts_expired"] += 1
                self.quarter_context = None
                context = None
            else:
                if context.direction > 0:
                    context.favorable_extreme = max(
                        context.favorable_extreme,
                        float(row["high"]),
                    )
                else:
                    context.favorable_extreme = min(
                        context.favorable_extreme,
                        float(row["low"]),
                    )
                if not context.accepted and quarter_context_accepted(
                    direction=context.direction,
                    boundary_close=context.boundary_close,
                    favorable_extreme=context.favorable_extreme,
                    atr=context.atr,
                ):
                    context.accepted = True
                    self.diagnostics["quarter_hour_contexts_accepted"] += 1

        moment = datetime.fromtimestamp(
            int(row["ts"]) / 1_000_000_000,
            tz=timezone.utc,
        )
        direction = quarter_hour_repricing_direction(
            minute_of_hour=moment.minute,
            flow_open_10s=self._feature("flow_open_10s"),
            notional_open_10s_burst=self._feature("notional_open_10s_burst"),
            ret_60s_bps=self._feature("ret_60s_bps"),
            efficiency_60s=self._feature("efficiency_60s"),
        )
        if direction == 0:
            return
        self.quarter_context = QuarterHourContext(
            direction=direction,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            boundary_open=float(row["open"]),
            boundary_high=float(row["high"]),
            boundary_low=float(row["low"]),
            boundary_close=float(row["close"]),
            atr=atr,
            favorable_extreme=(float(row["high"]) if direction > 0 else float(row["low"])),
        )
        self.diagnostics["quarter_hour_contexts"] += 1

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        previous_pending = self.pending
        super()._detect_sweep(row, previous_close)
        setup = self.pending
        if setup is None or setup is previous_pending:
            return

        source = str(setup.details.get("pool_source", ""))
        strength = int(setup.details.get("pool_strength", 1))
        external_or_clustered = source == "CONFIRMED_5M_SWING" or strength >= 3
        quality = inventory_trap_confirmed(
            side=setup.side,
            penetration_atr=float(setup.details.get("penetration_atr", math.nan)),
            flow_15s=float(setup.details.get("flow_15s", math.nan)),
            flow_60s=float(setup.details.get("flow_60s", math.nan)),
            depth_imbalance=float(setup.details.get("depth_imbalance_1", math.nan)),
            close=float(row["close"]),
            trade_vwap=self._feature("trade_vwap_60s"),
            external_or_clustered=external_or_clustered,
        )
        if not quality:
            self.diagnostics["inventory_quality_rejections"] += 1
            self._expire_pending(row, "INVENTORY_TRAP_FLOW_DEPTH_VWAP_NOT_CONFIRMED")
            return

        if external_or_clustered:
            setup.details.update(
                {
                    "v39_state": "EXTERNAL_OR_CLUSTERED_INVENTORY_TRAP",
                    "v39_trade_vwap_60s": self._feature("trade_vwap_60s"),
                },
            )
            self.diagnostics["external_inventory_traps"] += 1
            return

        context = self.quarter_context
        if context is None or not quarter_internal_sweep_eligible(
            setup_side=setup.side,
            context_direction=context.direction if context is not None else 0,
            context_age_bars=(
                self.bar_index - context.created_index if context is not None else -1
            ),
            context_accepted=(context.accepted if context is not None else False),
        ):
            self.diagnostics["quarter_context_rejections"] += 1
            self._expire_pending(row, "INTERNAL_SWEEP_LACKED_ACCEPTED_QUARTER_REPRICING_CONTEXT")
            return

        setup.details.update(
            {
                "v39_state": "QUARTER_HOUR_REPRICING_INTERNAL_INVENTORY_TRAP",
                "v39_trade_vwap_60s": self._feature("trade_vwap_60s"),
                "quarter_context_direction": context.direction,
                "quarter_context_created_ts": context.created_ts,
                "quarter_context_age_bars": self.bar_index - context.created_index,
                "quarter_context_boundary_close": context.boundary_close,
                "quarter_context_accepted": context.accepted,
            },
        )
        self.diagnostics["quarter_internal_inventory_traps"] += 1

    def _detect_position_building_balance(
        self,
        row: dict[str, float | int],
    ) -> None:
        self.diagnostics["legacy_balance_branch_disabled"] += 1
        return None

    def _qualify_target_exit(
        self,
        *,
        event: Any,
        target: Any,
    ) -> None:
        self.diagnostics["legacy_target_handoff_disabled"] += 1
        return None


__all__ = ["InventoryRepricingStrategy", "QuarterHourContext"]
