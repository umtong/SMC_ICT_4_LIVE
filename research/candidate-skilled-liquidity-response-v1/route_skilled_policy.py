#!/usr/bin/env python3
"""Route skilled-liquidity-response plans through one market-wide account."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import route_directional_policy as base


def _reason_counts(episodes: pd.DataFrame) -> dict[str, int]:
    if episodes.empty or "order_exists" not in episodes:
        return {}
    rejected = episodes[~base._bool_series(episodes.order_exists)].copy()
    if rejected.empty or "no_trade_reason" not in rejected:
        return {}
    return {
        str(reason): int(count)
        for reason, count in rejected.no_trade_reason.astype(str).value_counts().items()
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    episodes, period_days, source_summaries = base.load_universe(args.root)
    if episodes.empty:
        raise RuntimeError(f"No episode artifacts found under {args.root}")
    orders = episodes[base._bool_series(episodes["order_exists"])].copy()
    selected, closed, skipped, account = base.route_account(orders)
    audit = base.no_trade_audit(episodes)

    calendar_days = int(sum(period_days.values()))
    account["diagnostic_calendar_days"] = calendar_days
    account["closed_trades_per_diagnostic_day"] = (
        float(len(closed) / calendar_days) if calendar_days else 0.0
    )
    account["by_period"] = base._group_metrics(closed, "period")
    account["by_role"] = base._group_metrics(closed, "role")
    account["by_family"] = base._group_metrics(closed, "family")
    account["by_symbol"] = base._group_metrics(closed, "symbol")
    account["by_response_kind"] = base._group_metrics(
        closed, "auction_response_kind"
    )
    account["by_entry_geometry"] = base._group_metrics(closed, "entry_geometry")

    summary = {
        "policy": (
            "public liquidity boundary -> causal price-volume shock/response -> "
            "settled failed, accepted or initiative auction -> one family-specific "
            "first-return OB/FVG/source price -> event invalidation -> nearest live "
            "opposing liquidity target -> market-event de-duplication -> one "
            "continuous three-percent-risk account"
        ),
        "episode_rows": int(len(episodes)),
        "order_rows": int(len(orders)),
        "no_trade_rows": int((~base._bool_series(episodes.order_exists)).sum()),
        "no_trade_reason_counts": _reason_counts(episodes),
        "account": account,
        "period_days": period_days,
        "source_summaries": source_summaries,
        "decision_primitive": "causal_liquidity_boundary_impulse_response",
        "one_plan_per_symbol_episode": True,
        "one_trade_per_market_event": True,
        "same_minute_arbitration_is_causal": True,
        "fitted_admission_model": False,
        "symbol_identity_feature": False,
        "outcome_fields_used_for_decision": False,
        "future_diagnostics_used_for_decision": False,
        "fixed_rr_target_lattice": False,
        "target_selected_before_rr": True,
        "filled_position_exit_contract": "TAKE_PROFIT_OR_STOP_LOSS_ONLY",
        "single_global_account_slot": True,
        "diagnostic_windows_are_not_a_long_continuous_backtest": True,
    }

    episodes.to_csv(
        args.output / "all_episodes.csv.gz", index=False, compression="gzip"
    )
    orders.to_csv(
        args.output / "all_plans.csv.gz", index=False, compression="gzip"
    )
    selected.to_csv(args.output / "selected_orders.csv", index=False)
    closed.to_csv(args.output / "closed_trades.csv", index=False)
    skipped.to_csv(
        args.output / "market_event_or_account_conflicts.csv", index=False
    )
    audit.to_csv(args.output / "no_trade_opportunity_audit.csv", index=False)
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
