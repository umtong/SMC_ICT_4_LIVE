"""Thin adapters around the shared Candidate 21 NautilusTrader runner.

The shared runner remains authoritative for data, catalog construction,
latency, fees, matching, positions, margin, liquidation, portfolio accounting,
and continuous NAV. This branch replaces only:

* the sparse one-print-per-minute latency clock with bounded, volume-preserving
  actual aggTrade execution windows; and
* the strategy import with the causal failed-auction router and a
  price-protected LIMIT-GTD execution policy.

NautilusTrader remains the sole matching and account engine.
"""
from __future__ import annotations

from typing import Any

from nautilus_trader.config import ImportableStrategyConfig as _NativeStrategyConfig

import candidate21_backtest as _shared
from execution_window_ticks import append_execution_window_ticks


def _external_strategy_config(*args: Any, **kwargs: Any) -> Any:
    values = dict(kwargs)
    values["strategy_path"] = "causal_failure_router:FailureRouterStrategy"
    values["config_path"] = "causal_failure_router:FailureRouterConfig"
    return _NativeStrategyConfig(*args, **values)


_shared.ImportableStrategyConfig = _external_strategy_config
_shared._append_execution_ticks = append_execution_window_ticks
run_backtest = _shared.run_backtest


__all__ = ["run_backtest"]
