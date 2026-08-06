#!/usr/bin/env python3
"""Prepare and run one frozen BTC week through NautilusTrader BacktestNode."""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict
from decimal import Decimal
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

CANDIDATE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CANDIDATE_DIR.parents[1]
SRC = REPO_ROOT / "src"
sys.path[:0] = [str(REPO_ROOT), str(SRC), str(CANDIDATE_DIR)]

from nautilus_trader.backtest.config import MarginModelConfig
from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.backtest.node import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.node import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.model.data import FundingRateUpdate, QuoteTick
from nautilus_trader.model.identifiers import InstrumentId, Venue

from nt_lvcfr_data import CandidateConfig, NS_PER_DAY, NS_PER_MINUTE, date_to_ns, prepare_week
from smc_ict_4.manifest import create_run_manifest, write_json_atomic



def json_safe(value: Any) -> Any:
    """Convert Nautilus/msgspec/fixed-point result values to strict JSON."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if hasattr(value, "to_dict"):
        try:
            return json_safe(value.to_dict())
        except Exception:
            pass
    return str(value)


def max_drawdown(curve: list[dict[str, Any]], initial: float) -> float:
    peak = initial
    result = 0.0
    for row in curve:
        equity = float(row["equity"])
        peak = max(peak, equity)
        if peak > 0:
            result = max(result, 1.0 - equity / peak)
    return result


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key); keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, sort_keys=True, default=str) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def build_metrics(
    *,
    config: CandidateConfig,
    strategy_summary: dict[str, Any],
    final_equity: float,
    week_start: date,
    result: Any,
    account: Any,
    data_manifest: dict[str, Any],
) -> dict[str, Any]:
    episodes_all = list(strategy_summary.get("episodes", []))
    episodes = [episode for episode in episodes_all if episode.get("legs")]
    wins = [episode for episode in episodes if float(episode.get("native_account_pnl", 0.0)) > 0]
    losses = [episode for episode in episodes if float(episode.get("native_account_pnl", 0.0)) <= 0]
    episode_returns = [float(episode.get("return", 0.0)) for episode in episodes]
    evaluation_days = 7.0
    daily_growth = (final_equity / config.initial_nav) ** (1.0 / evaluation_days) - 1.0 if final_equity > 0 else -1.0
    curve = list(strategy_summary.get("equity_curve", []))
    mdd = max_drawdown(curve, config.initial_nav)
    legs = list(strategy_summary.get("legs", []))
    rejected = int(strategy_summary.get("counters", {}).get("entries_rejected", 0))
    incomplete = int(strategy_summary.get("counters", {}).get("incomplete_at_end", 0))
    return {
        "candidate": config.candidate,
        "engine": "NautilusTrader 1.230.0 BacktestNode",
        "week_start_utc": week_start.isoformat(),
        "week_end_utc": (week_start + timedelta(days=7)).isoformat(),
        "initial_nav": config.initial_nav,
        "final_nav": final_equity,
        "net_return": final_equity / config.initial_nav - 1.0,
        "evaluation_days": evaluation_days,
        "daily_geometric_growth": daily_growth,
        "daily_log_growth": math.log(final_equity / config.initial_nav) / evaluation_days if final_equity > 0 else float("-inf"),
        "target_daily_geometric_growth": config.minimum_daily_geometric_growth,
        "target_met": daily_growth >= config.minimum_daily_geometric_growth,
        "independent_episodes": len(episodes),
        "zero_fill_scenarios": len(episodes_all) - len(episodes),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(episodes) if episodes else 0.0,
        "mean_episode_return": mean(episode_returns) if episode_returns else 0.0,
        "mean_episode_pnl": mean([float(episode.get("native_account_pnl", 0.0)) for episode in episodes]) if episodes else 0.0,
        "max_drawdown": mdd,
        "native_orders": int(result.total_orders),
        "native_positions": int(result.total_positions),
        "native_events": int(result.total_events),
        "native_iterations": int(result.iterations),
        "native_elapsed_time_seconds": float(result.elapsed_time),
        "entry_rejections": rejected,
        "incomplete_at_end": incomplete,
        "single_slot_enforced": True,
        "risk_fraction": config.risk_fraction,
        "native_account": str(account),
        "native_result_summary": json_safe(dict(result.summary)),
        "native_stats_pnls": json_safe(result.stats_pnls),
        "native_stats_returns": json_safe(result.stats_returns),
        "strategy_counters": strategy_summary.get("counters", {}),
        "signals": data_manifest.get("signals", 0),
        "quote_ticks_retained": data_manifest.get("catalog", {}).get("quote_ticks_retained", 0),
        "funding_updates": data_manifest.get("catalog", {}).get("funding_updates", 0),
        "source_manifest": json_safe(data_manifest),
        "episodes_detail": episodes_all,
        "legs_detail": legs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=CANDIDATE_DIR / "nt_lvcfr_config.json")
    parser.add_argument("--prepared-root", type=Path)
    args = parser.parse_args()

    config = CandidateConfig.load(args.config)
    if args.week_start.isoformat() not in config.validation_weeks:
        parser.error(f"week is not frozen: {config.validation_weeks}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    prepared = (args.prepared_root or (output / "prepared")).resolve()
    prepared.mkdir(parents=True, exist_ok=True)

    data_manifest_path = prepared / "data_manifest.json"
    if data_manifest_path.exists():
        data_manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
    else:
        data_manifest = prepare_week(week_start=args.week_start, output_root=prepared, config=config)

    start_ns = date_to_ns(args.week_start)
    end_ns = date_to_ns(args.week_start + timedelta(days=7))
    run_end_ns = end_ns + 2 * NS_PER_MINUTE
    instrument_id = InstrumentId.from_str(config.instrument_id)
    strategy = ImportableStrategyConfig(
        strategy_path="nt_lvcfr_strategy:NTLvcfrStrategy",
        config_path="nt_lvcfr_strategy:NTLvcfrConfig",
        config={
            "instrument_id": instrument_id,
            "signals_path": str(prepared / "signals.json"),
            "output_dir": str(output),
            "evaluation_start_ns": start_ns,
            "evaluation_end_ns": end_ns,
            "risk_fraction": config.risk_fraction,
            "taker_fee_bps": config.taker_fee_bps,
            "slippage_impact_bps": config.slippage_impact_bps,
            "continuation_target_net_r": config.continuation_target_net_r,
            "continuation_protection_activate_r": config.continuation_protection_activate_r,
            "continuation_protection_lock_r": config.continuation_protection_lock_r,
            "continuation_trail_minutes": config.continuation_trail_minutes,
            "continuation_trail_buffer_atr": config.continuation_trail_buffer_atr,
            "continuation_max_holding_minutes": config.continuation_max_holding_minutes,
            "rapid_failure_minutes": config.rapid_failure_minutes,
            "reversal_entry_delay_minutes": config.reversal_entry_delay_minutes,
            "reversal_stop_buffer_atr": config.reversal_stop_buffer_atr,
            "reversal_target_net_r": config.reversal_target_net_r,
            "reversal_max_holding_minutes": config.reversal_max_holding_minutes,
        },
    )
    catalog_path = str(prepared / "catalog")
    run_config = BacktestRunConfig(
        engine=BacktestEngineConfig(strategies=[strategy], run_analysis=True),
        data=[
            BacktestDataConfig(
                catalog_path=catalog_path,
                data_cls=QuoteTick,
                instrument_id=instrument_id,
                start_time=start_ns,
                end_time=run_end_ns,
                optimize_file_loading=True,
            ),
            BacktestDataConfig(
                catalog_path=catalog_path,
                data_cls=FundingRateUpdate,
                instrument_id=instrument_id,
                start_time=start_ns - NS_PER_DAY,
                end_time=run_end_ns,
                optimize_file_loading=True,
            ),
        ],
        venues=[
            BacktestVenueConfig(
                name="BINANCE",
                oms_type="NETTING",
                account_type="MARGIN",
                base_currency="USDT",
                starting_balances=[f"{config.initial_nav:.2f} USDT"],
                default_leverage=1.0,
                margin_model=MarginModelConfig(model_type="standard"),
                book_type="L1_MBP",
                reject_stop_orders=False,
                use_position_ids=False,
                use_reduce_only=True,
                bar_execution=False,
                trade_execution=False,
                liquidity_consumption=True,
                price_protection_points=0,
                liquidation_enabled=True,
                liquidation_trigger_ratio=1.0,
                liquidation_cancel_open_orders=True,
            )
        ],
        # NT 1.230 streaming uses a Rust-only DataBackendSession which
        # misclassifies legacy FundingRateUpdate as custom data. One-shot
        # BacktestNode instead dispatches QuoteTick to the Rust query and
        # FundingRateUpdate to its registered PyArrow codec, sorts the mixed
        # stream, and still executes the same native BacktestEngine.
        chunk_size=None,
        start=start_ns - NS_PER_DAY,
        end=run_end_ns,
        raise_exception=True,
        dispose_on_completion=False,
    )

    node = BacktestNode(configs=[run_config])
    results = node.run()
    if len(results) != 1:
        raise RuntimeError(f"expected one result, got {len(results)}")
    result = results[0]
    engine = node.get_engine(run_config.id)
    if engine is None:
        raise RuntimeError("BacktestNode did not retain its engine")
    portfolio = engine.kernel.portfolio
    account = portfolio.account(Venue("BINANCE"))
    if account is None:
        raise RuntimeError("native margin account missing")
    equity = portfolio.equity(Venue("BINANCE"))
    if equity is None:
        equity = account.balance_total(account.base_currency)
    final_equity = float(equity)
    summary_path = output / "strategy_summary.json"
    if not summary_path.exists():
        raise RuntimeError("strategy did not write its native summary")
    strategy_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = build_metrics(
        config=config,
        strategy_summary=strategy_summary,
        final_equity=final_equity,
        week_start=args.week_start,
        result=result,
        account=account,
        data_manifest=data_manifest,
    )
    episodes = metrics.pop("episodes_detail")
    legs = metrics.pop("legs_detail")
    write_rows(output / "episodes.csv", episodes)
    write_rows(output / "positions.csv", legs)
    write_json_atomic(output / "metrics.json", json_safe(metrics))
    run_manifest = create_run_manifest(
        run_id=f"nt-lvcfr-{args.week_start.isoformat()}",
        candidate=config.candidate,
        config_path=args.config,
        data_manifest_path=data_manifest_path,
        extra={
            "engine": "NautilusTrader BacktestNode",
            "nautilus_version": "1.230.0",
            "week_start": args.week_start.isoformat(),
            "week_end": (args.week_start + timedelta(days=7)).isoformat(),
            "single_slot": True,
            "risk_fraction": config.risk_fraction,
            "native_total_orders": result.total_orders,
            "native_total_positions": result.total_positions,
        },
    )
    write_json_atomic(output / "run.json", json_safe(run_manifest))
    printable = {key: metrics[key] for key in (
        "week_start_utc", "signals", "independent_episodes", "win_rate", "mean_episode_pnl",
        "final_nav", "daily_geometric_growth", "max_drawdown", "native_orders", "native_positions",
        "entry_rejections", "incomplete_at_end", "target_met",
    )}
    print(json.dumps(printable, indent=2, sort_keys=True, default=str))
    node.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
