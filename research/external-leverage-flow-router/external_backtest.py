"""Thin adapter around the shared Candidate 21 NautilusTrader runner.

The shared runner remains authoritative for data, catalog construction,
latency, fees, matching, positions, margin, liquidation, and continuous NAV.
Only the importable strategy class is replaced for this research branch.
"""
from __future__ import annotations

from typing import Any

from nautilus_trader.config import ImportableStrategyConfig as _NativeStrategyConfig

import candidate21_backtest as _shared


def _external_strategy_config(*args: Any, **kwargs: Any) -> Any:
    values = dict(kwargs)
    values["strategy_path"] = "market_entry_strategy:MarketEntryStrategy"
    values["config_path"] = "market_entry_strategy:MarketEntryConfig"
    return _NativeStrategyConfig(*args, **values)


# Candidate21's runner resolves this global when run_backtest is invoked.  This
# changes no engine behavior; it only selects the local strategy implementation.
_shared.ImportableStrategyConfig = _external_strategy_config
run_backtest = _shared.run_backtest


__all__ = ["run_backtest"]
