"""Direction-prior / liquidity-event-posterior extension of coherent auction v4.

A skilled trader carries a directional map before the raid, then updates it after the
fakeout/acceptance sequence.  This module records both states explicitly.  It does not
force prior agreement: a failed auction can legitimately overturn the prior, while an
accepted auction can strengthen it.  The blocked learner determines how each branch
uses that update without seeing asset or period identity.
"""
from __future__ import annotations

from typing import Any

import coherent_policy as core
import coherent_system as v3
import coherent_system_v4 as v4


POLICY = v4.POLICY + ":PRE_EVENT_DIRECTION_PRIOR_AND_POST_EVENT_UPDATE"
_ORIGINAL_COMMON = v4._common_features


def _prefix(values: dict[str, Any], prefix: str) -> dict[str, Any]:
    return {f"{prefix}{key}": value for key, value in values.items()}


def hierarchical_common_features(
    data,
    levels,
    metadata,
    source,
    setup,
    response,
    event_meta,
    obstacle,
    route_features,
    entry,
    stop,
):
    features = _ORIGINAL_COMMON(
        data,
        levels,
        metadata,
        source,
        setup,
        response,
        event_meta,
        obstacle,
        route_features,
        entry,
        stop,
    )
    prior_index = max(0, int(setup.interaction_index) - 1)
    posterior_index = int(response["response_index"])
    prior_semantic = v3._semantic_map_features(data, levels, metadata, prior_index)
    prior_liquidity = core._liquidity_map_features(data, levels, prior_index)
    prior_structure = core._active_structure_features(data, levels, prior_index)
    prior_state = core._row_state_features(data, prior_index, setup.side, "state")
    features.update(_prefix(prior_semantic, "prior_"))
    features.update(_prefix(prior_liquidity, "prior_"))
    features.update(_prefix(prior_structure, "prior_"))
    features.update(_prefix(prior_state, "prior_"))
    features["prior_to_interaction_minutes"] = float(setup.interaction_index - prior_index)
    features["prior_structure_liquidity_direction"] = (
        float(prior_semantic.get("semantic_attraction_normalized", 0.0))
        + float(prior_structure.get("structure_multiscale_trend_vote", 0.0))
    ) / 2.0
    features["posterior_structure_liquidity_direction"] = (
        float(features.get("semantic_attraction_normalized", 0.0))
        + float(features.get("structure_multiscale_trend_vote", 0.0))
    ) / 2.0
    features["event_direction_update"] = (
        features["posterior_structure_liquidity_direction"]
        - features["prior_structure_liquidity_direction"]
    )
    for key in (
        "semantic_attraction_normalized",
        "structure_multiscale_trend_vote",
        "structure_multiscale_trend_agreement",
        "dealing_range_position",
        "liquidity_attraction_normalized",
    ):
        if key in features and f"prior_{key}" in features:
            features[f"event_update_{key}"] = float(features[key]) - float(features[f"prior_{key}"])
    return features


v4._common_features = hierarchical_common_features
run_research = v4.run_research
generate_symbol = v4.generate_symbol
label_action = v4.label_action
MAX_HOLD_MINUTES = v4.MAX_HOLD_MINUTES
LIMIT_EXPIRY_MINUTES = v4.LIMIT_EXPIRY_MINUTES

__all__ = ["POLICY", "run_research", "generate_symbol", "label_action"]
