#!/usr/bin/env python3
"""One causal liquidity episode and at most one destination-first plan."""
from __future__ import annotations

from typing import Any, Sequence

import pandas as pd

from episode_detection import (
    accepted_signal,
    dedupe_signals,
    failed_signal,
    mitigation_signals,
)
from liquidity_world import dc_source_events, merge_source_events, semantic_source_events
from plan_geometry import no_plan_record, plan_from_signal
from world_model_common import (
    EVENT_SCALE,
    LARGE_SCALE,
    MEDIUM_SCALE,
    EpisodeSignal,
    atr_array,
    core,
    dc,
    fixed,
)


def generate_symbol(
    symbol: str,
    data: pd.DataFrame,
    levels: Sequence[Any],
    metadata: dict[str, Any],
    trading_start: Any,
) -> tuple[pd.DataFrame, dict[str, int]]:
    tick = core.CONTRACTS[symbol].tick_size
    start = pd.Timestamp(trading_start)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    start_index = int(data.index.searchsorted(start))
    decision_end_ns = getattr(fixed, "_DECISION_END_NS", None)
    end_index = (
        len(data)
        if decision_end_ns is None
        else int(data.index.searchsorted(pd.Timestamp(decision_end_ns, unit="ns", tz="UTC")))
    )

    atr = atr_array(data)
    nodes_by_scale = {
        scale: dc.directional_change(data, scale, atr)
        for scale in (EVENT_SCALE, MEDIUM_SCALE, LARGE_SCALE)
    }
    source_events = merge_source_events(
        [
            *semantic_source_events(levels, metadata, data),
            *dc_source_events(data, nodes_by_scale, atr),
        ],
        atr,
        tick,
    )
    small_nodes = nodes_by_scale[EVENT_SCALE]
    signals: list[EpisodeSignal] = []
    external_events = 0
    for source in source_events:
        if source.interaction_index < start_index or source.interaction_index >= end_index:
            continue
        external_events += 1
        failed = failed_signal(data, source, small_nodes, atr)
        accepted = accepted_signal(data, source, small_nodes, atr)
        if failed is not None and accepted is not None:
            signals.append(min((failed, accepted), key=lambda item: item.decision_index))
        elif failed is not None:
            signals.append(failed)
        elif accepted is not None:
            signals.append(accepted)
    signals.extend(
        mitigation_signals(
            data,
            small_nodes,
            nodes_by_scale[MEDIUM_SCALE],
            atr,
            start_index,
            end_index,
        )
    )
    signals = dedupe_signals(signals)

    records: list[dict[str, Any]] = []
    plan_count = 0
    for signal in signals:
        if signal.decision_index >= end_index:
            continue
        plan, reason = plan_from_signal(
            symbol,
            data,
            levels,
            metadata,
            nodes_by_scale,
            small_nodes,
            signal,
            atr,
            tick,
        )
        if plan is None:
            records.append(no_plan_record(symbol, signal, data, reason, atr))
        else:
            records.append(plan)
            plan_count += 1
    frame = pd.DataFrame(records)
    return frame, {
        "semantic_and_dc_source_events": int(len(source_events)),
        "external_events_in_window": int(external_events),
        "causal_episode_signals": int(len(signals)),
        "one_plan_episodes": int(plan_count),
        "no_trade_episodes": int(len(records) - plan_count),
        "plans": int(len(records)),
    }


__all__ = ["generate_symbol"]
