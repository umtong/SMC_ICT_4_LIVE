#!/usr/bin/env python3
"""Executable wrapper for the strict causal-departure harvester."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import coherent_policy as policy
import hierarchical_liquidity_bpr as hl
import departure_first_return_harvest as core


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


core.DepartureCandidate = DepartureCandidate
core._departure_candidates = departure_candidates

if __name__ == "__main__":
    core.main()
