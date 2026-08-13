"""Final lazy research registry including complete-causal-episode risk."""
from __future__ import annotations

from dataclasses import dataclass

from research_variant_registry_v11 import VARIANTS as BASE_VARIANTS


@dataclass(frozen=True, slots=True)
class ResearchVariantSpecV12:
    bundle: str
    strategy: str
    families: str | None = None
    mechanisms: str | None = None


VARIANTS: dict[str, ResearchVariantSpecV12] = {
    name: ResearchVariantSpecV12(
        bundle=spec.bundle,
        strategy=spec.strategy,
        families=spec.families,
        mechanisms=spec.mechanisms,
    )
    for name, spec in BASE_VARIANTS.items()
}

DECISION = "execution_re1_management:EasyChartRE1DecisionSwingStrategy"
STATIC = "execution_re1_management:EasyChartRE1StaticStrategy"
INVALIDATION = "execution_re1_invalidation:EasyChartRE1InvalidationDecisionStrategy"
GEOMETRY = "easychart_re1_episode_geometry:EasyChartRE1EpisodeGeometryBundle"
DECISION_AREA = "easychart_re1_episode_geometry:EasyChartRE1EpisodeDecisionAreaBundle"


VARIANTS.update(
    {
        "episode-geometry-5m": ResearchVariantSpecV12(GEOMETRY, DECISION),
        "episode-geometry-static": ResearchVariantSpecV12(GEOMETRY, STATIC),
        "episode-geometry-invalidation": ResearchVariantSpecV12(GEOMETRY, INVALIDATION),
        "episode-decision-area-5m": ResearchVariantSpecV12(DECISION_AREA, DECISION),
        "episode-decision-area-static": ResearchVariantSpecV12(DECISION_AREA, STATIC),
        "episode-decision-area-invalidation": ResearchVariantSpecV12(
            DECISION_AREA,
            INVALIDATION,
        ),
    },
)
