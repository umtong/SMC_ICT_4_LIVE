#!/usr/bin/env python3
"""Candidate-1k exact-route harvester with an immutable 1.5 net-R realization barrier.

The inherited generator discovers direction, causal liquidity episode, source, entry
origin, structural invalidation and the first still-live opposing obstacle.  This wrapper
changes only the whole-position realization target when that obstacle lies beyond the
day-trading horizon.  The capped price is solved through the source cost model and fixed
before future bars label the already-immutable order.
"""
from __future__ import annotations

import math
from typing import Any

import candidate_1k_harvest as source

MIN_TARGET_NET_R = 1.0
MAX_REALIZED_TARGET_NET_R = 1.5
ORIGINAL_TARGET_PLAN = source._target_plan


def _economics_at(
    side: str,
    entry: float,
    stop: float,
    target: float,
    tick: float,
) -> dict[str, float] | None:
    return source.core._raw_economics(side, entry, stop, target, tick)


def _capped_target(
    side: str,
    entry: float,
    stop: float,
    structural_target: float,
    tick: float,
) -> tuple[float, dict[str, float]]:
    structural = _economics_at(side, entry, stop, structural_target, tick)
    if structural is None:
        raise ValueError("source structural target lacks executable economics")
    if float(structural["target_net_r"]) <= MAX_REALIZED_TARGET_NET_R + 1e-12:
        return structural_target, structural

    if side == "LONG":
        low, high = float(entry), float(structural_target)
        for _ in range(64):
            middle = (low + high) / 2.0
            economics = _economics_at(side, entry, stop, middle, tick)
            if (
                economics is None
                or float(economics["target_net_r"]) < MAX_REALIZED_TARGET_NET_R
            ):
                low = middle
            else:
                high = middle
        target = high
    else:
        low, high = float(structural_target), float(entry)
        for _ in range(64):
            middle = (low + high) / 2.0
            economics = _economics_at(side, entry, stop, middle, tick)
            if (
                economics is None
                or float(economics["target_net_r"]) < MAX_REALIZED_TARGET_NET_R
            ):
                high = middle
            else:
                low = middle
        target = low

    if side == "LONG":
        target = math.floor(float(target) / float(tick) + 1e-12) * float(tick)
    else:
        target = math.ceil(float(target) / float(tick) - 1e-12) * float(tick)
    economics = _economics_at(side, entry, stop, target, tick)
    if economics is None:
        raise ValueError("capped target lacks executable economics")
    return float(target), economics


def target_plan(
    data: Any,
    levels: Any,
    metadata: dict[str, Any],
    candidate: Any,
    entry: float,
    stop: float,
    tick: float,
):
    plan = ORIGINAL_TARGET_PLAN(data, levels, metadata, candidate, entry, stop, tick)
    if plan is None:
        return None
    obstacle, route_features, structural_target, _gross_rr, _economics = plan
    side = str(candidate.setup.side)
    target, economics = _capped_target(
        side,
        float(entry),
        float(stop),
        float(structural_target),
        float(tick),
    )
    risk = abs(float(entry) - float(stop))
    gross_rr = abs(float(target) - float(entry)) / max(risk, source.EPS)
    if not math.isfinite(gross_rr) or gross_rr + 1e-12 < 1.0:
        return None
    if float(economics["target_net_r"]) + 1e-12 < MIN_TARGET_NET_R:
        return None

    route_features = dict(route_features)
    route_features["structural_route_target_price"] = float(structural_target)
    route_features["realization_target_price"] = float(target)
    route_features["route_realization_fraction"] = (
        abs(float(target) - float(entry))
        / max(abs(float(structural_target) - float(entry)), source.EPS)
    )
    return obstacle, route_features, target, gross_rr, economics


source._target_plan = target_plan
source.core.POLICY = (
    "ML_EASYCHART_B_CAUSAL_LIQUIDITY_CONTROL_"
    "EXACT_ROUTE_IMMUTABLE_1P5_NET_R_REALIZATION"
)


if __name__ == "__main__":
    source.core.main()
