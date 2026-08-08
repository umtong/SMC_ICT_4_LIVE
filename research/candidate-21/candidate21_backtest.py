"""NautilusTrader runner for Candidate 21.

Reuses Candidate 05 data/catalog/accounting and Candidate 20's sparse actual
aggTrade execution clock.  The only new preparation is causal quarter-hour
feature enrichment.  NautilusTrader remains the sole matching, order,
position, margin, liquidation, portfolio, and NAV engine.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import json
import shutil
from typing import Any

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

import backtest as base
from clock_phase_features import augment_feature_file
from tick_backtest import TickRunnerError
from tick_backtest import _append_execution_ticks


class Candidate21RunnerError(RuntimeError):
    """Raised when Candidate 21 cannot preserve a trusted causal run."""


def _clock_feature_evidence(path: Path) -> dict[str, Any]:
    frame = pd.read_csv(path, compression="infer")
    ready = frame["qh_feature_ready"]
    if ready.dtype != bool:
        ready = ready.astype(str).str.lower().isin({"true", "1", "yes"})
    boundary = frame["qh_boundary"]
    if boundary.dtype != bool:
        boundary = boundary.astype(str).str.lower().isin({"true", "1", "yes"})
    observed = pd.to_numeric(frame["observed_time_ns"], errors="raise").astype("int64")
    return {
        "schema": "candidate-21-quarter-hour-causal-features-v1",
        "rows": int(len(frame)),
        "boundary_rows": int(boundary.sum()),
        "ready_boundary_rows": int(ready.sum()),
        "strictly_increasing_observation_time": bool(
            observed.is_monotonic_increasing and not observed.duplicated().any()
        ),
        "baseline_is_lagged": True,
        "event_window": "first 10 seconds of each UTC minute",
        "clock_condition": "minute modulo 15 equals zero",
    }


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
        raise Candidate21RunnerError("evaluation must be contained in build range")
    try:
        contract = base.instrument_contract(str(config["symbol"]))
    except (KeyError, ValueError) as exc:
        raise Candidate21RunnerError(str(exc)) from exc
    if abs(float(config["risk_fraction"]) - 0.03) > 1e-12:
        raise Candidate21RunnerError("project risk fraction must remain 3%")

    strategy_config = dict(config["strategy"])
    period_minutes = int(strategy_config["clock_period_minutes"])
    baseline_periods = int(strategy_config["clock_baseline_periods"])
    min_baseline_samples = int(strategy_config["clock_min_baseline_samples"])

    instrument_id = InstrumentId.from_str(contract.instrument_id)
    bar_type = base.BarType.from_str(contract.bar_type)
    klines, base_feature_path, raw_files, _ = base.load_range(
        symbol=contract.symbol,
        start=build_start,
        end=build_end,
        cache=cache.resolve(),
        output=output,
    )
    feature_path = augment_feature_file(
        feature_path=base_feature_path,
        raw_files=raw_files,
        destination=output / "features_clock.csv.gz",
        period_minutes=period_minutes,
        baseline_periods=baseline_periods,
        min_baseline_samples=min_baseline_samples,
    )
    feature_evidence = _clock_feature_evidence(feature_path)
    if not feature_evidence["strictly_increasing_observation_time"]:
        raise Candidate21RunnerError("clock feature timestamps are not causal and ordered")
    base.write_json_atomic(output / "clock_feature_contract.json", feature_evidence)

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
    strategy_values = dict(strategy_config)
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
        strategy_path="candidate21_strategy:Candidate21Strategy",
        config_path="candidate21_strategy:Candidate21Config",
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
            raise Candidate21RunnerError(f"expected one Nautilus result, got {len(results)}")
        result = results[0]
        engines = node.get_engines()
        if len(engines) != 1:
            raise Candidate21RunnerError(f"expected one Nautilus engine, got {len(engines)}")
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
                "candidate": "candidate-21-quarter-hour-auction-router",
                "execution_clock": "SPARSE_ACTUAL_AGGTRADE_SUBMINUTE",
                "execution_tick_count": execution_tick_count,
                "clock_boundary_rows": feature_evidence["boundary_rows"],
                "clock_ready_boundary_rows": feature_evidence["ready_boundary_rows"],
                "bar_execution": True,
                "trade_execution": True,
            },
        )
        base.write_json_atomic(output / "metrics.json", metrics)
        base.write_json_atomic(
            output / "run.json",
            base.create_run_manifest(
                run_id=f"candidate-21-{contract.symbol.lower()}-{evaluation_start}-{evaluation_end}",
                candidate="candidate-21-quarter-hour-auction-router",
                config_path=config_path,
                data_manifest_path=manifest_path,
                extra={
                    "engine": "NautilusTrader BacktestNode",
                    "strategy": "Candidate21Strategy",
                    "alpha_parent": "lagged-phase-normalized quarter-hour opening flow",
                    "router": "later effort/result plus displayed-liquidity response",
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
    except TickRunnerError as exc:
        raise Candidate21RunnerError(str(exc)) from exc
    finally:
        node.dispose()
        if catalog_path.exists():
            shutil.rmtree(catalog_path)


__all__ = ["run_backtest"]
