"""Pure causal logic for separating local rejection from systemic repricing."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


TWO_TO_ONE_DIRECTIONAL_IMBALANCE = 1.0 / 3.0
MIN_CONFIRMING_PEERS = 2


@dataclass(frozen=True, slots=True)
class PeerAuctionState:
    symbol: str
    ts_event: int
    return_atr: float
    flow_3m: float
    efficiency_60s: float
    depth_imbalance: float


@dataclass(frozen=True, slots=True)
class SystemicRepricingDecision:
    blocked: bool
    repricing_direction: int
    eligible_peers: tuple[str, ...]
    confirming_peers: tuple[str, ...]


def peer_confirms_systemic_repricing(
    *,
    state: PeerAuctionState,
    direction: int,
    minimum_return_atr: float,
    minimum_efficiency: float,
    minimum_directional_depth: float,
    minimum_directional_flow: float = TWO_TO_ONE_DIRECTIONAL_IMBALANCE,
) -> bool:
    """Whether a completed peer state confirms efficient common repricing."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    values = (
        state.return_atr,
        state.flow_3m,
        state.efficiency_60s,
        state.depth_imbalance,
        minimum_return_atr,
        minimum_efficiency,
        minimum_directional_depth,
        minimum_directional_flow,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False
    return (
        direction * state.return_atr >= minimum_return_atr
        and direction * state.flow_3m >= minimum_directional_flow
        and state.efficiency_60s >= minimum_efficiency
        and direction * state.depth_imbalance >= minimum_directional_depth
    )


def systemic_repricing_decision(
    *,
    trade_side: int,
    current_symbol: str,
    current_ts: int,
    peer_states: Iterable[PeerAuctionState],
    minimum_return_atr: float,
    minimum_efficiency: float,
    minimum_directional_depth: float,
    maximum_age_ns: int,
    minimum_confirming_peers: int = MIN_CONFIRMING_PEERS,
) -> SystemicRepricingDecision:
    """Block a local reversal only when independent peers confirm its opposite.

    ``trade_side`` is the proposed local reversal. The systemic repricing
    direction is therefore ``-trade_side``. Only observations strictly earlier
    than the current event are eligible, preventing same-timestamp strategy
    registration order from becoming information.
    """
    if trade_side not in (-1, 1):
        raise ValueError("trade_side must be -1 or 1")
    if maximum_age_ns <= 0 or minimum_confirming_peers < 1:
        raise ValueError("age and confirming-peer requirements must be positive")

    direction = -trade_side
    eligible: list[str] = []
    confirming: list[str] = []
    seen: set[str] = set()
    for state in peer_states:
        if state.symbol == current_symbol or state.symbol in seen:
            continue
        seen.add(state.symbol)
        age = current_ts - int(state.ts_event)
        if age <= 0 or age > maximum_age_ns:
            continue
        eligible.append(state.symbol)
        if peer_confirms_systemic_repricing(
            state=state,
            direction=direction,
            minimum_return_atr=minimum_return_atr,
            minimum_efficiency=minimum_efficiency,
            minimum_directional_depth=minimum_directional_depth,
        ):
            confirming.append(state.symbol)

    return SystemicRepricingDecision(
        blocked=len(confirming) >= minimum_confirming_peers,
        repricing_direction=direction,
        eligible_peers=tuple(sorted(eligible)),
        confirming_peers=tuple(sorted(confirming)),
    )


__all__ = [
    "MIN_CONFIRMING_PEERS",
    "PeerAuctionState",
    "SystemicRepricingDecision",
    "TWO_TO_ONE_DIRECTIONAL_IMBALANCE",
    "peer_confirms_systemic_repricing",
    "systemic_repricing_decision",
]
