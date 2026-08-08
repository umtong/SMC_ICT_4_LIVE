"""Minimal path adapter consumed by the reused shared-account runner."""
from __future__ import annotations

from portfolio_strategy import STRATEGY_PATHS


def final_shared_strategy_path(winner: str, symbol: str) -> str:
    del winner
    return STRATEGY_PATHS[symbol]


__all__ = ["final_shared_strategy_path"]
