#!/usr/bin/env python3
"""Final shared-account wrappers using the strict release-phase coordinator."""
from __future__ import annotations

from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
import strategy_global_slot_wrappers_v4 as _v4


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


class FinalSharedAccountV36Strategy(_v4.SharedAccountV36Strategy):
    pass


class FinalSharedAccountV37Strategy(_v4.SharedAccountV37Strategy):
    pass


__all__ = [
    "FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR",
    "FinalSharedAccountNoEarlySponsoredStrategy",
    "FinalSharedAccountV26Strategy",
    "FinalSharedAccountV29bStrategy",
    "FinalSharedAccountV30Strategy",
    "FinalSharedAccountV31Strategy",
    "FinalSharedAccountV32Strategy",
    "FinalSharedAccountV36Strategy",
    "FinalSharedAccountV37Strategy",
]
