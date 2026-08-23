#!/usr/bin/env python3
"""Pure causal target-selection logic for candidate 3b.

The input is a completed auction state.  The function never sees a future fill or
outcome.  It decides whether the state has already proved enough directional delivery
to arm a first-return order and, if so, which immutable completion target is justified.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

EPS = 1e-12
ACCEPTED = "ACCEPTED_AUCTION_CONTINUATION"
FIRST_RETEST = "FIRST_RETEST_FORMING"

# These are not independent pattern strategies.  They are locations at which the
# accepted-auction first return contains both a liquidity boundary and an execution
# footprint, matching the source material's requirement that FVG/OB refine a meaningful
# liquidity event instead of voting on direction alone.
CONFLUENT_LOCATIONS = frozenset(
    {
        "BOUNDARY_FVG_OVERLAP",
        "TRANSFERRED_BOUNDARY_OB_OVERLAP",
        "BOUNDARY_FVG_OVERLAP_OB_OVERLAP",
    }
)

DELIVERED_TARGET_R = 1.50
DELIVERED_PROOF_BUFFER_R = 0.25
DELIVERED_ROUTE_UTILIZATION_MAX = 0.20
CONFLUENT_TARGET_R = 1.20
CONFLUENT_PROOF_R = 1.20
CONFLUENT_EFFICIENCY_MIN = 2.0


@dataclass(frozen=True, slots=True)
class EvidenceTier:
    scenario_family: str
    scenario_rank: int
    target_r: float
    proof_required_r: float
    route_required_r: float


def minimum_target_net_r(target_r: float) -> float:
    """Require executable reward, not merely a nominal gross-R label.

    At least one third of the nominal target and at least 0.30R must remain after the
    inherited maker/taker fees and stop slippage.  This rejects ultra-tight geometric
    stops whose apparent RR is mostly consumed by execution cost.
    """
    return max(0.30, float(target_r) / 3.0)


def evidence_tiers(
    *,
    family: str,
    location_kind: str,
    auction_phase: str,
    best_progress_r: float,
    effort_result: float,
    route_rr: float,
) -> Iterable[EvidenceTier]:
    """Yield justified targets in causal priority order."""
    if str(family) != ACCEPTED:
        return ()

    best = float(best_progress_r)
    route = float(route_rr)
    effort = float(effort_result)
    output: list[EvidenceTier] = []

    delivered_route_required = DELIVERED_TARGET_R / DELIVERED_ROUTE_UTILIZATION_MAX
    if (
        best + EPS >= DELIVERED_TARGET_R + DELIVERED_PROOF_BUFFER_R
        and route + EPS >= delivered_route_required
    ):
        output.append(
            EvidenceTier(
                scenario_family="PROVEN_DELIVERY_DEEP_ROUTE",
                scenario_rank=1,
                target_r=DELIVERED_TARGET_R,
                proof_required_r=DELIVERED_TARGET_R + DELIVERED_PROOF_BUFFER_R,
                route_required_r=delivered_route_required,
            )
        )

    if (
        str(location_kind) in CONFLUENT_LOCATIONS
        and str(auction_phase) == FIRST_RETEST
        and best + EPS >= CONFLUENT_PROOF_R
        and effort + EPS >= CONFLUENT_EFFICIENCY_MIN
        and route + EPS >= CONFLUENT_TARGET_R
    ):
        output.append(
            EvidenceTier(
                scenario_family="CONFLUENT_FIRST_RETURN_COMPLETION",
                scenario_rank=0,
                target_r=CONFLUENT_TARGET_R,
                proof_required_r=CONFLUENT_PROOF_R,
                route_required_r=CONFLUENT_TARGET_R,
            )
        )
    return tuple(output)


def directional_target(
    *, entry: float, stop: float, side: str, target_r: float, tick: float
) -> float:
    """Return a tick-valid target rounded toward entry, never overstating planned RR."""
    entry = float(entry)
    stop = float(stop)
    tick = float(tick)
    risk = abs(entry - stop)
    sign = 1.0 if str(side) == "LONG" else -1.0
    raw = entry + sign * float(target_r) * risk
    if tick <= 0.0:
        return raw
    units = raw / tick
    if sign > 0.0:
        return math.floor(units + 1e-12) * tick
    return math.ceil(units - 1e-12) * tick


def route_is_clear(*, side: str, target: float, route_price: float, tick: float) -> bool:
    if str(side) == "LONG":
        return float(target) <= float(route_price) + float(tick) + EPS
    return float(target) >= float(route_price) - float(tick) - EPS
