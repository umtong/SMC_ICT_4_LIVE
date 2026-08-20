"""Point-in-time public-liquidity map and honest destination selection."""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

import event_time_auction_harvest as dc
from world_model_common import (
    EPS,
    SOURCE_DC_SCALES,
    Destination,
    SourceEvent,
    finite,
    sign,
    stable,
)


def _semantic_strength(level: Any, metadata: dict[str, Any]) -> float:
    meta = metadata.get(str(level.level_id))
    semantic = finite(getattr(meta, "semantic_weight", 0.0)) if meta is not None else 0.0
    scale = math.log1p(max(finite(getattr(level, "timeframe_minutes", 1.0)), 1.0))
    defense = math.log1p(max(finite(getattr(level, "defense_count", 0.0)), 0.0))
    ratio = math.log1p(max(finite(getattr(level, "strength_ratio", 0.0)), 0.0))
    return 1.0 + semantic + 0.18 * scale + 0.15 * defense + 0.12 * ratio


def semantic_source_events(
    levels: Sequence[Any], metadata: dict[str, Any], data: pd.DataFrame
) -> list[SourceEvent]:
    output: list[SourceEvent] = []
    for level in levels:
        interaction = getattr(level, "first_penetration_index", None)
        if interaction is None:
            continue
        interaction = int(interaction)
        observed = int(getattr(level, "observed_index_1m", 0))
        side = str(getattr(level, "side", ""))
        if interaction <= observed or interaction >= len(data) or side not in {"HIGH", "LOW"}:
            continue
        lower = finite(getattr(level, "lower", getattr(level, "price", 0.0)))
        upper = finite(getattr(level, "upper", getattr(level, "price", 0.0)))
        lower, upper = sorted((lower, upper))
        level_id = str(getattr(level, "level_id", stable(side, observed, lower, upper)))
        meta = metadata.get(level_id)
        if meta is not None and hasattr(meta, "direction_source") and not bool(meta.direction_source):
            continue
        output.append(
            SourceEvent(
                source_id=f"SEM:{level_id}",
                side=side,
                lower=lower,
                upper=upper,
                price=finite(getattr(level, "price", 0.5 * (lower + upper))),
                observed_index=observed,
                interaction_index=interaction,
                scale=max(finite(getattr(level, "timeframe_minutes", 1.0)), 1.0),
                strength=_semantic_strength(level, metadata),
                kind=f"SEMANTIC:{getattr(meta, 'pool_kind', 'LEVEL')}",
            )
        )
    return output


def dc_source_events(
    data: pd.DataFrame,
    nodes_by_scale: dict[float, list[Any]],
    atr: np.ndarray,
) -> list[SourceEvent]:
    output: list[SourceEvent] = []
    for scale in SOURCE_DC_SCALES:
        for node in nodes_by_scale.get(scale, []):
            interaction = dc._first_penetration(data, node, atr)
            if interaction is None:
                continue
            width = max(0.035 * float(atr[node.observed_index]), EPS)
            output.append(
                SourceEvent(
                    source_id=f"DC:{scale:.2f}:{node.side}:{node.observed_index}:{stable(node.price)}",
                    side=str(node.side),
                    lower=float(node.price - width),
                    upper=float(node.price + width),
                    price=float(node.price),
                    observed_index=int(node.observed_index),
                    interaction_index=int(interaction),
                    scale=float(scale * 60.0),
                    strength=float(1.0 + scale),
                    kind=f"DIRECTIONAL_CHANGE_{scale:.2f}",
                )
            )
    return output


def merge_source_events(
    events: Iterable[SourceEvent], atr: np.ndarray, tick: float
) -> list[SourceEvent]:
    groups: list[list[SourceEvent]] = []
    for event in sorted(events, key=lambda item: (item.interaction_index, item.side, item.price)):
        matched = False
        for group in reversed(groups[-8:]):
            owner = group[0]
            if owner.side != event.side or abs(owner.interaction_index - event.interaction_index) > 2:
                continue
            if abs(owner.price - event.price) <= max(4.0 * tick, 0.18 * float(atr[event.interaction_index])):
                group.append(event)
                matched = True
                break
        if not matched:
            groups.append([event])
    output: list[SourceEvent] = []
    for group in groups:
        # A merged source may not borrow a component which became observable only
        # after the source interaction. Use the latest near-synchronous penetration
        # as the common event clock, then keep only components already public at it.
        interaction = max(item.interaction_index for item in group)
        causal = [item for item in group if item.observed_index < interaction]
        if not causal:
            continue
        owner = max(causal, key=lambda item: (item.strength, item.scale, -item.observed_index))
        weights = np.asarray([max(item.strength, EPS) for item in causal])
        output.append(
            SourceEvent(
                source_id="SRC:" + stable(*(item.source_id for item in causal)),
                side=owner.side,
                lower=min(item.lower for item in causal),
                upper=max(item.upper for item in causal),
                price=float(np.average([item.price for item in causal], weights=weights)),
                observed_index=max(item.observed_index for item in causal),
                interaction_index=interaction,
                scale=max(item.scale for item in causal),
                strength=float(sum(item.strength for item in causal)),
                kind="+".join(sorted({item.kind for item in causal})),
                confluence_count=len(causal),
            )
        )
    return sorted(output, key=lambda item: (item.interaction_index, item.side, -item.strength))


def _semantic_destinations(
    levels: Sequence[Any], metadata: dict[str, Any], decision: int
) -> list[Destination]:
    output: list[Destination] = []
    for level in levels:
        observed = int(getattr(level, "observed_index_1m", 0))
        first = getattr(level, "first_penetration_index", None)
        side = str(getattr(level, "side", ""))
        if observed >= decision or (first is not None and int(first) <= decision) or side not in {"HIGH", "LOW"}:
            continue
        scale = max(finite(getattr(level, "timeframe_minutes", 1.0)), 1.0)
        if scale < 30.0 and finite(getattr(level, "defense_count", 0.0)) < 2.0:
            continue
        lower = finite(getattr(level, "lower", getattr(level, "price", 0.0)))
        upper = finite(getattr(level, "upper", getattr(level, "price", 0.0)))
        lower, upper = sorted((lower, upper))
        level_id = str(getattr(level, "level_id", stable(side, observed, lower, upper)))
        meta = metadata.get(level_id)
        if meta is not None and hasattr(meta, "direction_source") and not bool(meta.direction_source):
            continue
        output.append(
            Destination(
                destination_id=f"SEMDEST:{level_id}",
                side=side,
                lower=lower,
                upper=upper,
                price=finite(getattr(level, "price", 0.5 * (lower + upper))),
                observed_index=observed,
                scale=scale,
                strength=_semantic_strength(level, metadata),
                kind=f"SEMANTIC:{getattr(meta, 'pool_kind', 'LEVEL')}",
            )
        )
    return output


def _dc_fresh(data: pd.DataFrame, node: Any, decision: int, atr: np.ndarray) -> bool:
    if int(node.observed_index) >= decision:
        return False
    part = data.iloc[int(node.observed_index) + 1 : decision]
    if part.empty:
        return True
    buffer = max(0.04 * float(atr[int(node.observed_index)]), EPS)
    return (
        float(part.high.max()) < float(node.price) + buffer
        if str(node.side) == "HIGH"
        else float(part.low.min()) > float(node.price) - buffer
    )


def _dc_destinations(
    data: pd.DataFrame,
    nodes_by_scale: dict[float, list[Any]],
    decision: int,
    atr: np.ndarray,
) -> list[Destination]:
    output: list[Destination] = []
    for scale in SOURCE_DC_SCALES:
        for node in nodes_by_scale.get(scale, []):
            if not _dc_fresh(data, node, decision, atr):
                continue
            width = max(0.035 * float(atr[int(node.observed_index)]), EPS)
            output.append(
                Destination(
                    destination_id=f"DCDEST:{scale:.2f}:{node.side}:{node.observed_index}:{stable(node.price)}",
                    side=str(node.side),
                    lower=float(node.price - width),
                    upper=float(node.price + width),
                    price=float(node.price),
                    observed_index=int(node.observed_index),
                    scale=float(scale * 60.0),
                    strength=float(1.0 + scale),
                    kind=f"DIRECTIONAL_CHANGE_{scale:.2f}",
                )
            )
    return output


def _previous_day(data: pd.DataFrame, decision: int, side: str) -> Destination | None:
    now = data.index[decision]
    day = now.normalize()
    previous = data[(data.index >= day - pd.Timedelta(days=1)) & (data.index < day)]
    current = data[(data.index >= day) & (data.index < now)]
    if len(previous) < 120:
        return None
    if side == "HIGH":
        price = float(previous.high.max())
        if len(current) and float(current.high.max()) >= price:
            return None
        kind = "PREVIOUS_DAY_HIGH"
    else:
        price = float(previous.low.min())
        if len(current) and float(current.low.min()) <= price:
            return None
        kind = "PREVIOUS_DAY_LOW"
    return Destination(
        destination_id=f"{kind}:{day.date().isoformat()}",
        side=side,
        lower=price,
        upper=price,
        price=price,
        observed_index=max(0, int(data.index.searchsorted(day)) - 1),
        scale=1440.0,
        strength=3.0,
        kind=kind,
    )


def _cluster(
    items: Iterable[Destination], decision: int, atr: np.ndarray, tick: float
) -> list[Destination]:
    threshold = max(4.0 * tick, 0.16 * float(atr[decision]))
    output: list[Destination] = []
    for side in ("HIGH", "LOW"):
        groups: list[list[Destination]] = []
        for item in sorted((x for x in items if x.side == side), key=lambda x: x.price):
            if groups and abs(item.price - groups[-1][-1].price) <= threshold:
                groups[-1].append(item)
            else:
                groups.append([item])
        for group in groups:
            weights = np.asarray([max(item.strength, EPS) for item in group])
            output.append(
                Destination(
                    destination_id="DEST:" + stable(*(item.destination_id for item in group)),
                    side=side,
                    lower=min(item.lower for item in group),
                    upper=max(item.upper for item in group),
                    price=float(np.average([item.price for item in group], weights=weights)),
                    observed_index=max(item.observed_index for item in group),
                    scale=max(item.scale for item in group),
                    strength=float(sum(item.strength for item in group)),
                    kind="+".join(sorted({item.kind for item in group})),
                )
            )
    return output


def choose_destination(
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    nodes_by_scale: dict[float, list[Any]],
    decision: int,
    entry: float,
    side: str,
    atr: np.ndarray,
    tick: float,
) -> Destination | None:
    target_side = "HIGH" if side == "LONG" else "LOW"
    items = [
        *_semantic_destinations(levels, metadata, decision),
        *_dc_destinations(data, nodes_by_scale, decision, atr),
    ]
    previous = _previous_day(data, decision, target_side)
    if previous is not None:
        items.append(previous)
    direction = sign(side)
    ahead = [
        item
        for item in _cluster(items, decision, atr, tick)
        if item.side == target_side
        and direction * ((item.lower if side == "LONG" else item.upper) - entry) > tick
    ]
    if not ahead:
        return None
    ahead.sort(key=lambda item: (direction * (item.price - entry), -item.strength, -item.scale))
    nearest = direction * (ahead[0].price - entry)
    near = [item for item in ahead if direction * (item.price - entry) <= nearest + 0.20 * float(atr[decision])]
    return max(near, key=lambda item: (item.strength, item.scale, -abs(item.price - entry)))
