#!/usr/bin/env python3
"""Final shared-account wrapper for the v46 inventory-transfer system."""
from __future__ import annotations

# Importing v5 installs the final strict-release coordinator into the v4 mixin.
from strategy_global_slot_wrappers_v5 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as _v4
from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy


class FinalSharedAccountV46Strategy(
    _v4.SharedAccountEntryLifecycleMixin,
    NoPostRetraceBreakawayStrategy,
):
    """Run unchanged v46 market logic under one audited four-symbol slot."""


__all__ = [
    "FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR",
    "FinalSharedAccountV46Strategy",
]
