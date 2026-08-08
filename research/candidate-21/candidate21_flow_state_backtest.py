"""Bar-volume runner for Candidate 21 state-resolved flow sweep."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import candidate21_flow_sweep_backtest as base
from smc_ict_4.manifest import write_json_atomic


_ORIGINAL_FLOW_SWEEP_STRATEGY_CONFIG = base._flow_sweep_strategy_config


def _flow_state_strategy_config(*args: Any, **kwargs: Any) -> Any:
    values = dict(kwargs)
    if values.get("strategy_path") == (
        "candidate21_event_strategy:Candidate21EventStrategy"
    ):
        values["strategy_path"] = (
            "candidate21_flow_state_strategy:Candidate21FlowStateStrategy"
        )
        values["config_path"] = (
            "candidate21_flow_state_strategy:Candidate21FlowStateConfig"
        )
    return base._ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(*args, **values)


def _rewrite_manifest(output: Path) -> None:
    path = output.resolve() / "run.json"
    if not path.exists():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["candidate"] = "candidate-21-state-resolved-flow-sweep"
    extra = dict(manifest.get("extra", {}))
    extra.update(
        {
            "strategy": "Candidate21FlowStateStrategy",
            "state_transition": (
                "persistent normalized aggressor sponsorship OR strictly "
                "higher response price efficiency and directional return"
            ),
            "no_trade": (
                "parent flow decayed and response did not release into more "
                "efficient price discovery"
            ),
            "state_thresholds": "NONE; relational comparisons only",
            "entry": (
                "full risk-sized GTC market parent across successive external "
                "10-second bar volume"
            ),
        },
    )
    manifest["extra"] = extra
    write_json_atomic(path, manifest)


def run_backtest(**kwargs: Any) -> dict[str, Any]:
    previous = base._flow_sweep_strategy_config
    base._flow_sweep_strategy_config = _flow_state_strategy_config
    try:
        metrics = base.run_backtest(**kwargs)
    finally:
        base._flow_sweep_strategy_config = previous

    _rewrite_manifest(Path(kwargs["output"]))
    metrics.update(
        {
            "candidate": "candidate-21-state-resolved-flow-sweep",
            "state_transition": (
                "PERSISTENT_SPONSORSHIP_OR_DELAYED_PRICE_DISCOVERY"
            ),
            "state_numeric_thresholds": None,
            "entry_order": "MARKET_GTC",
            "entry_execution": (
                "SUCCESSIVE_COMPLETED_EXTERNAL_10S_BAR_VOLUME"
            ),
            "protective_order": "FULL_PLAN_REDUCE_ONLY_STOP_MARKET_GTC",
            "target_policy": None,
            "exit_policy": "FOUR_HOURS_OR_BEFORE_FUNDING",
        },
    )
    return metrics


__all__ = ["run_backtest"]
