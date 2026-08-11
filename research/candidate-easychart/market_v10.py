"""Cross-sectional session-raid state for candidate-easychart v10.

A local sweep can mean either an isolated stop raid or common market-wide price
discovery.  Treating both as the same reversal was a central ambiguity in the
source material and in the earlier session screen.  This module adapts the
project's prior isolated-SMT work into a bar-available, range-normalized state:

* ISOLATED: at least two of three peers have not swept the corresponding side,
  and the candidate is the deepest normalized excursion.
* BROAD_RECLAIM: at least two peers swept the same side and have also reclaimed
  their own range boundary.
* UNRESOLVED: neither causal market state is established.

This is not a confidence score.  It routes one surface pattern into different
latent auction states using only prices observable at the setup close.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from domain_v3 import Side


ISOLATED = "ISOLATED"
BROAD_RECLAIM = "BROAD_RECLAIM"
UNRESOLVED = "UNRESOLVED"
INSUFFICIENT_PEERS = "INSUFFICIENT_PEERS"


@dataclass(frozen=True, slots=True)
class PeerRangeObservation:
    symbol: str
    side: Side
    range_low: float
    range_high: float
    excursion_low: float
    excursion_high: float
    close: float

    def __post_init__(self) -> None:
        values = (
            self.range_low,
            self.range_high,
            self.excursion_low,
            self.excursion_high,
            self.close,
        )
        if not self.symbol or not all(math.isfinite(value) for value in values):
            raise ValueError("peer range observation must be finite and identified")
        if self.range_high <= self.range_low:
            raise ValueError("range high must exceed range low")
        if self.excursion_high < self.excursion_low:
            raise ValueError("excursion high cannot be below excursion low")

    @property
    def width(self) -> float:
        return self.range_high - self.range_low

    @property
    def penetration(self) -> float:
        if self.side is Side.LONG:
            return max(0.0, (self.range_low - self.excursion_low) / self.width)
        return max(0.0, (self.excursion_high - self.range_high) / self.width)

    @property
    def swept(self) -> bool:
        return self.penetration > 0.0

    @property
    def reclaimed(self) -> bool:
        if self.side is Side.LONG:
            return self.close >= self.range_low
        return self.close <= self.range_high


@dataclass(frozen=True, slots=True)
class CrossSectionalRaidDecision:
    state: str
    candidate_symbol: str
    candidate_penetration: float
    valid_peers: tuple[str, ...]
    swept_peers: tuple[str, ...]
    reclaimed_swept_peers: tuple[str, ...]
    nonconfirming_peers: tuple[str, ...]
    deepest_symbols: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return self.state in {ISOLATED, BROAD_RECLAIM}


def classify_cross_sectional_raid(
    *,
    candidate: PeerRangeObservation,
    peers: Iterable[PeerRangeObservation],
    required_peer_majority: int = 2,
    required_peer_count: int = 3,
) -> CrossSectionalRaidDecision:
    """Classify an observed local reclaim without future information."""
    if required_peer_majority < 1 or required_peer_count < 1:
        raise ValueError("peer requirements must be positive")
    unique: dict[str, PeerRangeObservation] = {}
    for peer in peers:
        if peer.symbol == candidate.symbol or peer.symbol in unique:
            continue
        if peer.side is not candidate.side:
            raise ValueError("all cross-sectional observations must use the same side")
        unique[peer.symbol] = peer
    valid_symbols = tuple(sorted(unique))
    if len(valid_symbols) != required_peer_count:
        return CrossSectionalRaidDecision(
            state=INSUFFICIENT_PEERS,
            candidate_symbol=candidate.symbol,
            candidate_penetration=candidate.penetration,
            valid_peers=valid_symbols,
            swept_peers=(),
            reclaimed_swept_peers=(),
            nonconfirming_peers=(),
            deepest_symbols=(),
        )

    swept = tuple(sorted(symbol for symbol, peer in unique.items() if peer.swept))
    reclaimed = tuple(
        sorted(
            symbol
            for symbol, peer in unique.items()
            if peer.swept and peer.reclaimed
        ),
    )
    nonconfirming = tuple(sorted(symbol for symbol, peer in unique.items() if not peer.swept))
    all_observations = [candidate, *unique.values()]
    deepest_value = max(observation.penetration for observation in all_observations)
    deepest = tuple(
        sorted(
            observation.symbol
            for observation in all_observations
            if math.isclose(observation.penetration, deepest_value, rel_tol=1e-12, abs_tol=1e-12)
        ),
    )

    if (
        len(nonconfirming) >= required_peer_majority
        and candidate.symbol in deepest
    ):
        state = ISOLATED
    elif len(reclaimed) >= required_peer_majority:
        state = BROAD_RECLAIM
    else:
        state = UNRESOLVED

    return CrossSectionalRaidDecision(
        state=state,
        candidate_symbol=candidate.symbol,
        candidate_penetration=candidate.penetration,
        valid_peers=valid_symbols,
        swept_peers=swept,
        reclaimed_swept_peers=reclaimed,
        nonconfirming_peers=nonconfirming,
        deepest_symbols=deepest,
    )


__all__ = [
    "BROAD_RECLAIM",
    "CrossSectionalRaidDecision",
    "INSUFFICIENT_PEERS",
    "ISOLATED",
    "PeerRangeObservation",
    "UNRESOLVED",
    "classify_cross_sectional_raid",
]
