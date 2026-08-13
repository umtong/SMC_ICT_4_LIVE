"""Frozen production registry extended with proven-router structural variants."""
from __future__ import annotations

from variant_registry import VARIANTS as BASE_VARIANTS, VariantSpec


DECISION = "execution_re1_venue_safe:EasyChartRE1VenueSafeDecisionStrategy"
STATIC = "execution_re1_venue_safe:EasyChartRE1VenueSafeStaticStrategy"
INVALIDATION = "execution_re1_venue_safe:EasyChartRE1VenueSafeInvalidationStrategy"
PAPER_DECISION = "paper_re1_generic:EasyChartRE1VenueSafeDecisionPaperStrategy"
PAPER_STATIC = "paper_re1_generic:EasyChartRE1VenueSafeStaticPaperStrategy"
PAPER_INVALIDATION = "paper_re1_generic:EasyChartRE1VenueSafeInvalidationPaperStrategy"


VARIANTS: dict[str, VariantSpec] = dict(BASE_VARIANTS)
VARIANTS.update(
    {
        "geometry-v2-5m": VariantSpec(
            bundle="easychart_re1_geometry_v2:EasyChartRE1GeometryV2Bundle",
            venue_strategy=DECISION,
            paper_strategy=PAPER_DECISION,
        ),
        "geometry-v2-static": VariantSpec(
            bundle="easychart_re1_geometry_v2:EasyChartRE1GeometryV2Bundle",
            venue_strategy=STATIC,
            paper_strategy=PAPER_STATIC,
        ),
        "geometry-v2-invalidation": VariantSpec(
            bundle="easychart_re1_geometry_v2:EasyChartRE1GeometryV2Bundle",
            venue_strategy=INVALIDATION,
            paper_strategy=PAPER_INVALIDATION,
        ),
        "zone-v2-5m": VariantSpec(
            bundle="easychart_re1_zone_targets_v2:EasyChartRE1ZoneTargetV2Bundle",
            venue_strategy=DECISION,
            paper_strategy=PAPER_DECISION,
        ),
        "zone-v2-static": VariantSpec(
            bundle="easychart_re1_zone_targets_v2:EasyChartRE1ZoneTargetV2Bundle",
            venue_strategy=STATIC,
            paper_strategy=PAPER_STATIC,
        ),
        "zone-v2-invalidation": VariantSpec(
            bundle="easychart_re1_zone_targets_v2:EasyChartRE1ZoneTargetV2Bundle",
            venue_strategy=INVALIDATION,
            paper_strategy=PAPER_INVALIDATION,
        ),
        "decision-area-v2-5m": VariantSpec(
            bundle="easychart_re1_decision_area_v2:EasyChartRE1DecisionAreaV2Bundle",
            venue_strategy=DECISION,
            paper_strategy=PAPER_DECISION,
        ),
        "decision-area-v2-invalidation": VariantSpec(
            bundle="easychart_re1_decision_area_v2:EasyChartRE1DecisionAreaV2Bundle",
            venue_strategy=INVALIDATION,
            paper_strategy=PAPER_INVALIDATION,
        ),
        "displacement-v2-5m": VariantSpec(
            bundle="easychart_re1_displacement_v2:EasyChartRE1DisplacementV2Bundle",
            venue_strategy=DECISION,
            paper_strategy=PAPER_DECISION,
        ),
        "displacement-v2-invalidation": VariantSpec(
            bundle="easychart_re1_displacement_v2:EasyChartRE1DisplacementV2Bundle",
            venue_strategy=INVALIDATION,
            paper_strategy=PAPER_INVALIDATION,
        ),
    },
)
