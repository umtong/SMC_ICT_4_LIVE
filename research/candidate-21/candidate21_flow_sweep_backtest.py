"""Bar-volume market-sweep runner for Candidate 21 synchronized carry."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import candidate21_event_backtest as base
from smc_ict_4.manifest import write_json_atomic


_ORIGINAL_IMPORTABLE_STRATEGY_CONFIG = base.ImportableStrategyConfig
_ORIGINAL_BACKTEST_RUN_CONFIG = base.BacktestRunConfig
_ORIGINAL_BACKTEST_VENUE_CONFIG = base.BacktestVenueConfig


def _flow_sweep_strategy_config(*args: Any, **kwargs: Any) -> Any:
    values = dict(kwargs)
    if values.get("strategy_path") == (
        "candidate21_event_strategy:Candidate21EventStrategy"
    ):
        values["strategy_path"] = (
            "candidate21_flow_sweep_strategy:Candidate21FlowSweepStrategy"
        )
        values["config_path"] = (
            "candidate21_flow_sweep_strategy:Candidate21FlowSweepConfig"
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
        raise RuntimeError("flow-sweep runner did not remove sparse TradeTicks")
    if not filtered:
        raise RuntimeError("flow-sweep runner removed every data stream")
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
    manifest["candidate"] = "candidate-21-synchronized-flow-sweep"
    extra = dict(manifest.get("extra", {}))
    extra.update(
        {
            "strategy": "Candidate21FlowSweepStrategy",
            "alpha": (
                "full-hour native-10s acceptance aligned with causal "
                "one-hour and three-hour price discovery"
            ),
            "entry": (
                "GTC market parent consumes successive external 10-second "
                "bar volume until fully filled"
            ),
            "entry_cancel": (
                "remaining parent canceled when structural stop or timed "
                "exit closes the position"
            ),
            "trade_tick_execution": False,
            "bar_execution": True,
            "protection": (
                "full planned quantity reduce-only stop-market armed on "
                "the first partial fill"
            ),
            "exit": "four hours or before funding; no price target",
            "risk": "3% current continuous NAV planned loss",
        },
    )
    manifest["extra"] = extra
    write_json_atomic(path, manifest)


def run_backtest(**kwargs: Any) -> dict[str, Any]:
    previous_strategy = base.ImportableStrategyConfig
    previous_run = base.BacktestRunConfig
    previous_venue = base.BacktestVenueConfig
    base.ImportableStrategyConfig = _flow_sweep_strategy_config
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
            "candidate": "candidate-21-synchronized-flow-sweep",
            "entry_order": "MARKET_GTC",
            "entry_execution": (
                "SUCCESSIVE_COMPLETED_EXTERNAL_10S_BAR_VOLUME"
            ),
            "protective_order": "FULL_PLAN_REDUCE_ONLY_STOP_MARKET_GTC",
            "target_policy": None,
            "exit_policy": "FOUR_HOURS_OR_BEFORE_FUNDING",
            "bar_execution": True,
            "trade_execution": False,
            "sparse_tick_quantity_used_for_fills": False,
        },
    )
    return metrics


__all__ = ["run_backtest"]
