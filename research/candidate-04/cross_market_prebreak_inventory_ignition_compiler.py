#!/usr/bin/env python3
"""V49: enter the lag before the follower has completed its catch-up.

V48 waited for the follower to break its pre-event structure and then entered on
the next minute. The first independent week showed that this placed the order
after the proposed lead-lag edge had already been consumed. V49 keeps the same
BTC information event, thresholds, follower set, global selection, costs and
risk contract, but changes the central state transition:

1. BTC must move first; a same-direction follower tail in the immediately
   preceding completed minute invalidates the underreaction state.
2. The follower must then show its own aligned flow, return, basis and new OI
   while the pre-event structure boundary remains completely untouched.
3. Entry remains on the following minute. The untouched pre-event boundary is
   the causal catch-up target; candidates below 1.2 net R after round-trip costs
   are not emitted.

This is a single economic change from post-break chase to pre-break inventory
ignition. NautilusTrader still owns every order, fill, fee, position, PnL and
NAV calculation.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

import cross_market_information_transfer_compiler_v2 as adapter
from nt_liquidity_strategy import net_r_at_price


base = adapter.base

CANDIDATE = "candidate-04-v49-cross-market-prebreak-inventory-ignition"
COMPILER = "candidate-04-cross-market-prebreak-inventory-ignition-v1"
SCENARIO = "CROSS_MARKET_PREBREAK_INVENTORY_IGNITION"
COST_RATE = 0.00075
MINIMUM_NET_R = 1.20

_ORIGINAL_UNDERREACTION = base.follower_underreacted
_ORIGINAL_WRITE_OUTPUTS = base.write_outputs


def leader_first_underreacted(
    data: pd.DataFrame,
    index: int,
    side: int,
    median_absolute_return: float,
) -> tuple[bool, float]:
    """Reject followers which had already displaced before the BTC event."""

    passed, boundary = _ORIGINAL_UNDERREACTION(
        data,
        index,
        side,
        median_absolute_return,
    )
    if not passed or index < 1:
        return False, boundary
    prior_directional_return = side * float(data["ret_60s_bps"].iloc[index - 1])
    if not math.isfinite(prior_directional_return):
        return False, boundary
    return prior_directional_return < median_absolute_return, boundary


def follower_inventory_ignition(
    data: pd.DataFrame,
    leader_index: int,
    signal_index: int,
    side: int,
    structure: float,
    flow_cutoff: float,
    oi_cutoff: float,
) -> tuple[bool, dict[str, float | int | bool | str]]:
    """Confirm follower inventory before the catch-up boundary is reached."""

    if not (
        leader_index < signal_index < len(data)
        and side in (-1, 1)
        and all(math.isfinite(value) for value in (structure, flow_cutoff, oi_cutoff))
    ):
        return False, {}
    row = data.iloc[signal_index]
    close = float(row["close"])
    high = float(row["high"])
    low = float(row["low"])
    flow = side * float(row["flow_60s"])
    return_bps = side * float(row["ret_60s_bps"])
    basis = side * float(row["basis_change_5m"])
    notional = float(row["notional_60s"])
    event_close = float(data["close"].iloc[leader_index])
    directional_progress = side * (close - event_close) > 0.0
    structure_unbroken = side * (close - structure) <= 0.0
    directional_extreme = high if side > 0 else low
    target_untouched = side * (directional_extreme - structure) < 0.0
    oi_passed, oi_change = base.state_oi_creation(
        data["metric_sum_open_interest"].astype(float),
        max(leader_index - 1, 0),
        signal_index,
        oi_cutoff,
    )
    values = (close, high, low, flow, return_bps, basis, notional, oi_change)
    passed = bool(
        structure_unbroken
        and target_untouched
        and directional_progress
        and all(math.isfinite(value) for value in values)
        and flow >= flow_cutoff > 0.0
        and return_bps > 0.0
        and basis > 0.0
        and oi_passed
    )
    return passed, {
        "follower_structure": structure,
        "follower_structure_broken": False,
        "follower_structure_unbroken": structure_unbroken,
        "follower_target_untouched": target_untouched,
        "follower_directional_progress": directional_progress,
        "follower_directional_flow_60s": flow,
        "follower_directional_return_60s_bps": return_bps,
        "follower_directional_basis_change_5m_bps": basis,
        "follower_state_open_interest_change": oi_change,
        "follower_confirmation_notional_60s": notional,
        "follower_flow_cutoff": flow_cutoff,
        "follower_oi_creation_cutoff": oi_cutoff,
        "causal_target_reference": structure,
        "causal_target_source": "causal_pivot_pool_pre_event_cross_market_boundary",
        "causal_target_observed_index": max(leader_index - 1, 0),
        "causal_target_registry": "untouched_pre_event_five_minute_structure",
        "target_enrichment_changed_entry_logic": False,
    }


def tradeable_boundary_candidates(
    candidates: list[Any],
    frames: dict[str, pd.DataFrame],
) -> tuple[list[Any], int]:
    selected: list[Any] = []
    rejected = 0
    for item in candidates:
        frame = frames[item.symbol]
        entry = float(frame["close"].iloc[item.signal_index])
        stop = float(item.stop_level)
        target = float(item.details["causal_target_reference"])
        price_loss = item.side * (entry - stop)
        planned_loss = price_loss + COST_RATE * (entry + stop)
        values = (entry, stop, target, price_loss, planned_loss)
        if not all(math.isfinite(value) for value in values):
            rejected += 1
            continue
        if price_loss <= 0.0 or planned_loss <= 0.0:
            rejected += 1
            continue
        net_r = net_r_at_price(
            entry,
            target,
            item.side,
            planned_loss,
            COST_RATE,
        )
        if not math.isfinite(net_r) or net_r < MINIMUM_NET_R:
            rejected += 1
            continue
        details = dict(item.details)
        details.update(
            {
                "causal_target_net_r_at_compilation": net_r,
                "compiler": COMPILER,
                "controlled_change": (
                    "post-break catch-up chase replaced by pre-break inventory ignition"
                ),
            }
        )
        selected.append(
            base.Candidate(
                symbol=item.symbol,
                leader_index=item.leader_index,
                signal_index=item.signal_index,
                side=item.side,
                stop_level=item.stop_level,
                confirmation_notional=item.confirmation_notional,
                details=details,
            )
        )
    return selected, rejected


def _rewrite_outputs(output: Path) -> None:
    for symbol in base.SYMBOLS:
        signal_path = output / symbol / "signals.json"
        rows = json.loads(signal_path.read_text(encoding="utf-8"))
        for row in rows:
            row["scenario"] = SCENARIO
            details = dict(row.get("details") or {})
            details["compiler"] = COMPILER
            row["details"] = details
        signal_path.write_text(
            json.dumps(rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        summary_path = output / symbol / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary.update(
            {
                "candidate": CANDIDATE,
                "scenario": SCENARIO,
                "ablation_of": "candidate-04-v48-cross-market-information-transfer",
            }
        )
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    aggregate_path = output / "summary.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate.update(
        {
            "candidate": CANDIDATE,
            "compiler": COMPILER,
            "scenario": SCENARIO,
            "ablation_of": "candidate-04-v48-cross-market-information-transfer",
            "controlled_change": (
                "enter on leader-first follower inventory ignition while the catch-up "
                "boundary remains untouched, rather than after its structure break"
            ),
            "scenario_contract": {
                "leader": (
                    "tail efficient BTC flow and return with basis alignment and "
                    "material OI creation"
                ),
                "leader_first": (
                    "follower had no same-direction tail above its shifted median in "
                    "the immediately preceding completed minute"
                ),
                "underreaction": (
                    "follower pre-event structure remains unbroken and event response "
                    "is below its shifted median absolute return"
                ),
                "ignition": (
                    "follower flow, return, basis and state OI align while the entire "
                    "pre-event structure boundary remains untouched"
                ),
                "selection": "earliest ignition, then highest completed notional",
                "stop": "full leader-event to follower-ignition adverse excursion",
                "target": (
                    "untouched pre-event structure boundary, emitted only at >=1.2 "
                    "cost-aware net R"
                ),
                "execution": "one-account NautilusTrader BacktestNode",
            },
        }
    )
    aggregate_path.write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(aggregate, indent=2, sort_keys=True))


def write_ignition_outputs(
    output: Path,
    candidates: list[Any],
    counts: dict[str, Any],
    frames: dict[str, pd.DataFrame],
    nt_frames: dict[str, pd.DataFrame],
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
) -> None:
    selected, rejected = tradeable_boundary_candidates(candidates, frames)
    counts["uneconomic_boundary_targets"] = rejected
    counts["tradeable_prebreak_ignitions"] = len(selected)
    _ORIGINAL_WRITE_OUTPUTS(
        output,
        selected,
        counts,
        frames,
        nt_frames,
        evaluation_start,
        evaluation_end,
    )
    _rewrite_outputs(output)


base.SCENARIO = SCENARIO
base.follower_underreacted = leader_first_underreacted
base.follower_confirmation = follower_inventory_ignition
base.write_outputs = write_ignition_outputs


if __name__ == "__main__":
    base.main()
