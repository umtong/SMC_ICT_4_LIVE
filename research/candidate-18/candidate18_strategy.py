"""Import adapter for Candidate 18's effective v5 strategy module."""
from __future__ import annotations

# The v4 fill-managed execution owner is retained in the inheritance chain:
# managed_protection_ioc_strategy
from trade_tick_emulated_protection_strategy import Candidate18Config
from trade_tick_emulated_protection_strategy import Candidate18Strategy

__all__ = ["Candidate18Config", "Candidate18Strategy"]
