#!/usr/bin/env python3
"""Run v52/v53 in the existing four-symbol NautilusTrader shared account.

Only dependency injection differs from ``shared_account_backtest_v2``: the
validated-winner resolver is replaced by the explicit v52/v53 wrapper resolver.
Data loading, timestamp contracts, fills, fees, positions, current-NAV sizing,
portfolio accounting, and the final global entry-slot coordinator remain owned
by the existing project/NautilusTrader implementation.
"""
from __future__ import annotations

# Importing v2 installs timestamp, wrangler, positioning, basis and depth-gap
# contracts, and applies the same instrument/equity compatibility repairs.
import shared_account_backtest_v2 as _v2
import shared_account_backtest as _base

from relative_value_context import reset as reset_relative_value_context
from residual_shared_strategy_variants import residual_shared_strategy_path


# shared_account_backtest resolves this module global at strategy-config build
# time, so replacing it here is sufficient and avoids mutating any strategy
# source file on disk.
_base.final_shared_strategy_path = residual_shared_strategy_path


def main() -> None:
    reset_relative_value_context()
    _v2.reset_shared_cross_asset_context()
    _v2.reset_shared_smt_session_context()
    _base.main()


if __name__ == "__main__":
    main()
