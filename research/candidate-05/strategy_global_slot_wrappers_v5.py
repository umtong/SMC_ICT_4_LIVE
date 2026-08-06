#!/usr/bin/env python3
"""Final shared-account wrappers using the strict release-phase coordinator."""
from __future__ import annotations

from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as _v4


# The inherited methods intentionally resolve their coordinator through the v4
# module global.  Replace that singleton before any shared-account strategy is
# instantiated; all final wrapper classes below then use the stricter lifecycle
# contract without duplicating market or order-lifecycle code.
_v4.SHARED_ACCOUNT_ENTRY_COORDINATOR = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR


class FinalSharedAccountV26Strategy(_v4.SharedAccountV26Strategy):
    pass


class FinalSharedAccountNoEarlySponsoredStrategy(
    _v4.SharedAccountNoEarlySponsoredStrategy,
):
    pass


class FinalSharedAccountV29bStrategy(_v4.SharedAccountV29bStrategy):
    pass


class FinalSharedAccountV30Strategy(_v4.SharedAccountV30Strategy):
    pass


class FinalSharedAccountV31Strategy(_v4.SharedAccountV31Strategy):
    pass


class FinalSharedAccountV32Strategy(_v4.SharedAccountV32Strategy):
    pass


__all__ = [
    "FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR",
    "FinalSharedAccountNoEarlySponsoredStrategy",
    "FinalSharedAccountV26Strategy",
    "FinalSharedAccountV29bStrategy",
    "FinalSharedAccountV30Strategy",
    "FinalSharedAccountV31Strategy",
    "FinalSharedAccountV32Strategy",
]
