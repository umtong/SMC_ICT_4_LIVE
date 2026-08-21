"""Causal liquidity-event detection using settled auction responses.

The categorical event is completed first.  Price-volume impulse-response evidence is
then attached at that same decision timestamp; it never looks beyond the decision.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from auction_response import (
    accepted_auction_evidence,
    failed_auction_evidence,
    initiative_evidence,
)
from world_model_common import (
    MEDIUM_SCALE,
    EpisodeSignal,
    SourceEvent,
    control_features,
)


def _boundary(source: SourceEvent) -> float:
    return float(source.upper if source.side == "HIGH" else source.lower)


def failed_signal(
    data: pd.DataFrame,
    source: SourceEvent,
    small_nodes: Sequence[Any],
    atr: np.ndarray,
) -> EpisodeSignal | None:
    """Overshoot a public pool, then settle back inside it."""
    interaction = int(source.interaction_index)
    horizon = min(len(data) - 2, interaction + 90)
    candidates = [
        node
        for node in small_nodes
        if str(node.side) == source.side
        and int(node.extreme_index) >= interaction
        and int(node.observed_index) > int(node.extreme_index)
        and int(node.observed_index) <= horizon
    ]
    for node in candidates:
        decision = int(node.observed_index)
        close = float(data.close.iloc[decision])
        penetrated = (
            float(node.price) >= float(source.upper)
            if source.side == "HIGH"
            else float(node.price) <= float(source.lower)
        )
        reclaimed = (
            close < float(source.upper)
            if source.side == "HIGH"
            else close > float(source.lower)
        )
        if not (penetrated and reclaimed):
            continue
        side = "SHORT" if source.side == "HIGH" else "LONG"
        event_extreme = (
            float(data.high.iloc[interaction : decision + 1].max())
            if side == "SHORT"
            else float(data.low.iloc[interaction : decision + 1].min())
        )
        evidence = control_features(
            data,
            int(node.extreme_index),
            decision,
            side,
            float(atr[interaction]),
        )
        evidence.update(
            failed_auction_evidence(
                data,
                interaction=interaction,
                extreme_index=int(node.extreme_index),
                decision=decision,
                source_side=source.side,
                boundary=_boundary(source),
                event_extreme=event_extreme,
                atr_price=float(atr[interaction]),
            )
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
            evidence=evidence,
        )
    return None


def accepted_signal(
    data: pd.DataFrame,
    source: SourceEvent,
    small_nodes: Sequence[Any],
    atr: np.ndarray,
) -> EpisodeSignal | None:
    """Break a public pool, settle outside, and hold the first pullback."""
    interaction = int(source.interaction_index)
    horizon = min(len(data) - 2, interaction + 150)
    pullback_side = "LOW" if source.side == "HIGH" else "HIGH"
    firsts = [
        node
        for node in small_nodes
        if str(node.side) == source.side
        and int(node.extreme_index) >= interaction
        and int(node.observed_index) > int(node.extreme_index)
        and int(node.observed_index) <= horizon
        and (
            float(node.price) > float(source.upper)
            if source.side == "HIGH"
            else float(node.price) < float(source.lower)
        )
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
            pullback_held = (
                float(second.price) > float(source.lower)
                if source.side == "HIGH"
                else float(second.price) < float(source.upper)
            )
            if not pullback_held:
                break
            side = "LONG" if source.side == "HIGH" else "SHORT"
            decision = int(second.observed_index)
            settled_outside = (
                float(data.close.iloc[decision]) > float(source.upper)
                if side == "LONG"
                else float(data.close.iloc[decision]) < float(source.lower)
            )
            if not settled_outside:
                continue
            evidence = control_features(
                data,
                int(second.extreme_index),
                decision,
                side,
                float(atr[interaction]),
            )
            evidence.update(
                accepted_auction_evidence(
                    data,
                    interaction=interaction,
                    impulse_extreme_index=int(first.extreme_index),
                    decision=decision,
                    source_side=source.side,
                    boundary=_boundary(source),
                    impulse_extreme=float(first.price),
                    pullback_extreme=float(second.price),
                    atr_price=float(atr[interaction]),
                )
            )
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
                evidence=evidence,
            )
    return None


def _latest_medium(nodes: Sequence[Any], decision: int) -> Any | None:
    eligible = [node for node in nodes if int(node.observed_index) < int(decision)]
    return max(eligible, key=lambda item: int(item.observed_index)) if eligible else None


def mitigation_signals(
    data: pd.DataFrame,
    small_nodes: Sequence[Any],
    medium_nodes: Sequence[Any],
    atr: np.ndarray,
    start_index: int,
    end_index: int,
) -> list[EpisodeSignal]:
    """Initiative displacement followed by a held first mitigation."""
    output: list[EpisodeSignal] = []
    ordered = sorted(small_nodes, key=lambda item: int(item.observed_index))
    for previous, current in zip(ordered, ordered[1:]):
        decision = int(current.observed_index)
        if decision < int(start_index) or decision >= int(end_index):
            continue
        if decision <= int(current.extreme_index):
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
        held = (
            float(current.price) > float(medium.price)
            if side == "LONG"
            else float(current.price) < float(medium.price)
        )
        if not held:
            continue
        evidence = control_features(
            data,
            int(current.extreme_index),
            decision,
            side,
            float(atr[decision]),
        )
        evidence.update(
            initiative_evidence(
                data,
                impulse_start=int(current.extreme_index),
                decision=decision,
                side=side,
                atr_price=float(atr[decision]),
            )
        )
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
                evidence=evidence,
            )
        )
    return output


def dedupe_signals(signals: Iterable[EpisodeSignal]) -> list[EpisodeSignal]:
    """Keep one causal interpretation of one local event without inflating frequency."""
    output: list[EpisodeSignal] = []
    for signal in sorted(
        signals,
        key=lambda item: (item.decision_index, item.interaction_index, item.family),
    ):
        duplicate_index: int | None = None
        for index in range(len(output) - 1, max(-1, len(output) - 12), -1):
            prior = output[index]
            if prior.side != signal.side:
                continue
            if (
                abs(int(prior.interaction_index) - int(signal.interaction_index)) <= 3
                or abs(int(prior.decision_index) - int(signal.decision_index)) <= 3
            ):
                duplicate_index = index
                break
        if duplicate_index is None:
            output.append(signal)
            continue
        prior = output[duplicate_index]
        prior_score = float(prior.evidence.get("auction_response_score", float("-inf")))
        current_score = float(signal.evidence.get("auction_response_score", float("-inf")))
        if current_score > prior_score:
            output[duplicate_index] = signal
    return output


__all__ = [
    "accepted_signal",
    "dedupe_signals",
    "failed_signal",
    "mitigation_signals",
]
