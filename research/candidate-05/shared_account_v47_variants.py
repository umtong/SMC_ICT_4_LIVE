#!/usr/bin/env python3
"""Direct four-symbol shared-account wrappers for frozen Candidate 05 v47.

This module changes no market decision.  It only combines the already audited
one-global-slot lifecycle with the frozen relative-value strategy so that v47
can finally reach NautilusTrader market replay instead of failing in workflow
command extraction.
"""
from __future__ import annotations

from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as _shared

# Use the same strict intent -> position -> closed -> release coordinator as the
# other final shared-account variants.
_shared.SHARED_ACCOUNT_ENTRY_COORDINATOR = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR

from strategy_global_slot_wrappers_v4 import SharedAccountEntryLifecycleMixin
from strategy_v47_relative_value import RelativeValueDislocationStrategy


def _variant(name: str) -> type:
    return type(
        name,
        (SharedAccountEntryLifecycleMixin, RelativeValueDislocationStrategy),
        {
            "__module__": __name__,
            "__doc__": (
                "Frozen v47 relative-value policy with the audited one-account "
                "global entry/position lifecycle."
            ),
        },
    )


for _symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
    _name = f"FinalSharedV47{_symbol}Strategy"
    globals()[_name] = _variant(_name)


def v47_shared_strategy_path(symbol: str) -> str:
    if symbol not in {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}:
        raise ValueError(f"unsupported project symbol: {symbol}")
    return f"{__name__}:FinalSharedV47{symbol}Strategy"


__all__ = [
    "FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR",
    "v47_shared_strategy_path",
    *[
        f"FinalSharedV47{symbol}Strategy"
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    ],
]
