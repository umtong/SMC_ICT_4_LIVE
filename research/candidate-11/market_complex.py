"""Causal cross-market confirmation for the Candidate 11 SCDAM portfolio.

This module has no order matching, fill, accounting, or PnL logic. It converts
same-timestamp completed observations into dimensionless auction breadth and
leave-one-out divergence evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from statistics import median
from typing import Mapping

ALLOWED_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"})


class BoundarySide(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class SourceRange:
    low: Decimal
    high: Decimal

    def __post_init__(self) -> None:
        if self.low <= 0 or self.high <= self.low:
            raise ValueError("source range must be positive and non-degenerate")

    @property
    def width(self) -> Decimal:
        return self.high - self.low


@dataclass(frozen=True, slots=True)
class ComplexObservation:
    symbol: str
    ts_ns: int
    high: Decimal
    low: Decimal
    close: Decimal
    signed_flow: Decimal
    source_range: SourceRange

    def __post_init__(self) -> None:
        if self.symbol not in ALLOWED_SYMBOLS:
            raise ValueError(f"unsupported symbol: {self.symbol}")
        if self.ts_ns < 0 or self.low <= 0 or self.high < self.low:
            raise ValueError("invalid observation geometry")
        if not self.low <= self.close <= self.high:
            raise ValueError("close must lie inside the completed bar")
        if not Decimal("-1") <= self.signed_flow <= Decimal("1"):
            raise ValueError("signed_flow must be in [-1, 1]")

    def normalized(self, price: Decimal) -> Decimal:
        return (price - self.source_range.low) / self.source_range.width

    @property
    def close_location(self) -> Decimal:
        return self.normalized(self.close)

    def extreme_location(self, side: BoundarySide) -> Decimal:
        return self.normalized(self.high if side == BoundarySide.HIGH else self.low)

    def outside_close(self, side: BoundarySide, tolerance: Decimal = Decimal("0")) -> bool:
        if side == BoundarySide.HIGH:
            return self.close_location >= Decimal("1") + tolerance
        return self.close_location <= -tolerance

    def raided(self, side: BoundarySide, min_penetration: Decimal = Decimal("0.02")) -> bool:
        loc = self.extreme_location(side)
        if side == BoundarySide.HIGH:
            return loc >= Decimal("1") + min_penetration
        return loc <= -min_penetration


@dataclass(frozen=True, slots=True)
class CrossMarketEvidence:
    symbol: str
    ts_ns: int
    side: BoundarySide
    own_extreme_location: Decimal
    own_close_location: Decimal
    peer_median_extreme_location: Decimal
    same_side_outside_closes: int
    same_side_raids: int
    peer_count: int
    residual: Decimal
    far_nonconfirmation: bool
    aac_breadth_confirmation: bool
    reason_codes: tuple[str, ...]


class MarketComplex:
    """Build leave-one-out evidence from one completed timestamp across markets."""

    def __init__(
        self,
        *,
        far_min_residual: Decimal = Decimal("0.18"),
        far_max_peer_raids: int = 1,
        aac_min_outside_closes: int = 3,
        min_penetration: Decimal = Decimal("0.02"),
    ) -> None:
        if far_min_residual <= 0 or far_max_peer_raids < 0:
            raise ValueError("invalid FAR confirmation settings")
        if not 2 <= aac_min_outside_closes <= len(ALLOWED_SYMBOLS):
            raise ValueError("invalid AAC breadth setting")
        self.far_min_residual = far_min_residual
        self.far_max_peer_raids = far_max_peer_raids
        self.aac_min_outside_closes = aac_min_outside_closes
        self.min_penetration = min_penetration

    def evaluate(
        self,
        observations: Mapping[str, ComplexObservation],
        *,
        symbol: str,
        side: BoundarySide,
    ) -> CrossMarketEvidence:
        if symbol not in observations:
            raise KeyError(symbol)
        if len(observations) < 3:
            raise ValueError("at least three synchronized liquid markets are required")
        ts_values = {obs.ts_ns for obs in observations.values()}
        if len(ts_values) != 1:
            raise ValueError("cross-market evidence must use one completed timestamp")
        own = observations[symbol]
        peers = [obs for key, obs in observations.items() if key != symbol]
        own_extreme = own.extreme_location(side)
        peer_extremes = [obs.extreme_location(side) for obs in peers]
        peer_median = Decimal(str(median(peer_extremes)))
        residual = own_extreme - peer_median if side == BoundarySide.HIGH else peer_median - own_extreme
        all_obs = list(observations.values())
        outside_count = sum(obs.outside_close(side) for obs in all_obs)
        raid_count = sum(obs.raided(side, self.min_penetration) for obs in all_obs)
        peer_raids = sum(obs.raided(side, self.min_penetration) for obs in peers)
        far = (
            own.raided(side, self.min_penetration)
            and peer_raids <= self.far_max_peer_raids
            and residual >= self.far_min_residual
        )
        aac = own.outside_close(side) and outside_count >= self.aac_min_outside_closes
        reasons: list[str] = []
        if far:
            reasons.append("LEAVE_ONE_OUT_RAID_NONCONFIRMATION")
        if aac:
            reasons.append("CROSS_MARKET_OUTSIDE_CLOSE_BREADTH")
        if far and aac:
            far = False
            aac = False
            reasons = ["CONFLICTING_COMPLEX_EVIDENCE"]
        if not reasons:
            reasons.append("COMPLEX_EVIDENCE_INSUFFICIENT")
        return CrossMarketEvidence(
            symbol=symbol,
            ts_ns=own.ts_ns,
            side=side,
            own_extreme_location=own_extreme,
            own_close_location=own.close_location,
            peer_median_extreme_location=peer_median,
            same_side_outside_closes=outside_count,
            same_side_raids=raid_count,
            peer_count=len(peers),
            residual=residual,
            far_nonconfirmation=far,
            aac_breadth_confirmation=aac,
            reason_codes=tuple(reasons),
        )
