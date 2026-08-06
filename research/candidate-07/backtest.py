"""NautilusTrader-only replay and evidence production for candidate-07."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from math import log
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel, MakerTakerFeeModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.data import BarType
try:
    from nautilus_trader.model.data import FundingRateUpdate
except ImportError:
    from nautilus_trader.model.data.funding import FundingRateUpdate
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency, Money
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import create_run_manifest, write_json_atomic

from data import load_bundle, write_bundle_summary
from strategy import Candidate07Strategy, Candidate07StrategyConfig


_MONEY_PATTERN = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _utc_ns(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1e9)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    return str(value)


def _money_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    match = _MONEY_PATTERN.search(str(value).replace(",", ""))
    return float(match.group(0)) if match else 0.0


def _max_drawdown(nav_points: Iterable[Mapping[str, Any]]) -> float:
    peak = 0.0
    drawdown = 0.0
    for item in nav_points:
        nav = float(item["nav"])
        peak = max(peak, nav)
        if peak > 0.0:
            drawdown = max(drawdown, 1.0 - nav / peak)
    return drawdown


def _one_slot_invariant(trades: list[Mapping[str, Any]]) -> bool:
    intervals = sorted(
        (int(item["opened_ns"]), int(item["closed_ns"]))
        for item in trades
        if item.get("opened_ns") is not None and item.get("closed_ns") is not None
    )
    return all(current[0] >= previous[1] for previous, current in zip(intervals, intervals[1:]))


def _metrics(
    *,
    config: Mapping[str, Any],
    stage: str,
    start: date,
    end: date,
    strategy: Candidate07Strategy,
    fills: pd.DataFrame,
    positions: pd.DataFrame,
    account: pd.DataFrame,
    funding_points: int,
) -> dict[str, Any]:
    start_ns = _utc_ns(start)
    end_ns = _utc_ns(end)
    nav_points = [dict(item) for item in strategy.nav_series if start_ns <= int(item["timestamp_ns"]) <= end_ns]
    initial_nav = float(config["initial_nav"])
    final_nav = float(nav_points[-1]["nav"]) if nav_points else initial_nav
    days = max(1.0, (end_ns - start_ns) / 86_400_000_000_000)
    daily_geometric_growth = (final_nav / initial_nav) ** (1.0 / days) - 1.0 if final_nav > 0.0 else -1.0
    daily_log_growth = log(final_nav / initial_nav) / days if final_nav > 0.0 else float("-inf")
    trades = [dict(item) for item in strategy.trade_diagnostics]
    wins = [item for item in trades if float(item["net_pnl"]) > 0.0]
    losses = [item for item in trades if float(item["net_pnl"]) <= 0.0]
    gross_profit = sum(float(item["net_pnl"]) for item in wins)
    gross_loss = abs(sum(float(item["net_pnl"]) for item in losses))
    close_dates = {
        datetime.fromtimestamp(int(item["closed_ns"]) / 1e9, tz=timezone.utc).date().isoformat()
        for item in trades if item.get("closed_ns") is not None
    }
    single_winner_share = max(float(item["net_pnl"]) for item in wins) / gross_profit if wins and gross_profit > 0.0 else 0.0
    by_scenario: dict[str, dict[str, Any]] = {}
    for kind in sorted({str(item["kind"]) for item in trades}):
        subset = [item for item in trades if item["kind"] == kind]
        by_scenario[kind] = {
            "trades": len(subset),
            "wins": sum(float(item["net_pnl"]) > 0.0 for item in subset),
            "net_pnl": sum(float(item["net_pnl"]) for item in subset),
            "mean_nav_return": sum(float(item["net_return_on_nav"]) for item in subset) / len(subset) if subset else 0.0,
        }
    reason_counts: dict[str, int] = defaultdict(int)
    state_counts: dict[str, int] = defaultdict(int)
    for event in strategy.research_events:
        reason_counts[event.reason_code] += 1
        state_counts[event.next_state] += 1

    commission_columns = [column for column in fills.columns if "commission" in str(column).lower()]
    total_commissions = sum(_money_number(value) for column in commission_columns for value in fills[column].tolist())
    gate = dict(config["weekly_gate"])
    checks = {
        "daily_growth": daily_geometric_growth >= float(gate["minimum_daily_geometric_growth"]),
        "trades": len(trades) >= int(gate["minimum_trades"]),
        "active_days": len(close_dates) >= int(gate["minimum_active_days"]),
        "drawdown": _max_drawdown(nav_points) <= float(gate["maximum_drawdown"]),
        "winner_concentration": single_winner_share <= float(gate["maximum_single_winner_share"]),
        "positive_nav": final_nav > 0.0,
        "single_slot": _one_slot_invariant(trades),
    }
    daily_nav: list[dict[str, Any]] = []
    if nav_points:
        grouped: dict[str, float] = {}
        for point in nav_points:
            key = datetime.fromtimestamp(int(point["timestamp_ns"]) / 1e9, tz=timezone.utc).date().isoformat()
            grouped[key] = float(point["nav"])
        previous = initial_nav
        cursor = start
        while cursor < end:
            key = cursor.isoformat()
            nav = grouped.get(key, previous)
            daily_nav.append({"date": key, "nav": nav, "return": nav / previous - 1.0})
            previous = nav
            cursor = cursor.fromordinal(cursor.toordinal() + 1)

    return {
        "candidate": "candidate-07",
        "stage": stage,
        "period": {"start": start.isoformat(), "end_exclusive": end.isoformat(), "days": days},
        "initial_nav": initial_nav,
        "final_nav": final_nav,
        "net_return": final_nav / initial_nav - 1.0,
        "daily_log_growth": daily_log_growth,
        "daily_geometric_growth": daily_geometric_growth,
        "target_daily_geometric_growth": 0.01,
        "target_met": daily_geometric_growth >= 0.01,
        "trades": len(trades),
        "trades_per_day": len(trades) / days,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else (float("inf") if gross_profit > 0.0 else 0.0),
        "mean_nav_return_per_trade": sum(float(item["net_return_on_nav"]) for item in trades) / len(trades) if trades else 0.0,
        "max_drawdown": _max_drawdown(nav_points),
        "active_days": len(close_dates),
        "single_winner_share": single_winner_share,
        "by_scenario": by_scenario,
        "scenario_reason_counts": dict(sorted(reason_counts.items())),
        "scenario_state_counts": dict(sorted(state_counts.items())),
        "engine_reports": {
            "fills": int(len(fills.index)),
            "positions": int(len(positions.index)),
            "account_rows": int(len(account.index)),
            "total_reported_commissions": total_commissions,
            "funding_points_replayed": funding_points,
        },
        "risk_contract": {
            "risk_fraction": float(config["risk_fraction"]),
            "sizing_nav_basis": "full current NautilusTrader portfolio equity",
            "unit_loss_components": [
                "entry-to-adverse-stop-fill distance", "entry taker fee", "stop taker fee",
                "one adverse tick on entry and stop", "configured adverse funding reserve",
            ],
            "arbitrary_notional_cap": False,
            "model_score_risk_multiplier": False,
            "single_slot_enforced": _one_slot_invariant(trades),
        },
        "execution_contract": {
            "engine": "NautilusTrader BacktestEngine",
            "orders": "market-parent bracket with take-profit limit and stop-market child",
            "entry_delay": "first 1-minute bar close after causal 5-minute confirmation",
            "prob_slippage": float(config["fill_model"]["prob_slippage"]),
            "prob_fill_on_limit": float(config["fill_model"]["prob_fill_on_limit"]),
            "funding": "historical Binance funding replayed through FundingRateUpdate",
        },
        "weekly_gate": {"checks": checks, "passed": all(checks.values()), "thresholds": gate},
        "daily_nav": daily_nav,
        "trades_detail": trades,
    }


def run_week(*, config_path: Path, stage: str, start: date, end: date, output: Path, cache_root: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    data_manifest_path = output / "data_manifest.json"
    bundle = load_bundle(
        symbol=str(config["symbol"]), trade_start=start, trade_end=end,
        warmup_days=int(config["warmup_days"]), cache_root=cache_root,
        manifest_destination=data_manifest_path,
    )
    write_bundle_summary(output / "data_summary.json", bundle)

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    bars = BarDataWrangler(bar_type, instrument).process(bundle.frame)
    funding_updates = [
        FundingRateUpdate(
            instrument_id=instrument.id, rate=point.rate, interval=point.interval_minutes,
            next_funding_ns=None, ts_event=point.ts_event_ns, ts_init=point.ts_event_ns,
        )
        for point in bundle.funding
    ]

    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR"), shutdown_on_error=True))
    venue = Venue("BINANCE")
    usdt = Currency.from_str("USDT")
    fill_model = FillModel(
        prob_fill_on_limit=float(config["fill_model"]["prob_fill_on_limit"]),
        prob_slippage=float(config["fill_model"]["prob_slippage"]),
        random_seed=int(config["fill_model"]["random_seed"]),
    )
    strategy = Candidate07Strategy(
        Candidate07StrategyConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            trade_start_ns=_utc_ns(start),
            trade_end_ns=_utc_ns(end),
            initial_nav=Decimal(str(config["initial_nav"])),
            risk_fraction=Decimal(str(config["risk_fraction"])),
            risk_funding_reserve_bps=Decimal(str(config["risk_funding_reserve_bps"])),
            max_hold_minutes=int(config["max_hold_minutes"]),
            logic_json=json.dumps(config["logic"], sort_keys=True),
        )
    )

    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(Decimal(str(config["initial_nav"])), usdt)],
            base_currency=usdt,
            default_leverage=Decimal(str(config["venue"]["default_leverage"])),
            fill_model=fill_model,
            fee_model=MakerTakerFeeModel(),
            bar_adaptive_high_low_ordering=bool(config["venue"]["bar_adaptive_high_low_ordering"]),
            use_position_ids=True,
            use_reduce_only=True,
        )
        engine.add_instrument(instrument)
        engine.add_data([*bars, *funding_updates])
        engine.add_strategy(strategy)
        engine.run()

        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(venue)
        fills.to_csv(output / "fills.csv", index=False)
        positions.to_csv(output / "positions.csv", index=False)
        account.to_csv(output / "account.csv", index=False)
        pd.DataFrame(strategy.nav_series).to_csv(output / "nav.csv", index=False)
        pd.DataFrame(strategy.trade_diagnostics).to_csv(output / "trades.csv", index=False)
        write_events(output / "events.jsonl", strategy.research_events)
        write_json_atomic(output / "scenario_diagnostics.json", {"observations": list(strategy.scenario_diagnostics)})
        metrics = _metrics(
            config=config, stage=stage, start=start, end=end, strategy=strategy,
            fills=fills, positions=positions, account=account, funding_points=len(funding_updates),
        )
        write_json_atomic(output / "metrics.json", _json_safe(metrics))
        run_id = f"candidate-07-{stage}-{start.isoformat()}"
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=run_id,
                candidate="candidate-07",
                config_path=config_path,
                data_manifest_path=data_manifest_path,
                extra={
                    "stage": stage, "start": start.isoformat(), "end_exclusive": end.isoformat(),
                    "instrument_id": str(instrument.id), "bar_type": str(bar_type),
                    "engine": "NautilusTrader BacktestEngine", "bars": len(bars),
                    "funding_updates": len(funding_updates),
                },
            ),
        )
        return metrics
    finally:
        engine.dispose()
