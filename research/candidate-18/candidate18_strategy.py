"""Import adapter for Candidate 18's effective v7 strategy module."""
from __future__ import annotations

# Retained execution lineage for source-contract tests and failure evidence:
# bounded_gtd_entry_strategy -> trade_tick_emulated_protection_strategy
# -> managed_protection_ioc_strategy
from local_twin_trigger_strategy import Candidate18Config
from local_twin_trigger_strategy import Candidate18Strategy

__all__ = ["Candidate18Config", "Candidate18Strategy"]
