"""Thin runner adapter selecting Candidate 21's response-window strategy.

All data preparation, catalog construction, NautilusTrader venue, fill model,
fees, latency, accounting, continuous NAV, and report generation remain owned
by candidate21_backtest. Only the importable strategy class is replaced.
"""
from __future__ import annotations

from typing import Any

import candidate21_backtest as base


_ORIGINAL_IMPORTABLE_STRATEGY_CONFIG = base.ImportableStrategyConfig


def _response_strategy_config(*args: Any, **kwargs: Any) -> Any:
    values = dict(kwargs)
    if values.get("strategy_path") == "candidate21_strategy:Candidate21Strategy":
        values["strategy_path"] = (
            "candidate21_response_strategy:Candidate21ResponseStrategy"
        )
        values["config_path"] = (
            "candidate21_response_strategy:Candidate21ResponseConfig"
        )
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(*args, **values)


def run_backtest(**kwargs: Any) -> dict[str, Any]:
    previous = base.ImportableStrategyConfig
    base.ImportableStrategyConfig = _response_strategy_config
    try:
        metrics = base.run_backtest(**kwargs)
    finally:
        base.ImportableStrategyConfig = previous
    metrics["candidate"] = "candidate-21-same-minute-response-router"
    metrics["new_alpha"] = (
        "first-10-second shock separated from strictly later 10-60-second response"
    )
    return metrics


__all__ = ["run_backtest"]
