"""Frozen research role for Candidate 05 SMT session divergence."""
from __future__ import annotations

SMT_ROLE = "CONTEXT_NOT_STANDALONE_SIGNAL"
SAME_TIMESTAMP_PEERS_ALLOWED = False
REQUIRED_NONCONFIRMING_PEERS = 2

__all__ = [
    "REQUIRED_NONCONFIRMING_PEERS",
    "SAME_TIMESTAMP_PEERS_ALLOWED",
    "SMT_ROLE",
]
