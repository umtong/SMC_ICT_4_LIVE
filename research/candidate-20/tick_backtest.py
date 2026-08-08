"""NautilusTrader runner with an actual sub-minute execution clock.

The shared Candidate 05 runner already downloads Binance aggTrades but writes
only one-minute bars to the Nautilus catalog. With a non-zero latency model and
no events between bars, an order submitted from on_bar effectively arrives one
full minute later. Candidate 20 does not add an execution or accounting engine;
it feeds a sparse, deterministic subset of the already downloaded aggTrades to
the same NautilusTrader BacktestNode so its native nanosecond clock can settle
orders after realistic sub-minute latency.

One actual aggregate trade is retained per minute: the first trade at least one
second after the minute boundary (falling back to the first trade if necessary).
This is enough to drain the 250 ms insertion latency without fabricating prices
or using future information in strategy decisions. Bars remain the strategy
clock and continue to drive conservative stop/target matching.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import json
import math
import shutil
from typing import Any, Iterable

import pandas as pd

from nautilus_trader.backtest.config import BacktestDataConfig
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.config import BacktestRunConfig
from nautilus_trader.backtest.config import BacktestVenueConfig
from nautilus_trader.backtest.config import ImportableFeeModelConfig
from nautilus_trader.backtest.config import ImportableFillModelConfig
from nautilus_trader.backtest.config import ImportableLatencyModelConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import Bar, TradeTick
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import TradeTickDataWrangler

import backtest as base
from features import AGG_COLUMNS


class TickRunnerError(RuntimeError):
    """Raised when the actual-trade execution clock cannot be trusted."""


def _agg_reader(path: Path, chunksize: int = 500_000) -> Iterable[pd.DataFrame]:
    probe = pd.read_csv(path, compression="zip", nrows=1)
    if set(AGG_COLUMNS).issubset(probe.columns):
        return pd.read_csv(
            path,
            compression="zip",
            usecols=AGG_COLUMNS,
            chunksize=chunksize,
        )
    return pd.read_csv(
        path,
        compression="zip",
        header=None,
        names=AGG_COLUMNS,
        usecols=range(len(AGG_COLUMNS)),
        chunksize=chunksize,
    )


def _maker_mask(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin(
        {"true", "1", "t", "yes"},
    )


def _sparse_actual_trades(paths: list[Path]) -> pd.DataFrame:
    candidates: list[pd.DataFrame] = []
    agg_paths = sorted(
        path
        for path in paths
        if path.suffix == ".zip" and "-aggTrades-" in path.name
    )
    if not agg_paths:
        raise TickRunnerError("no aggTrades archives were supplied by load_range")

    minute_ns = 60_000_000_000
    one_second_ns = 1_000_000_000
    for path in agg_paths:
        day_candidates: list[pd.DataFrame] = []
        for chunk in _agg_reader(path):
            transact = pd.to_numeric(chunk["transact_time"], errors="raise").astype("int64")
            factor = 1_000 if int(transact.iloc[0]) > 10**14 else 1_000_000
            ts_ns = transact * factor
            work = pd.DataFrame(
                {
                    "ts_ns": ts_ns,
                    "minute_ns": (ts_ns // minute_ns) * minute_ns,
                    "eligible": (ts_ns % minute_ns) >= one_second_ns,
                    "price": pd.to_numeric(chunk["price"], errors="raise").astype(float),
                    "quantity": pd.to_numeric(chunk["quantity"], errors="raise").astype(float),
                    "trade_id": pd.to_numeric(
                        chunk["agg_trade_id"],
                        errors="raise",
                    ).astype("int64").astype(str),
                    "buyer_maker": _maker_mask(chunk["is_buyer_maker"]).to_numpy(),
                },
            )
            work = work[(work["price"] > 0.0) & (work["quantity"] > 0.0)]
            if work.empty:
                continue
            fallback = work.sort_values("ts_ns").drop_duplicates("minute_ns", keep="first")
            eligible = (
                work[work["eligible"]]
                .sort_values("ts_ns")
                .drop_duplicates("minute_ns", keep="first")
            )
            day_candidates.extend([fallback, eligible])
        if not day_candidates:
            raise TickRunnerError(f"empty aggregate-trade archive {path}")
        candidates.append(pd.concat(day_candidates, ignore_index=True))

    selected = pd.concat(candidates, ignore_index=True)
    selected = selected.sort_values(
        ["minute_ns", "eligible", "ts_ns"],
        ascending=[True, False, True],
    ).drop_duplicates("minute_ns", keep="first")
    selected = selected.sort_values("ts_ns")
    if selected["ts_ns"].duplicated().any():
        raise TickRunnerError("duplicate execution tick timestamps")
    if not selected["ts_ns"].is_monotonic_increasing:
        raise TickRunnerError("execution ticks are not time ordered")

    frame = selected[["price", "quantity", "trade_id", "buyer_maker"]].copy()
    frame.index = pd.to_datetime(selected["ts_ns"], unit="ns", utc=True)
    frame.index.name = "timestamp"
    return frame


def _append_execution_ticks(
    *,
    raw_files: list[Path],
    catalog_path: Path,
    instrument: Any,
    output: Path,
) -> int:
    frame = _sparse_actual_trades(raw_files)
    ticks = TradeTickDataWrangler(instrument).process(frame, ts_init_delta=0)
    if not ticks:
        raise TickRunnerError("TradeTickDataWrangler produced no ticks")
    ParquetDataCatalog(catalog_path).write_data(ticks)
    evidence = {
        "schema": "candidate-20-sparse-aggtrade-clock-v1",
        "selection": "first actual aggTrade >= 1 second after each minute boundary; fallback first trade",
        "source_rows": len(frame),
        "first_ts_event": int(ticks[0].ts_event),
        "last_ts_event": int(ticks[-1].ts_event),
        "strictly_increasing": all(
            int(left.ts_event) < int(right.ts_event)
            for left, right in zip(ticks, ticks[1:])
        ),
    }
    base.write_json_atomic(output / "execution_clock.json", evidence)
    return len(ticks)


def run_backtest(
    *,
    config_path: Path,
    build_start: date,
    build_end: date,
    evaluation_start: date,
    evaluation_end: date,
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not build_start <= evaluation_start <= evaluation_end <= build_end:
        raise TickRunnerError("evaluation must be contained in build range")
    try:
        contract = base.instrument_contract(str(config["symbol"]))
    except (KeyError, ValueError) as exc:
        raise TickRunnerError(str(exc)) from exc
    if abs(float(config["risk_fraction"]) - 0.03) > 1e-12:
        raise TickRunnerError("project risk fraction must remain 3%")

    instrument_id = InstrumentId.from_str(contract.instrument_id)
    bar_type = base.BarType.from_str(contract.bar_type)
    klines, feature_path, raw_files, _ = base.load_range(
        symbol=contract.symbol,
        start=build_start,
        end=build_end,
        cache=cache.resolve(),
        output=output,
    )
    catalog_path = output / "catalog"
    instrument, manifest_path = base.prepare_catalog(
        klines=klines,
        raw_files=raw_files,
        raw_cache=cache.resolve(),
        feature_path=feature_path,
        catalog_path=catalog_path,
        output=output,
        config=config,
        contract=contract,
        instrument_id=instrument_id,
        bar_type=bar_type,
        build_start=build_start,
        build_end=build_end,
    )
    execution_tick_count = _append_execution_ticks(
        raw_files=raw_files,
        catalog_path=catalog_path,
        instrument=instrument,
        output=output,
    )

    evaluation_start_ts = pd.Timestamp(evaluation_start, tz="UTC")
    evaluation_end_ts = (
        pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC")
        - pd.Timedelta(nanoseconds=1)
    )
    strategy_values = dict(config["strategy"])
    strategy_values.update(
        {
            "instrument_id": str(instrument_id),
            "bar_type": str(bar_type),
            "output_dir": str(output),
            "features_path": str(feature_path.resolve()),
            "evaluation_start_ns": int(evaluation_start_ts.value),
            "evaluation_end_ns": int(evaluation_end_ts.value),
            "starting_nav": float(config["starting_nav"]),
            "risk_fraction": float(config["risk_fraction"]),
            "all_in_cost_bps_each_side": float(config["all_in_cost_bps_each_side"]),
            "adverse_slippage_bps_each_side": float(
                config["adverse_slippage_bps_each_side"],
            ),
        },
    )
    strategy = ImportableStrategyConfig(
        strategy_path="candidate19_strategy:Candidate19Strategy",
        config_path="candidate19_strategy:Candidate19Config",
        config=strategy_values,
    )
    fill_model = ImportableFillModelConfig(
        fill_model_path="nautilus_trader.backtest.models:FillModel",
        config_path="nautilus_trader.backtest.config:FillModelConfig",
        config={
            "prob_fill_on_limit": 1.0,
            "prob_slippage": 1.0,
            "random_seed": int(config["execution_seed"]),
        },
    )
    latency_model = ImportableLatencyModelConfig(
        latency_model_path="nautilus_trader.backtest.models:LatencyModel",
        config_path="nautilus_trader.backtest.config:LatencyModelConfig",
        config={
            "base_latency_nanos": 100_000_000,
            "insert_latency_nanos": 150_000_000,
            "update_latency_nanos": 100_000_000,
            "cancel_latency_nanos": 100_000_000,
        },
    )
    fee_model = ImportableFeeModelConfig(
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
        fill_model=fill_model,
        latency_model=latency_model,
        fee_model=fee_model,
        use_position_ids=True,
        use_reduce_only=True,
        support_contingent_orders=True,
        bar_execution=True,
        bar_adaptive_high_low_ordering=True,
        trade_execution=True,
        liquidation_enabled=True,
        liquidation_trigger_ratio=1.0,
    )
    common_data = {
        "catalog_path": str(catalog_path),
        "instrument_id": instrument_id,
        "start_time": pd.Timestamp(build_start, tz="UTC").isoformat(),
        "end_time": (
            pd.Timestamp(build_end + timedelta(days=1), tz="UTC")
            - pd.Timedelta(nanoseconds=1)
        ).isoformat(),
    }
    data = [
        BacktestDataConfig(data_cls=TradeTick, **common_data),
        BacktestDataConfig(data_cls=Bar, bar_spec="1-MINUTE-LAST", **common_data),
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
        start=common_data["start_time"],
        end=common_data["end_time"],
    )

    node = BacktestNode(configs=[run_config])
    try:
        results = node.run()
        if len(results) != 1:
            raise TickRunnerError(f"expected one Nautilus result, got {len(results)}")
        result = results[0]
        engines = node.get_engines()
        if len(engines) != 1:
            raise TickRunnerError(f"expected one Nautilus engine, got {len(engines)}")
        nt_engine = engines[0]
        orders = nt_engine.trader.generate_order_fills_report()
        positions = nt_engine.trader.generate_positions_report()
        account = nt_engine.trader.generate_account_report(Venue("BINANCE"))
        orders.to_csv(output / "orders.csv", index=False)
        positions.to_csv(output / "positions.csv", index=False)
        account.to_csv(output / "account.csv", index=False)

        equity = base.read_equity(output / "equity.csv")
        metrics = base.build_metrics(
            equity=equity,
            positions=positions,
            output=output,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            config=config,
            instrument_id=instrument_id,
            result=result,
        )
        metrics.update(
            {
                "execution_clock": "SPARSE_ACTUAL_AGGTRADE_SUBMINUTE",
                "execution_tick_count": execution_tick_count,
                "bar_execution": True,
                "trade_execution": True,
            },
        )
        base.write_json_atomic(output / "metrics.json", metrics)
        base.write_json_atomic(
            output / "run.json",
            base.create_run_manifest(
                run_id=f"candidate-20-{contract.symbol.lower()}-{evaluation_start}-{evaluation_end}",
                candidate="candidate-20-actual-trade-execution-clock",
                config_path=config_path,
                data_manifest_path=manifest_path,
                extra={
                    "engine": "NautilusTrader BacktestNode",
                    "strategy": "Candidate19Strategy unchanged",
                    "execution_clock": "one actual aggTrade per minute after one second",
                    "execution_tick_count": execution_tick_count,
                    "build_start": str(build_start),
                    "build_end": str(build_end),
                    "evaluation_start": str(evaluation_start),
                    "evaluation_end": str(evaluation_end),
                },
            ),
        )
        return metrics
    finally:
        node.dispose()
        if catalog_path.exists():
            shutil.rmtree(catalog_path)


__all__ = ["run_backtest"]
