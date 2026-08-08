"""Nautilus replay composition for protected one-minute MSS/retest."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import backtest as _base
from smc_ict_4.manifest import write_json_atomic
from strategy_progress import ThreeRProgressProtectionMixin
from strategy_structural_mss import Candidate07Strategy as _StructuralStrategy


class Candidate07Strategy(
    ThreeRProgressProtectionMixin,
    _StructuralStrategy,
):
    """Protected-swing state transition plus unchanged execution management."""


_original_run_week = _base.run_week
_base.Candidate07Strategy = Candidate07Strategy


def run_week(
    *,
    config_path: Path,
    stage: str,
    start: date,
    end: date,
    output: Path,
    cache_root: Path,
) -> dict[str, Any]:
    metrics = _original_run_week(
        config_path=config_path,
        stage=stage,
        start=start,
        end=end,
        output=output,
        cache_root=cache_root,
    )
    metrics["execution_contract"].update(
        {
            "entry_delay": (
                "first completed one-minute bar after causal same-boundary "
                "one-minute retest confirmation"
            ),
            "source_clock": "completed 5-minute liquidity sweep/reclaim",
            "state_clock": "independent protected 1-minute swing break",
            "entry_clock": "first completed 1-minute retest of that same swing",
        }
    )
    metrics["structural_contract"] = {
        "source_event": "unchanged causal 5M external sweep/reclaim",
        "source_and_boundary_independent": True,
        "boundary_confirmed_before_source_bar_began": True,
        "mss": (
            "ranked directional 1M displacement close through protected swing"
        ),
        "retest": "first subsequent 1M touch must defend the same boundary",
        "source_objective_preconsumption_rejected": True,
        "source_extreme_invalidation_unchanged": True,
        "opposing_internal_external_target_unchanged": True,
        "parameter_fit_to_evaluation_outcomes": False,
    }
    write_json_atomic(output / "metrics.json", _base._json_safe(metrics))
    return metrics


__all__ = ["run_week"]
