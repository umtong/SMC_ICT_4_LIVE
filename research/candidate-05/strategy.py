"""Candidate 05 hybrid: positioning reset plus internal inventory traps.

The validated five-minute positioning-reset state machine remains authoritative
for external liquidity. One- and three-minute internal liquidity is observed in
an independent pool store so it cannot change external pool selection,
consumption, targets, or diagnostics. Internal inventory traps may compete for
the same NautilusTrader execution slot only when no external event consumed
liquidity on the completed bar.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math

from inventory_repricing_logic import QH_CONTEXT_MAX_AGE_BARS
from inventory_repricing_logic import QH_CONTEXT_MIN_AGE_BARS
from inventory_repricing_logic import inventory_trap_confirmed
from inventory_repricing_logic import quarter_context_accepted
from inventory_repricing_logic import quarter_context_invalidated
from inventory_repricing_logic import quarter_hour_repricing_direction
from logic import Pool
from logic import is_confirmed_pivot
from retrace_logic import aggregate_completed_bar
from strategy_base import LiquidityResponseConfig
from strategy_v39_positioning_reset import PositioningResetReversalStrategy


MINUTE_NS = 60_000_000_000
_INTERNAL_SOURCE_RANK = {
    "CONFIRMED_1M_INTERNAL": 1,
    "CONFIRMED_3M_INTERNAL": 2,
}


@dataclass(slots=True)
class QuarterHourContext:
    direction: int
    created_index: int
    created_ts: int
    boundary_high: float
    boundary_low: float
    boundary_close: float
    atr: float
    favorable_extreme: float
    accepted: bool = False


class PositioningResetInventoryHybridStrategy(PositioningResetReversalStrategy):
    """Preserve the external v39 baseline and add independent internal traps.

    External branch
        Unchanged ``PositioningResetReversalStrategy`` over its original 5m
        liquidity hierarchy, targets, positioning reset, order geometry, fees,
        3% current-NAV sizing and Nautilus lifecycle.

    Internal branch
        A causally confirmed 1m/3m pivot is swept after its minimum age. The
        sweep must show reversal-side tail-flow improvement, visible depth
        sponsorship and a close through its own trade VWAP. An accepted mature
        quarter-hour repricing state in the opposite direction vetoes the
        reversal; absence of such a context is neutral. The inherited CHoCH,
        actual live-liquidity target, bracket and lifecycle remain unchanged.

    Internal pools never enter ``active_pools`` outside the short, isolated
    detector call. Thus they cannot strengthen, consume, reprioritize or become
    targets for the validated external branch.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.internal_pools: dict[str, Pool] = {}
        self.three_bars: deque[dict[str, float | int]] = deque(maxlen=1_500)
        self.three_rows: list[dict[str, float | int]] = []
        self.three_bucket: int | None = None
        self.quarter_context: QuarterHourContext | None = None
        self.diagnostics.update(
            {
                "internal_1m_pools": 0,
                "internal_3m_pools": 0,
                "internal_pool_strengthenings": 0,
                "internal_pool_expirations": 0,
                "internal_pools_shadowed_by_external": 0,
                "internal_inventory_quality_rejections": 0,
                "internal_quarter_context_absence_neutral": 0,
                "internal_quarter_context_aligned_support": 0,
                "internal_quarter_context_opposition_veto": 0,
                "internal_inventory_traps_armed": 0,
                "quarter_hour_contexts": 0,
                "quarter_hour_contexts_accepted": 0,
                "quarter_hour_contexts_invalidated": 0,
                "quarter_hour_contexts_expired": 0,
            },
        )

    def on_bar(self, bar) -> None:  # Nautilus ``Bar`` kept implicit for compatibility.
        # Parent detects and trades using observations known before the current
        # bar's post-processing. This preserves its exact causal path.
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        self._prune_internal_pools(row)
        self._update_internal_liquidity(row)
        self._update_quarter_hour_context(row)

    def _add_internal_pool(
        self,
        kind: str,
        level: float,
        event_time_ns: int,
        observed_time_ns: int,
        source: str,
        *,
        strength: int,
    ) -> None:
        atr = self._atr()
        tolerance = (
            self.config.pool_merge_tolerance_atr * atr
            if math.isfinite(atr)
            else 0.0
        )
        merge: Pool | None = None
        for pool in self.internal_pools.values():
            if pool.kind == kind and abs(pool.level - level) <= tolerance:
                merge = pool
                break
        if merge is not None:
            new_level = (
                max(merge.level, level)
                if kind == "HIGH"
                else min(merge.level, level)
            )
            promoted_source = (
                source
                if _INTERNAL_SOURCE_RANK[source]
                > _INTERNAL_SOURCE_RANK.get(merge.source, 0)
                else merge.source
            )
            updated = replace(
                merge,
                level=new_level,
                observed_time_ns=observed_time_ns,
                source=promoted_source,
                strength=merge.strength + strength,
            )
            self.internal_pools[merge.pool_id] = updated
            self.diagnostics["internal_pool_strengthenings"] += 1
            self._transition(
                merge.pool_id,
                "INTERNAL_POOL_STRENGTHENED",
                event_time_ns,
                observed_time_ns,
                "POOL_ARMED",
                "NEAR_EQUAL_INTERNAL_LIQUIDITY_CLUSTER",
                new_level,
                {
                    "source": promoted_source,
                    "strength": updated.strength,
                    "hierarchy": "INTERNAL_ONLY",
                },
            )
            return

        self.pool_counter += 1
        pool_id = f"internal-pool-{self.pool_counter:07d}"
        pool = Pool(
            pool_id=pool_id,
            kind=kind,
            level=level,
            event_time_ns=event_time_ns,
            observed_time_ns=observed_time_ns,
            source=source,
            strength=strength,
            created_index=self.bar_index,
        )
        self.internal_pools[pool_id] = pool
        self._transition(
            pool_id,
            "INTERNAL_POOL_CONFIRMED",
            event_time_ns,
            observed_time_ns,
            "POOL_ARMED",
            source,
            level,
            {
                "kind": kind,
                "strength": strength,
                "hierarchy": "INTERNAL_ONLY",
            },
        )

    def _update_internal_liquidity(
        self,
        row: dict[str, float | int],
    ) -> None:
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
            self._add_internal_pool(
                "HIGH",
                float(center["high"]),
                int(center["ts"]),
                observed_ns,
                "CONFIRMED_1M_INTERNAL",
                strength=1,
            )
            self.diagnostics["internal_1m_pools"] += 1
        if is_confirmed_pivot(lows, span=span, kind="LOW"):
            self._add_internal_pool(
                "LOW",
                float(center["low"]),
                int(center["ts"]),
                observed_ns,
                "CONFIRMED_1M_INTERNAL",
                strength=1,
            )
            self.diagnostics["internal_1m_pools"] += 1

    def _update_three_minute(
        self,
        row: dict[str, float | int],
    ) -> None:
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
            self._add_internal_pool(
                "HIGH",
                float(center["high"]),
                int(center["ts"]),
                observed_ns,
                "CONFIRMED_3M_INTERNAL",
                strength=2,
            )
            self.diagnostics["internal_3m_pools"] += 1
        if is_confirmed_pivot(lows, span=span, kind="LOW"):
            self._add_internal_pool(
                "LOW",
                float(center["low"]),
                int(center["ts"]),
                observed_ns,
                "CONFIRMED_3M_INTERNAL",
                strength=2,
            )
            self.diagnostics["internal_3m_pools"] += 1

    def _prune_internal_pools(
        self,
        row: dict[str, float | int],
    ) -> None:
        expired = [
            pool
            for pool in self.internal_pools.values()
            if self.bar_index - pool.created_index > self.config.pool_max_age_bars
        ]
        for pool in expired:
            self._transition(
                pool.pool_id,
                "INTERNAL_POOL_EXPIRED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "MAX_INTERNAL_POOL_AGE",
                pool.level,
                {"age_bars": self.bar_index - pool.created_index},
            )
            self.internal_pools.pop(pool.pool_id, None)
            self.diagnostics["internal_pool_expirations"] += 1

    def _update_quarter_hour_context(
        self,
        row: dict[str, float | int],
    ) -> None:
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
                self.quarter_context = None
                self.diagnostics["quarter_hour_contexts_invalidated"] += 1
                context = None
            elif age > QH_CONTEXT_MAX_AGE_BARS:
                self.quarter_context = None
                self.diagnostics["quarter_hour_contexts_expired"] += 1
                context = None
            else:
                context.favorable_extreme = (
                    max(context.favorable_extreme, float(row["high"]))
                    if context.direction > 0
                    else min(context.favorable_extreme, float(row["low"]))
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
            boundary_high=float(row["high"]),
            boundary_low=float(row["low"]),
            boundary_close=float(row["close"]),
            atr=atr,
            favorable_extreme=(
                float(row["high"]) if direction > 0 else float(row["low"])
            ),
        )
        self.diagnostics["quarter_hour_contexts"] += 1

    def _shadow_internal_crosses(
        self,
        *,
        row: dict[str, float | int],
        previous_close: float,
        external_kinds: set[str],
    ) -> None:
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        min_age = self.config.pool_min_age_bars
        shadowed: list[Pool] = []
        for pool in self.internal_pools.values():
            if pool.kind not in external_kinds:
                continue
            if self.bar_index - pool.created_index < min_age:
                continue
            crossed = (
                previous_close <= pool.level
                and float(row["high"])
                >= pool.level + self.config.sweep_min_penetration_atr * atr
                if pool.kind == "HIGH"
                else previous_close >= pool.level
                and float(row["low"])
                <= pool.level - self.config.sweep_min_penetration_atr * atr
            )
            if crossed:
                shadowed.append(pool)
        for pool in shadowed:
            self._transition(
                pool.pool_id,
                "INTERNAL_POOL_CONSUMED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "SHADOWED_BY_EXTERNAL_LIQUIDITY_ACCESS",
                pool.level,
                {"kind": pool.kind, "source": pool.source},
            )
            self.internal_pools.pop(pool.pool_id, None)
            self.diagnostics["internal_pools_shadowed_by_external"] += 1

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        # First run the exact validated external detector against its untouched
        # pool store.
        baseline_before = dict(self.active_pools)
        previous_scenario = None if self.pending is None else self.pending.scenario_id
        PositioningResetReversalStrategy._detect_sweep(
            self,
            row,
            previous_close,
        )
        consumed_ids = set(baseline_before) - set(self.active_pools)
        if consumed_ids or self.pending is not None:
            if consumed_ids:
                self._shadow_internal_crosses(
                    row=row,
                    previous_close=previous_close,
                    external_kinds={baseline_before[item].kind for item in consumed_ids},
                )
            return

        self._prune_internal_pools(row)
        if not self.internal_pools:
            return

        # Isolate the internal detector. The temporary store is restored before
        # any later target selection, so inherited targets remain external/live.
        baseline_pools = self.active_pools
        self.active_pools = self.internal_pools
        try:
            PositioningResetReversalStrategy._detect_sweep(
                self,
                row,
                previous_close,
            )
        finally:
            updated_internal = self.active_pools
            self.active_pools = baseline_pools
            self.internal_pools = updated_internal

        setup = self.pending
        if setup is None or setup.scenario_id == previous_scenario:
            return
        quality = inventory_trap_confirmed(
            side=setup.side,
            penetration_atr=float(setup.details.get("penetration_atr", math.nan)),
            flow_15s=float(setup.details.get("flow_15s", math.nan)),
            flow_60s=float(setup.details.get("flow_60s", math.nan)),
            depth_imbalance=float(
                setup.details.get("depth_imbalance_1", math.nan),
            ),
            close=float(row["close"]),
            trade_vwap=self._feature("trade_vwap_60s"),
            external_or_clustered=False,
        )
        if not quality:
            self.diagnostics["internal_inventory_quality_rejections"] += 1
            self._expire_pending(
                row,
                "INTERNAL_TRAP_FLOW_DEPTH_VWAP_NOT_CONFIRMED",
            )
            return

        context = self.quarter_context
        context_age = -1
        active_context = False
        if context is not None:
            context_age = self.bar_index - context.created_index
            active_context = (
                context.accepted
                and QH_CONTEXT_MIN_AGE_BARS
                <= context_age
                <= QH_CONTEXT_MAX_AGE_BARS
            )
        if active_context and context is not None and context.direction != setup.side:
            self.diagnostics["internal_quarter_context_opposition_veto"] += 1
            self._expire_pending(
                row,
                "INTERNAL_TRAP_OPPOSED_ACCEPTED_QUARTER_REPRICING",
            )
            return
        if active_context:
            self.diagnostics["internal_quarter_context_aligned_support"] += 1
            context_state = "ALIGNED_ACCEPTED_QUARTER_REPRICING"
        else:
            self.diagnostics["internal_quarter_context_absence_neutral"] += 1
            context_state = "NO_ACTIVE_QUARTER_REPRICING"

        setup.details.update(
            {
                "hybrid_state": "INTERNAL_INVENTORY_TRAP",
                "internal_pool_source": setup.details.get("pool_source"),
                "internal_pool_strength": setup.details.get("pool_strength"),
                "trade_vwap_60s": self._feature("trade_vwap_60s"),
                "quarter_context_state": context_state,
                "quarter_context_direction": (
                    context.direction if context is not None else None
                ),
                "quarter_context_age_bars": context_age,
                "quarter_context_accepted": (
                    context.accepted if context is not None else False
                ),
            },
        )
        self.diagnostics["internal_inventory_traps_armed"] += 1


LiquidityResponseStrategy = PositioningResetInventoryHybridStrategy

__all__ = [
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "PositioningResetInventoryHybridStrategy",
    "QuarterHourContext",
]
