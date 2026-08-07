#!/usr/bin/env python3
"""Run v38 and its exact control in the existing shared NautilusTrader node."""
from __future__ import annotations

import shared_account_backtest_v2 as _compat
from cross_asset_repricing_context import reset_shared_cross_asset_context
from isolated_smt_context import reset_shared_isolated_smt_context
from shared_account_strategy_variants_v38 import experimental_shared_strategy_path
from smt_session_context import reset_shared_smt_session_context


# Compatibility routing only. The underlying BacktestNode remains responsible
# for replay, order matching, fills, fees, positions, margin and shared NAV.
_compat._base.final_shared_strategy_path = experimental_shared_strategy_path


def main() -> None:
    reset_shared_cross_asset_context()
    reset_shared_smt_session_context()
    reset_shared_isolated_smt_context()
    _compat.main()


if __name__ == "__main__":
    main()
