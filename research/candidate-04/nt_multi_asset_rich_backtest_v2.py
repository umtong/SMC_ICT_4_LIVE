#!/usr/bin/env python3
"""Run the four-instrument account with the exact trusted venue contract.

All data preparation, strategy configuration, global entry coordination,
Nautilus reports and evidence verification remain in
``nt_multi_asset_rich_backtest.py``.  This wrapper replaces only its run-config
factory so the venue object is instantiated from the exact
``nt_backtest.py`` AST by ``nt_trusted_execution_factory``.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from nautilus_trader.backtest.config import BacktestDataConfig
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.config import BacktestRunConfig
from nautilus_trader.config import LoggingConfig

import nt_multi_asset_rich_backtest as base
from nt_trusted_execution_factory import execution_contract_evidence
from nt_trusted_execution_factory import make_trusted_venue_config


_TRUSTED_CONFIG: dict[str, Any] | None = None


def _argument_value(name: str) -> str | None:
    for index, value in enumerate(sys.argv[:-1]):
        if value == name:
            return sys.argv[index + 1]
    prefix = name + "="
    for value in sys.argv[1:]:
        if value.startswith(prefix):
            return value[len(prefix) :]
    return None


def _load_config() -> dict[str, Any]:
    path = _argument_value("--config")
    if path is None:
        raise RuntimeError("--config is required")
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("execution config must be a JSON object")
    return value


def build_run_config(
    catalog_path: Path,
    strategies: list[Any],
    evaluation_start: Any,
    evaluation_end: Any,
    starting_nav: float,
    *_ignored: Any,
) -> BacktestRunConfig:
    del starting_nav, _ignored
    if _TRUSTED_CONFIG is None:
        raise RuntimeError("trusted execution config was not loaded")
    venue = make_trusted_venue_config(_TRUSTED_CONFIG)
    data = [
        BacktestDataConfig(
            **base.accepted_kwargs(
                BacktestDataConfig,
                {
                    "catalog_path": str(catalog_path),
                    "data_cls": "nautilus_trader.model.data:Bar",
                    "instrument_id": str(base.instrument_id(symbol)),
                    "bar_types": [str(base.bar_type(symbol))],
                    "start_time": str(evaluation_start),
                    "end_time": str(evaluation_end),
                },
            )
        )
        for symbol in base.SYMBOLS
    ]
    engine = BacktestEngineConfig(
        **base.accepted_kwargs(
            BacktestEngineConfig,
            {
                "strategies": strategies,
                "logging": LoggingConfig(log_level="ERROR"),
            },
        )
    )
    return BacktestRunConfig(
        **base.accepted_kwargs(
            BacktestRunConfig,
            {
                "engine": engine,
                "venues": [venue],
                "data": data,
            },
        )
    )


def _append_contract_evidence() -> None:
    output = _argument_value("--output")
    if output is None:
        return
    metrics_path = Path(output) / "metrics.json"
    if not metrics_path.exists():
        return
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["trusted_single_asset_execution_contract"] = (
        execution_contract_evidence()
    )
    metrics["venue_configuration_source"] = (
        "exact BacktestVenueConfig AST from nt_backtest.py"
    )
    metrics_path.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    global _TRUSTED_CONFIG
    _TRUSTED_CONFIG = _load_config()
    base.build_run_config = build_run_config
    base.main()
    _append_contract_evidence()


if __name__ == "__main__":
    main()
