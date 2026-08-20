#!/usr/bin/env python3
"""Executable wrapper for the strict causal-departure harvester."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

import coherent_policy as policy
import hierarchical_liquidity_bpr as hl
import departure_first_return_harvest as core


@dataclass(frozen=True, slots=True)
class BarrierLabel:
    fill_state: str
    outcome: str
    fill_index: int | None
    fill_time_ns: int | None
    resolution_index: int | None
    resolution_time_ns: int | None
    order_terminal_index: int
    order_terminal_time_ns: int
    entry_wait_minutes: float | None
    holding_minutes: float | None
    actual_entry: float | None
    actual_target_net_r: float | None
    actual_stop_net_r: float | None
    actual_gross_rr: float | None
    net_r: float | None
    mfe_r: float | None
    mae_r: float | None = None


@dataclass(frozen=True, slots=True)
class DepartureCandidate:
    confirmation_index: int
    departure_index: int
    setup: hl.Setup
    event_meta: dict[str, Any]
    source: hl.LiquidityLevel


def departure_candidates(data, source, tick):
    output = []
    for detector in (policy._reversal_setup, policy._continuation_setup):
        detected = detector(data, source, tick)
        if detected is None:
            continue
        setup, event_meta = detected
        departure = core._departure_index(data, setup, tick)
        if departure is None:
            continue
        output.append(
            DepartureCandidate(
                confirmation_index=int(setup.confirmation_index),
                departure_index=int(departure),
                setup=setup,
                event_meta=event_meta,
                source=source,
            )
        )
    output.sort(
        key=lambda item: (
            item.confirmation_index,
            item.departure_index,
            str(item.event_meta["narrative_branch"]),
        )
    )
    if len(output) >= 2:
        first, second = output[0], output[1]
        if first.confirmation_index == second.confirmation_index and first.setup.side != second.setup.side:
            return []
    return output[:1]


# Preserve the economic policy while fixing the incomplete positional constructor
# used on unfilled-order paths in the core module.  The omitted last field is MAE,
# which is undefined before entry and therefore correctly defaults to None.
core.BarrierLabel = BarrierLabel
core.DepartureCandidate = DepartureCandidate
core._departure_candidates = departure_candidates

_BASE_GENERATE = core.generate_symbol
_BASE_RUN = core.run_research
_DECISION_END_NS: int | None = None


def generate_symbol(symbol, data, levels, metadata, trading_start):
    frame, counts = _BASE_GENERATE(symbol, data, levels, metadata, trading_start)
    if _DECISION_END_NS is not None and not frame.empty:
        frame = frame[pd.to_numeric(frame.order_time_ns, errors="coerce") < _DECISION_END_NS].copy()
        counts = dict(counts)
        counts["plans"] = int(len(frame))
        counts["states_after_decision_window_filter"] = int(frame.state_id.nunique())
    return frame, counts


def run_research(*, start, end, warmup_days, symbols, cache, output):
    global _DECISION_END_NS
    _DECISION_END_NS = int(pd.Timestamp(end, tz="UTC").value)
    return _BASE_RUN(
        start=start,
        end=end,
        warmup_days=warmup_days,
        symbols=symbols,
        cache=cache,
        output=output,
    )


core.generate_symbol = generate_symbol
core.run_research = run_research

if __name__ == "__main__":
    core.main()
