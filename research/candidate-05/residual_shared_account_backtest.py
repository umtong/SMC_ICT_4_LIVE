#!/usr/bin/env python3
"""Run v52/v53 in the existing four-symbol NautilusTrader shared account.

Only orchestration dependency injection differs from
``shared_account_backtest_v2``.  Data loading, timestamp contracts, fills,
fees, positions, current-NAV sizing, portfolio accounting, and the final global
entry-slot coordinator remain owned by the existing project/NautilusTrader
implementation.  The residual families are explicitly marked as preregistered
mechanisms, not mislabeled as validated winners.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Importing v2 installs timestamp, wrangler, positioning, basis and depth-gap
# contracts, and applies the same instrument/equity compatibility repairs.
import shared_account_backtest_v2 as _v2
import shared_account_backtest as _base

from relative_value_context import reset as reset_relative_value_context
from residual_shared_strategy_variants import residual_shared_strategy_path


def load_pre_registered_family(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        raise _base.SharedAccountError(f"residual family manifest not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("classification") != "PRE_REGISTERED_FOUR_ASSET_MECHANISM":
        raise _base.SharedAccountError(
            "residual runner requires PRE_REGISTERED_FOUR_ASSET_MECHANISM",
        )
    winner = payload.get("winner")
    if not isinstance(winner, str) or not winner:
        raise _base.SharedAccountError("residual family manifest contains no strategy")
    for symbol in _base.PROJECT_SYMBOLS:
        residual_shared_strategy_path(winner, symbol)
    return payload, winner


# shared_account_backtest resolves these module globals at run/config-build time.
# No strategy source file is overwritten.
_base.final_shared_strategy_path = residual_shared_strategy_path
_base.load_validated_winner = load_pre_registered_family
run_shared_account = _base.run_shared_account


def main() -> None:
    reset_relative_value_context()
    _v2.reset_shared_cross_asset_context()
    _v2.reset_shared_smt_session_context()
    _base.main()


if __name__ == "__main__":
    main()
