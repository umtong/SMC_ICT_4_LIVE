"""Pure causal classification for cross-asset leader-to-laggard transfer."""
from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median


@dataclass(frozen=True, slots=True)
class TransferThresholds:
    minimum_peer_median_atr: float = 0.75
    minimum_confirming_peer_atr: float = 0.35
    minimum_lag_gap_atr: float = 0.60
    minimum_local_reprice_atr: float = 0.10
    maximum_peer_countermove_atr: float = 0.25
    minimum_confirming_peers: int = 2

    def __post_init__(self) -> None:
        values = (
            self.minimum_peer_median_atr,
            self.minimum_confirming_peer_atr,
            self.minimum_lag_gap_atr,
            self.minimum_local_reprice_atr,
            self.maximum_peer_countermove_atr,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise ValueError("transfer thresholds must be finite and nonnegative")
        if self.minimum_confirming_peers not in (2, 3):
            raise ValueError("minimum_confirming_peers must be two or three")


@dataclass(frozen=True, slots=True)
class TransferEvidence:
    peer_returns_5m_atr: tuple[float, float, float]
    peer_returns_1m_atr: tuple[float, float, float]
    own_return_5m_atr: float
    own_return_1m_atr: float
    previous_own_return_1m_atr: float
    close: float
    atr: float


@dataclass(frozen=True, slots=True)
class TransferDecision:
    side: int
    peer_median_5m_atr: float
    peer_median_1m_atr: float
    lag_gap_atr: float
    target: float
    confirming_peers: int
    reason: str


UNRESOLVED = TransferDecision(
    side=0,
    peer_median_5m_atr=0.0,
    peer_median_1m_atr=0.0,
    lag_gap_atr=0.0,
    target=math.nan,
    confirming_peers=0,
    reason="NO_CAUSAL_LEADER_TO_LAGGARD_TRANSFER",
)


def classify_leader_laggard_transfer(
    evidence: TransferEvidence,
    thresholds: TransferThresholds = TransferThresholds(),
) -> TransferDecision:
    """Classify a synchronized peer move followed by a local catch-up start."""
    peer5 = tuple(float(value) for value in evidence.peer_returns_5m_atr)
    peer1 = tuple(float(value) for value in evidence.peer_returns_1m_atr)
    scalars = (
        *peer5,
        *peer1,
        evidence.own_return_5m_atr,
        evidence.own_return_1m_atr,
        evidence.previous_own_return_1m_atr,
        evidence.close,
        evidence.atr,
    )
    if not all(math.isfinite(value) for value in scalars):
        return UNRESOLVED
    if evidence.close <= 0.0 or evidence.atr <= 0.0:
        return UNRESOLVED

    peer_median_5m = median(peer5)
    if abs(peer_median_5m) < thresholds.minimum_peer_median_atr:
        return UNRESOLVED
    side = 1 if peer_median_5m > 0.0 else -1
    confirming = sum(
        side * value >= thresholds.minimum_confirming_peer_atr
        for value in peer5
    )
    if confirming < thresholds.minimum_confirming_peers:
        return UNRESOLVED

    lag_gap = side * (peer_median_5m - evidence.own_return_5m_atr)
    if lag_gap < thresholds.minimum_lag_gap_atr:
        return UNRESOLVED

    peer_median_1m = median(peer1)
    if side * peer_median_1m < -thresholds.maximum_peer_countermove_atr:
        return UNRESOLVED
    local_reprice = side * evidence.own_return_1m_atr
    prior_local = side * evidence.previous_own_return_1m_atr
    if (
        local_reprice < thresholds.minimum_local_reprice_atr
        or local_reprice <= prior_local
    ):
        return UNRESOLVED

    atr_fraction = evidence.atr / evidence.close
    signed_gap = peer_median_5m - evidence.own_return_5m_atr
    target = evidence.close * math.exp(signed_gap * atr_fraction)
    if not math.isfinite(target) or target <= 0.0:
        return UNRESOLVED
    if (side > 0 and target <= evidence.close) or (
        side < 0 and target >= evidence.close
    ):
        return UNRESOLVED
    return TransferDecision(
        side=side,
        peer_median_5m_atr=peer_median_5m,
        peer_median_1m_atr=peer_median_1m,
        lag_gap_atr=lag_gap,
        target=target,
        confirming_peers=confirming,
        reason="SYNCHRONIZED_PEER_PRICE_DISCOVERY_LOCAL_REPRICE_STARTED",
    )


__all__ = [
    "TransferDecision",
    "TransferEvidence",
    "TransferThresholds",
    "UNRESOLVED",
    "classify_leader_laggard_transfer",
]
