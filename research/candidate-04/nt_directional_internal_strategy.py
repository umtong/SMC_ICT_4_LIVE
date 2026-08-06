#!/usr/bin/env python3
"""Directional parent-auction internal-liquidity resumption.

The scenario formalizes an ICT-style continuation without treating SMC labels as
isolated candle patterns:

1. A completed parent impulse establishes a directional auction. Both close
   displacement and volume-weighted fair value must migrate in the same
   direction with non-trivial path efficiency.
2. A later pullback travels against that direction without erasing most of the
   parent impulse.
3. The pullback takes a completed internal liquidity pool and closes back beyond
   it (liquidity sweep/reclaim).
4. Within four completed bars, price closes through pre-sweep micro structure
   with directional displacement, participation and close location. An optional
   variant additionally requires a three-candle FVG on that confirmation bar.
5. The target is the nearest pre-confirmation external liquidity level which
   provides at least 1.2R after costs. The stop is beyond the swept extreme.

Orders, fills, fees, contingent orders, account balances, margin and NAV remain
owned by NautilusTrader. The strategy shares the base global pending/open
position gate, so only one entry can exist.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from nt_auction_excess_strategy import weighted_location
from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy
from nt_liquidity_strategy import PendingSetup
from nt_low_impact_external_strategy import LowImpactExternalLiquidityStrategy
from nt_low_impact_external_strategy import choose_external_liquidity_target


SCENARIO = "DIRECTIONAL_INTERNAL_LIQUIDITY_RESUMPTION"


@dataclass(frozen=True, slots=True)
class DirectionalScale:
    name: str
    parent_bars: int
    pullback_bars: int
    internal_pool_bars: int
    min_parent_displacement_atr: float
    min_parent_efficiency: float
    min_fair_migration_atr: float
    min_pullback_atr: float
    max_pullback_fraction: float
    min_sweep_atr: float
    require_confirmation_fvg: bool = False


ONE_HOUR = DirectionalScale(
    name="PARENT_1H_INTERNAL_5M",
    parent_bars=60,
    pullback_bars=15,
    internal_pool_bars=5,
    min_parent_displacement_atr=1.50,
    min_parent_efficiency=0.18,
    min_fair_migration_atr=0.50,
    min_pullback_atr=0.30,
    max_pullback_fraction=0.75,
    min_sweep_atr=0.04,
)

ONE_HOUR_FVG = DirectionalScale(
    name="PARENT_1H_INTERNAL_5M_FVG",
    parent_bars=60,
    pullback_bars=15,
    internal_pool_bars=5,
    min_parent_displacement_atr=1.50,
    min_parent_efficiency=0.18,
    min_fair_migration_atr=0.50,
    min_pullback_atr=0.30,
    max_pullback_fraction=0.75,
    min_sweep_atr=0.04,
    require_confirmation_fvg=True,
)

FOUR_HOUR = DirectionalScale(
    name="PARENT_4H_INTERNAL_15M",
    parent_bars=240,
    pullback_bars=60,
    internal_pool_bars=15,
    min_parent_displacement_atr=3.00,
    min_parent_efficiency=0.18,
    min_fair_migration_atr=1.00,
    min_pullback_atr=0.45,
    max_pullback_fraction=0.75,
    min_sweep_atr=0.04,
)


class DirectionalInternalLiquidityStrategy(LiquidityTransitionStrategy):
    """Base class; subclasses select structurally distinct parent scales."""

    SCALES: tuple[DirectionalScale, ...] = (ONE_HOUR,)
    MIN_TARGET_NET_R = 1.20
    CONFIRMATION_BODY_ATR = 0.45
    CONFIRMATION_VOLUME_BURST = 1.10
    CONFIRMATION_CLOSE_LOCATION = 0.65

    def _detect_session_sweep(self, row: dict[str, float | int]) -> bool:
        return False

    @staticmethod
    def _segment_efficiency(rows: list[dict[str, float | int]]) -> float:
        if len(rows) < 2:
            return 0.0
        closes = [float(item["close"]) for item in rows]
        path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
        return abs(closes[-1] - closes[0]) / path if path > 0.0 else 0.0

    @staticmethod
    def _weighted_fair(rows: list[dict[str, float | int]]) -> float:
        typical = [
            (float(item["high"]) + float(item["low"]) + float(item["close"])) / 3.0
            for item in rows
        ]
        volumes = [float(item["volume"]) for item in rows]
        fair, _ = weighted_location(typical, volumes)
        return float(fair)

    def _parent_state(
        self,
        scale: DirectionalScale,
        atr: float,
    ) -> dict[str, Any] | None:
        rows = list(self.bars)
        required = scale.parent_bars + scale.pullback_bars + 2
        if len(rows) < required:
            return None

        # The parent impulse ends before the pullback window. This prevents the
        # sweep or confirmation from manufacturing its own higher-timeframe bias.
        parent_end = len(rows) - scale.pullback_bars - 1
        parent_start = parent_end - scale.parent_bars
        parent = rows[parent_start : parent_end + 1]
        if len(parent) != scale.parent_bars + 1:
            return None

        displacement = (float(parent[-1]["close"]) - float(parent[0]["close"])) / atr
        if abs(displacement) < scale.min_parent_displacement_atr:
            return None
        side = 1 if displacement > 0.0 else -1
        efficiency = self._segment_efficiency(parent)
        if efficiency < scale.min_parent_efficiency:
            return None

        half = max(10, scale.parent_bars // 2)
        fair_early = self._weighted_fair(parent[:half])
        fair_late = self._weighted_fair(parent[-half:])
        fair_migration = side * (fair_late - fair_early) / atr
        if fair_migration < scale.min_fair_migration_atr:
            return None

        impulse_end_close = float(parent[-1]["close"])
        previous_close = float(rows[-2]["close"])
        pullback = side * (previous_close - impulse_end_close) / atr
        pullback_magnitude = -pullback
        if pullback_magnitude < scale.min_pullback_atr:
            return None
        if pullback_magnitude > scale.max_pullback_fraction * abs(displacement):
            return None

        # Fair value must still be ordered in the parent direction at the end of
        # the pullback. A full inversion is a new auction, not a continuation.
        pullback_rows = rows[-(scale.pullback_bars + 1) : -1]
        pullback_fair = self._weighted_fair(pullback_rows)
        fair_ordered = side * (pullback_fair - fair_early) / atr
        if fair_ordered <= 0.0:
            return None

        return {
            "side": side,
            "parent_displacement_atr": displacement,
            "parent_efficiency": efficiency,
            "parent_fair_early": fair_early,
            "parent_fair_late": fair_late,
            "parent_fair_migration_atr": fair_migration,
            "pullback_atr": pullback,
            "pullback_fair": pullback_fair,
            "pullback_fair_ordered_atr": fair_ordered,
            "impulse_end_close": impulse_end_close,
        }

    def _detect_scale(
        self,
        scale: DirectionalScale,
        row: dict[str, float | int],
        atr: float,
    ) -> bool:
        state = self._parent_state(scale, atr)
        if state is None:
            return False
        side = int(state["side"])
        rows = list(self.bars)
        pool_rows = rows[-(scale.internal_pool_bars + 1) : -1]
        if len(pool_rows) != scale.internal_pool_bars:
            return False

        if side > 0:
            pool_level = min(float(item["low"]) for item in pool_rows)
            penetration = (pool_level - float(row["low"])) / atr
            swept = (
                penetration >= scale.min_sweep_atr
                and float(row["close"]) > pool_level
            )
            extreme = float(row["low"])
            structure = max(
                float(item["high"])
                for item in rows[-(self.config.pre_sweep_structure_bars + 1) : -1]
            )
        else:
            pool_level = max(float(item["high"]) for item in pool_rows)
            penetration = (float(row["high"]) - pool_level) / atr
            swept = (
                penetration >= scale.min_sweep_atr
                and float(row["close"]) < pool_level
            )
            extreme = float(row["high"])
            structure = min(
                float(item["low"])
                for item in rows[-(self.config.pre_sweep_structure_bars + 1) : -1]
            )
        if not swept:
            return False

        self.pending = PendingSetup(
            scenario=SCENARIO,
            side=side,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.confirmation_bars,
            extreme=extreme,
            structure=structure,
            atr=atr,
            target_reference=None,
            details={
                **state,
                "directional_scale": scale.name,
                "parent_bars": scale.parent_bars,
                "pullback_bars": scale.pullback_bars,
                "internal_pool_bars": scale.internal_pool_bars,
                "pool_level": pool_level,
                "sweep_extreme": extreme,
                "sweep_penetration_atr": penetration,
                "require_confirmation_fvg": scale.require_confirmation_fvg,
            },
        )
        self._event("DIRECTIONAL_INTERNAL_SWEEP_DETECTED", SCENARIO, row, self.pending.details)
        return True

    def _detect_trend_sweep(self, row: dict[str, float | int]) -> bool:
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return False
        # Larger parent auctions receive priority in the composite variant.
        for scale in self.SCALES:
            if self._detect_scale(scale, row, atr):
                return True
        return False

    @staticmethod
    def _confirmation_has_fvg(
        rows: list[dict[str, float | int]],
        side: int,
    ) -> bool:
        if len(rows) < 3:
            return False
        current = rows[-1]
        two_back = rows[-3]
        if side > 0:
            return float(current["low"]) > float(two_back["high"])
        return float(current["high"]) < float(two_back["low"])

    def _try_confirm_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None or setup.scenario != SCENARIO:
            return super()._try_confirm_pending(row)
        if self.bar_index <= setup.created_index:
            return False

        side = setup.side
        close_invalidated = (
            float(row["close"]) < setup.extreme
            if side > 0
            else float(row["close"]) > setup.extreme
        )
        if close_invalidated:
            self._event("DIRECTIONAL_SWEEP_INVALIDATED", SCENARIO, row, setup.details)
            self.pending = None
            return False

        broken = (
            float(row["close"]) > setup.structure
            if side > 0
            else float(row["close"]) < setup.structure
        )
        if not broken:
            return False

        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            self.pending = None
            return False
        body = side * (float(row["close"]) - float(row["open"])) / atr
        volume_burst = self._volume_burst()
        close_location = self._close_location(row, side)
        fvg = self._confirmation_has_fvg(list(self.bars), side)
        requires_fvg = bool(setup.details["require_confirmation_fvg"])
        details = {
            **setup.details,
            "confirmation_body_atr": body,
            "confirmation_volume_burst": volume_burst,
            "confirmation_close_location": close_location,
            "confirmation_fvg": fvg,
            "structure": setup.structure,
        }
        passed = (
            body >= self.CONFIRMATION_BODY_ATR
            and volume_burst >= self.CONFIRMATION_VOLUME_BURST
            and close_location >= self.CONFIRMATION_CLOSE_LOCATION
            and (fvg or not requires_fvg)
        )
        if not passed:
            self._event("DIRECTIONAL_WEAK_FIRST_BREAK", SCENARIO, row, details)
            self.pending = None
            return False

        if self._funding_blackout(int(row["ts"])):
            self.pending = None
            return False
        last_entry = self.last_entry_by_scenario.get(SCENARIO, -10**12)
        if self.bar_index - last_entry < self.config.cooldown_bars:
            self.pending = None
            return False

        entry = float(row["close"])
        stop = setup.extreme - side * self.config.stop_buffer_atr * atr
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        target = choose_external_liquidity_target(
            LowImpactExternalLiquidityStrategy._external_levels(self, side),
            entry=entry,
            stop=stop,
            side=side,
            cost_rate=cost_rate,
            minimum_net_r=self.MIN_TARGET_NET_R,
        )
        if target is None:
            self._event("DIRECTIONAL_NO_CAUSAL_TARGET", SCENARIO, row, details)
            self.pending = None
            return False

        routed = PendingSetup(
            scenario=SCENARIO,
            side=side,
            created_index=setup.created_index,
            expires_index=self.bar_index,
            extreme=setup.extreme,
            structure=setup.structure,
            atr=atr,
            target_reference=target.price,
            details=dict(details),
        )
        routed_details = {
            **details,
            "external_target": target.price,
            "external_target_source": target.source,
            "external_target_net_r_at_confirmation": target.net_r,
            "minimum_target_net_r": self.MIN_TARGET_NET_R,
        }
        submitted = LiquidityTransitionStrategy._submit_bracket(
            self,
            routed,
            row,
            target.net_r,
            routed_details,
        )
        if not submitted:
            self._event("DIRECTIONAL_EXECUTION_REJECTED", SCENARIO, row, routed_details)
        self.pending = None
        return submitted


class OneHourDirectionalInternalStrategy(DirectionalInternalLiquidityStrategy):
    SCALES = (ONE_HOUR,)


class OneHourFvgDirectionalInternalStrategy(DirectionalInternalLiquidityStrategy):
    SCALES = (ONE_HOUR_FVG,)


class FourHourDirectionalInternalStrategy(DirectionalInternalLiquidityStrategy):
    SCALES = (FOUR_HOUR,)


class CompositeDirectionalInternalStrategy(DirectionalInternalLiquidityStrategy):
    SCALES = (FOUR_HOUR, ONE_HOUR)


__all__ = [
    "CompositeDirectionalInternalStrategy",
    "DirectionalInternalLiquidityStrategy",
    "DirectionalScale",
    "FOUR_HOUR",
    "FourHourDirectionalInternalStrategy",
    "ONE_HOUR",
    "ONE_HOUR_FVG",
    "OneHourDirectionalInternalStrategy",
    "OneHourFvgDirectionalInternalStrategy",
]
