"""Prior-only order-flow surprise and realized impact-efficiency primitives.

The module keeps the new market claim independent from execution and accounting.
It consumes only completed higher-timeframe auctions.  The current auction is
never inserted into its own expectation or reference distributions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Iterable, Sequence

from adaptive_fresh_core import linear_quantile


def _finite(values: Iterable[float]) -> list[float]:
    result = [float(value) for value in values]
    if any(value != value for value in result):
        raise ValueError("surprise-impact reference values must not contain NaN")
    return result


@dataclass(frozen=True, slots=True)
class SurpriseImpactAssessment:
    use_surprise: bool
    use_impact_efficiency: bool
    ready: bool
    passed: bool
    classification: str
    history_size: int
    efficiency_history_size: int
    flow_quantile: float
    expected_flow: float | None
    current_flow: float
    directional_surprise: float | None
    surprise_threshold: float | None
    surprise_percentile: float
    directional_displacement_atr: float
    impact_efficiency: float | None
    impact_efficiency_threshold: float | None
    impact_efficiency_percentile: float

    def details(self) -> dict[str, float | int | bool | str | None]:
        return asdict(self)


def _percentile(values: Sequence[float], current: float) -> float:
    data = _finite(values)
    if not data:
        return 0.0
    below = sum(value < current for value in data)
    equal = sum(value == current for value in data)
    return (below + 0.5 * equal) / len(data)


def assess_surprise_impact(
    *,
    prior_flow_intensity: Sequence[float],
    prior_signed_displacement_atr: Sequence[float],
    current_flow_intensity: float,
    current_directional_displacement_atr: float,
    direction: str,
    use_surprise: bool,
    use_impact_efficiency: bool,
    lookback: int,
    minimum_history: int,
    flow_quantile: float,
    minimum_efficiency_history: int,
) -> SurpriseImpactAssessment:
    """Classify a completed accepted break as continuation or absorption.

    ``flow intensity`` is signed aggressive volume normalized by a sealed prior
    volume baseline.  Surprise is the deviation from the prior median, not raw
    buy/sell pressure.  Impact efficiency asks whether that unexpected pressure
    produced direction-consistent close-to-open displacement.  Large surprise
    with sub-median displacement-per-surprise is classified as absorption.
    """

    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported direction: {direction}")
    if lookback <= 0 or minimum_history <= 0 or minimum_efficiency_history <= 0:
        raise ValueError("history parameters must be positive")
    if not 0.0 <= flow_quantile <= 1.0:
        raise ValueError("flow_quantile must be in [0, 1]")

    pair_count = min(len(prior_flow_intensity), len(prior_signed_displacement_atr))
    size = min(pair_count, int(lookback))
    flows = _finite(prior_flow_intensity[-size:])
    displacements = _finite(prior_signed_displacement_atr[-size:])
    current_flow = float(current_flow_intensity)
    current_displacement = float(current_directional_displacement_atr)
    sign = 1.0 if direction == "LONG" else -1.0

    if len(flows) < minimum_history:
        return SurpriseImpactAssessment(
            use_surprise=bool(use_surprise),
            use_impact_efficiency=bool(use_impact_efficiency),
            ready=False,
            passed=False,
            classification="SURPRISE_IMPACT_WARMUP",
            history_size=len(flows),
            efficiency_history_size=0,
            flow_quantile=float(flow_quantile),
            expected_flow=None,
            current_flow=current_flow,
            directional_surprise=None,
            surprise_threshold=None,
            surprise_percentile=0.0,
            directional_displacement_atr=current_displacement,
            impact_efficiency=None,
            impact_efficiency_threshold=None,
            impact_efficiency_percentile=0.0,
        )

    expected = float(median(flows))
    residuals = [value - expected for value in flows]
    absolute_residuals = [abs(value) for value in residuals]
    surprise_threshold = linear_quantile(absolute_residuals, flow_quantile)
    directional_surprise = sign * (current_flow - expected)
    surprise_percentile = _percentile(absolute_residuals, max(directional_surprise, 0.0))

    historical_efficiencies: list[float] = []
    for residual, displacement in zip(residuals, displacements):
        magnitude = abs(residual)
        if magnitude <= 1e-12:
            continue
        residual_sign = 1.0 if residual > 0.0 else -1.0
        aligned_displacement = residual_sign * displacement
        if aligned_displacement > 0.0:
            historical_efficiencies.append(aligned_displacement / magnitude)

    efficiency_ready = (
        not use_impact_efficiency
        or len(historical_efficiencies) >= minimum_efficiency_history
    )
    if not efficiency_ready:
        return SurpriseImpactAssessment(
            use_surprise=bool(use_surprise),
            use_impact_efficiency=bool(use_impact_efficiency),
            ready=False,
            passed=False,
            classification="SURPRISE_IMPACT_EFFICIENCY_WARMUP",
            history_size=len(flows),
            efficiency_history_size=len(historical_efficiencies),
            flow_quantile=float(flow_quantile),
            expected_flow=expected,
            current_flow=current_flow,
            directional_surprise=directional_surprise,
            surprise_threshold=surprise_threshold,
            surprise_percentile=surprise_percentile,
            directional_displacement_atr=current_displacement,
            impact_efficiency=None,
            impact_efficiency_threshold=None,
            impact_efficiency_percentile=0.0,
        )

    directional_positive = directional_surprise > 1e-12
    surprise_passed = (
        directional_positive
        and (
            not use_surprise
            or directional_surprise >= max(surprise_threshold, 0.0)
        )
    )
    efficiency_threshold = (
        float(median(historical_efficiencies))
        if historical_efficiencies
        else None
    )
    impact_efficiency = (
        max(current_displacement, 0.0) / directional_surprise
        if directional_positive
        else None
    )
    impact_percentile = (
        _percentile(historical_efficiencies, impact_efficiency)
        if historical_efficiencies and impact_efficiency is not None
        else 0.0
    )
    efficiency_passed = (
        not use_impact_efficiency
        or (
            impact_efficiency is not None
            and efficiency_threshold is not None
            and impact_efficiency >= efficiency_threshold
        )
    )

    if not surprise_passed:
        classification = "DIRECTIONAL_FLOW_NOT_SURPRISING_TO_PRIOR_EXPECTATION"
        passed = False
    elif not efficiency_passed:
        classification = "FLOW_SURPRISE_ABSORBED_WITH_WEAK_PRICE_RESPONSE"
        passed = False
    else:
        classification = "FLOW_SURPRISE_CONVERTED_TO_EFFECTIVE_DISPLACEMENT"
        passed = True

    return SurpriseImpactAssessment(
        use_surprise=bool(use_surprise),
        use_impact_efficiency=bool(use_impact_efficiency),
        ready=True,
        passed=passed,
        classification=classification,
        history_size=len(flows),
        efficiency_history_size=len(historical_efficiencies),
        flow_quantile=float(flow_quantile),
        expected_flow=expected,
        current_flow=current_flow,
        directional_surprise=directional_surprise,
        surprise_threshold=surprise_threshold,
        surprise_percentile=surprise_percentile,
        directional_displacement_atr=current_displacement,
        impact_efficiency=impact_efficiency,
        impact_efficiency_threshold=efficiency_threshold,
        impact_efficiency_percentile=impact_percentile,
    )
