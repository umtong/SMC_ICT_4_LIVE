"""Structural auction control v4: structural destination and stop provenance.

The earlier auction branches often generated a lattice of RR targets and entry
variants and then learned which plan to select. V4 forbids that abstraction from
leaking back through a reused sensor. A proposal may inherit geometry, but an
explicit synthetic fixed-R/percentage/ATR target or stop cannot own an episode.
When duplicate representations remain, target provenance is ordered by the first
causal obstacle rather than by a fitted plan score.
"""
from __future__ import annotations

import math
from typing import Any

from contracts_v5 import V5TradePlan
from structural_auction_control_v2 import (
    StructuralProposal,
    _descriptor,
    _has_any,
    _price_geometry,
    _text,
)
from structural_auction_control_v3 import StructuralAuctionControlV3Bundle as _Base


SYNTHETIC_GEOMETRY_TOKENS = (
    "FIXED_RR",
    "RR_LATTICE",
    "R_MULTIPLE_TARGET",
    "PERCENT_TARGET",
    "ATR_TARGET",
    "FIXED_PERCENT_TARGET",
    "FIXED_STOP",
    "PERCENT_STOP",
    "ATR_STOP",
    "CLOCK_STOP",
)


def _target_descriptor(plan: V5TradePlan) -> str:
    values = (
        getattr(plan, "target_kind", ""),
        getattr(plan, "target_zone_kind", ""),
        getattr(plan, "target_zone_id", ""),
        getattr(plan, "objective_kind", ""),
        getattr(plan, "objective_id", ""),
        getattr(plan, "target_provenance", ""),
        getattr(plan, "family", ""),
    )
    return "|".join(_text(value) for value in values)


def _destination_rank(plan: V5TradePlan) -> int:
    text = _target_descriptor(plan)
    if _has_any(text, ("OPPOSITE_CHANNEL", "CHANNEL_EDGE", "CHANNEL_MID", "CHANNEL_OBJECTIVE")):
        return 0
    if _has_any(text, ("EQUAL_HIGH", "EQUAL_LOW", "SWING", "SESSION_HIGH", "SESSION_LOW", "LIQUIDITY")):
        return 1
    if _has_any(text, ("TRENDLINE", "TREND_LINE", "DIAGONAL")):
        return 2
    if _has_any(text, ("ORDER_BLOCK", "ORDERBLOCK", "FVG", "IMBALANCE")):
        return 3
    if _has_any(text, ("VOLUME_NODE", "POC", "VALUE_AREA", "VWAP")):
        return 4
    return 8


class StructuralAuctionControlV4Bundle(_Base):
    """V3 control state with non-synthetic destination/stop ownership."""

    def _proposal(self, plan: V5TradePlan, source: str) -> StructuralProposal | None:
        descriptor = _descriptor(plan)
        if _has_any(descriptor, SYNTHETIC_GEOMETRY_TOKENS):
            self._inc("explicit_synthetic_rr_or_stop_geometry_rejected")
            return None
        proposal = super()._proposal(plan, source)
        if proposal is None:
            return None
        return proposal

    def _geometry_rank(self, proposal: StructuralProposal) -> tuple[Any, ...]:
        plan = proposal.plan
        entry, _, target = _price_geometry(plan)
        distance = abs(target - entry) if math.isfinite(entry) and math.isfinite(target) else math.inf
        descriptor = _descriptor(plan)
        location_rank = 0 if _has_any(descriptor, ("ORDER_BLOCK", "ORDERBLOCK", "FVG", "IMBALANCE")) else 1
        return (
            self._PRIORITY[proposal.mechanism],
            _destination_rank(plan),
            location_rank,
            distance,
            proposal.observed_time_ns,
            plan.plan_id,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        base = super().diagnostics
        base["structural_auction_control_v4"] = {
            "geometry_rule": "explicit synthetic RR/ATR/percent/clock target or stop cannot own an episode",
            "destination_order": (
                "channel objective",
                "public swing/liquidity",
                "trend line",
                "OB/FVG imbalance",
                "volume node",
                "unknown inherited structural destination",
            ),
        }
        return base


MultiScaleScenarioBundle = StructuralAuctionControlV4Bundle
