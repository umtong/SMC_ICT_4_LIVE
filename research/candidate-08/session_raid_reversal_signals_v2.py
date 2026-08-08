"""Causal next-bucket adapter for direct Session Raid Reversal V2.

The V1 scenario builder is preserved. V2 changes only an execution-observation contract: a raid
completed during warm-up cannot jump across an arbitrary data gap and use the first row of the
actual evaluation window as its entry. A valid direct-raid entry must be observed in the next
contiguous ten-second execution bucket.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

import session_raid_reversal_signals_v1 as v1
from day_liquidity_delivery_context_v1 import (
    first_execution_position_after as _first_execution_position_after,
)
from quote_resiliency_signals import QuoteResiliencySignalBundle


SIGNAL_REVISION = "SESSION_RAID_REVERSAL_SIGNALS_V2_CAUSAL_NEXT_BUCKET"
MAX_NEXT_BUCKET_GAP_NS = 11 * 1_000_000_000
STALE_REASON = "NO_CONTIGUOUS_NEXT_TEN_SECOND_EXECUTION_BUCKET"
LEGACY_NO_LATER_REASON = "NO_LATER_COMPLETED_TEN_SECOND_EXECUTION_BUCKET"


def contiguous_first_execution_position_after(
    data_times: np.ndarray,
    completed_ns: int,
) -> int | None:
    """Return the first later bucket only when it is causally contiguous."""

    position = _first_execution_position_after(data_times, int(completed_ns))
    if position is None:
        return None
    gap_ns = int(data_times[position]) - int(completed_ns)
    if gap_ns <= 0:
        raise RuntimeError("execution observation must follow the completed raid")
    if gap_ns > MAX_NEXT_BUCKET_GAP_NS:
        return None
    return position


def _rewrite_gap_rejections(
    bundle: QuoteResiliencySignalBundle,
    *,
    data_times: np.ndarray,
) -> tuple[dict[str, int], tuple[dict[str, Any], ...]]:
    diagnostics: Counter[str] = Counter(bundle.diagnostics)
    rewritten: list[dict[str, Any]] = []
    for raw in bundle.rejected_scenarios:
        item = dict(raw)
        if str(item.get("reason")) == LEGACY_NO_LATER_REASON:
            trigger_ns = int(item.get("trigger_time_ns", item.get("interaction_time_ns", 0)))
            raw_position = _first_execution_position_after(data_times, trigger_ns)
            if raw_position is not None:
                observed_ns = int(data_times[raw_position])
                gap_ns = observed_ns - trigger_ns
                if gap_ns > MAX_NEXT_BUCKET_GAP_NS:
                    item["reason"] = STALE_REASON
                    details = dict(item.get("details") or {})
                    details.update(
                        {
                            "first_later_execution_time_ns": observed_ns,
                            "execution_gap_ns": gap_ns,
                            "maximum_allowed_gap_ns": MAX_NEXT_BUCKET_GAP_NS,
                            "signal_revision": SIGNAL_REVISION,
                        }
                    )
                    item["details"] = details
                    diagnostics[LEGACY_NO_LATER_REASON] -= 1
                    diagnostics[STALE_REASON] += 1
        rewritten.append(item)
    if diagnostics.get(LEGACY_NO_LATER_REASON, 0) <= 0:
        diagnostics.pop(LEGACY_NO_LATER_REASON, None)
    return dict(sorted(diagnostics.items())), tuple(rewritten)


def build_session_raid_reversal_signals(**kwargs: Any) -> QuoteResiliencySignalBundle:
    """Run the unchanged V1 scenario with a bounded next-execution observation."""

    data = kwargs.get("data")
    if not isinstance(data, pd.DataFrame):
        raise TypeError("data must be a pandas DataFrame")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("ten-second execution data must use a timezone-aware DatetimeIndex")
    data_times = data.index.as_unit("ns").asi8

    original = v1.first_execution_position_after
    v1.first_execution_position_after = contiguous_first_execution_position_after
    try:
        bundle = v1.build_session_raid_reversal_signals(**kwargs)
    finally:
        v1.first_execution_position_after = original

    diagnostics, rejected = _rewrite_gap_rejections(bundle, data_times=data_times)
    grouped: dict[int, tuple[Any, ...]] = {}
    accepted = 0
    for timestamp, signals in bundle.signals_by_time_ns.items():
        adjusted: list[Any] = []
        for signal in signals:
            gap_ns = int(signal.signal_time_ns) - int(signal.response_time_ns)
            if gap_ns <= 0 or gap_ns > MAX_NEXT_BUCKET_GAP_NS:
                diagnostics[STALE_REASON] = int(diagnostics.get(STALE_REASON, 0)) + 1
                rejected = rejected + (
                    {
                        "scenario_id": signal.scenario_id,
                        "scenario_family": signal.scenario_family,
                        "reason": STALE_REASON,
                        "trigger_time_ns": int(signal.response_time_ns),
                        "signal_time_ns": int(signal.signal_time_ns),
                        "details": {
                            "execution_gap_ns": gap_ns,
                            "maximum_allowed_gap_ns": MAX_NEXT_BUCKET_GAP_NS,
                            "signal_revision": SIGNAL_REVISION,
                        },
                    },
                )
                continue
            details = dict(signal.details)
            details.update(
                {
                    "signal_revision": SIGNAL_REVISION,
                    "next_execution_bucket_gap_ns": gap_ns,
                    "maximum_next_bucket_gap_ns": MAX_NEXT_BUCKET_GAP_NS,
                }
            )
            events = tuple(
                replace(
                    event,
                    details={
                        **dict(event.details),
                        "signal_revision": SIGNAL_REVISION,
                        "next_execution_bucket_gap_ns": gap_ns,
                    },
                )
                for event in signal.events
            )
            adjusted.append(replace(signal, details=details, events=events))
            accepted += 1
        if adjusted:
            grouped[int(timestamp)] = tuple(adjusted)

    diagnostics["V2_CONTIGUOUS_NEXT_BUCKET_PASS"] = accepted
    diagnostics["SIGNAL"] = accepted
    diagnostics["SIGNAL_TIMES"] = len(grouped)
    return QuoteResiliencySignalBundle(
        signals_by_time_ns=dict(sorted(grouped.items())),
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=rejected,
    )


__all__ = [
    "LEGACY_NO_LATER_REASON",
    "MAX_NEXT_BUCKET_GAP_NS",
    "SIGNAL_REVISION",
    "STALE_REASON",
    "build_session_raid_reversal_signals",
    "contiguous_first_execution_position_after",
]
