#!/usr/bin/env python3
"""Structural V4 harvester: first still-live causal completion, never an R cap.

V3 already discovers the liquidity episode, side, source entry and structural
invalidation.  This wrapper broadens the target lattice with structures a trader
could actually see at arm time:

* confirmed local pivot liquidity;
* unconsumed pre-event range/session boundary;
* the event impulse reclaim;
* the inherited opposing live frontier.

The nearest executable structure is chosen.  A 1R gross floor and positive
cost-adjusted completion are admission tests only; no target is manufactured,
shortened or extended to a fixed R.
"""
from __future__ import annotations

import inspect
import math
from typing import Any

import numpy as np
import pandas as pd

import harvest_structural as base

POLICY = "ML_EASYCHART_B_V4_CAUSAL_TRIGGER_NEAREST_LIVE_STRUCTURE"
MIN_NET_COMPLETION_R = 0.25
ORIGINAL_STRUCTURAL_TARGET = base._structural_target_at_arm
ORIGINAL_SIGNATURE = inspect.signature(ORIGINAL_STRUCTURAL_TARGET)


def _value(bound: inspect.BoundArguments, *names: str) -> Any:
    for name in names:
        if name in bound.arguments:
            return bound.arguments[name]
    return None


def _setup_attr(candidate: Any, name: str, default: Any = None) -> Any:
    if candidate is None:
        return default
    if hasattr(candidate, name):
        return getattr(candidate, name)
    setup = getattr(candidate, "setup", None)
    if setup is not None and hasattr(setup, name):
        return getattr(setup, name)
    return default


def _side(candidate: Any) -> str:
    value = _setup_attr(candidate, "side", "")
    text = str(value).upper()
    return "LONG" if "LONG" in text else "SHORT"


def _inside_level(side: str, level: float, tick: float) -> float:
    if side == "LONG":
        raw = float(level) - float(tick)
        return math.floor(raw / tick + 1e-12) * tick
    raw = float(level) + float(tick)
    return math.ceil(raw / tick - 1e-12) * tick


def _economics(
    side: str,
    entry: float,
    stop: float,
    target: float,
    tick: float,
) -> tuple[float, dict[str, float]] | None:
    try:
        economics = base.core._raw_economics(side, entry, stop, target, tick)
    except Exception:
        economics = None
    if economics is None:
        return None
    risk = abs(float(entry) - float(stop))
    if not math.isfinite(risk) or risk <= 0.0:
        return None
    gross_rr = abs(float(target) - float(entry)) / risk
    if not math.isfinite(gross_rr) or gross_rr + 1e-12 < 1.0:
        return None
    if float(economics.get("target_net_r", -math.inf)) + 1e-12 < MIN_NET_COMPLETION_R:
        return None
    return float(gross_rr), economics


def _not_consumed(
    data: pd.DataFrame,
    side: str,
    start: int,
    arm: int,
    target: float,
) -> bool:
    if arm < start:
        return True
    window = data.iloc[max(0, int(start)) : int(arm) + 1]
    if window.empty:
        return True
    if side == "LONG":
        return float(pd.to_numeric(window["high"], errors="coerce").max()) < target
    return float(pd.to_numeric(window["low"], errors="coerce").min()) > target


def _obstacle(
    kind: str,
    price: float,
    created_ns: int,
    evidence: dict[str, Any],
) -> Any:
    obstacle_type = base.core.v4.Obstacle
    return obstacle_type(
        kind=kind,
        price=float(price),
        created_ns=int(created_ns),
        evidence=evidence,
    )


def _candidate_record(
    *,
    data: pd.DataFrame,
    side: str,
    entry: float,
    stop: float,
    tick: float,
    level: float,
    kind: str,
    created_index: int,
    live_start: int,
    arm: int,
    evidence: dict[str, Any],
) -> tuple[Any, dict[str, Any], float, float, dict[str, float]] | None:
    target = _inside_level(side, float(level), float(tick))
    if side == "LONG" and target <= entry:
        return None
    if side == "SHORT" and target >= entry:
        return None
    if not _not_consumed(data, side, live_start, arm, target):
        return None
    computed = _economics(side, entry, stop, target, tick)
    if computed is None:
        return None
    gross_rr, economics = computed
    created_index = max(0, min(int(created_index), len(data) - 1))
    created_ns = int(pd.Timestamp(data.index[created_index]).value)
    obstacle = _obstacle(kind, target, created_ns, evidence)
    route_features = {
        "structural_target_provenance": kind,
        "structural_target_created_index": int(created_index),
        "structural_target_age_bars": int(max(0, arm - created_index)),
        "structural_target_level_price": float(level),
        "structural_target_price": float(target),
        "structural_target_distance_bps": (
            abs(float(target) - float(entry)) / max(abs(float(entry)), 1e-12) * 10_000.0
        ),
        **evidence,
    }
    return obstacle, route_features, float(target), float(gross_rr), economics


def _pivot_candidates(
    data: pd.DataFrame,
    candidate: Any,
    arm: int,
    entry: float,
    stop: float,
    tick: float,
) -> list[tuple[Any, dict[str, Any], float, float, dict[str, float]]]:
    side = _side(candidate)
    start = max(3, int(arm) - 480)
    end = int(arm) - 2
    if end <= start:
        return []
    high = pd.to_numeric(data["high"], errors="coerce").to_numpy(float)
    low = pd.to_numeric(data["low"], errors="coerce").to_numpy(float)
    volume_source = (
        data["volume"]
        if "volume" in data
        else pd.Series(1.0, index=data.index, dtype=float)
    )
    volume = pd.to_numeric(volume_source, errors="coerce").to_numpy(float)
    found: list[tuple[Any, dict[str, Any], float, float, dict[str, float]]] = []
    # Two right-hand bars make each local extreme observable before the arm.
    for pivot in range(start, end + 1):
        left = max(start - 3, pivot - 3)
        right = min(int(arm), pivot + 2)
        if side == "LONG":
            value = high[pivot]
            if not math.isfinite(value):
                continue
            if value < np.nanmax(high[left:pivot]) or value <= np.nanmax(high[pivot + 1 : right + 1]):
                continue
        else:
            value = low[pivot]
            if not math.isfinite(value):
                continue
            if value > np.nanmin(low[left:pivot]) or value >= np.nanmin(low[pivot + 1 : right + 1]):
                continue
        tolerance = max(abs(value) * 0.00035, float(tick) * 2.0)
        if side == "LONG":
            touches = int(np.sum(np.abs(high[max(start, pivot - 120): pivot + 1] - value) <= tolerance))
        else:
            touches = int(np.sum(np.abs(low[max(start, pivot - 120): pivot + 1] - value) <= tolerance))
        vol_slice = volume[max(start, pivot - 5) : min(int(arm) + 1, pivot + 6)]
        volume_ratio = float(
            volume[pivot] / max(float(np.nanmedian(vol_slice)), 1e-12)
        ) if len(vol_slice) and math.isfinite(volume[pivot]) else 0.0
        record = _candidate_record(
            data=data,
            side=side,
            entry=entry,
            stop=stop,
            tick=tick,
            level=value,
            kind="CONFIRMED_PIVOT_LIQUIDITY",
            created_index=pivot,
            live_start=pivot + 3,
            arm=arm,
            evidence={
                "structural_target_touch_count": touches,
                "structural_target_pivot_volume_ratio": volume_ratio,
                "structural_target_horizon_minutes": int(arm - pivot),
            },
        )
        if record is not None:
            found.append(record)
    # Nearest twelve are enough; farther pivots cannot beat a nearer structure.
    found.sort(key=lambda item: abs(float(item[2]) - float(entry)))
    return found[:12]


def _range_candidates(
    data: pd.DataFrame,
    candidate: Any,
    arm: int,
    entry: float,
    stop: float,
    tick: float,
) -> list[tuple[Any, dict[str, Any], float, float, dict[str, float]]]:
    side = _side(candidate)
    departure = int(
        _setup_attr(candidate, "departure_index", _setup_attr(candidate, "event_index", arm))
    )
    departure = max(1, min(departure, int(arm)))
    high = pd.to_numeric(data["high"], errors="coerce").to_numpy(float)
    low = pd.to_numeric(data["low"], errors="coerce").to_numpy(float)
    found: list[tuple[Any, dict[str, Any], float, float, dict[str, float]]] = []
    seen: set[int] = set()
    for horizon in (30, 60, 120, 240, 480, 720, 1440):
        start = max(0, departure - horizon)
        if departure - start < min(15, horizon):
            continue
        if side == "LONG":
            local = high[start:departure]
            offset = int(np.nanargmax(local))
            level = float(local[offset])
        else:
            local = low[start:departure]
            offset = int(np.nanargmin(local))
            level = float(local[offset])
        created = start + offset
        key = int(round(level / max(tick, 1e-12)))
        if key in seen:
            continue
        seen.add(key)
        record = _candidate_record(
            data=data,
            side=side,
            entry=entry,
            stop=stop,
            tick=tick,
            level=level,
            kind=f"PRE_EVENT_RANGE_{horizon}M",
            created_index=created,
            live_start=departure,
            arm=arm,
            evidence={
                "structural_target_touch_count": 1,
                "structural_target_pivot_volume_ratio": 0.0,
                "structural_target_horizon_minutes": int(horizon),
            },
        )
        if record is not None:
            found.append(record)
    return found


def structural_target_at_arm(*args: Any, **kwargs: Any):
    bound = ORIGINAL_SIGNATURE.bind_partial(*args, **kwargs)
    data = _value(bound, "data", "frame")
    candidate = _value(bound, "candidate", "setup_candidate")
    arm = _value(bound, "arm", "arm_index", "index")
    entry = _value(bound, "entry", "entry_price")
    stop = _value(bound, "stop", "stop_price")
    tick = _value(bound, "tick", "price_tick")
    original = ORIGINAL_STRUCTURAL_TARGET(*args, **kwargs)
    if (
        not isinstance(data, pd.DataFrame)
        or candidate is None
        or arm is None
        or entry is None
        or stop is None
        or tick is None
    ):
        return original

    options: list[
        tuple[Any, dict[str, Any], float, float, dict[str, float]]
    ] = []
    if original is not None:
        obstacle, features, target, gross_rr, economics = original
        features = dict(features)
        features.setdefault("structural_target_provenance", "IMPULSE_OR_OPPOSING_FRONTIER")
        options.append(
            (obstacle, features, float(target), float(gross_rr), economics)
        )
    options.extend(
        _pivot_candidates(data, candidate, int(arm), float(entry), float(stop), float(tick))
    )
    options.extend(
        _range_candidates(data, candidate, int(arm), float(entry), float(stop), float(tick))
    )
    if not options:
        return None

    # Price distance, not an R bucket, determines which market obstacle is first.
    options.sort(
        key=lambda item: (
            abs(float(item[2]) - float(entry)),
            0 if str(item[1].get("structural_target_provenance", "")).startswith(
                "CONFIRMED_PIVOT"
            ) else 1,
            int(item[0].created_ns),
        )
    )
    chosen = options[0]
    features = dict(chosen[1])
    features["structural_target_candidate_count"] = int(len(options))
    features["structural_target_selection_rule"] = (
        "NEAREST_STILL_LIVE_CAUSAL_STRUCTURE_NO_FIXED_R_CAP"
    )
    return chosen[0], features, chosen[2], chosen[3], chosen[4]


base._structural_target_at_arm = structural_target_at_arm
base.POLICY = POLICY
base.core.POLICY = POLICY


if __name__ == "__main__":
    base.core.main()
