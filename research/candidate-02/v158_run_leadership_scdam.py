#!/usr/bin/env python3
"""Candidate-02 V158 Nautilus runner built on exact Candidate-13 v4 source."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any

import market_leadership as _market_leadership
from runner_materializer_v4 import materialize_runner_source
from semantic_logic import install as _install_semantic_logic
from semantic_post_gate import amend_after_leadership
from v158_oi_router import (
    OIGatedSemanticMarketLeadershipGate,
    ROUTER,
    write_summary,
)

_market_leadership.MarketLeadershipGate = OIGatedSemanticMarketLeadershipGate
_install_semantic_logic()

_BASE = Path(__file__).resolve().with_name("run_leadership_scdam_base.py")
_SOURCE = materialize_runner_source(_BASE.read_text(encoding="utf-8"))
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
_CANDIDATE13_V4_RUN = run  # type: ignore[name-defined]
_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def run(config_path: Path, week_id: str, output_dir: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    selected = config["selection"]["weeks"][week_id]
    evaluation_start = date.fromisoformat(str(selected["start"]))
    evaluation_end = date.fromisoformat(str(selected["end_exclusive"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    ROUTER.prepare(
        symbols=_SYMBOLS,
        evaluation_start=evaluation_start,
        evaluation_end_exclusive=evaluation_end,
        cache=output_dir / "oi_data",
    )
    metrics = _CANDIDATE13_V4_RUN(config_path, week_id, output_dir)
    write_summary(output_dir / "oi_router.json")
    router_summary = ROUTER.summary()
    metrics = dict(metrics)
    metrics.update({
        "candidate": "candidate-02-v158-candidate13-v4-oi-reset-router",
        "oi_router": {
            "threshold": router_summary["threshold"],
            "decision_counts": router_summary["decision_counts"],
            "future_information_used": False,
            "missing_archives_synthetically_filled": False,
        },
        "candidate02_v158": {
            "aac_unchanged": True,
            "far_requires_post_sweep_visible_metric": True,
            "far_maximum_oi_change_15m": 0.001,
            "risk_fraction_changed": False,
            "execution_changed": False,
        },
    })
    _write_json(output_dir / "metrics.json", metrics)
    return metrics
