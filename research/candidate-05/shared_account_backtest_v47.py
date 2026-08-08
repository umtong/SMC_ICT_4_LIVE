#!/usr/bin/env python3
"""Run frozen v47 through the existing one-account NautilusTrader runner.

The base shared runner remains authoritative for data, instruments, orders,
fills, costs, positions, margin, liquidation and NAV.  This adapter changes
only the importable strategy path for the explicitly named v47 winner.
"""
from __future__ import annotations

import shared_account_backtest_v2 as _v2
import shared_account_backtest as _base
from shared_account_strategy_variants_v2 import final_shared_strategy_path as _original_path
from shared_account_v47_variants import v47_shared_strategy_path


V47_WINNER = "strategy_v47_relative_value:RelativeValueDislocationStrategy"


def _strategy_path(winner: str, symbol: str) -> str:
    if winner == V47_WINNER:
        return v47_shared_strategy_path(symbol)
    return _original_path(winner, symbol)


# load_validated_winner resolves this module global at runtime.  No account or
# execution code is replaced.
_base.final_shared_strategy_path = _strategy_path


def main() -> None:
    _v2.main()


if __name__ == "__main__":
    main()
