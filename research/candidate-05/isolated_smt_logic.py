"""Pure cross-asset predicates for an isolated session-liquidity reversal."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from smt_session_divergence_logic import SmtSessionDecision


REQUIRED_PEERS = 3
MAX_COMMON_CONTINUATION_PEERS = 1


@dataclass(frozen=True, slots=True)
class PeerMicroState:
    """One strictly prior completed-minute peer observation."""

    symbol: str
    ts_event: int
    ret_60s_bps: float
    flow_60s: float
    efficiency_60s: float


@dataclass(frozen=True, slots=True)
class IsolatedSmtDecision:
    confirmed: bool
    reason_code: str
    valid_micro_peers: tuple[str, ...]
    common_continuation_peers: tuple[str, ...]


def isolated_smt_reversal_context(
    *,
    current_symbol: str,
    current_ts: int,
    side: int,
    session_decision: SmtSessionDecision,
    micro_states: Iterable[PeerMicroState],
    maximum_age_ns: int,
    minimum_counterflow: float,
    minimum_efficiency: float,
    required_peers: int = REQUIRED_PEERS,
    max_common_continuation_peers: int = MAX_COMMON_CONTINUATION_PEERS,
) -> IsolatedSmtDecision:
    """Confirm a local raid only when it is isolated from a common price move.

    Session non-confirmation alone is not enough. All three independent peers
    must fail to consume corresponding session liquidity. The reversal is then
    rejected when at least two peers are still making an efficient price move in
    the raid direction with aligned aggressor flow. That state is common price
    discovery, not an isolated local liquidity excursion.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or 1")
    if maximum_age_ns <= 0 or required_peers < 1:
        raise ValueError("age and required peer count must be positive")
    if max_common_continuation_peers < 0:
        raise ValueError("maximum common continuation peers must be nonnegative")
    if minimum_counterflow < 0.0 or minimum_efficiency < 0.0:
        raise ValueError("flow and efficiency thresholds must be nonnegative")

    valid_session = tuple(sorted(set(session_decision.valid_peers)))
    nonconfirming = tuple(sorted(set(session_decision.nonconfirming_peers)))
    confirming = tuple(sorted(set(session_decision.same_side_sweep_peers)))
    if (
        len(valid_session) != required_peers
        or len(nonconfirming) != required_peers
        or confirming
        or set(valid_session) != set(nonconfirming)
    ):
        return IsolatedSmtDecision(
            confirmed=False,
            reason_code="SESSION_RAID_NOT_ISOLATED_ACROSS_ALL_PEERS",
            valid_micro_peers=(),
            common_continuation_peers=(),
        )

    valid: dict[str, PeerMicroState] = {}
    for state in micro_states:
        if state.symbol == current_symbol or state.symbol in valid:
            continue
        if state.symbol not in valid_session:
            continue
        age = current_ts - int(state.ts_event)
        values = (state.ret_60s_bps, state.flow_60s, state.efficiency_60s)
        if age <= 0 or age > maximum_age_ns:
            continue
        if not all(math.isfinite(float(value)) for value in values):
            continue
        valid[state.symbol] = state

    valid_symbols = tuple(sorted(valid))
    if len(valid_symbols) != required_peers:
        return IsolatedSmtDecision(
            confirmed=False,
            reason_code="INSUFFICIENT_PRIOR_COMPLETED_PEER_MICRO_STATES",
            valid_micro_peers=valid_symbols,
            common_continuation_peers=(),
        )

    continuation: list[str] = []
    for symbol in valid_symbols:
        state = valid[symbol]
        # Proposed reversal side is opposite the raid's continuation direction.
        # Price, aggressive flow and efficiency must agree before a peer counts
        # as continuing common price discovery.
        if (
            side * state.ret_60s_bps < 0.0
            and side * state.flow_60s <= -minimum_counterflow
            and state.efficiency_60s >= minimum_efficiency
        ):
            continuation.append(symbol)

    continuation_symbols = tuple(sorted(continuation))
    if len(continuation_symbols) > max_common_continuation_peers:
        return IsolatedSmtDecision(
            confirmed=False,
            reason_code="COMMON_PEER_PRICE_DISCOVERY_CONTINUES_RAID_DIRECTION",
            valid_micro_peers=valid_symbols,
            common_continuation_peers=continuation_symbols,
        )

    return IsolatedSmtDecision(
        confirmed=True,
        reason_code="ALL_PEERS_NONCONFIRM_AND_COMMON_CONTINUATION_ABSENT",
        valid_micro_peers=valid_symbols,
        common_continuation_peers=continuation_symbols,
    )


__all__ = [
    "IsolatedSmtDecision",
    "MAX_COMMON_CONTINUATION_PEERS",
    "PeerMicroState",
    "REQUIRED_PEERS",
    "isolated_smt_reversal_context",
]
