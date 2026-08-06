"""Causal primitives for adaptive HTF acceptance and directional freshness.

The functions in this module are deliberately independent of NautilusTrader and
of the candidate state-machine hierarchy.  They make the two new hypotheses
unit-testable without duplicating execution or accounting:

1. A completed HTF auction is exceptional only relative to *prior* completed
   auctions.  The current auction is never inserted into its own reference set.
2. A directional context remains fresh only while completed closes continue to
   establish new direction-consistent extremes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import floor
from typing import Iterable, Sequence


def _finite(values: Iterable[float]) -> list[float]:
    result = [float(value) for value in values]
    if any(value != value for value in result):
        raise ValueError("quality reference values must not contain NaN")
    return result


def linear_quantile(values: Sequence[float], probability: float) -> float:
    """Return a deterministic linearly interpolated sample quantile.

    This is equivalent to the common ``(n - 1) * q`` interpolation contract and
    avoids version-dependent statistics-library conventions.
    """

    data = sorted(_finite(values))
    if not data:
        raise ValueError("quantile requires at least one value")
    q = float(probability)
    if not 0.0 <= q <= 1.0:
        raise ValueError("probability must be in [0, 1]")
    if len(data) == 1:
        return data[0]
    position = (len(data) - 1) * q
    lower = floor(position)
    upper = min(lower + 1, len(data) - 1)
    weight = position - lower
    return data[lower] * (1.0 - weight) + data[upper] * weight


def empirical_percentile(values: Sequence[float], current: float) -> float:
    """Return the mid-rank percentile of ``current`` against prior values."""

    data = _finite(values)
    if not data:
        return 0.0
    below = sum(value < current for value in data)
    equal = sum(value == current for value in data)
    return (below + 0.5 * equal) / len(data)


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    enabled: bool
    ready: bool
    passed: bool
    history_size: int
    lookback: int
    quantile: float
    range_value: float
    range_threshold: float | None
    range_percentile: float
    volume_value: float
    volume_threshold: float | None
    volume_percentile: float
    body_fraction: float
    body_floor: float

    def details(self) -> dict[str, float | int | bool | None]:
        return asdict(self)


def assess_prior_only_quality(
    *,
    prior_ranges: Sequence[float],
    prior_volumes: Sequence[float],
    current_range: float,
    current_volume: float,
    current_body_fraction: float,
    enabled: bool,
    lookback: int,
    minimum_history: int,
    quantile: float,
    body_floor: float,
) -> QualityAssessment:
    """Assess a completed auction against a sealed prior-only distribution.

    Range, volume and body each represent a distinct part of accepted
    displacement: price travelled, participation arrived, and the auction did
    not merely print a long wick.  The contract therefore requires all three;
    it does not search for whichever component happened to perform best.
    """

    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if minimum_history <= 0:
        raise ValueError("minimum_history must be positive")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be in [0, 1]")
    if not 0.0 <= body_floor <= 1.0:
        raise ValueError("body_floor must be in [0, 1]")

    pair_count = min(len(prior_ranges), len(prior_volumes))
    ranges = _finite(prior_ranges[-min(pair_count, lookback) :])
    volumes = _finite(prior_volumes[-min(pair_count, lookback) :])
    ready = len(ranges) >= minimum_history and len(volumes) >= minimum_history
    if not enabled:
        return QualityAssessment(
            enabled=False,
            ready=ready,
            passed=True,
            history_size=min(len(ranges), len(volumes)),
            lookback=lookback,
            quantile=quantile,
            range_value=float(current_range),
            range_threshold=None,
            range_percentile=empirical_percentile(ranges, float(current_range)),
            volume_value=float(current_volume),
            volume_threshold=None,
            volume_percentile=empirical_percentile(volumes, float(current_volume)),
            body_fraction=float(current_body_fraction),
            body_floor=body_floor,
        )

    if not ready:
        return QualityAssessment(
            enabled=True,
            ready=False,
            passed=False,
            history_size=min(len(ranges), len(volumes)),
            lookback=lookback,
            quantile=quantile,
            range_value=float(current_range),
            range_threshold=None,
            range_percentile=empirical_percentile(ranges, float(current_range)),
            volume_value=float(current_volume),
            volume_threshold=None,
            volume_percentile=empirical_percentile(volumes, float(current_volume)),
            body_fraction=float(current_body_fraction),
            body_floor=body_floor,
        )

    range_threshold = linear_quantile(ranges, quantile)
    volume_threshold = linear_quantile(volumes, quantile)
    passed = (
        float(current_range) >= range_threshold
        and float(current_volume) >= volume_threshold
        and float(current_body_fraction) >= body_floor
    )
    return QualityAssessment(
        enabled=True,
        ready=True,
        passed=passed,
        history_size=min(len(ranges), len(volumes)),
        lookback=lookback,
        quantile=quantile,
        range_value=float(current_range),
        range_threshold=range_threshold,
        range_percentile=empirical_percentile(ranges, float(current_range)),
        volume_value=float(current_volume),
        volume_threshold=volume_threshold,
        volume_percentile=empirical_percentile(volumes, float(current_volume)),
        body_fraction=float(current_body_fraction),
        body_floor=body_floor,
    )


@dataclass(slots=True)
class DirectionalFreshnessClock:
    """Track completed-close progress inside one accepted directional context."""

    direction: str
    last_close_extreme: float
    last_refresh_index: int

    def __post_init__(self) -> None:
        if self.direction not in {"LONG", "SHORT"}:
            raise ValueError(f"unsupported direction: {self.direction}")

    def observe(self, *, close: float, index: int) -> bool:
        value = float(close)
        refreshed = (
            value > self.last_close_extreme
            if self.direction == "LONG"
            else value < self.last_close_extreme
        )
        if refreshed:
            self.last_close_extreme = value
            self.last_refresh_index = int(index)
        return refreshed

    def age(self, index: int) -> int:
        return max(0, int(index) - int(self.last_refresh_index))

    def is_stale(self, *, index: int, maximum_age_bars: int) -> bool:
        if maximum_age_bars <= 0:
            raise ValueError("maximum_age_bars must be positive")
        return self.age(index) > int(maximum_age_bars)
