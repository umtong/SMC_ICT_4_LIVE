"""Production registry for final non-duplicated structural/mechanism candidates."""
from __future__ import annotations

from dataclasses import dataclass

from variant_registry_v3 import VARIANTS as BASE_VARIANTS


@dataclass(frozen=True, slots=True)
class ProductionVariantSpecV4:
    bundle: str
    venue_strategy: str
    paper_strategy: str
    families: str | None = None
    mechanisms: str | None = None


VARIANTS: dict[str, ProductionVariantSpecV4] = {
    name: ProductionVariantSpecV4(
        bundle=spec.bundle,
        venue_strategy=spec.venue_strategy,
        paper_strategy=spec.paper_strategy,
        families=spec.families,
        mechanisms=spec.mechanisms,
    )
    for name, spec in BASE_VARIANTS.items()
}

VENUE_DECISION = "execution_re1_venue_safe:EasyChartRE1VenueSafeDecisionStrategy"
VENUE_STATIC = "execution_re1_venue_safe:EasyChartRE1VenueSafeStaticStrategy"
VENUE_INVALIDATION = "execution_re1_venue_safe:EasyChartRE1VenueSafeInvalidationStrategy"
PAPER_DECISION = "paper_re1_generic:EasyChartRE1VenueSafeDecisionPaperStrategy"
PAPER_STATIC = "paper_re1_generic:EasyChartRE1VenueSafeStaticPaperStrategy"
PAPER_INVALIDATION = "paper_re1_generic:EasyChartRE1VenueSafeInvalidationPaperStrategy"
VENUE_MECHANISM_DECISION = (
    "execution_re1_venue_mechanism:EasyChartRE1VenueSafeMechanismDecisionStrategy"
)
VENUE_MECHANISM_INVALIDATION = (
    "execution_re1_venue_mechanism:EasyChartRE1VenueSafeMechanismInvalidationStrategy"
)
PAPER_MECHANISM_DECISION = (
    "paper_re1_mechanism:EasyChartRE1VenueSafeMechanismDecisionPaperStrategy"
)
PAPER_MECHANISM_INVALIDATION = (
    "paper_re1_mechanism:EasyChartRE1VenueSafeMechanismInvalidationPaperStrategy"
)


def structural(name: str, bundle: str, venue: str, paper: str) -> None:
    VARIANTS[name] = ProductionVariantSpecV4(
        bundle=bundle,
        venue_strategy=venue,
        paper_strategy=paper,
    )


structural(
    "geometry-v3-5m",
    "easychart_re1_geometry_v3:EasyChartRE1GeometryV3Bundle",
    VENUE_DECISION,
    PAPER_DECISION,
)
structural(
    "geometry-v3-static",
    "easychart_re1_geometry_v3:EasyChartRE1GeometryV3Bundle",
    VENUE_STATIC,
    PAPER_STATIC,
)
structural(
    "geometry-v3-invalidation",
    "easychart_re1_geometry_v3:EasyChartRE1GeometryV3Bundle",
    VENUE_INVALIDATION,
    PAPER_INVALIDATION,
)
structural(
    "zone-v3-5m",
    "easychart_re1_zone_targets_v3:EasyChartRE1ZoneTargetV3Bundle",
    VENUE_DECISION,
    PAPER_DECISION,
)
structural(
    "zone-v3-static",
    "easychart_re1_zone_targets_v3:EasyChartRE1ZoneTargetV3Bundle",
    VENUE_STATIC,
    PAPER_STATIC,
)
structural(
    "zone-v3-invalidation",
    "easychart_re1_zone_targets_v3:EasyChartRE1ZoneTargetV3Bundle",
    VENUE_INVALIDATION,
    PAPER_INVALIDATION,
)
structural(
    "decision-area-v3-5m",
    "easychart_re1_decision_area_v3:EasyChartRE1DecisionAreaV3Bundle",
    VENUE_DECISION,
    PAPER_DECISION,
)
structural(
    "decision-area-v3-invalidation",
    "easychart_re1_decision_area_v3:EasyChartRE1DecisionAreaV3Bundle",
    VENUE_INVALIDATION,
    PAPER_INVALIDATION,
)
structural(
    "displacement-v3-5m",
    "easychart_re1_displacement_v3:EasyChartRE1DisplacementV3Bundle",
    VENUE_DECISION,
    PAPER_DECISION,
)
structural(
    "displacement-v3-invalidation",
    "easychart_re1_displacement_v3:EasyChartRE1DisplacementV3Bundle",
    VENUE_INVALIDATION,
    PAPER_INVALIDATION,
)

MECHANISM_BUNDLE = "easychart_re1_decision_area_v3:EasyChartRE1DecisionAreaV3Bundle"


def mechanism(
    name: str,
    enabled: str,
    venue: str = VENUE_MECHANISM_DECISION,
    paper: str = PAPER_MECHANISM_DECISION,
) -> None:
    VARIANTS[name] = ProductionVariantSpecV4(
        bundle=MECHANISM_BUNDLE,
        venue_strategy=venue,
        paper_strategy=paper,
        mechanisms=enabled,
    )


mechanism("mechanism-v3-diagonal-acceptance", "DIAGONAL_ACCEPTANCE")
mechanism("mechanism-v3-diagonal-rejection", "DIAGONAL_REJECTION")
mechanism("mechanism-v3-horizontal-acceptance", "HORIZONTAL_ACCEPTANCE")
mechanism("mechanism-v3-horizontal-rejection", "HORIZONTAL_REJECTION")
mechanism("mechanism-v3-horizontal-rotation", "HORIZONTAL_ROTATION")
mechanism("mechanism-v3-major-liquidity-rejection", "MAJOR_LIQUIDITY_REJECTION")
mechanism("mechanism-v3-decision-ob-rejection", "DECISION_AREA_OB_REJECTION")
mechanism("mechanism-v3-decision-ob-rotation", "DECISION_AREA_OB_ROTATION")

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

mechanism("mechanism-v3-continuation-5m", CONTINUATION)
mechanism(
    "mechanism-v3-continuation-invalidation",
    CONTINUATION,
    VENUE_MECHANISM_INVALIDATION,
    PAPER_MECHANISM_INVALIDATION,
)
mechanism("mechanism-v3-rejection-5m", REJECTION)
mechanism(
    "mechanism-v3-rejection-invalidation",
    REJECTION,
    VENUE_MECHANISM_INVALIDATION,
    PAPER_MECHANISM_INVALIDATION,
)
mechanism("mechanism-v3-easychart-core-5m", EASYCHART_CORE)
mechanism(
    "mechanism-v3-easychart-core-invalidation",
    EASYCHART_CORE,
    VENUE_MECHANISM_INVALIDATION,
    PAPER_MECHANISM_INVALIDATION,
)
mechanism("mechanism-v3-liquidity-reversal-5m", LIQUIDITY_REVERSAL)
mechanism(
    "mechanism-v3-liquidity-reversal-invalidation",
    LIQUIDITY_REVERSAL,
    VENUE_MECHANISM_INVALIDATION,
    PAPER_MECHANISM_INVALIDATION,
)
