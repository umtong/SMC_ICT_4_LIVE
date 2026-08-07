#!/usr/bin/env python3
"""Run the four-instrument account with the exact trusted venue contract.

All data preparation, strategy configuration, global entry coordination,
Nautilus reports and evidence verification remain in
``nt_multi_asset_rich_backtest.py``. This wrapper replaces only its run-config
factory so the venue object is instantiated from the exact ``nt_backtest.py``
AST by ``nt_trusted_execution_factory``.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

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


def calendar_window_iso(
    start_value: Any,
    end_value: Any,
) -> tuple[str, str]:
    """Return inclusive UTC boundaries for two declared calendar dates."""

    start = pd.Timestamp(start_value, tz="UTC")
    end = (
        pd.Timestamp(end_value + timedelta(days=1), tz="UTC")
        - pd.Timedelta(nanoseconds=1)
    )
    if end < start:
        raise RuntimeError("calendar window end precedes start")
    return start.isoformat(), end.isoformat()


def evaluation_window_iso(
    evaluation_start: Any,
    evaluation_end: Any,
) -> tuple[str, str]:
    return calendar_window_iso(evaluation_start, evaluation_end)


def build_window_iso() -> tuple[str, str]:
    start_text = _argument_value("--build-start")
    end_text = _argument_value("--build-end")
    if start_text is None or end_text is None:
        raise RuntimeError("--build-start and --build-end are required")
    return calendar_window_iso(
        date.fromisoformat(start_text),
        date.fromisoformat(end_text),
    )


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
    # Validate the declared evaluation dates, while streaming the full build
    # interval so precompiled signals retain the same completed-history warmup
    # as their compiler. Strategy evaluation bounds still prevent warmup trades.
    evaluation_window_iso(evaluation_start, evaluation_end)
    start_time, end_time = build_window_iso()
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
                    "start_time": start_time,
                    "end_time": end_time,
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
                "run_analysis": True,
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
                "raise_exception": True,
                "dispose_on_completion": False,
                "start": start_time,
                "end": end_time,
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
