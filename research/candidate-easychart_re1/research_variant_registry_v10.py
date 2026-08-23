"""Lazy research registry extended with complete causal mechanism subsets."""
from __future__ import annotations

from dataclasses import dataclass

from research_variant_registry_v9 import VARIANTS as BASE_VARIANTS


@dataclass(frozen=True, slots=True)
class ResearchVariantSpecV10:
    bundle: str
    strategy: str
    families: str | None = None
    mechanisms: str | None = None


VARIANTS: dict[str, ResearchVariantSpecV10] = {
    name: ResearchVariantSpecV10(
        bundle=spec.bundle,
        strategy=spec.strategy,
        families=spec.families,
    )
    for name, spec in BASE_VARIANTS.items()
}

DECISION = "execution_re1_mechanism_filter:EasyChartRE1MechanismDecisionStrategy"
INVALIDATION = "execution_re1_mechanism_filter:EasyChartRE1MechanismInvalidationStrategy"
BUNDLE = "easychart_re1_decision_area_v2:EasyChartRE1DecisionAreaV2Bundle"


def add(name: str, mechanisms: str, strategy: str = DECISION) -> None:
    VARIANTS[name] = ResearchVariantSpecV10(
        bundle=BUNDLE,
        strategy=strategy,
        mechanisms=mechanisms,
    )


add("mechanism-diagonal-acceptance", "DIAGONAL_ACCEPTANCE")
add("mechanism-diagonal-rejection", "DIAGONAL_REJECTION")
add("mechanism-horizontal-acceptance", "HORIZONTAL_ACCEPTANCE")
add("mechanism-horizontal-rejection", "HORIZONTAL_REJECTION")
add("mechanism-horizontal-rotation", "HORIZONTAL_ROTATION")
add("mechanism-major-liquidity-rejection", "MAJOR_LIQUIDITY_REJECTION")
add("mechanism-decision-ob-rejection", "DECISION_AREA_OB_REJECTION")
add("mechanism-decision-ob-rotation", "DECISION_AREA_OB_ROTATION")

CONTINUATION = "DIAGONAL_ACCEPTANCE,HORIZONTAL_ACCEPTANCE,DECISION_AREA_OB_ROTATION"
REJECTION = (
    "DIAGONAL_REJECTION,HORIZONTAL_REJECTION,"
    "MAJOR_LIQUIDITY_REJECTION,DECISION_AREA_OB_REJECTION"
)
EASYCHART_CORE = (
    "DIAGONAL_ACCEPTANCE,HORIZONTAL_ACCEPTANCE,MAJOR_LIQUIDITY_REJECTION,"
    "DECISION_AREA_OB_REJECTION,DECISION_AREA_OB_ROTATION"
)
LIQUIDITY_REVERSAL = (
    "HORIZONTAL_REJECTION,MAJOR_LIQUIDITY_REJECTION,DECISION_AREA_OB_REJECTION"
)

add("mechanism-continuation-5m", CONTINUATION)
add("mechanism-continuation-invalidation", CONTINUATION, INVALIDATION)
add("mechanism-rejection-5m", REJECTION)
add("mechanism-rejection-invalidation", REJECTION, INVALIDATION)
add("mechanism-easychart-core-5m", EASYCHART_CORE)
add("mechanism-easychart-core-invalidation", EASYCHART_CORE, INVALIDATION)
add("mechanism-liquidity-reversal-5m", LIQUIDITY_REVERSAL)
add("mechanism-liquidity-reversal-invalidation", LIQUIDITY_REVERSAL, INVALIDATION)
