#!/usr/bin/env python3
"""V50 wrapper around the frozen gated single-symbol experiment harness."""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any


_HERE = Path(__file__).resolve().parent
_BASE_PATH = _HERE.parent / "v49_quarter_hour_direct" / "run_experiment.py"
_spec = importlib.util.spec_from_file_location("candidate09_v49_harness", _BASE_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot load frozen experiment harness: {_BASE_PATH}")
_base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _base
_spec.loader.exec_module(_base)
_BASE_COMPACT = _base.compact

DEVELOPMENT = {
    "build_start": "2024-09-24", "build_end": "2024-10-31",
    "evaluation_start": "2024-10-01", "evaluation_end": "2024-10-31",
}
HOLDOUT = {
    "build_start": "2025-09-24", "build_end": "2025-10-31",
    "evaluation_start": "2025-10-01", "evaluation_end": "2025-10-31",
}
LONG = {
    "build_start": "2024-10-25", "build_end": "2025-09-30",
    "evaluation_start": "2024-11-01", "evaluation_end": "2025-09-30",
}
VARIANTS = {
    "forced-deleveraging-exhaustion": True,
    "price-flow-exhaustion-control": False,
}


def configured(
    base: dict[str, Any], *, clock_offset: bool, calendar_days: int,
) -> dict[str, Any]:
    cfg = json.loads(json.dumps(base))
    minimum_trades = max(14, math.ceil(0.5 * calendar_days))
    cfg["execution_seed"] = 500050
    cfg["gate"] = {
        "min_geometric_daily_growth": 0.01,
        "min_trades": minimum_trades,
        "min_wins": math.ceil(0.40 * minimum_trades),
        "min_win_rate": 0.40,
        "min_active_days": max(8, math.ceil(0.25 * calendar_days)),
        "max_drawdown": 0.30,
        "max_largest_winner_share": 0.35,
    }
    cfg["strategy"].update({
        "candidate33_require_stacked_imbalance": False,
        "candidate33_min_stacked_levels": 3,
        "candidate33_stack_boundary_tolerance_atr": 0.25,
        "candidate33_trade_failed_auction": False,
        "candidate35_include_confirmed_swings": False,
        "candidate35_enable_15m": True,
        "candidate35_enable_60m": True,
        "candidate35_enable_daily": True,
        "candidate50_require_forced_deleveraging": bool(clock_offset),
        "candidate50_event_minutes": 5,
        "candidate50_publication_delay_minutes": 5,
    })
    return cfg


def compact(metrics: dict[str, Any]) -> dict[str, Any]:
    value = _BASE_COMPACT(metrics)
    diagnostics = metrics.get("strategy_diagnostics", {})
    value["diagnostics"] = {
        key: item for key, item in diagnostics.items()
        if key.startswith("candidate50_")
        or key.startswith("candidate35_")
        or key in {
            "entry_submissions", "order_rejections",
            "max_simultaneous_entry_intents", "max_open_positions_observed",
        }
    }
    return value


def _rewrite_decision(output: Path) -> None:
    path = output / "FINAL_DECISION.json"
    decision = json.loads(path.read_text(encoding="utf-8"))
    decision["candidate"] = "candidate-09-v50-forced-deleveraging-exhaustion-reversal"
    decision["source_lineage"] = {
        "cause": "five-minute price shock with falling OI and directional premium expansion",
        "exhaustion": "same-direction flow fails to extend price during the full metrics delay",
        "transition": "later opposite structure break with flow, efficiency, delta and POC migration",
        "execution": "completed opposite-initiative close",
        "invalidation": "full shock-plus-delay extreme",
        "target": "pre-shock balance boundary from the same auction leg",
        "exact_control": "identical market path with only OI/premium classification disabled",
    }
    _base.write_json(path, decision)


def main() -> int:
    _base.DEVELOPMENT = DEVELOPMENT
    _base.HOLDOUT = HOLDOUT
    _base.LONG = LONG
    _base.VARIANTS = VARIANTS
    _base.configured = configured
    _base.compact = compact
    code = _base.main()
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()
    _rewrite_decision(args.output.resolve())
    return code


if __name__ == "__main__":
    raise SystemExit(main())
