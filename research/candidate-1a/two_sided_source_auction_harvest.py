#!/usr/bin/env python3
"""Resolve a penetrated semantic-liquidity boundary by the market's later auction outcome.

The first candidate-1a control-transfer prototype still inherited direction from the
older setup detector.  That made BPR/FVG/OB geometry participate in direction even
though those objects should refine location only.  This overlay keeps the inherited
semantic source map, event ownership, market/flow state, micro control-transfer entry,
structural stop, route target and cost accounting, but replaces direction selection:

* a HIGH pool can either be reclaimed (short failed auction) or accepted (long);
* a LOW pool can either be reclaimed (long failed auction) or accepted (short);
* both explanations are generated causally from completed closes;
* each explanation must subsequently create a fresh micro control-transfer zone and
  defend its first return;
* the one-account router later takes the first completed response of the semantic
  episode, so tools never vote independently and a causal episode cannot be counted
  twice.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

import control_transfer_harvest as base

core = base.core
narrative = base.narrative
hl = base.hl

MAX_BRANCH_RESOLUTION_MINUTES = 16


def _candidate(
    data: pd.DataFrame,
    source: Any,
    tick: float,
    branch: str,
) -> list[Any]:
    interaction = int(source.first_penetration_index)
    if interaction >= len(data) - 2:
        return []

    if branch == "FAILED_AUCTION_REVERSAL":
        side = "SHORT" if source.side == "HIGH" else "LONG"
        confirmation = None
        end = min(len(data), interaction + MAX_BRANCH_RESOLUTION_MINUTES + 1)
        for index in range(interaction, end):
            row = data.iloc[index]
            reclaimed = (
                float(row.close) <= float(source.lower)
                if source.side == "HIGH"
                else float(row.close) >= float(source.upper)
            )
            aligned = (
                float(row.close) < float(row.open)
                if side == "SHORT"
                else float(row.close) > float(row.open)
            )
            if reclaimed and aligned:
                confirmation = index
                break
        setup_kind = "SOURCE_BOUNDARY_RECLAIM"
        location_kind = "RECLAIM_THEN_FRESH_CONTROL_TRANSFER"
    elif branch == "ACCEPTED_AUCTION_CONTINUATION":
        side = "LONG" if source.side == "HIGH" else "SHORT"
        confirmation = None
        first_outside = None
        end = min(len(data), interaction + MAX_BRANCH_RESOLUTION_MINUTES + 1)
        for index in range(interaction, end):
            row = data.iloc[index]
            outside = (
                float(row.close) >= float(source.upper) + tick
                if side == "LONG"
                else float(row.close) <= float(source.lower) - tick
            )
            if not outside:
                first_outside = None
                continue
            if first_outside is None:
                first_outside = index
                continue
            previous = data.iloc[index - 1]
            previous_outside = (
                float(previous.close) >= float(source.upper)
                if side == "LONG"
                else float(previous.close) <= float(source.lower)
            )
            aligned = (
                float(row.close) > float(row.open)
                if side == "LONG"
                else float(row.close) < float(row.open)
            )
            if previous_outside and aligned:
                confirmation = index
                break
        setup_kind = "SOURCE_BOUNDARY_ACCEPTANCE"
        location_kind = "ACCEPTANCE_THEN_FRESH_CONTROL_TRANSFER"
    else:
        raise ValueError(branch)

    if confirmation is None:
        return []

    event = data.iloc[interaction : confirmation + 1]
    before = data.iloc[max(0, interaction - 12) : interaction]
    event_extreme = (
        float(event.low.min()) if side == "LONG" else float(event.high.max())
    )
    pre_event_control = (
        float(before.low.min())
        if side == "LONG" and not before.empty
        else float(before.high.max())
        if side == "SHORT" and not before.empty
        else float(source.price)
    )
    directional_gap = narrative._synthetic_gap(
        side,
        int(confirmation),
        float(source.lower),
        float(source.upper),
        data,
    )
    setup = hl.Setup(
        setup_kind=setup_kind,
        side=side,
        interaction_index=interaction,
        reclaim_index=int(confirmation),
        event_extreme=event_extreme,
        confirmation_index=int(confirmation),
        lower=float(source.lower),
        upper=float(source.upper),
        manipulation_gap=None,
        directional_gap=directional_gap,
        pre_event_control=pre_event_control,
    )
    candidate = core.DepartureCandidate(
        source=source,
        confirmation_index=int(confirmation),
        departure_index=int(confirmation),
        setup=setup,
        event_meta={
            "narrative_branch": branch,
            "location_kind": location_kind,
            "order_block_index": -1.0,
            "source_boundary_resolution_index": float(confirmation),
        },
    )
    return [candidate]


def _run_branch(
    symbol: str,
    data: pd.DataFrame,
    levels: Any,
    metadata: Any,
    trading_start: Any,
    branch: str,
):
    original = core._departure_candidates
    try:
        core._departure_candidates = (
            lambda frame, source, tick: _candidate(
                frame, source, tick, branch
            )
        )
        frame, counts = base.generate_symbol(
            symbol, data, levels, metadata, trading_start
        )
    finally:
        core._departure_candidates = original
    if not frame.empty:
        frame = frame.copy()
        frame["source_auction_resolution"] = branch
        frame["action_id"] = branch + ":" + frame.action_id.astype(str)
    return frame, counts


def generate_symbol(symbol, data, levels, metadata, trading_start):
    frames = []
    branch_counts = {}
    for branch in (
        "FAILED_AUCTION_REVERSAL",
        "ACCEPTED_AUCTION_CONTINUATION",
    ):
        frame, counts = _run_branch(
            symbol, data, levels, metadata, trading_start, branch
        )
        frames.append(frame)
        branch_counts[branch] = counts

    nonempty = [frame for frame in frames if not frame.empty]
    output = (
        pd.concat(nonempty, ignore_index=True, sort=False)
        if nonempty
        else pd.DataFrame()
    )
    if not output.empty:
        output = (
            output.sort_values(
                [
                    "episode_id",
                    "order_time_ns",
                    "planned_target_net_r",
                    "actual_fill_gross_rr",
                ],
                ascending=[True, True, False, False],
            )
            .drop_duplicates("action_id", keep="first")
            .reset_index(drop=True)
        )
        competing = output.groupby("episode_id").family.transform("nunique")
        output["competing_auction_outcomes"] = competing.astype(float)

    counts = {
        "semantic_sources": max(
            (value.get("semantic_sources", 0) for value in branch_counts.values()),
            default=0,
        ),
        "source_interactions": max(
            (value.get("source_interactions", 0) for value in branch_counts.values()),
            default=0,
        ),
        "causal_departures": sum(
            value.get("causal_departures", 0)
            for value in branch_counts.values()
        ),
        "control_structures": sum(
            value.get("control_structures", 0)
            for value in branch_counts.values()
        ),
        "confirmed_responses": sum(
            value.get("confirmed_responses", 0)
            for value in branch_counts.values()
        ),
        "plans": int(len(output)),
        "states": int(output.state_id.nunique()) if not output.empty else 0,
        "episodes_with_both_outcomes": int(
            (output.groupby("episode_id").family.nunique() > 1).sum()
        ) if not output.empty else 0,
        "by_branch": branch_counts,
    }
    return output, counts


core.POLICY = (
    "SEMANTIC_SOURCE_PENETRATION_THEN_CAUSAL_RECLAIM_OR_ACCEPTANCE_"
    "THEN_FRESH_MICRO_CONTROL_TRANSFER_THEN_FIRST_RETURN_PRICE_FLOW_"
    "DEFENSE_THEN_NEXT_MINUTE_ENTRY_WITH_STRUCTURAL_STOP_TO_FIRST_"
    "OPPOSING_UNCONSUMED_ROUTE"
)
core.generate_symbol = generate_symbol

if __name__ == "__main__":
    core.main()
