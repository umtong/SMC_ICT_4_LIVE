#!/usr/bin/env python3
"""V25 causal compiler: non-climactic parent-auction resumption.

V24 separated external-pool failed discovery from parent-auction interruption.
A one-scenario ablation showed the parent-resumption mechanism contributed
material positive PnL in two weeks but harmed a third, so it is not discarded.
The losing completed entries shared one causal state violation: the confirmation
minute moved farther than the original interruption shock. Such a bar is a new
impact event, not an orderly resumption of the parent auction.

V25 changes one relation only:

    abs(confirmation 60-second return) <= abs(original shock return)

All V24 detectors, thresholds, stops, targets, costs, risk sizing and execution
remain unchanged. The equality is structural rather than fitted: resumption
cannot be more impulsive than the event it is claimed to have absorbed.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v24 as v24


Intent = v24.Intent
PARENT_SCENARIO = "PARENT_AUCTION_INTERRUPTION_RESUMPTION"
ACCEPTED_SCENARIO = "NON_CLIMACTIC_PARENT_AUCTION_RESUMPTION"


def confirmation_to_shock_ratio(
    confirmation_return_bps: float,
    shock_absolute_return_bps: float,
) -> float:
    if not all(
        math.isfinite(value)
        for value in (confirmation_return_bps, shock_absolute_return_bps)
    ):
        return float("nan")
    if shock_absolute_return_bps <= 0.0:
        return float("nan")
    return abs(confirmation_return_bps) / shock_absolute_return_bps


def is_non_climactic_resumption(
    confirmation_return_bps: float,
    shock_absolute_return_bps: float,
) -> bool:
    ratio = confirmation_to_shock_ratio(
        confirmation_return_bps,
        shock_absolute_return_bps,
    )
    return math.isfinite(ratio) and ratio <= 1.0


def _copy_parent(
    parent: Intent,
    details: dict[str, Any],
) -> Intent:
    return Intent(
        scenario=ACCEPTED_SCENARIO,
        side=int(parent.side),
        signal_index=int(parent.signal_index),
        entry_index=int(parent.entry_index),
        stop_level=float(parent.stop_level),
        event_indices=tuple(int(value) for value in parent.event_indices),
        details=details,
    )


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    intents, parent_summary = v24.collect_signals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )

    accepted: list[Intent] = []
    accepted_count = 0
    rejected_count = 0
    ratios: list[float] = []
    for intent in intents:
        if intent.scenario != PARENT_SCENARIO:
            accepted.append(intent)
            continue

        index = int(intent.signal_index)
        confirmation_return = float(data["ret_60s_bps"].iloc[index])
        shock_return = float(intent.details["absolute_return_bps"])
        ratio = confirmation_to_shock_ratio(
            confirmation_return,
            shock_return,
        )
        ratios.append(ratio)
        passed = is_non_climactic_resumption(
            confirmation_return,
            shock_return,
        )
        details = {
            **intent.details,
            "confirmation_return_60s_bps": confirmation_return,
            "confirmation_absolute_return_60s_bps": abs(confirmation_return),
            "original_shock_absolute_return_bps": shock_return,
            "confirmation_to_shock_return_ratio": ratio,
            "non_climactic_parent_resumption": passed,
            "compiler": "candidate-04-v25",
        }
        if passed:
            accepted.append(_copy_parent(intent, details))
            accepted_count += 1
        else:
            rejected_count += 1

    priority = {
        "EXTERNAL_POOL_FAILED_DISCOVERY_REVERSAL": 0,
        ACCEPTED_SCENARIO: 1,
        "NORMAL_FAILED_AUCTION_RESUMPTION": 2,
        "TAIL_CONFIRMED_STRESS_FAILED_AUCTION": 3,
        "ORDERLY_INVENTORY_DISPLACEMENT": 4,
    }
    accepted.sort(
        key=lambda item: (
            int(item.signal_index),
            priority.get(item.scenario, 99),
        ),
    )

    route_counts = dict(parent_summary.get("route_counts", {}))
    route_counts.update(
        {
            "non_climactic_parent_resumption": accepted_count,
            "climactic_parent_resumption_rejected": rejected_count,
        },
    )
    return accepted, {
        **parent_summary,
        "candidate": "candidate-04-v25-non-climactic-parent-resumption",
        "compiler": "candidate-04-v25",
        "raw_routed_signals": len(accepted),
        "unique_signal_bars": len(accepted),
        "route_counts": route_counts,
        "parent_resumption_contract": {
            "condition": (
                "abs(confirmation 60s return) <= abs(original shock return)"
            ),
            "reason": (
                "a resumption confirmation cannot be a larger impact event "
                "than the interruption it is claimed to absorb"
            ),
            "observed_ratios": ratios,
            "changed_variables_from_v24": 1,
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
