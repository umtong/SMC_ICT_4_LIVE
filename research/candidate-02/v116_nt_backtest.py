#!/usr/bin/env python3
"""NautilusTrader-only runner for candidate-02 v116."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import v53_nt_backtest as _base
from v116_session_acceptance_core import (
    SessionAcceptanceConfig,
    build_rotation_signals,
    build_state,
    get_last_scenario_diagnostics,
)

_original_run_first_week = _base.run_first_week

_base.RotationConfig = SessionAcceptanceConfig
_base.build_state = build_state
_base.build_rotation_signals = build_rotation_signals


def run_first_week(*, config_path: Path, input_root: Path, output: Path) -> dict[str, Any]:
    metrics = _original_run_first_week(
        config_path=config_path,
        input_root=input_root,
        output=output,
    )
    diagnostics = get_last_scenario_diagnostics()
    metrics["smc_ict_scenario_diagnostics"] = diagnostics["summary"]
    _base._write_json(output / "scenario_diagnostics.json", diagnostics)
    _base._write_json(output / "metrics.json", metrics)
    return metrics


_base.run_first_week = run_first_week

if __name__ == "__main__":
    raise SystemExit(_base.main())
