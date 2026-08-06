"""NautilusTrader-only backtest runner for candidate-09."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
import json
from math import exp, log
from pathlib import Path
from collections import Counter
from typing import Any, Mapping

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import create_run_manifest, write_json_atomic

from strategy import LRAEStrategy, LRAEStrategyConfig


def _price(value: str | float) -> Price:
    return Price.from_str(f"{float(value):.1f}")


def _quantity(value: str | float) -> Quantity:
    rounded = max(0.001, round(float(value), 3))
    return Quantity.from_str(f"{rounded:.3f}")


def build_instrument(config: Mapping[str, Any]) -> CryptoPerpetual:
    """Build the Binance linear perpetual with conservative all-in effective costs.

    Both maker and taker rates are set to the same effective rate because the
    bar-only dataset cannot identify queue position or spread.  The configured
    6.5 bps/side includes the stated exchange fee assumption plus expected
    spread, slippage and market impact.  This deliberately avoids receiving a
    maker rebate in a bar simulation.
    """

    venue = Venue("BINANCE")
    usdt = Currency.from_str("USDT")
    btc = Currency.from_str("BTC")
    fee = Decimal(str(config["effective_fee_rate_per_side"]))
    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol("BTCUSDT-PERP"), venue),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=btc,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=1,
        size_precision=3,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
        max_quantity=Quantity.from_str("1000.000"),
        min_quantity=Quantity.from_str("0.001"),
        min_notional=Money(10.0, usdt),
        max_price=Price.from_str("1000000.0"),
        min_price=Price.from_str("10.0"),
        margin_init=Decimal("0.05"),
        margin_maint=Decimal("0.025"),
        maker_fee=fee,
        taker_fee=fee,
    )


def load_bars(csv_path: str | Path, instrument: CryptoPerpetual) -> tuple[BarType, list[Bar]]:
    path = Path(csv_path)
    bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
    bars: list[Bar] = []
    last_ts = -1
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            ts_ns = int(row["close_time_ns"])
            if ts_ns <= last_ts:
                raise ValueError(f"bar timestamps must strictly increase: {ts_ns} <= {last_ts}")
            last_ts = ts_ns
            bars.append(
                Bar(
                    bar_type=bar_type,
                    open=_price(row["open"]),
                    high=_price(row["high"]),
                    low=_price(row["low"]),
                    close=_price(row["close"]),
                    volume=_quantity(row["volume"]),
                    ts_event=ts_ns,
                    ts_init=ts_ns,
                )
            )
    if not bars:
        raise ValueError(f"no bars loaded from {path}")
    return bar_type, bars


def _write_csv(path: Path, rows: list[Mapping[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})


def _max_drawdown(curve: list[Mapping[str, Any]]) -> float:
    peak = 0.0
    max_dd = 0.0
    for sample in curve:
        equity = float(sample["equity"])
        peak = max(peak, equity)
        if peak > 0.0:
            max_dd = max(max_dd, 1.0 - equity / peak)
    return max_dd


def _trade_metrics(trades: list[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [float(item["realized_pnl"]) for item in trades]
    wins = [value for value in pnls if value > 0.0]
    losses = [value for value in pnls if value < 0.0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    scenario: dict[str, dict[str, Any]] = {}
    for trade in trades:
        key = str(trade["scenario_type"])
        bucket = scenario.setdefault(key, {"trades": 0, "wins": 0, "pnl": 0.0})
        bucket["trades"] += 1
        bucket["wins"] += int(float(trade["realized_pnl"]) > 0.0)
        bucket["pnl"] += float(trade["realized_pnl"])
    for bucket in scenario.values():
        bucket["win_rate"] = bucket["wins"] / bucket["trades"] if bucket["trades"] else 0.0

    positive_concentration = 0.0
    if gross_profit > 0.0:
        positive_concentration = sum(sorted(wins, reverse=True)[:3]) / gross_profit

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else (float("inf") if gross_profit > 0 else 0.0),
        "average_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
        "median_pnl": sorted(pnls)[len(pnls) // 2] if pnls else 0.0,
        "top3_positive_pnl_concentration": positive_concentration,
        "scenario_attribution": scenario,
    }


def _daily_metrics(
    daily_equity: Mapping[str, float],
    *,
    initial_nav: float,
    start_date: str,
    calendar_days: int,
) -> dict[str, Any]:
    ordered = sorted((date, float(value)) for date, value in daily_equity.items())
    previous = initial_nav
    returns: list[dict[str, Any]] = []
    for date, equity in ordered:
        day_return = equity / previous - 1.0 if previous > 0.0 else -1.0
        returns.append({"date": date, "equity": equity, "return": day_return})
        previous = equity

    # The week is evaluated over all seven calendar days, including flat/no-trade days.
    final_nav = previous
    geometric = (final_nav / initial_nav) ** (1.0 / calendar_days) - 1.0 if final_nav > 0.0 else -1.0
    return {
        "start_date": start_date,
        "calendar_days": calendar_days,
        "samples": returns,
        "positive_days": sum(int(item["return"] > 0.0) for item in returns),
        "negative_days": sum(int(item["return"] < 0.0) for item in returns),
        "flat_days": max(0, calendar_days - sum(int(abs(item["return"]) > 1e-12) for item in returns)),
        "geometric_daily_growth": geometric,
    }


def run_backtest(
    *,
    research_config_path: str | Path,
    feature_path: str | Path,
    output_dir: str | Path,
    variant: str,
    data_manifest_path: str | Path,
    week_name: str,
    start_date: str,
) -> dict[str, Any]:
    config_path = Path(research_config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    instrument = build_instrument(config)
    bar_type, bars = load_bars(feature_path, instrument)
    venue = Venue("BINANCE")
    usdt = Currency.from_str("USDT")
    initial_nav = float(config["initial_nav"])
    run_id = f"candidate09-{variant}-{week_name}"

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level=str(config.get("nautilus_log_level", "ERROR"))),
        )
    )
    strategy = LRAEStrategy(
        LRAEStrategyConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            feature_path=str(Path(feature_path).resolve()),
            research_config_path=str(config_path.resolve()),
            variant=variant,
            final_ts_ns=int(bars[-1].ts_init),
        )
    )

    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(initial_nav, usdt)],
            base_currency=usdt,
            default_leverage=Decimal(str(config["default_leverage"])),
            book_type=BookType.L1_MBP,
            bar_execution=True,
            bar_adaptive_high_low_ordering=True,
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()

        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(venue)
        fills.to_csv(destination / "fills.csv", index=False)
        positions.to_csv(destination / "positions.csv", index=False)
        account.to_csv(destination / "account.csv", index=False)

        evidence = strategy.evidence()
        curve = list(evidence.pop("equity_curve"))
        trades = list(evidence["closed_trades"])
        final_nav = float(evidence["final_equity"])
        total_return = final_nav / initial_nav - 1.0
        daily = _daily_metrics(
            evidence["daily_equity"],
            initial_nav=initial_nav,
            start_date=start_date,
            calendar_days=int(config["week_calendar_days"]),
        )
        metrics = {
            "run_id": run_id,
            "week": week_name,
            "variant": variant,
            "bar_type": str(bar_type),
            "bars": len(bars),
            "first_ts_ns": int(bars[0].ts_init),
            "last_ts_ns": int(bars[-1].ts_init),
            "initial_nav": initial_nav,
            "final_nav": final_nav,
            "total_return": total_return,
            "geometric_daily_growth": daily["geometric_daily_growth"],
            "max_drawdown": _max_drawdown(curve),
            "trade_metrics": _trade_metrics(trades),
            "daily": daily,
            "strategy": evidence,
            "event_reason_counts": dict(Counter(event.reason_code for event in strategy.events)),
            "event_state_counts": dict(Counter(event.next_state for event in strategy.events)),
            "flat_at_end": bool(engine.portfolio.is_flat(instrument.id)),
            "cost_model": {
                "effective_fee_rate_per_side": float(config["effective_fee_rate_per_side"]),
                "round_trip_rate_at_equal_prices": 2.0 * float(config["effective_fee_rate_per_side"]),
                "description": "commission plus spread/slippage/impact folded into both maker and taker fees",
            },
            "execution_model": {
                "engine": "NautilusTrader BacktestEngine",
                "bar_execution": True,
                "bar_adaptive_high_low_ordering": True,
                "book_type": "L1_MBP",
                "bar_ts_init_on_close": True,
            },
        }

        if not metrics["flat_at_end"]:
            raise RuntimeError("candidate did not finish flat")
        if evidence["rejections"]:
            metrics["implementation_warning"] = "orders were rejected or denied"

        write_json_atomic(destination / "metrics.json", metrics)
        write_json_atomic(destination / "trades.json", {"trades": trades})
        _write_csv(
            destination / "equity.csv",
            curve,
            ["ts_ns", "equity"],
        )
        write_events(destination / "events.jsonl", strategy.events)
        write_json_atomic(
            destination / "run.json",
            create_run_manifest(
                run_id=run_id,
                candidate="candidate-09-lrae",
                config_path=config_path,
                data_manifest_path=data_manifest_path,
                extra={
                    "week": week_name,
                    "variant": variant,
                    "feature_path": str(Path(feature_path)),
                    "bar_type": str(bar_type),
                    "instrument_id": str(instrument.id),
                    "result": {
                        "final_nav": final_nav,
                        "geometric_daily_growth": daily["geometric_daily_growth"],
                        "max_drawdown": metrics["max_drawdown"],
                        "trades": metrics["trade_metrics"]["trades"],
                    },
                },
            ),
        )
        return metrics
    finally:
        engine.dispose()
