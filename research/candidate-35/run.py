#!/usr/bin/env python3
"""Run Candidate 35 as one four-symbol NautilusTrader account.

This direct runner is intentionally small: Candidate 05 supplies verified
Binance ingestion, instrument contracts and metric parsing; NautilusTrader owns
catalog replay, fills, fees, orders, positions, margin, liquidation and NAV.
Long replays use the same strategy through ``run_continuous.py``.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
import math
from pathlib import Path
import shutil
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"
for path in (HERE, CANDIDATE16, CANDIDATE05):
    sys.path.insert(0, str(path))

from timestamp_contract import install as install_timestamp_contract
from wrangler_contract import install as install_wrangler_contract
from positioning_contract import install as install_positioning_contract
from basis_contract import install as install_basis_contract
from book_depth_gap_contract import install as install_book_depth_gap_contract

install_timestamp_contract()
install_wrangler_contract()
install_positioning_contract()
install_basis_contract()
install_book_depth_gap_contract()

import backtest as c05
from features import load_range, sha256_file
from instrument_contracts import instrument_contract
from nautilus_trader.backtest.config import (
    BacktestDataConfig,
    BacktestEngineConfig,
    BacktestRunConfig,
    BacktestVenueConfig,
    ImportableFeeModelConfig,
    ImportableFillModelConfig,
    ImportableLatencyModelConfig,
)
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import ImportableStrategyConfig, LoggingConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import BarDataWrangler
from smc_ict_4.manifest import create_run_manifest, write_json_atomic

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class Candidate35RunError(RuntimeError):
    pass


def _expected_rows(start: date, end: date) -> int:
    return ((end - start).days + 1) * 1_440


def load_inputs(
    *, start: date, end: date, cache: Path, output: Path
) -> tuple[dict[str, pd.DataFrame], dict[str, Path], dict[str, Any]]:
    klines: dict[str, pd.DataFrame] = {}
    features: dict[str, Path] = {}
    records: dict[str, Any] = {}
    expected = _expected_rows(start, end)
    reference: pd.DatetimeIndex | None = None
    for symbol in SYMBOLS:
        frame, feature_path, raw_files, evidence = load_range(
            symbol=symbol,
            start=start,
            end=end,
            cache=cache,
            output=output / "source" / symbol,
        )
        if len(frame) != expected:
            raise Candidate35RunError(f"{symbol} rows={len(frame)} expected={expected}")
        clock = pd.DatetimeIndex(pd.to_datetime(frame["close_time_dt"], utc=True))
        if reference is None:
            reference = clock
        elif not clock.equals(reference):
            raise Candidate35RunError(f"cross-symbol minute clock differs for {symbol}")
        klines[symbol] = frame
        features[symbol] = feature_path
        records[symbol] = {
            "mode": "direct-checksum-verified-binance",
            "rows": len(frame),
            "raw_files": len(raw_files),
            "feature_sha256": sha256_file(feature_path),
            "evidence_files": len(evidence),
            "evidence_endpoints": dict(Counter(item.endpoint for item in evidence)),
        }
    return klines, features, records


def prepare_catalog(
    *, klines: dict[str, pd.DataFrame], config: dict[str, Any], path: Path
) -> tuple[dict[str, InstrumentId], dict[str, BarType], dict[str, Any]]:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(path)
    instruments: dict[str, Any] = {}
    ids: dict[str, InstrumentId] = {}
    types: dict[str, BarType] = {}
    contracts: dict[str, Any] = {}
    all_bars: list[Any] = []
    for symbol in SYMBOLS:
        contract = instrument_contract(symbol)
        instrument_id = InstrumentId.from_str(contract.instrument_id)
        bar_type = BarType.from_str(contract.bar_type)
        instrument = c05.make_instrument(config, contract, instrument_id)
        frame = klines[symbol].set_index("close_time_dt")[[
            "open", "high", "low", "close", "volume"
        ]].astype(float)
        bars = BarDataWrangler(bar_type, instrument).process(frame)
        if not bars:
            raise Candidate35RunError(f"no Nautilus bars for {symbol}")
        instruments[symbol] = instrument
        ids[symbol] = instrument_id
        types[symbol] = bar_type
        contracts[symbol] = contract
        all_bars.extend(bars)
    catalog.write_data(list(instruments.values()))
    catalog.write_data(all_bars)
    return ids, types, contracts


def _symbol_counts(positions: pd.DataFrame) -> dict[str, int]:
    if positions.empty:
        return {}
    normalized = {str(column).lower().replace(" ", "_"): column for column in positions.columns}
    column = next(
        (original for name, original in normalized.items() if "instrument" in name),
        None,
    )
    if column is None:
        return {}
    result: dict[str, int] = {}
    for value in positions[column].astype(str):
        symbol = value.split(".")[0]
        result[symbol] = result.get(symbol, 0) + 1
    return result


def _rolling(daily: dict[str, float], starting_nav: float, window: int) -> dict[str, Any]:
    ordered = pd.Series(daily, dtype=float).sort_index()
    nav = starting_nav * (1.0 + ordered).cumprod()
    total = (nav / nav.shift(window) - 1.0).dropna()
    geo = ((nav / nav.shift(window)).pow(1.0 / window) - 1.0).dropna()
    if total.empty:
        return {"window_days": window, "windows": 0}
    return {
        "window_days": window,
        "windows": int(total.size),
        "positive_window_share": float((total > 0.0).mean()),
        "min_total_return": float(total.min()),
        "median_total_return": float(total.median()),
        "max_total_return": float(total.max()),
        "min_geometric_daily_growth": float(geo.min()),
        "median_geometric_daily_growth": float(geo.median()),
        "max_geometric_daily_growth": float(geo.max()),
        "worst_window_end": str(total.idxmin()),
        "best_window_end": str(total.idxmax()),
    }


def build_metrics(
    *, equity: pd.DataFrame, positions: pd.DataFrame, output: Path,
    start: date, end: date, config: dict[str, Any], result: Any,
    input_records: dict[str, Any],
) -> dict[str, Any]:
    starting_nav = float(config["starting_nav"])
    daily, ending_nav, geometric, drawdown, min_equity = c05.equity_metrics(
        equity, start, end, starting_nav
    )
    pnls = c05.extract_position_pnls(positions)
    wins = sum(value > 0.0 for value in pnls)
    losses = sum(value < 0.0 for value in pnls)
    gross_profit = sum(value for value in pnls if value > 0.0)
    gross_loss = -sum(value for value in pnls if value < 0.0)
    trades = len(pnls)
    days = (end - start).days + 1
    diagnostics = json.loads(
        (output / "strategy_diagnostics.json").read_text(encoding="utf-8")
    )
    win_rate = wins / trades if trades else 0.0
    expectancy = sum(pnls) / trades if trades else 0.0
    largest_winner_share = (
        max((value for value in pnls if value > 0.0), default=0.0) / gross_profit
        if gross_profit > 0.0 else 1.0
    )
    liquidations = any(
        "LIQUIDAT" in str(value).upper()
        for value in positions.astype(str).to_numpy().ravel()
    )
    checks = {
        "geometric_daily_growth": geometric >= 0.01,
        "independent_trades_at_least_calendar_days": trades >= days,
        "win_rate": win_rate >= 0.40,
        "positive_expectancy": expectancy > 0.0,
        "max_drawdown": drawdown <= 0.20,
        "positive_nav": ending_nav > 0.0 and min_equity > 0.0,
        "no_liquidation": not liquidations,
        "no_order_rejections": int(diagnostics.get("order_rejections", 0)) == 0,
        "single_entry_intent": int(diagnostics.get("max_simultaneous_entry_intents", 0)) <= 1,
        "single_position": int(diagnostics.get("max_open_positions_observed", 0)) <= 1,
        "no_global_position_violation": int(diagnostics.get("global_position_violations", 0)) == 0,
        "nautilus_positions_match": int(result.total_positions) == trades,
    }
    return {
        "candidate": "candidate-35-clock-phase-auction-router",
        "engine": "NautilusTrader BacktestNode",
        "universe": list(SYMBOLS),
        "single_continuous_account": True,
        "single_strategy_process": True,
        "global_entry_or_position_limit": 1,
        "evaluation_start": str(start),
        "evaluation_end": str(end),
        "calendar_days": days,
        "starting_nav": starting_nav,
        "ending_nav": ending_nav,
        "total_return": ending_nav / starting_nav - 1.0,
        "geometric_daily_growth": geometric,
        "max_drawdown": drawdown,
        "min_equity": min_equity,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (
            gross_profit / gross_loss if gross_loss > 0.0
            else (None if gross_profit > 0.0 else 0.0)
        ),
        "expectancy_usdt": expectancy,
        "active_days": sum(abs(value) > 1e-12 for value in daily.values()),
        "largest_winner_share": largest_winner_share,
        "position_counts_by_symbol": _symbol_counts(positions),
        "daily_returns": daily,
        "rolling_windows": {
            str(window): _rolling(daily, starting_nav, window)
            for window in (30, 90, 180, 365)
        },
        "strategy_diagnostics": diagnostics,
        "input_records": input_records,
        "nautilus_result": {
            "run_id": result.run_id,
            "iterations": result.iterations,
            "total_events": result.total_events,
            "total_orders": result.total_orders,
            "total_positions": result.total_positions,
            "summary": result.summary,
            "stats_pnls": result.stats_pnls,
            "stats_returns": result.stats_returns,
        },
        "gate_checks": checks,
        "gate_pass": all(checks.values()),
    }


def run(
    *, config_path: Path, start: date, end: date,
    cache: Path, output: Path, workspace: Path,
) -> dict[str, Any]:
    del workspace  # kept in the CLI contract; direct mode does not assemble chunks.
    output = output.resolve()
    cache = cache.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if tuple(config.get("symbols", ())) != SYMBOLS:
        raise Candidate35RunError(f"universe must be exactly {SYMBOLS}")
    if abs(float(config["risk_fraction"]) - 0.03) > 1e-12:
        raise Candidate35RunError("risk_fraction must remain 0.03")

    klines, features, input_records = load_inputs(
        start=start, end=end, cache=cache, output=output
    )
    catalog_path = output / "catalog"
    ids, bar_types, contracts = prepare_catalog(
        klines=klines, config=config, path=catalog_path
    )
    input_manifest = {
        "candidate": "candidate-35-clock-phase-auction-router",
        "start": str(start),
        "end": str(end),
        "calendar_days": (end - start).days + 1,
        "minute_rows_per_symbol": _expected_rows(start, end),
        "symbols": input_records,
        "continuous_account": True,
        "account_restarts": 0,
        "strategy_restarts": 0,
    }
    manifest_path = output / "data_manifest.json"
    write_json_atomic(manifest_path, input_manifest)

    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end + timedelta(days=1), tz="UTC") - pd.Timedelta(nanoseconds=1)
    values = dict(config["strategy"])
    values.update({
        "btc_instrument_id": str(ids["BTCUSDT"]),
        "eth_instrument_id": str(ids["ETHUSDT"]),
        "sol_instrument_id": str(ids["SOLUSDT"]),
        "xrp_instrument_id": str(ids["XRPUSDT"]),
        "btc_bar_type": str(bar_types["BTCUSDT"]),
        "eth_bar_type": str(bar_types["ETHUSDT"]),
        "sol_bar_type": str(bar_types["SOLUSDT"]),
        "xrp_bar_type": str(bar_types["XRPUSDT"]),
        "btc_features_path": str(features["BTCUSDT"].resolve()),
        "eth_features_path": str(features["ETHUSDT"].resolve()),
        "sol_features_path": str(features["SOLUSDT"].resolve()),
        "xrp_features_path": str(features["XRPUSDT"].resolve()),
        "output_dir": str(output),
        "evaluation_start_ns": int(start_ts.value),
        "evaluation_end_ns": int(end_ts.value),
        "starting_nav": float(config["starting_nav"]),
        "risk_fraction": float(config["risk_fraction"]),
        "all_in_cost_bps_each_side": float(config["all_in_cost_bps_each_side"]),
        "adverse_slippage_bps_each_side": float(config["adverse_slippage_bps_each_side"]),
        "funding_reserve_bps": float(config["funding_reserve_bps"]),
    })
    strategy = ImportableStrategyConfig(
        strategy_path="strategy:Candidate35Strategy",
        config_path="strategy:Candidate35Config",
        config=values,
    )
    fill = ImportableFillModelConfig(
        fill_model_path="nautilus_trader.backtest.models:FillModel",
        config_path="nautilus_trader.backtest.config:FillModelConfig",
        config={
            "prob_fill_on_limit": 1.0,
            "prob_slippage": 1.0,
            "random_seed": int(config["execution_seed"]),
        },
    )
    latency = ImportableLatencyModelConfig(
        latency_model_path="nautilus_trader.backtest.models:LatencyModel",
        config_path="nautilus_trader.backtest.config:LatencyModelConfig",
        config={
            "base_latency_nanos": 100_000_000,
            "insert_latency_nanos": 150_000_000,
            "update_latency_nanos": 100_000_000,
            "cancel_latency_nanos": 100_000_000,
        },
    )
    fee = ImportableFeeModelConfig(
        fee_model_path="nautilus_trader.backtest.models:MakerTakerFeeModel",
        config_path="nautilus_trader.backtest.config:MakerTakerFeeModelConfig",
        config={},
    )
    venue = BacktestVenueConfig(
        name="BINANCE",
        oms_type="NETTING",
        account_type="MARGIN",
        base_currency="USDT",
        starting_balances=[f"{float(config['starting_nav']):.2f} USDT"],
        default_leverage=float(config["venue_leverage"]),
        book_type="L1_MBP",
        fill_model=fill,
        latency_model=latency,
        fee_model=fee,
        use_position_ids=True,
        use_reduce_only=True,
        support_contingent_orders=True,
        oto_trigger_mode="PARTIAL",
        bar_execution=True,
        bar_adaptive_high_low_ordering=True,
        trade_execution=False,
        liquidation_enabled=True,
        liquidation_trigger_ratio=1.0,
    )
    data = [
        BacktestDataConfig(
            catalog_path=str(catalog_path),
            data_cls=Bar,
            instrument_id=ids[symbol],
            bar_spec="1-MINUTE-LAST",
            start_time=start_ts.isoformat(),
            end_time=end_ts.isoformat(),
        )
        for symbol in SYMBOLS
    ]
    engine = BacktestEngineConfig(
        logging=LoggingConfig(log_level="ERROR"),
        strategies=[strategy],
        run_analysis=True,
    )
    run_config = BacktestRunConfig(
        engine=engine,
        venues=[venue],
        data=data,
        raise_exception=True,
        dispose_on_completion=False,
        start=start_ts.isoformat(),
        end=end_ts.isoformat(),
    )

    node = BacktestNode(configs=[run_config])
    try:
        results = node.run()
        if len(results) != 1 or len(node.get_engines()) != 1:
            raise Candidate35RunError("expected exactly one Nautilus run and engine")
        result = results[0]
        trader = node.get_engines()[0].trader
        orders = trader.generate_order_fills_report()
        positions = trader.generate_positions_report()
        account = trader.generate_account_report(Venue("BINANCE"))
        orders.to_csv(output / "orders.csv", index=False)
        positions.to_csv(output / "positions.csv", index=False)
        account.to_csv(output / "account.csv", index=False)
        equity = c05.read_equity(output / "equity.csv")
        metrics = build_metrics(
            equity=equity,
            positions=positions,
            output=output,
            start=start,
            end=end,
            config=config,
            result=result,
            input_records=input_records,
        )
        write_json_atomic(output / "metrics.json", metrics)
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=f"candidate-35-{start}-{end}",
                candidate="candidate-35-clock-phase-auction-router",
                config_path=config_path.resolve(),
                data_manifest_path=manifest_path,
                extra={
                    "engine": "NautilusTrader BacktestNode",
                    "universe": list(SYMBOLS),
                    "single_continuous_account": True,
                    "single_strategy_process": True,
                    "global_entry_or_position_limit": 1,
                    "risk_fraction": 0.03,
                    "instrument_contracts": {
                        symbol: {
                            "instrument_id": contracts[symbol].instrument_id,
                            "bar_type": contracts[symbol].bar_type,
                            "price_increment": contracts[symbol].price_increment,
                            "size_increment": contracts[symbol].size_increment,
                            "metadata_source": contracts[symbol].metadata_source,
                        }
                        for symbol in SYMBOLS
                    },
                },
            ),
        )
        return metrics
    finally:
        node.dispose()
        if catalog_path.exists():
            shutil.rmtree(catalog_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    metrics = run(
        config_path=args.config,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        cache=args.cache,
        output=args.output,
        workspace=args.workspace,
    )
    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
