"""Select Candidate 21 response strategy and a latency-aligned trade clock."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import candidate21_backtest as base
from latency_aligned_tick_clock import EXECUTION_OFFSET_NS
from latency_aligned_tick_clock import MODELED_ORDER_LATENCY_NS
from latency_aligned_tick_clock import append_latency_aligned_execution_ticks
from smc_ict_4.manifest import write_json_atomic


_ORIGINAL_IMPORTABLE_STRATEGY_CONFIG = base.ImportableStrategyConfig


def _response_strategy_config(*args: Any, **kwargs: Any) -> Any:
    values = dict(kwargs)
    if values.get("strategy_path") == "candidate21_strategy:Candidate21Strategy":
        values["strategy_path"] = "candidate21_response_strategy:Candidate21ResponseStrategy"
        values["config_path"] = "candidate21_response_strategy:Candidate21ResponseConfig"
    return _ORIGINAL_IMPORTABLE_STRATEGY_CONFIG(*args, **values)


def _rewrite_manifest(output: Path) -> None:
    path = output.resolve() / "run.json"
    if not path.exists():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    extra = dict(manifest.get("extra", {}))
    extra.update(
        {
            "strategy": "Candidate21ResponseStrategy",
            "router": "first-10-second shock followed by non-overlapping 10-60-second response",
            "execution_clock": "first actual aggTrade at or after 300 ms into each minute",
            "modeled_order_latency_ns": MODELED_ORDER_LATENCY_NS,
            "execution_selection_offset_ns": EXECUTION_OFFSET_NS,
        },
    )
    manifest["candidate"] = "candidate-21-same-minute-response-router"
    manifest["extra"] = extra
    write_json_atomic(path, manifest)


def run_backtest(**kwargs: Any) -> dict[str, Any]:
    previous_strategy = base.ImportableStrategyConfig
    previous_clock = base._append_execution_ticks
    base.ImportableStrategyConfig = _response_strategy_config
    base._append_execution_ticks = append_latency_aligned_execution_ticks
    try:
        metrics = base.run_backtest(**kwargs)
    finally:
        base.ImportableStrategyConfig = previous_strategy
        base._append_execution_ticks = previous_clock

    _rewrite_manifest(Path(kwargs["output"]))
    metrics.update(
        {
            "candidate": "candidate-21-same-minute-response-router",
            "new_alpha": "first-10-second shock separated from strictly later 10-60-second response",
            "execution_clock": "LATENCY_ALIGNED_ACTUAL_AGGTRADE_300MS",
            "modeled_order_latency_ns": MODELED_ORDER_LATENCY_NS,
            "execution_selection_offset_ns": EXECUTION_OFFSET_NS,
        },
    )
    return metrics


__all__ = ["run_backtest"]
