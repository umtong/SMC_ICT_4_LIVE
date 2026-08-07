"""Pure intermarket session-liquidity divergence predicates."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


MIN_NONCONFIRMING_PEERS = 2


@dataclass(frozen=True, slots=True)
class PeerSessionState:
    symbol: str
    ts_event: int
    high: float
    low: float
    close: float
    atr: float
    previous_session_high: float
    previous_session_low: float
    flow_15s: float
    flow_60s: float
    depth_imbalance: float


@dataclass(frozen=True, slots=True)
class SmtSessionDecision:
    confirmed: bool
    valid_peers: tuple[str, ...]
    same_side_sweep_peers: tuple[str, ...]
    nonconfirming_peers: tuple[str, ...]


def session_level_swept(
    *,
    state: PeerSessionState,
    swept_kind: str,
    minimum_penetration_atr: float,
) -> bool:
    if swept_kind not in {"HIGH", "LOW"}:
        raise ValueError("swept_kind must be HIGH or LOW")
    values = (
        state.high,
        state.low,
        state.atr,
        state.previous_session_high,
        state.previous_session_low,
        minimum_penetration_atr,
    )
    if not all(math.isfinite(float(value)) for value in values) or state.atr <= 0.0:
        return False
    if swept_kind == "HIGH":
        return state.high >= state.previous_session_high + minimum_penetration_atr * state.atr
    return state.low <= state.previous_session_low - minimum_penetration_atr * state.atr


def smt_session_divergence(
    *,
    current_symbol: str,
    current_ts: int,
    swept_kind: str,
    peer_states: Iterable[PeerSessionState],
    minimum_penetration_atr: float,
    maximum_age_ns: int,
    minimum_nonconfirming_peers: int = MIN_NONCONFIRMING_PEERS,
) -> SmtSessionDecision:
    """Whether the local session raid was not confirmed by independent peers."""
    if maximum_age_ns <= 0 or minimum_nonconfirming_peers < 1:
        raise ValueError("age and peer requirements must be positive")
    valid: list[str] = []
    swept: list[str] = []
    nonconfirming: list[str] = []
    seen: set[str] = set()
    for state in peer_states:
        if state.symbol == current_symbol or state.symbol in seen:
            continue
        seen.add(state.symbol)
        age = current_ts - int(state.ts_event)
        if age <= 0 or age > maximum_age_ns:
            continue
        reference = (
            state.previous_session_high
            if swept_kind == "HIGH"
            else state.previous_session_low
        )
        if not math.isfinite(reference) or not math.isfinite(state.atr) or state.atr <= 0.0:
            continue
        valid.append(state.symbol)
        if session_level_swept(
            state=state,
            swept_kind=swept_kind,
            minimum_penetration_atr=minimum_penetration_atr,
        ):
            swept.append(state.symbol)
        else:
            nonconfirming.append(state.symbol)
    return SmtSessionDecision(
        confirmed=len(nonconfirming) >= minimum_nonconfirming_peers,
        valid_peers=tuple(sorted(valid)),
        same_side_sweep_peers=tuple(sorted(swept)),
        nonconfirming_peers=tuple(sorted(nonconfirming)),
    )


def local_session_raid_response(
    *,
    side: int,
    swept_kind: str,
    boundary: float,
    close: float,
    flow_15s: float,
    flow_60s: float,
    depth_imbalance: float,
    minimum_tail_improvement: float,
    minimum_directional_depth: float,
) -> bool:
    """Require local reclaim plus reversal flow improvement and resting depth."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    expected_side = -1 if swept_kind == "HIGH" else 1 if swept_kind == "LOW" else 0
    if side != expected_side:
        return False
    values = (
        boundary,
        close,
        flow_15s,
        flow_60s,
        depth_imbalance,
        minimum_tail_improvement,
        minimum_directional_depth,
    )
    if not all(math.isfinite(float(value)) for value in values):
        return False
    reclaimed = close < boundary if swept_kind == "HIGH" else close > boundary
    return (
        reclaimed
        and side * (flow_15s - flow_60s) >= minimum_tail_improvement
        and side * depth_imbalance >= minimum_directional_depth
    )


__all__ = [
    "MIN_NONCONFIRMING_PEERS",
    "PeerSessionState",
    "SmtSessionDecision",
    "local_session_raid_response",
    "session_level_swept",
    "smt_session_divergence",
]
