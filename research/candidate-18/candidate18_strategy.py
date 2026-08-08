"""Import adapter for Candidate 18's effective v6 strategy module."""
from __future__ import annotations

# Retained execution lineage:
# trade_tick_emulated_protection_strategy -> managed_protection_ioc_strategy
from bounded_gtd_entry_strategy import Candidate18Config
from bounded_gtd_entry_strategy import Candidate18Strategy

__all__ = ["Candidate18Config", "Candidate18Strategy"]
