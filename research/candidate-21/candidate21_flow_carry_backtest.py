"""Nautilus runner adapter for Candidate 21 synchronized flow carry."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import candidate21_event_backtest as base
from smc_ict_4.manifest import write_json_atomic


_ORIGINAL_IMPORTABLE_STRATEGY_CONFIG = base.ImportableStrategyConfig


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
            "entry": "Nautilus market order after completed response",
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
    previous = base.ImportableStrategyConfig
    base.ImportableStrategyConfig = _flow_carry_strategy_config
    try:
        metrics = base.run_backtest(**kwargs)
    finally:
        base.ImportableStrategyConfig = previous

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
            "protective_order": "REDUCE_ONLY_STOP_MARKET_GTC",
        },
    )
    return metrics


__all__ = ["run_backtest"]
