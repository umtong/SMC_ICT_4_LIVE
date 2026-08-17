#!/usr/bin/env python3
"""Enriched complete-narrative harvest used by policy research.

This wrapper keeps the tested event generator intact while correcting diagnostic
R geometry and adding causal liquidity-map/phase features. It monkey-patches
only action-row construction before delegating to the original CLI.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import bpr_transfer_study as base
from liquidity_event_graph import EntryZone, InteractionEpisode
from liquidity_narrative_features import (
    liquidity_map_features,
    narrative_phase_features,
)

_ORIGINAL_BUILD_ACTION_ROW = base.build_action_row


def _net_r_target_price(
    side: int,
    entry: float,
    stop: float,
    tick: float,
    desired_r: float,
) -> float:
    """Solve target price for desired account R after planned execution costs."""
    entry_fill = entry + side * base.ENTRY_SLIPPAGE_TICKS * tick
    stop_fill = stop - side * base.STOP_SLIPPAGE_TICKS * tick
    stop_return = (
        side * (stop_fill - entry_fill) / entry_fill - 2.0 * base.TAKER
    )
    risk_fraction = -stop_return
    if risk_fraction <= 0.0:
        raise RuntimeError(f"non-positive planned risk: {risk_fraction}")
    required_gross_return = (
        desired_r * risk_fraction + base.TAKER + base.MAKER
    )
    unrounded = entry_fill * (1.0 + side * required_gross_return)
    if side > 0:
        return math.ceil(unrounded / tick - 1e-12) * tick
    return math.floor(unrounded / tick + 1e-12) * tick


def _build_action_row(
    symbol: str,
    period: str,
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    pools: list,
    pivots: list,
    episode: InteractionEpisode,
    zone: EntryZone,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
) -> dict[str, Any] | None:
    row = _ORIGINAL_BUILD_ACTION_ROW(
        symbol,
        period,
        frame,
        funding,
        pools,
        pivots,
        episode,
        zone,
        start_ts,
        end_ts,
    )
    if row is None:
        return None

    decision_ts = pd.Timestamp(row["decision_ts"])
    entry_ts = pd.Timestamp(row["entry_ts"])
    location = frame.index.get_loc(entry_ts)
    if isinstance(location, slice):
        raise RuntimeError(
            f"duplicate entry timestamp in feature frame: {entry_ts}"
        )
    entry_i = int(location)
    entry = float(row["entry"])
    stop = float(row["stop"])
    target = float(row["structural_target"])
    tick = base.TICKS[symbol]

    row.update(
        liquidity_map_features(
            frame,
            pools,
            pivots,
            episode,
            decision_ts,
            entry,
            stop,
            target,
        )
    )
    row.update(
        narrative_phase_features(
            frame,
            episode,
            zone.formed_ts,
            decision_ts,
        )
    )

    for desired_r in base.R_TARGETS:
        tag = str(desired_r).replace(".", "p")
        objective = _net_r_target_price(
            episode.side,
            entry,
            stop,
            tick,
            desired_r,
        )
        row[f"r_{tag}_target"] = objective
        labelled = base._simulate_plan(
            frame,
            funding,
            episode.side,
            entry_i,
            entry,
            stop,
            objective,
            tick,
        )
        for key, value in labelled.items():
            row[f"r_{tag}_{key}"] = value

    row["fixed_r_definition"] = "NET_AFTER_PLANNED_FEES_AND_SLIPPAGE"
    row["narrative_feature_version"] = "LIQUIDITY_MAP_PHASE_V1"
    return row


base.build_action_row = _build_action_row


if __name__ == "__main__":
    base.main()
