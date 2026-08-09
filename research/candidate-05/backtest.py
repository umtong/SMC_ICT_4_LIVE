#!/usr/bin/env python3
"""Run Candidate 05 exclusively through NautilusTrader BacktestNode.

Data transformation is observational only. NautilusTrader owns event replay,
order matching, fills, contingent orders, fees, positions, margin, liquidation,
portfolio accounting, and NAV for every weekly and long evaluation.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from decimal import Decimal
import json
import math
from pathlib import Path
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
from nautilus_trader.model.currencies import BTC, ETH, SOL, USDT, XRP
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import BarDataWrangler

from features import load_range
from features import sha256_file
from instrument_contracts import InstrumentContract
from instrument_contracts import instrument_contract
from smc_ict_4.manifest import build_data_manifest
from smc_ict_4.manifest import create_run_manifest
from smc_ict_4.manifest import write_data_manifest
from smc_ict_4.manifest import write_json_atomic


_BASE_CURRENCIES = {
    "BTC": BTC,
    "ETH": ETH,
    "SOL": SOL,
    "XRP": XRP,
}


class RunnerError(RuntimeError):
    """Raised when a Nautilus result cannot be trusted."""


def make_instrument(
    config: dict[str, Any],
    contract: InstrumentContract,
    instrument_id: InstrumentId,
) -> CryptoPerpetual:
    cost_rate = Decimal(str(float(config["all_in_cost_bps_each_side"]) / 10_000.0))
    leverage = Decimal(str(float(config["venue_leverage"])))
    margin_init = Decimal("1") / leverage
    margin_maint = Decimal(str(float(config["maintenance_margin_rate"])))
    try:
        base_currency = _BASE_CURRENCIES[contract.base_currency_code]
    except KeyError as exc:
        raise RunnerError(
            f"no Nautilus currency registered for {contract.base_currency_code}",
        ) from exc

    return CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol(contract.symbol),
        base_currency=base_currency,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=contract.price_precision,
        price_increment=Price.from_str(contract.price_increment),
        size_precision=contract.size_precision,
        size_increment=Quantity.from_str(contract.size_increment),
        max_quantity=Quantity.from_str(contract.max_quantity),
        min_quantity=Quantity.from_str(contract.min_quantity),
        max_notional=None,
        min_notional=Money(contract.min_notional, USDT),
        max_price=Price.from_str(contract.max_price),
        min_price=Price.from_str(contract.min_price),
        margin_init=margin_init,
        margin_maint=margin_maint,
        maker_fee=cost_rate,
        taker_fee=cost_rate,
        ts_event=0,
        ts_init=0,
    )


def prepare_catalog(
    *,
    klines: pd.DataFrame,
    raw_files: list[Path],
    raw_cache: Path,
    feature_path: Path,
    catalog_path: Path,
    output: Path,
    config: dict[str, Any],
    contract: InstrumentContract,
    instrument_id: InstrumentId,
    bar_type: BarType,
    build_start: date,
    build_end: date,
) -> tuple[CryptoPerpetual, Path]:
    if catalog_path.exists():
        shutil.rmtree(catalog_path)
    catalog_path.mkdir(parents=True, exist_ok=True)

    instrument = make_instrument(config, contract, instrument_id)
    frame = klines.set_index("close_time_dt")[
        ["open", "high", "low", "close", "volume"]
    ].astype(float)
    bars = BarDataWrangler(bar_type, instrument).process(frame)
    if not bars:
        raise RunnerError("BarDataWrangler produced no bars")
    catalog = ParquetDataCatalog(catalog_path)
    catalog.write_data([instrument])
    catalog.write_data(bars)

    manifest = build_data_manifest(
        raw_cache,
        dataset=(
            f"binance-usdm-{contract.symbol.lower()}-"
            "1m-aggtrades-bookdepth"
        ),
        include=raw_files,
        metadata_values={
            "symbol": contract.symbol,
            "instrument_id": str(instrument_id),
            "bar_type": str(bar_type),
            "build_start": str(build_start),
            "build_end": str(build_end),
            "bars": len(bars),
            "timestamp_semantics": "completed Binance one-minute bar close_time",
            "feature_path": str(feature_path),
            "feature_sha256": sha256_file(feature_path),
            "feature_observation_contract": (
                "observed_time_ns <= strategy bar ts_event"
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
    write_data_manifest(manifest_path, manifest)
    return instrument, manifest_path


def as_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    method = getattr(value, "as_double", None)
    if callable(method):
        number = float(method())
        return number if math.isfinite(number) else None
    text = str(value).strip().split()[0].replace("_", "").replace(",", "")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def find_numeric_column(
    frame: pd.DataFrame,
    candidates: tuple[str, ...],
) -> str | None:
    normalized = {
        str(column).lower().replace(" ", "_"): column
        for column in frame.columns
    }
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    for normalized_name, original in normalized.items():
        if any(candidate in normalized_name for candidate in candidates):
            return original
    return None


def extract_position_pnls(positions: pd.DataFrame) -> list[float]:
    if positions.empty:
        return []
    column = find_numeric_column(
        positions,
        ("realized_pnl", "realized_return", "pnl"),
    )
    if column is None:
        return []
    values = [as_number(value) for value in positions[column].tolist()]
    return [value for value in values if value is not None]


def read_equity(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        raise RunnerError("strategy did not persist Nautilus portfolio equity")
    frame = pd.read_csv(path)
    if not {"ts_event", "equity"}.issubset(frame.columns):
        raise RunnerError(f"invalid equity schema: {list(frame.columns)}")
    frame["ts_event"] = pd.to_numeric(
        frame["ts_event"],
        errors="raise",
    ).astype("int64")
    frame["equity"] = pd.to_numeric(frame["equity"], errors="raise")
    frame["time"] = pd.to_datetime(frame["ts_event"], unit="ns", utc=True)
    return frame.sort_values("time").drop_duplicates("time", keep="last")


def equity_metrics(
    equity: pd.DataFrame,
    evaluation_start: date,
    evaluation_end: date,
    starting_nav: float,
) -> tuple[dict[str, float], float, float, float, float]:
    start_ts = pd.Timestamp(evaluation_start, tz="UTC")
    end_ts = pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC")
    selected = equity[
        (equity["time"] >= start_ts) & (equity["time"] < end_ts)
    ].copy()
    if selected.empty:
        raise RunnerError("no equity observations in evaluation range")
    selected["day"] = selected["time"].dt.date
    daily_close = selected.groupby("day", sort=True)["equity"].last()
    cursor = starting_nav
    daily_returns: dict[str, float] = {}
    for offset in range((evaluation_end - evaluation_start).days + 1):
        day = evaluation_start + timedelta(days=offset)
        close = float(daily_close.get(day, cursor))
        daily_returns[str(day)] = close / cursor - 1.0
        cursor = close
    ending_nav = float(selected["equity"].iloc[-1])
    days = (evaluation_end - evaluation_start).days + 1
    geometric_daily = (
        (ending_nav / starting_nav) ** (1.0 / days) - 1.0
        if ending_nav > 0.0
        else -1.0
    )
    values = selected["equity"].astype(float)
    peaks = values.cummax()
    max_drawdown = float((1.0 - values / peaks).max())
    min_equity = float(values.min())
    return (
        daily_returns,
        ending_nav,
        geometric_daily,
        max_drawdown,
        min_equity,
    )


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def match_scenarios(
    output: Path,
    pnls: list[float],
) -> dict[str, dict[str, float | int]]:
    closed = load_json(output / "closed_scenarios.json", [])
    branches = [str(item.get("branch", "UNKNOWN")) for item in closed]
    if len(branches) != len(pnls):
        branches = ["UNMATCHED"] * len(pnls)
    result: dict[str, dict[str, float | int]] = {}
    for branch, pnl in zip(branches, pnls):
        bucket = result.setdefault(
            branch,
            {"trades": 0, "wins": 0, "net_pnl": 0.0},
        )
        bucket["trades"] = int(bucket["trades"]) + 1
        bucket["wins"] = int(bucket["wins"]) + int(pnl > 0.0)
        bucket["net_pnl"] = float(bucket["net_pnl"]) + pnl
    return result


def count_event_reasons(path: Path) -> tuple[dict[str, int], int]:
    reasons: dict[str, int] = {}
    liquidations = 0
    if not path.exists():
        return reasons, liquidations
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        reason = str(item.get("reason_code", "UNKNOWN"))
        reasons[reason] = reasons.get(reason, 0) + 1
        if "LIQUIDAT" in json.dumps(item, sort_keys=True).upper():
            liquidations += 1
    return reasons, liquidations


def build_metrics(
    *,
    equity: pd.DataFrame,
    positions: pd.DataFrame,
    output: Path,
    evaluation_start: date,
    evaluation_end: date,
    config: dict[str, Any],
    instrument_id: InstrumentId,
    result: Any,
) -> dict[str, Any]:
    starting_nav = float(config["starting_nav"])
    (
        daily_returns,
        ending_nav,
        geometric_daily,
        max_drawdown,
        min_equity,
    ) = equity_metrics(
        equity,
        evaluation_start,
        evaluation_end,
        starting_nav,
    )
    pnls = extract_position_pnls(positions)
    wins = sum(value > 0.0 for value in pnls)
    losses = sum(value < 0.0 for value in pnls)
    gross_profit = sum(value for value in pnls if value > 0.0)
    gross_loss = -sum(value for value in pnls if value < 0.0)
    trades = len(pnls)
    win_rate = wins / trades if trades else 0.0
    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0.0
        else (math.inf if gross_profit > 0.0 else 0.0)
    )
    expectancy = sum(pnls) / trades if trades else 0.0
    largest_winner_share = (
        max((value for value in pnls if value > 0.0), default=0.0)
        / gross_profit
        if gross_profit > 0.0
        else 1.0
    )
    active_days = sum(abs(value) > 1e-12 for value in daily_returns.values())
    diagnostics = load_json(output / "strategy_diagnostics.json", {})
    event_reasons, event_liquidations = count_event_reasons(
        output / "scenario_events.jsonl",
    )
    closed = load_json(output / "closed_scenarios.json", [])
    text_liquidations = sum(
        "LIQUIDAT" in json.dumps(item).upper()
        for item in closed
    )
    liquidations = event_liquidations + text_liquidations

    gate = config["gate"]
    checks = {
        "geometric_daily_growth": (
            geometric_daily >= float(gate["min_geometric_daily_growth"])
        ),
        "trades": trades >= int(gate["min_trades"]),
        "wins": wins >= int(gate["min_wins"]),
        "win_rate": win_rate >= float(gate["min_win_rate"]),
        "active_days": active_days >= int(gate["min_active_days"]),
        "max_drawdown": max_drawdown <= float(gate["max_drawdown"]),
        "largest_winner_share": (
            largest_winner_share <= float(gate["max_largest_winner_share"])
        ),
        "positive_nav": ending_nav > 0.0 and min_equity > 0.0,
        "no_liquidation": liquidations == 0,
        "no_order_rejections": (
            int(diagnostics.get("order_rejections", 0)) == 0
        ),
        "single_entry_intent": (
            int(diagnostics.get("max_simultaneous_entry_intents", 0)) <= 1
        ),
        "single_position": (
            int(diagnostics.get("max_open_positions_observed", 0)) <= 1
        ),
        "nautilus_orders": int(result.total_orders) > 0,
        "nautilus_positions": int(result.total_positions) == trades,
    }
    return {
        "candidate": "candidate-05-liquidity-response-transition",
        "engine": "NautilusTrader BacktestNode",
        "instrument": str(instrument_id),
        "evaluation_start": str(evaluation_start),
        "evaluation_end": str(evaluation_end),
        "calendar_days": (evaluation_end - evaluation_start).days + 1,
        "starting_nav": starting_nav,
        "ending_nav": ending_nav,
        "total_return": ending_nav / starting_nav - 1.0,
        "geometric_daily_growth": geometric_daily,
        "max_drawdown": max_drawdown,
        "min_equity": min_equity,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (
            None if math.isinf(profit_factor) else profit_factor
        ),
        "expectancy_usdt": expectancy,
        "active_days": active_days,
        "largest_winner_share": largest_winner_share,
        "daily_returns": daily_returns,
        "scenario_metrics": match_scenarios(output, pnls),
        "strategy_diagnostics": diagnostics,
        "event_reason_counts": event_reasons,
        "liquidations": liquidations,
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
        raise RunnerError("evaluation must be contained in build range")
    try:
        contract = instrument_contract(str(config["symbol"]))
    except (KeyError, ValueError) as exc:
        raise RunnerError(str(exc)) from exc
    if abs(float(config["risk_fraction"]) - 0.03) > 1e-12:
        raise RunnerError("project risk fraction must remain 3%")

    instrument_id = InstrumentId.from_str(contract.instrument_id)
    bar_type = BarType.from_str(contract.bar_type)
    klines, feature_path, raw_files, _ = load_range(
        symbol=contract.symbol,
        start=build_start,
        end=build_end,
        cache=cache.resolve(),
        output=output,
    )
    catalog_path = output / "catalog"
    _, manifest_path = prepare_catalog(
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
            "all_in_cost_bps_each_side": float(
                config["all_in_cost_bps_each_side"],
            ),
            "adverse_slippage_bps_each_side": float(
                config["adverse_slippage_bps_each_side"],
            ),
        },
    )
    strategy = ImportableStrategyConfig(
        strategy_path="strategy:LiquidityResponseStrategy",
        config_path="strategy:LiquidityResponseConfig",
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
        trade_execution=False,
        liquidation_enabled=True,
        liquidation_trigger_ratio=1.0,
    )
    data = BacktestDataConfig(
        catalog_path=str(catalog_path),
        data_cls=Bar,
        instrument_id=instrument_id,
        bar_spec="1-MINUTE-LAST",
        start_time=pd.Timestamp(build_start, tz="UTC").isoformat(),
        end_time=(
            pd.Timestamp(build_end + timedelta(days=1), tz="UTC")
            - pd.Timedelta(nanoseconds=1)
        ).isoformat(),
    )
    engine = BacktestEngineConfig(
        logging=LoggingConfig(log_level="ERROR"),
        strategies=[strategy],
        run_analysis=True,
    )
    run_config = BacktestRunConfig(
        engine=engine,
        venues=[venue],
        data=[data],
        raise_exception=True,
        dispose_on_completion=False,
        start=pd.Timestamp(build_start, tz="UTC").isoformat(),
        end=(
            pd.Timestamp(build_end + timedelta(days=1), tz="UTC")
            - pd.Timedelta(nanoseconds=1)
        ).isoformat(),
    )

    node = BacktestNode(configs=[run_config])
    try:
        results = node.run()
        if len(results) != 1:
            raise RunnerError(
                f"expected one Nautilus result, got {len(results)}",
            )
        result = results[0]
        engines = node.get_engines()
        if len(engines) != 1:
            raise RunnerError(
                f"expected one Nautilus engine, got {len(engines)}",
            )
        nt_engine = engines[0]
        orders = nt_engine.trader.generate_order_fills_report()
        positions = nt_engine.trader.generate_positions_report()
        account = nt_engine.trader.generate_account_report(Venue("BINANCE"))
        orders.to_csv(output / "orders.csv", index=False)
        positions.to_csv(output / "positions.csv", index=False)
        account.to_csv(output / "account.csv", index=False)

        equity = read_equity(output / "equity.csv")
        metrics = build_metrics(
            equity=equity,
            positions=positions,
            output=output,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            config=config,
            instrument_id=instrument_id,
            result=result,
        )
        write_json_atomic(output / "metrics.json", metrics)
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=(
                    f"candidate-05-{contract.symbol.lower()}-"
                    f"{evaluation_start}-{evaluation_end}"
                ),
                candidate="candidate-05-liquidity-response-transition",
                config_path=config_path,
                data_manifest_path=manifest_path,
                extra={
                    "engine": "NautilusTrader BacktestNode",
                    "symbol": contract.symbol,
                    "instrument_id": str(instrument_id),
                    "bar_type": str(bar_type),
                    "instrument_contract_source": contract.metadata_source,
                    "price_precision": contract.price_precision,
                    "price_increment": contract.price_increment,
                    "size_precision": contract.size_precision,
                    "size_increment": contract.size_increment,
                    "min_quantity": contract.min_quantity,
                    "min_notional": contract.min_notional,
                    "build_start": str(build_start),
                    "build_end": str(build_end),
                    "evaluation_start": str(evaluation_start),
                    "evaluation_end": str(evaluation_end),
                    "feature_path": str(feature_path),
                    "feature_sha256": sha256_file(feature_path),
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
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metrics = run_backtest(
        config_path=args.config,
        build_start=date.fromisoformat(args.build_start),
        build_end=date.fromisoformat(args.build_end),
        evaluation_start=date.fromisoformat(args.evaluation_start),
        evaluation_end=date.fromisoformat(args.evaluation_end),
        cache=args.cache,
        output=args.output,
    )
    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
