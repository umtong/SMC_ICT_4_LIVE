#!/usr/bin/env python3
"""Run one Candidate 51 account with data prehistory before evaluation.

Many public multi-timeframe systems need hundreds of completed informative
candles before their first eligible decision.  Treating missing EMA200/ATR
history as a strategy loss or zero-opportunity result would confuse an input
warmup defect with a logic verdict.  This launcher reuses the same Nautilus
runner, kline checksum contract, fees, fills, latency, margin account and 3%
risk rule while separating:

* ``data_start``: first replayed observation used only to build causal state;
* ``start``: first timestamp at which the strategy may submit a new entry;
* ``end``: final evaluation timestamp.

There are no account or strategy restarts between warmup and evaluation.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import json
from datetime import date, timedelta
from pathlib import Path
import shutil
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"
for path in (CANDIDATE05, CANDIDATE16, HERE):
    text = str(path)
    while text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

strategy_module = importlib.import_module("strategy")
resolved = Path(strategy_module.__file__).resolve()
expected = (HERE / "strategy.py").resolve()
if resolved != expected:
    raise RuntimeError(f"Candidate 51 strategy collision: {resolved} != {expected}")

import event_lifecycle_patch  # noqa: F401,E402
import kline_only_inputs  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "candidate51_warmup_direct_runner",
    HERE / "run.py",
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load Candidate 51 direct runner")
runner = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = runner
_spec.loader.exec_module(runner)
runner.load_range = kline_only_inputs.load_range

_original_build_metrics = runner.build_metrics


def _build_metrics_with_risk_contract(*args, **kwargs):
    metrics = _original_build_metrics(*args, **kwargs)
    bound = inspect.signature(_original_build_metrics).bind_partial(*args, **kwargs)
    config = bound.arguments["config"]
    checks = metrics.setdefault("gate_checks", {})
    checks["risk_fraction_exactly_three_percent"] = (
        abs(float(config["risk_fraction"]) - 0.03) <= 1e-12
    )
    metrics["gate_pass"] = all(bool(value) for value in checks.values())
    return metrics


runner.build_metrics = _build_metrics_with_risk_contract


def run_with_warmup(
    *,
    config_path: Path,
    data_start: date,
    start: date,
    end: date,
    cache: Path,
    output: Path,
    workspace: Path,
) -> dict[str, Any]:
    del workspace
    if not (data_start <= start <= end):
        raise runner.Candidate35RunError(
            f"require data_start <= start <= end, got {data_start}, {start}, {end}"
        )
    output = output.resolve()
    cache = cache.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if tuple(config.get("symbols", ())) != runner.SYMBOLS:
        raise runner.Candidate35RunError(
            f"universe must be exactly {runner.SYMBOLS}"
        )
    if abs(float(config["risk_fraction"]) - 0.03) > 1e-12:
        raise runner.Candidate35RunError("risk_fraction must remain 0.03")

    klines, features, input_records = runner.load_inputs(
        start=data_start,
        end=end,
        cache=cache,
        output=output,
    )
    catalog_path = output / "catalog"
    ids, bar_types, contracts = runner.prepare_catalog(
        klines=klines,
        config=config,
        path=catalog_path,
    )
    loaded_days = (end - data_start).days + 1
    evaluation_days = (end - start).days + 1
    input_manifest = {
        "candidate": "candidate-51-warmup-continuous-account",
        "data_start": str(data_start),
        "evaluation_start": str(start),
        "evaluation_end": str(end),
        "loaded_calendar_days": loaded_days,
        "evaluation_calendar_days": evaluation_days,
        "warmup_calendar_days": (start - data_start).days,
        "minute_rows_per_symbol": runner._expected_rows(data_start, end),
        "symbols": input_records,
        "continuous_account": True,
        "account_restarts": 0,
        "strategy_restarts": 0,
        "entries_disabled_before_evaluation_start": True,
    }
    manifest_path = output / "data_manifest.json"
    runner.write_json_atomic(manifest_path, input_manifest)

    data_start_ts = pd.Timestamp(data_start, tz="UTC")
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = (
        pd.Timestamp(end + timedelta(days=1), tz="UTC")
        - pd.Timedelta(nanoseconds=1)
    )
    values = dict(config["strategy"])
    values.update(
        {
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
            "all_in_cost_bps_each_side": float(
                config["all_in_cost_bps_each_side"]
            ),
            "adverse_slippage_bps_each_side": float(
                config["adverse_slippage_bps_each_side"]
            ),
            "funding_reserve_bps": float(config["funding_reserve_bps"]),
        }
    )
    strategy = runner.ImportableStrategyConfig(
        strategy_path="strategy:Candidate35Strategy",
        config_path="strategy:Candidate35Config",
        config=values,
    )
    fill = runner.ImportableFillModelConfig(
        fill_model_path="nautilus_trader.backtest.models:FillModel",
        config_path="nautilus_trader.backtest.config:FillModelConfig",
        config={
            "prob_fill_on_limit": 1.0,
            "prob_slippage": 1.0,
            "random_seed": int(config["execution_seed"]),
        },
    )
    latency = runner.ImportableLatencyModelConfig(
        latency_model_path="nautilus_trader.backtest.models:LatencyModel",
        config_path="nautilus_trader.backtest.config:LatencyModelConfig",
        config={
            "base_latency_nanos": 100_000_000,
            "insert_latency_nanos": 150_000_000,
            "update_latency_nanos": 100_000_000,
            "cancel_latency_nanos": 100_000_000,
        },
    )
    fee = runner.ImportableFeeModelConfig(
        fee_model_path="nautilus_trader.backtest.models:MakerTakerFeeModel",
        config_path=(
            "nautilus_trader.backtest.config:MakerTakerFeeModelConfig"
        ),
        config={},
    )
    venue = runner.BacktestVenueConfig(
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
        runner.BacktestDataConfig(
            catalog_path=str(catalog_path),
            data_cls=runner.Bar,
            instrument_id=ids[symbol],
            bar_spec="1-MINUTE-LAST",
            start_time=data_start_ts.isoformat(),
            end_time=end_ts.isoformat(),
        )
        for symbol in runner.SYMBOLS
    ]
    engine = runner.BacktestEngineConfig(
        logging=runner.LoggingConfig(log_level="ERROR"),
        strategies=[strategy],
        run_analysis=True,
    )
    run_config = runner.BacktestRunConfig(
        engine=engine,
        venues=[venue],
        data=data,
        raise_exception=True,
        dispose_on_completion=False,
        start=data_start_ts.isoformat(),
        end=end_ts.isoformat(),
    )

    node = runner.BacktestNode(configs=[run_config])
    try:
        results = node.run()
        if len(results) != 1 or len(node.get_engines()) != 1:
            raise runner.Candidate35RunError(
                "expected exactly one Nautilus run and engine"
            )
        result = results[0]
        trader = node.get_engines()[0].trader
        orders = trader.generate_order_fills_report()
        positions = trader.generate_positions_report()
        account = trader.generate_account_report(runner.Venue("BINANCE"))
        orders.to_csv(output / "orders.csv", index=False)
        positions.to_csv(output / "positions.csv", index=False)
        account.to_csv(output / "account.csv", index=False)
        equity = runner.c05.read_equity(output / "equity.csv")
        metrics = runner.build_metrics(
            equity=equity,
            positions=positions,
            output=output,
            start=start,
            end=end,
            config=config,
            result=result,
            input_records=input_records,
        )
        metrics["data_start"] = str(data_start)
        metrics["warmup_calendar_days"] = (start - data_start).days
        metrics["entries_disabled_before_evaluation_start"] = True
        runner.write_json_atomic(output / "metrics.json", metrics)
        runner.write_json_atomic(
            output / "run.json",
            runner.create_run_manifest(
                run_id=f"candidate-51-warmup-{data_start}-{start}-{end}",
                candidate="candidate-51-warmup-continuous-account",
                config_path=config_path.resolve(),
                data_manifest_path=manifest_path,
                extra={
                    "engine": "NautilusTrader BacktestNode",
                    "universe": list(runner.SYMBOLS),
                    "single_continuous_account": True,
                    "single_strategy_process": True,
                    "global_entry_or_position_limit": 1,
                    "risk_fraction": 0.03,
                    "data_start": str(data_start),
                    "evaluation_start": str(start),
                    "evaluation_end": str(end),
                    "account_restarts": 0,
                    "strategy_restarts": 0,
                    "instrument_contracts": {
                        symbol: {
                            "instrument_id": contracts[symbol].instrument_id,
                            "bar_type": contracts[symbol].bar_type,
                            "price_increment": contracts[symbol].price_increment,
                            "size_increment": contracts[symbol].size_increment,
                            "metadata_source": contracts[symbol].metadata_source,
                        }
                        for symbol in runner.SYMBOLS
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
    parser.add_argument("--data-start", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    metrics = run_with_warmup(
        config_path=args.config,
        data_start=date.fromisoformat(args.data_start),
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        cache=args.cache,
        output=args.output,
        workspace=args.workspace,
    )
    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
