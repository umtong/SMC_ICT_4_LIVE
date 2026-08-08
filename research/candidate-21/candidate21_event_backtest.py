"""NautilusTrader runner for Candidate 21's 10-second event-time system.

Verified Binance aggTrades are transformed into external 10-second bars and a
sparse stream of real execution ticks.  NautilusTrader remains the sole owner of
matching, order lifecycle, fills, fees, positions, margin, liquidation,
portfolio accounting and continuous NAV.
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
from nautilus_trader.model.data import Bar, BarType, TradeTick
from nautilus_trader.model.identifiers import InstrumentId, Venue

import backtest as base
from event_time_data import EventTimeDataError
from event_time_data import write_event_time_catalog
from features import download_checked


class Candidate21EventRunnerError(RuntimeError):
    """Raised when an event-time run cannot be trusted."""


def _download_aggtrade_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> tuple[list[Path], list[dict[str, Any]]]:
    if end < start:
        raise ValueError("end precedes start")
    raw_files: list[Path] = []
    evidence: list[dict[str, Any]] = []
    day = start
    while day <= end:
        archive, checksum, item = download_checked(
            "aggTrades",
            symbol,
            day,
            cache,
        )
        raw_files.extend([archive, checksum])
        evidence.append(
            {
                "endpoint": item.endpoint,
                "day": item.day,
                "archive": item.archive,
                "checksum": item.checksum,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            },
        )
        day += timedelta(days=1)
    return raw_files, evidence


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
        raise Candidate21EventRunnerError(
            "evaluation must be contained in build range",
        )
    try:
        contract = base.instrument_contract(str(config["symbol"]))
    except (KeyError, ValueError) as exc:
        raise Candidate21EventRunnerError(str(exc)) from exc
    if abs(float(config["risk_fraction"]) - 0.03) > 1e-12:
        raise Candidate21EventRunnerError(
            "project risk fraction must remain 3%",
        )

    strategy_config = dict(config["strategy"])
    period_minutes = int(strategy_config["clock_period_minutes"])
    baseline_periods = int(strategy_config["clock_baseline_periods"])
    min_baseline_samples = int(
        strategy_config["clock_min_baseline_samples"],
    )
    instrument_id = InstrumentId.from_str(contract.instrument_id)
    bar_type = BarType.from_str(
        f"{instrument_id}-10-SECOND-LAST-EXTERNAL",
    )

    cache = cache.resolve()
    raw_files, raw_evidence = _download_aggtrade_range(
        symbol=contract.symbol,
        start=build_start,
        end=build_end,
        cache=cache,
    )
    base.write_json_atomic(output / "raw_evidence.json", raw_evidence)

    catalog_path = output / "catalog"
    if catalog_path.exists():
        shutil.rmtree(catalog_path)
    catalog_path.mkdir(parents=True, exist_ok=True)
    instrument = base.make_instrument(
        config,
        contract,
        instrument_id,
    )
    try:
        catalog_result = write_event_time_catalog(
            raw_files=raw_files,
            catalog_path=catalog_path,
            instrument=instrument,
            bar_type=bar_type,
            output=output,
            period_minutes=period_minutes,
            baseline_periods=baseline_periods,
            min_baseline_samples=min_baseline_samples,
        )
    except EventTimeDataError as exc:
        raise Candidate21EventRunnerError(str(exc)) from exc

    manifest = base.build_data_manifest(
        cache,
        dataset=(
            f"binance-usdm-{contract.symbol.lower()}-"
            "aggtrades-10s-event-time"
        ),
        include=raw_files,
        metadata_values={
            "symbol": contract.symbol,
            "instrument_id": str(instrument_id),
            "bar_type": str(bar_type),
            "build_start": str(build_start),
            "build_end": str(build_end),
            "bars": catalog_result.bar_count,
            "execution_ticks": catalog_result.execution_tick_count,
            "timestamp_semantics": (
                "completed 10-second aggregate-trade interval; "
                "last nanosecond timestamp"
            ),
            "feature_path": str(catalog_result.feature_path),
            "feature_sha256": base.sha256_file(
                catalog_result.feature_path,
            ),
            "feature_observation_contract": (
                "observed_time_ns equals strategy bar ts_event"
            ),
            "baseline_contract": (
                "current boundary event excluded by one-period lag"
            ),
            "instrument_contract_source": contract.metadata_source,
            "price_precision": contract.price_precision,
            "price_increment": contract.price_increment,
            "size_precision": contract.size_precision,
            "size_increment": contract.size_increment,
            "min_quantity": contract.min_quantity,
            "min_notional": contract.min_notional,
        },
    )
    manifest_path = output / "data_manifest.json"
    base.write_data_manifest(manifest_path, manifest)

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
            "features_path": str(
                catalog_result.feature_path.resolve(),
            ),
            "evaluation_start_ns": int(evaluation_start_ts.value),
            "evaluation_end_ns": int(evaluation_end_ts.value),
            "starting_nav": float(config["starting_nav"]),
            "risk_fraction": float(config["risk_fraction"]),
            "all_in_cost_bps_each_side": float(
                config["all_in_cost_bps_each_side"],
            ),
            "adverse_slippage_bps_each_side": float(
                config["adverse_slippage_bps_each_side"],
            ),
        },
    )
    strategy = ImportableStrategyConfig(
        strategy_path=(
            "candidate21_event_strategy:Candidate21EventStrategy"
        ),
        config_path=(
            "candidate21_event_strategy:Candidate21EventConfig"
        ),
        config=strategy_values,
    )
    fill_model = ImportableFillModelConfig(
        fill_model_path="nautilus_trader.backtest.models:FillModel",
        config_path=(
            "nautilus_trader.backtest.config:FillModelConfig"
        ),
        config={
            "prob_fill_on_limit": 1.0,
            "prob_slippage": 1.0,
            "random_seed": int(config["execution_seed"]),
        },
    )
    latency_model = ImportableLatencyModelConfig(
        latency_model_path=(
            "nautilus_trader.backtest.models:LatencyModel"
        ),
        config_path=(
            "nautilus_trader.backtest.config:LatencyModelConfig"
        ),
        config={
            "base_latency_nanos": 100_000_000,
            "insert_latency_nanos": 150_000_000,
            "update_latency_nanos": 100_000_000,
            "cancel_latency_nanos": 100_000_000,
        },
    )
    fee_model = ImportableFeeModelConfig(
        fee_model_path=(
            "nautilus_trader.backtest.models:MakerTakerFeeModel"
        ),
        config_path=(
            "nautilus_trader.backtest.config:MakerTakerFeeModelConfig"
        ),
        config={},
    )
    venue = BacktestVenueConfig(
        name="BINANCE",
        oms_type="NETTING",
        account_type="MARGIN",
        base_currency="USDT",
        starting_balances=[
            f"{float(config['starting_nav']):.2f} USDT",
        ],
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
        "start_time": pd.Timestamp(
            build_start,
            tz="UTC",
        ).isoformat(),
        "end_time": (
            pd.Timestamp(
                build_end + timedelta(days=1),
                tz="UTC",
            )
            - pd.Timedelta(nanoseconds=1)
        ).isoformat(),
    }
    data = [
        BacktestDataConfig(
            data_cls=TradeTick,
            **common_data,
        ),
        BacktestDataConfig(
            data_cls=Bar,
            bar_spec="10-SECOND-LAST",
            **common_data,
        ),
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
            raise Candidate21EventRunnerError(
                f"expected one Nautilus result, got {len(results)}",
            )
        result = results[0]
        engines = node.get_engines()
        if len(engines) != 1:
            raise Candidate21EventRunnerError(
                f"expected one Nautilus engine, got {len(engines)}",
            )
        nt_engine = engines[0]
        orders = nt_engine.trader.generate_order_fills_report()
        positions = nt_engine.trader.generate_positions_report()
        account = nt_engine.trader.generate_account_report(
            Venue("BINANCE"),
        )
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
                "candidate": "candidate-21-event-time-10s-router",
                "alpha_parent": (
                    "lagged-phase-normalized first 10 seconds of "
                    "quarter-hour balance attack"
                ),
                "response": (
                    "immediate non-overlapping next 10-second interval"
                ),
                "execution_clock": (
                    "ACTUAL_AGGTRADE_PER_10S_AFTER_300MS"
                ),
                "bar_type": str(bar_type),
                "event_bar_count": catalog_result.bar_count,
                "execution_tick_count": (
                    catalog_result.execution_tick_count
                ),
                "event_boundary_rows": catalog_result.boundary_rows,
                "event_ready_boundary_rows": (
                    catalog_result.ready_boundary_rows
                ),
                "bar_execution": True,
                "trade_execution": True,
            },
        )
        base.write_json_atomic(output / "metrics.json", metrics)
        base.write_json_atomic(
            output / "run.json",
            base.create_run_manifest(
                run_id=(
                    f"candidate-21-event-{contract.symbol.lower()}-"
                    f"{evaluation_start}-{evaluation_end}"
                ),
                candidate="candidate-21-event-time-10s-router",
                config_path=config_path,
                data_manifest_path=manifest_path,
                extra={
                    "engine": "NautilusTrader BacktestNode",
                    "strategy": "Candidate21EventStrategy",
                    "bar_type": str(bar_type),
                    "parent_event": (
                        "quarter-hour first completed 10-second "
                        "balance attack"
                    ),
                    "response": (
                        "immediate next completed 10-second bar"
                    ),
                    "entry": (
                        "market bracket after immediate response"
                    ),
                    "execution_clock": (
                        "one actual aggTrade per 10-second bucket "
                        "after modeled latency"
                    ),
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
