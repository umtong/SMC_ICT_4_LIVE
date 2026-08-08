"""Nautilus runner adapter for Candidate 21 synchronized flow carry.

The event-time research runner writes one real trade tick per 10-second bucket to
provide a causal sub-second clock.  That sparse clock is valid for event ordering,
but its single-trade quantity is not a representation of executable market depth.
A market IOC can therefore be partially filled only up to one aggregate trade.

Flow carry is a multi-hour strategy, so this adapter deliberately removes sparse
TradeTick data from the replay and lets NautilusTrader fill the market parent on
the next completed external 10-second bar.  This is later and more conservative
than the 300 ms diagnostic clock, while preserving full order quantity without
inventing liquidity or implementing a custom matching engine.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import candidate21_event_backtest as base
from smc_ict_4.manifest import write_json_atomic


_ORIGINAL_IMPORTABLE_STRATEGY_CONFIG = base.ImportableStrategyConfig
_ORIGINAL_BACKTEST_RUN_CONFIG = base.BacktestRunConfig
_ORIGINAL_BACKTEST_VENUE_CONFIG = base.BacktestVenueConfig


def _flow_carry_strategy_config(*args: Any, **kwargs: Any) -> Any:
    values = dict(kwargs)
    if values.get("strategy_path") == (
        "candidate21_event_strategy:Candidate21EventStrategy"
    ):
        values["strategy_path"] = (
            "candidate21_flow_carry_strategy:Candidate21FlowCarryStrategy"
        )
        values["config_path"] = (
            "candidate21_flow_carry_strategy:Candidate21FlowCarryConfig"
        )
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(*args, **values)


def _is_trade_tick_config(config: Any) -> bool:
    data_cls = getattr(config, "data_cls", None)
    if data_cls is base.TradeTick:
        return True
    if getattr(data_cls, "__name__", None) == "TradeTick":
        return True
    return "TradeTick" in str(data_cls)


def _bar_only_run_config(*args: Any, **kwargs: Any) -> Any:
    values = dict(kwargs)
    data = list(values.get("data", []))
    filtered = [item for item in data if not _is_trade_tick_config(item)]
    if len(filtered) == len(data):
        raise RuntimeError(
            "flow-carry runner could not identify sparse TradeTick data",
        )
    if not filtered:
        raise RuntimeError("flow-carry runner removed every data stream")
    values["data"] = filtered
    return _ORIGINAL_BACKTEST_RUN_CONFIG(*args, **values)


def _bar_only_venue_config(*args: Any, **kwargs: Any) -> Any:
    values = dict(kwargs)
    values["trade_execution"] = False
    values["bar_execution"] = True
    return _ORIGINAL_BACKTEST_VENUE_CONFIG(*args, **values)


def _rewrite_manifest(output: Path) -> None:
    path = output.resolve() / "run.json"
    if not path.exists():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["candidate"] = "candidate-21-synchronized-flow-carry"
    extra = dict(manifest.get("extra", {}))
    extra.update(
        {
            "strategy": "Candidate21FlowCarryStrategy",
            "parent_event": (
                "full-hour first-10-second balance attack with immediate "
                "non-overlapping 10-second acceptance"
            ),
            "context": (
                "response close aligned with both causal one-hour and "
                "three-hour price discovery"
            ),
            "entry": (
                "Nautilus market IOC filled on the next completed external "
                "10-second bar"
            ),
            "entry_delay": (
                "one 10-second bar; conservative replacement for a sparse "
                "single-aggTrade quantity clock"
            ),
            "trade_tick_execution": False,
            "bar_execution": True,
            "protection": (
                "reduce-only stop-market at strictly prior one-hour "
                "opposite extreme plus 30-minute ATR buffer"
            ),
            "exit": "four hours or before funding; no arbitrary price target",
            "risk": "3% current continuous NAV planned loss",
        },
    )
    manifest["extra"] = extra
    write_json_atomic(path, manifest)


def run_backtest(**kwargs: Any) -> dict[str, Any]:
    previous_strategy = base.ImportableStrategyConfig
    previous_run = base.BacktestRunConfig
    previous_venue = base.BacktestVenueConfig
    base.ImportableStrategyConfig = _flow_carry_strategy_config
    base.BacktestRunConfig = _bar_only_run_config
    base.BacktestVenueConfig = _bar_only_venue_config
    try:
        metrics = base.run_backtest(**kwargs)
    finally:
        base.ImportableStrategyConfig = previous_strategy
        base.BacktestRunConfig = previous_run
        base.BacktestVenueConfig = previous_venue

    _rewrite_manifest(Path(kwargs["output"]))
    metrics.update(
        {
            "candidate": "candidate-21-synchronized-flow-carry",
            "alpha_parent": (
                "full-hour native-10s acceptance with one-hour and "
                "three-hour price-discovery alignment"
            ),
            "stop_policy": (
                "prior one-hour opposite extreme plus 30-minute ATR buffer"
            ),
            "target_policy": None,
            "exit_policy": "FOUR_HOURS_OR_BEFORE_FUNDING",
            "entry_order": "MARKET_IOC",
            "entry_execution": "NEXT_COMPLETED_EXTERNAL_10S_BAR",
            "protective_order": "REDUCE_ONLY_STOP_MARKET_GTC",
            "bar_execution": True,
            "trade_execution": False,
            "sparse_tick_quantity_used_for_fills": False,
        },
    )
    return metrics


__all__ = [
    "_bar_only_run_config",
    "_bar_only_venue_config",
    "_is_trade_tick_config",
    "run_backtest",
]
