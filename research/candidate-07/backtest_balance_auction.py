"""NautilusTrader-only replay for the balance-to-initiative auction."""
from __future__ import annotations

import json
from pathlib import Path

import backtest_positioning as _base
from smc_ict_4.manifest import write_json_atomic
from strategy_balance_auction import Candidate07BalanceStrategy


_base.Candidate07PositioningStrategy = Candidate07BalanceStrategy


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
                "completed rotational balance, OI-backed initiative break, "
                "held acceptance or trapped-inventory unwind"
            ),
            "scenario_target_fixed_at_entry_ready": True,
            "standalone_oi_release_reversal": False,
        }
    )
    metrics["balance_auction_contract"] = {
        "balance_state": (
            "frozen completed five-minute range with width, rotational path, "
            "boundary touches and balance OI accumulation"
        ),
        "initiative_state": (
            "completed close outside the frozen balance, aligned aggressor "
            "flow and new open-interest build"
        ),
        "accepted_branch": "outside hold/redisplacement with no OI release",
        "failed_branch": (
            "return inside balance, opposite flow and OI release from the "
            "newly built breakout inventory"
        ),
        "one_attempt_per_balance": True,
        "generic_breakout_continuation": False,
        "standalone_liquidation_reversal": False,
        "parent_entry_order": "MARKET on the next completed one-minute bar",
        "target_recomputed_after_signal": False,
    }
    write_json_atomic(output / "metrics.json", _base.base._json_safe(metrics))

    run_path = output / "run.json"
    run_payload = json.loads(run_path.read_text(encoding="utf-8"))
    run_payload["signal_model"] = "balance-to-initiative inventory auction"
    run_payload["candidate_version"] = "candidate-07-balance-auction-v1"
    write_json_atomic(run_path, run_payload)
    return metrics


__all__ = ["run_week"]
