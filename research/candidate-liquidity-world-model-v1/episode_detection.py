"""Causal failed, accepted and mitigation auction episode detection."""
from __future__ import annotations

from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from world_model_common import (
    MEDIUM_SCALE,
    EpisodeSignal,
    SourceEvent,
    control_features,
)


def failed_signal(
    data: pd.DataFrame,
    source: SourceEvent,
    small_nodes: Sequence[Any],
    atr: np.ndarray,
) -> EpisodeSignal | None:
    interaction = source.interaction_index
    horizon = min(len(data) - 2, interaction + 75)
    candidates = [
        node
        for node in small_nodes
        if str(node.side) == source.side
        and int(node.extreme_index) >= interaction
        and int(node.observed_index) > interaction
        and int(node.observed_index) > int(node.extreme_index)
        and int(node.observed_index) <= horizon
    ]
    for node in candidates:
        decision = int(node.observed_index)
        close = float(data.close.iloc[decision])
        reclaimed = close < source.upper if source.side == "HIGH" else close > source.lower
        penetrated = float(node.price) >= source.upper if source.side == "HIGH" else float(node.price) <= source.lower
        if not (reclaimed and penetrated):
            continue
        side = "SHORT" if source.side == "HIGH" else "LONG"
        event_extreme = (
            float(data.high.iloc[interaction : decision + 1].max())
            if side == "SHORT"
            else float(data.low.iloc[interaction : decision + 1].min())
        )
        return EpisodeSignal(
            family="FAILED_AUCTION_REVERSAL",
            side=side,
            interaction_index=interaction,
            decision_index=decision,
            event_extreme=event_extreme,
            pullback_extreme=event_extreme,
            source=source,
            context_scale=source.scale,
            impulse_start_index=int(node.extreme_index),
            impulse_end_index=decision,
            evidence=control_features(data, int(node.extreme_index), decision, side, float(atr[interaction])),
        )
    return None


def accepted_signal(
    data: pd.DataFrame,
    source: SourceEvent,
    small_nodes: Sequence[Any],
    atr: np.ndarray,
) -> EpisodeSignal | None:
    interaction = source.interaction_index
    horizon = min(len(data) - 2, interaction + 120)
    pullback_side = "LOW" if source.side == "HIGH" else "HIGH"
    firsts = [
        node
        for node in small_nodes
        if str(node.side) == source.side
        and int(node.extreme_index) >= interaction
        and int(node.observed_index) > interaction
        and int(node.observed_index) > int(node.extreme_index)
        and int(node.observed_index) <= horizon
        and (float(node.price) > source.upper if source.side == "HIGH" else float(node.price) < source.lower)
    ]
    for first in firsts:
        seconds = [
            node
            for node in small_nodes
            if str(node.side) == pullback_side
            and int(node.extreme_index) > int(first.extreme_index)
            and int(node.observed_index) > int(first.observed_index)
            and int(node.observed_index) > int(node.extreme_index)
            and int(node.observed_index) <= horizon
        ]
        for second in seconds:
            held = float(second.price) > source.lower if source.side == "HIGH" else float(second.price) < source.upper
            if not held:
                break
            side = "LONG" if source.side == "HIGH" else "SHORT"
            decision = int(second.observed_index)
            outside = float(data.close.iloc[decision]) > source.upper if side == "LONG" else float(data.close.iloc[decision]) < source.lower
            if not outside:
                continue
            return EpisodeSignal(
                family="ACCEPTED_AUCTION_CONTINUATION",
                side=side,
                interaction_index=interaction,
                decision_index=decision,
                event_extreme=float(first.price),
                pullback_extreme=float(second.price),
                source=source,
                context_scale=source.scale,
                impulse_start_index=int(second.extreme_index),
                impulse_end_index=decision,
                evidence=control_features(data, int(second.extreme_index), decision, side, float(atr[interaction])),
            )
    return None


def _latest_medium(nodes: Sequence[Any], decision: int) -> Any | None:
    eligible = [node for node in nodes if int(node.observed_index) < decision]
    return max(eligible, key=lambda item: int(item.observed_index)) if eligible else None


def mitigation_signals(
    data: pd.DataFrame,
    small_nodes: Sequence[Any],
    medium_nodes: Sequence[Any],
    atr: np.ndarray,
    start_index: int,
    end_index: int,
) -> list[EpisodeSignal]:
    output: list[EpisodeSignal] = []
    ordered = sorted(small_nodes, key=lambda item: int(item.observed_index))
    for previous, current in zip(ordered, ordered[1:]):
        decision = int(current.observed_index)
        if decision < start_index or decision >= end_index or decision <= int(current.extreme_index):
            continue
        medium = _latest_medium(medium_nodes, decision)
        if medium is None:
            continue
        side = "LONG" if str(medium.side) == "LOW" else "SHORT"
        prior_side = "HIGH" if side == "LONG" else "LOW"
        current_side = "LOW" if side == "LONG" else "HIGH"
        if str(previous.side) != prior_side or str(current.side) != current_side:
            continue
        if int(current.extreme_index) <= int(previous.extreme_index):
            continue
        held = float(current.price) > float(medium.price) if side == "LONG" else float(current.price) < float(medium.price)
        if not held:
            continue
        output.append(
            EpisodeSignal(
                family="INITIATIVE_MITIGATION_CONTINUATION",
                side=side,
                interaction_index=int(previous.extreme_index),
                decision_index=decision,
                event_extreme=float(previous.price),
                pullback_extreme=float(current.price),
                source=None,
                context_scale=float(MEDIUM_SCALE * 60.0),
                impulse_start_index=int(current.extreme_index),
                impulse_end_index=decision,
                evidence=control_features(data, int(current.extreme_index), decision, side, float(atr[decision])),
            )
        )
    return output


def dedupe_signals(signals: Iterable[EpisodeSignal]) -> list[EpisodeSignal]:
    output: list[EpisodeSignal] = []
    for signal in sorted(signals, key=lambda item: (item.decision_index, item.interaction_index, item.family)):
        duplicate_index: int | None = None
        for index in range(len(output) - 1, max(-1, len(output) - 11), -1):
            prior = output[index]
            if prior.side != signal.side:
                continue
            if (
                abs(prior.interaction_index - signal.interaction_index) <= 3
                or abs(prior.decision_index - signal.decision_index) <= 3
            ):
                duplicate_index = index
                break
        if duplicate_index is None:
            output.append(signal)
        elif (
            output[duplicate_index].family == "INITIATIVE_MITIGATION_CONTINUATION"
            and signal.family != "INITIATIVE_MITIGATION_CONTINUATION"
        ):
            output[duplicate_index] = signal
    return output
