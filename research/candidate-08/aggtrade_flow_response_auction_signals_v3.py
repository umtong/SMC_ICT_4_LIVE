"""Exact-cadence execution revision for the causal flow-response auction detector.

V2 corrected event-window separation, configured window lengths, causal impact surprise, and full
observed sweep invalidation. V3 adds the remaining time-base contract: every input bucket must be an
exact consecutive ten-second bucket. Otherwise three bars are not thirty seconds and the frozen
physical-time hypothesis has not been executed.

The V2 economic states, thresholds, external-level inventory, stop geometry, target selection and
cost-after gate are unchanged. V3 validates cadence before V2 detection and stamps every emitted or
rejected scenario with the exact V3 implementation revision.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import (
    AcceptanceLogicEvent,
    AcceptanceSignal,
    AcceptanceSignalBundle,
)
from aggtrade_flow_response_auction_signals_v2 import (
    ABSORPTION_FAMILY,
    INITIATIVE_FAMILY,
    FlowResponseAuctionConfig,
    build_flow_response_auction_signals as _build_v2,
)


IMPLEMENTATION_REVISION = "CAUSAL_FLOW_RESPONSE_EXTERNAL_AUCTION_V3_EXACT_TEN_SECOND_CADENCE"
TEN_SECOND_NS = 10_000_000_000


def validate_exact_ten_second_cadence(data: pd.DataFrame) -> None:
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("ten-second data must use a timezone-aware DatetimeIndex")
    if not data.index.is_monotonic_increasing or data.index.has_duplicates:
        raise ValueError("ten-second timestamps must be unique and increasing")
    timestamps = data.index.as_unit("ns").asi8
    if timestamps.size < 2:
        raise ValueError("flow-response detection requires at least two completed ten-second buckets")
    deltas = np.diff(timestamps)
    invalid = np.flatnonzero(deltas != TEN_SECOND_NS)
    if invalid.size:
        position = int(invalid[0])
        raise ValueError(
            "flow-response input is not an exact consecutive ten-second series: "
            f"position={position} start_ns={int(timestamps[position])} "
            f"end_ns={int(timestamps[position + 1])} delta_ns={int(deltas[position])}"
        )


def _stamp_event(event: AcceptanceLogicEvent) -> AcceptanceLogicEvent:
    return replace(
        event,
        details={
            **event.details,
            "implementation_revision": IMPLEMENTATION_REVISION,
        },
    )


def _stamp_signal(signal: AcceptanceSignal) -> AcceptanceSignal:
    return replace(
        signal,
        events=tuple(_stamp_event(event) for event in signal.events),
        details={
            **signal.details,
            "implementation_revision": IMPLEMENTATION_REVISION,
            "ten_second_cadence_contract": "EXACT_CONSECUTIVE_10_SECONDS",
        },
    )


def _stamp_rejection(value: dict[str, Any] | Any) -> dict[str, Any]:
    raw = dict(value)
    raw["implementation_revision"] = IMPLEMENTATION_REVISION
    raw["ten_second_cadence_contract"] = "EXACT_CONSECUTIVE_10_SECONDS"
    return raw


def build_flow_response_auction_signals(
    *,
    data: pd.DataFrame,
    context_times: np.ndarray,
    context_bars: tuple[Any, ...],
    snapshots: tuple[tuple[Any, ...], ...],
    symbol: str,
    instrument_id: str,
    tick: float,
    fee_rate: float,
    minimum_net_reward_risk: float,
    auction_config: FlowResponseAuctionConfig = FlowResponseAuctionConfig(),
    flow_response_features: pd.DataFrame | None = None,
    require_retest_contraction: bool = True,
) -> AcceptanceSignalBundle:
    validate_exact_ten_second_cadence(data)
    bundle = _build_v2(
        data=data,
        context_times=context_times,
        context_bars=context_bars,
        snapshots=snapshots,
        symbol=symbol,
        instrument_id=instrument_id,
        tick=tick,
        fee_rate=fee_rate,
        minimum_net_reward_risk=minimum_net_reward_risk,
        auction_config=auction_config,
        flow_response_features=flow_response_features,
        require_retest_contraction=require_retest_contraction,
    )
    stamped = {
        int(timestamp_ns): tuple(_stamp_signal(signal) for signal in signals)
        for timestamp_ns, signals in bundle.signals_by_time_ns.items()
    }
    diagnostics = Counter(bundle.diagnostics)
    diagnostics["EXACT_TEN_SECOND_CADENCE_VERIFIED"] = len(data.index)
    family_counts = Counter(
        str(signal.details.get("scenario_family", "UNCLASSIFIED_FLOW_RESPONSE_SCENARIO"))
        for signals in stamped.values()
        for signal in signals
    )
    if family_counts[INITIATIVE_FAMILY] != diagnostics.get(
        "TRADEABLE_FLOW_RESPONSE_INITIATIVE", 0
    ):
        raise RuntimeError("V3 initiative signal diagnostic count mismatch")
    if family_counts[ABSORPTION_FAMILY] != diagnostics.get(
        "TRADEABLE_FLOW_RESPONSE_ABSORPTION_REVERSAL", 0
    ):
        raise RuntimeError("V3 absorption signal diagnostic count mismatch")
    return AcceptanceSignalBundle(
        signals_by_time_ns=stamped,
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(_stamp_rejection(value) for value in bundle.rejected_scenarios),
    )


__all__ = [
    "ABSORPTION_FAMILY",
    "FlowResponseAuctionConfig",
    "IMPLEMENTATION_REVISION",
    "INITIATIVE_FAMILY",
    "TEN_SECOND_NS",
    "build_flow_response_auction_signals",
    "validate_exact_ten_second_cadence",
]
