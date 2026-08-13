"""Production registry extended with complete causal mechanism subsets."""
from __future__ import annotations

from dataclasses import dataclass

from variant_registry_v2 import VARIANTS as BASE_VARIANTS


@dataclass(frozen=True, slots=True)
class ProductionVariantSpecV3:
    bundle: str
    venue_strategy: str
    paper_strategy: str
    families: str | None = None
    mechanisms: str | None = None


VARIANTS: dict[str, ProductionVariantSpecV3] = {
    name: ProductionVariantSpecV3(
        bundle=spec.bundle,
        venue_strategy=spec.venue_strategy,
        paper_strategy=spec.paper_strategy,
        families=spec.families,
    )
    for name, spec in BASE_VARIANTS.items()
}

VENUE_DECISION = (
    "execution_re1_venue_mechanism:EasyChartRE1VenueSafeMechanismDecisionStrategy"
)
VENUE_INVALIDATION = (
    "execution_re1_venue_mechanism:EasyChartRE1VenueSafeMechanismInvalidationStrategy"
)
PAPER_DECISION = (
    "paper_re1_mechanism:EasyChartRE1VenueSafeMechanismDecisionPaperStrategy"
)
PAPER_INVALIDATION = (
    "paper_re1_mechanism:EasyChartRE1VenueSafeMechanismInvalidationPaperStrategy"
)
BUNDLE = "easychart_re1_decision_area_v2:EasyChartRE1DecisionAreaV2Bundle"


def add(
    name: str,
    mechanisms: str,
    venue: str = VENUE_DECISION,
    paper: str = PAPER_DECISION,
) -> None:
    VARIANTS[name] = ProductionVariantSpecV3(
        bundle=BUNDLE,
        venue_strategy=venue,
        paper_strategy=paper,
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
add(
    "mechanism-continuation-invalidation",
    CONTINUATION,
    VENUE_INVALIDATION,
    PAPER_INVALIDATION,
)
add("mechanism-rejection-5m", REJECTION)
add(
    "mechanism-rejection-invalidation",
    REJECTION,
    VENUE_INVALIDATION,
    PAPER_INVALIDATION,
)
add("mechanism-easychart-core-5m", EASYCHART_CORE)
add(
    "mechanism-easychart-core-invalidation",
    EASYCHART_CORE,
    VENUE_INVALIDATION,
    PAPER_INVALIDATION,
)
add("mechanism-liquidity-reversal-5m", LIQUIDITY_REVERSAL)
add(
    "mechanism-liquidity-reversal-invalidation",
    LIQUIDITY_REVERSAL,
    VENUE_INVALIDATION,
    PAPER_INVALIDATION,
)
