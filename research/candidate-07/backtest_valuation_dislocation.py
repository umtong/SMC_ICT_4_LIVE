"""NautilusTrader-only replay for valuation-dislocation reversion."""
from __future__ import annotations

import json
from pathlib import Path

import backtest_positioning as _base
from smc_ict_4.manifest import write_json_atomic
from strategy_valuation_dislocation import Candidate07ValuationStrategy


_base.Candidate07PositioningStrategy = Candidate07ValuationStrategy


def run_week(
    *,
    config_path: Path,
    stage: str,
    start,
    end,
    output: Path,
    cache_root: Path,
) -> dict:
    metrics = _base.run_week(
        config_path=config_path,
        stage=stage,
        start=start,
        end=end,
        output=output,
        cache_root=cache_root,
    )
    metrics["execution_contract"].update(
        {
            "signal_model": (
                "tail trade-price deviation from exchange-reported OI valuation "
                "anchor, followed by measured contraction and counterflow"
            ),
            "direction_source": "sign of price minus valuation anchor",
            "scenario_target_fixed_at_entry_ready": True,
            "standalone_oi_direction_rule": False,
        }
    )
    metrics["valuation_dislocation_contract"] = {
        "valuation_anchor": (
            "Binance USD-M sum_open_interest_value divided by sum_open_interest"
        ),
        "dislocation": (
            "past-distribution tail absolute deviation with same-direction "
            "aggressor flow and non-neutral OI impulse"
        ),
        "confirmation": (
            "deviation contraction plus opposite aggressor flow and price reversal"
        ),
        "target": "current valuation anchor adjusted by past median signed deviation",
        "direction_from_oi_sign": False,
        "normalization_required_before_new_episode": True,
        "parent_entry_order": "MARKET on the next completed one-minute bar",
        "target_recomputed_after_signal": False,
    }
    write_json_atomic(output / "metrics.json", _base.base._json_safe(metrics))

    run_path = output / "run.json"
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    run_payload["signal_model"] = "valuation-dislocation contraction reversion"
    run_payload["candidate_version"] = "candidate-07-valuation-dislocation-v1"
    write_json_atomic(run_path, run_payload)
    return metrics


__all__ = ["run_week"]
