"""Thin adapters around the shared Candidate 21 NautilusTrader runner.

The shared runner remains authoritative for data, catalog construction,
latency, fees, matching, positions, margin, liquidation, portfolio accounting,
and continuous NAV.  This branch replaces only:

* the sparse one-print-per-minute latency clock with bounded, volume-preserving
  actual aggTrade execution windows; and
* the strategy import with a price-protected LIMIT-GTD execution policy.

NautilusTrader remains the sole matching and account engine.
"""
from __future__ import annotations

from typing import Any

from nautilus_trader.config import ImportableStrategyConfig as _NativeStrategyConfig

import candidate21_backtest as _shared
from execution_window_ticks import append_execution_window_ticks


def _external_strategy_config(*args: Any, **kwargs: Any) -> Any:
    values = dict(kwargs)
    values["strategy_path"] = "market_entry_strategy:WindowedLimitStrategy"
    values["config_path"] = "market_entry_strategy:WindowedLimitConfig"
    return _NativeStrategyConfig(*args, **values)


# Candidate21 resolves both names from its module globals at run time.  The
# adapters change no matching, order, position, margin, liquidation, or NAV
# behavior inside NautilusTrader.
_shared.ImportableStrategyConfig = _external_strategy_config
_shared._append_execution_ticks = append_execution_window_ticks
run_backtest = _shared.run_backtest


__all__ = ["run_backtest"]
