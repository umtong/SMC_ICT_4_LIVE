#!/usr/bin/env python3
"""Candidate-04 v17: one-position external/internal liquidity router.

Two non-identical auctions are maintained concurrently inside one NautilusTrader
Strategy instance:

* EXTERNAL_30M: a 30-minute external pool inside a rotational four-hour auction.
  Participation is naturally distributed across more bars, so acceptance volume
  may be up to 1.10 times the original rejection volume.
* INTERNAL_5M: a five-minute internal pool inside a rotational one-hour auction.
  This faster state requires acceptance volume no greater than the original
  rejection volume.

The external state has priority when both describe the same event. A shared
scenario cooldown clusters confirmations within 30 bars, and the shared
``entry_pending``/portfolio state ensures at most one entry order or position.
The profitable external-first / pre-event range target hierarchy is unchanged.
All matching, fills, fees, positions, margin, liquidation and NAV are owned by
NautilusTrader.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nt_auction_failure_strategy import AuctionExcessFailureContinuationStrategy
from nt_auction_failure_strategy import ExcessProbe
from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_low_impact_hybrid_target_strategy import LowImpactHybridTargetStrategy


@dataclass(frozen=True, slots=True)
class AuctionScale:
    name: str
    value_window: int
    liquidity_window: int
    band_sigma: float
    max_efficiency: float
    max_delay: int
    max_volume_ratio: float
    max_body_atr: float


SCALES = (
    AuctionScale(
        name="EXTERNAL_30M",
        value_window=240,
        liquidity_window=30,
        band_sigma=1.50,
        max_efficiency=0.32,
        max_delay=3,
        max_volume_ratio=1.10,
        max_body_atr=1.0,
    ),
    AuctionScale(
        name="INTERNAL_5M",
        value_window=60,
        liquidity_window=5,
        band_sigma=1.00,
        max_efficiency=0.28,
        max_delay=3,
        max_volume_ratio=1.00,
        max_body_atr=1.0,
    ),
)


class CompositeLiquidityRouterStrategy(LowImpactHybridTargetStrategy):
    """Maintain independent causal probes but one shared execution state."""

    def __init__(self, config: LiquidityTransitionConfig) -> None:
        super().__init__(config)
        self.scale_probes: dict[str, ExcessProbe | None] = {
            scale.name: None for scale in SCALES
        }
        self.excess_probe = None

    def _configure_scale(self, scale: AuctionScale) -> None:
        self.VALUE_WINDOW = scale.value_window
        self.LIQUIDITY_WINDOW = scale.liquidity_window
        self.BAND_SIGMA = scale.band_sigma
        self.MAX_EFFICIENCY_240 = scale.max_efficiency
        self.DIRECT_MAX_DELAY_BARS = scale.max_delay
        self.DIRECT_MAX_VOLUME_RATIO = scale.max_volume_ratio
        self.DIRECT_MAX_BODY_ATR = scale.max_body_atr

    def _detect_session_sweep(self, row: dict[str, float | int]) -> bool:
        handled = False
        for scale in SCALES:
            self._configure_scale(scale)
            previous = self.scale_probes[scale.name]
            self.excess_probe = previous
            detected = AuctionExcessFailureContinuationStrategy._detect_session_sweep(
                self,
                row,
            )
            current = self.excess_probe
            if current is not None and current is not previous:
                current.details.update(
                    {
                        "liquidity_scale": scale.name,
                        "scale_value_window": scale.value_window,
                        "scale_liquidity_window": scale.liquidity_window,
                        "scale_band_sigma": scale.band_sigma,
                        "scale_max_efficiency": scale.max_efficiency,
                        "scale_max_volume_ratio": scale.max_volume_ratio,
                    },
                )
            self.scale_probes[scale.name] = current
            self.excess_probe = None
            handled = handled or detected
        return handled

    def _advance_probe(self, row: dict[str, float | int]) -> None:
        # External liquidity has deterministic priority. If it submits an order,
        # the shared entry_pending flag prevents the internal state from creating
        # a second pending entry on the same bar.
        for scale in SCALES:
            probe = self.scale_probes[scale.name]
            if probe is None:
                continue
            self._configure_scale(scale)
            self.excess_probe = probe
            LowImpactHybridTargetStrategy._advance_probe(self, row)
            self.scale_probes[scale.name] = self.excess_probe
            self.excess_probe = None


__all__ = [
    "AuctionScale",
    "CompositeLiquidityRouterStrategy",
    "LiquidityTransitionConfig",
    "SCALES",
]
