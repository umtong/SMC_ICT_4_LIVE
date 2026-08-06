#!/usr/bin/env python3
"""Run candidate-04 exclusively through NautilusTrader's BacktestNode.

This script performs only data ingestion, Nautilus configuration, report export
and analytics derived from Nautilus-owned account/portfolio state. It does not
implement order matching, fills, positions, fees, margin, liquidation or PnL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import urllib.request
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
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
from nautilus_trader.model.currencies import BTC, USDT
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

from smc_ict_4.manifest import build_data_manifest
from smc_ict_4.manifest import create_run_manifest
from smc_ict_4.manifest import write_data_manifest
from smc_ict_4.manifest import write_json_atomic


BASE = "https://data.binance.vision/data/futures/um/daily/klines"
KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]
INSTRUMENT_ID = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
BAR_TYPE = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")


class RunnerError(RuntimeError):
    """Raised when a Nautilus backtest result cannot be trusted."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def download_checked(day: date, cache: Path) -> tuple[Path, Path]:
    cache.mkdir(parents=True, exist_ok=True)
    name = f"BTCUSDT-1m-{day.isoformat()}.zip"
    url = f"{BASE}/BTCUSDT/1m/{name}"
    target = cache / name
    checksum = cache / f"{name}.CHECKSUM"
    if not target.exists():
        urllib.request.urlretrieve(url, target)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(target)
    if actual != expected:
        raise RunnerError(f"checksum mismatch for {target}: {actual} != {expected}")
    return target, checksum


def read_daily_kline(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip", header=None)
    if raw.shape[1] != len(KLINE_COLUMNS):
        with_header = pd.read_csv(path, compression="zip")
        if set(KLINE_COLUMNS).issubset(with_header.columns):
            raw = with_header[KLINE_COLUMNS]
        else:
            raise RunnerError(f"unexpected kline columns in {path}: {raw.shape[1]}")
    else:
        raw.columns = KLINE_COLUMNS
        first = str(raw.iloc[0]["open_time"])
        if not first.lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()

    for column in ("open", "high", "low", "close", "volume"):
        raw[column] = pd.to_numeric(raw[column], errors="raise")

    timestamp_values = pd.to_numeric(raw["close_time"], errors="raise")
    unit = "us" if float(timestamp_values.iloc[0]) > 10**14 else "ms"
    raw.index = pd.to_datetime(timestamp_values, unit=unit, utc=True)
    frame = raw[["open", "high", "low", "close", "volume"]].astype(float)
    if frame.index.has_duplicates:
        raise RunnerError(f"duplicate bar timestamps in {path}")
    return frame.sort_index()


def load_week(
    build_start: date,
    build_end: date,
    cache: Path,
) -> tuple[pd.DataFrame, list[Path]]:
    frames: list[pd.DataFrame] = []
    evidence: list[Path] = []
    day = build_start
    while day <= build_end:
        archive, checksum = download_checked(day, cache)
        frames.append(read_daily_kline(archive))
        evidence.extend((archive, checksum))
        day += timedelta(days=1)
    result = pd.concat(frames).sort_index()
    if result.index.has_duplicates:
        raise RunnerError("duplicate timestamps across daily files")
    expected_days = (build_end - build_start).days + 1
    if len(result) < expected_days * 1_430:
        raise RunnerError(f"incomplete minute data: {len(result)} rows")
    return result, evidence


def make_instrument(all_in_cost_bps_each_side: float) -> CryptoPerpetual:
    # Fee model deliberately embeds commissions plus expected spread/slippage
    # into every fill. This is conservative for limits and consistent between
    # strategy risk sizing and Nautilus account debits.
    fee_rate = Decimal(str(all_in_cost_bps_each_side / 10_000.0))
    return CryptoPerpetual(
        instrument_id=INSTRUMENT_ID,
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=1,
        price_increment=Price.from_str("0.1"),
        size_precision=3,
        size_increment=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("1000000.000"),
        min_quantity=Quantity.from_str("0.001"),
        max_notional=None,
        min_notional=Money(10.00, USDT),
        max_price=Price.from_str("2000000.0"),
        min_price=Price.from_str("0.1"),
        margin_init=Decimal("0.0500"),
        margin_maint=Decimal("0.0250"),
        maker_fee=fee_rate,
        taker_fee=fee_rate,
        ts_event=0,
        ts_init=0,
    )


def prepare_catalog(
    frame: pd.DataFrame,
    raw_files: list[Path],
    catalog_path: Path,
    raw_cache: Path,
    output: Path,
    config: dict[str, Any],
) -> tuple[CryptoPerpetual, Path]:
    if catalog_path.exists():
        shutil.rmtree(catalog_path)
    catalog_path.mkdir(parents=True, exist_ok=True)

    instrument = make_instrument(float(config["all_in_cost_bps_each_side"]))
    bars = BarDataWrangler(BAR_TYPE, instrument).process(frame)
    if not bars:
        raise RunnerError("BarDataWrangler produced no bars")

    catalog = ParquetDataCatalog(catalog_path)
    catalog.write_data([instrument])
    catalog.write_data(bars)

    manifest = build_data_manifest(
        raw_cache,
        dataset="binance-usdm-btcusdt-1m",
        include=raw_files,
        metadata_values={
            "bar_type": str(BAR_TYPE),
            "bars": len(bars),
            "timestamp_semantics": "Binance close_time; completed bars only",
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


def find_numeric_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    normalized = {str(column).lower().replace(" ", "_"): column for column in frame.columns}
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
        (
            "realized_pnl",
            "realized_return",
            "pnl",
        ),
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
    frame["ts_event"] = pd.to_numeric(frame["ts_event"], errors="raise").astype("int64")
    frame["equity"] = pd.to_numeric(frame["equity"], errors="raise")
    frame["time"] = pd.to_datetime(frame["ts_event"], unit="ns", utc=True)
    return frame.sort_values("time").drop_duplicates("time", keep="last")


def daily_equity_metrics(
    equity: pd.DataFrame,
    evaluation_start: date,
    evaluation_end: date,
    starting_nav: float,
) -> tuple[dict[str, float], float, float, float]:
    selected = equity[
        (equity["time"] >= pd.Timestamp(evaluation_start, tz="UTC"))
        & (equity["time"] < pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC"))
    ].copy()
    if selected.empty:
        raise RunnerError("no equity observations in evaluation range")

    selected["day"] = selected["time"].dt.date
    daily_close = selected.groupby("day", sort=True)["equity"].last()
    cursor = starting_nav
    daily_returns: dict[str, float] = {}
    for day in (
        evaluation_start + timedelta(days=offset)
        for offset in range((evaluation_end - evaluation_start).days + 1)
    ):
        close = float(daily_close.get(day, cursor))
        daily_returns[str(day)] = close / cursor - 1.0
        cursor = close

    ending_nav = float(selected["equity"].iloc[-1])
    days = (evaluation_end - evaluation_start).days + 1
    geometric_daily = (ending_nav / starting_nav) ** (1.0 / days) - 1.0

    values = selected["equity"].astype(float)
    peaks = values.cummax()
    max_drawdown = float((1.0 - values / peaks).max())
    return daily_returns, ending_nav, geometric_daily, max_drawdown


def match_scenarios_to_positions(
    output: Path,
    positions: pd.DataFrame,
) -> dict[str, dict[str, float | int]]:
    event_path = output / "closed_scenarios.json"
    scenario_rows: list[dict[str, Any]] = []
    if event_path.exists():
        scenario_rows = json.loads(event_path.read_text(encoding="utf-8"))
    pnls = extract_position_pnls(positions)

    # PositionClosed events and Nautilus closed-position rows are emitted in the
    # same deterministic order. PnL values always come from Nautilus reports.
    scenario_names = [str(item.get("scenario", "UNKNOWN")) for item in scenario_rows]
    if len(scenario_names) != len(pnls):
        scenario_names = ["UNMATCHED"] * len(pnls)

    result: dict[str, dict[str, float | int]] = {}
    for scenario, pnl in zip(scenario_names, pnls):
        bucket = result.setdefault(
            scenario,
            {"trades": 0, "wins": 0, "net_pnl": 0.0},
        )
        bucket["trades"] = int(bucket["trades"]) + 1
        bucket["wins"] = int(bucket["wins"]) + int(pnl > 0.0)
        bucket["net_pnl"] = float(bucket["net_pnl"]) + pnl
    return result


def gate_metrics(
    equity: pd.DataFrame,
    positions: pd.DataFrame,
    output: Path,
    evaluation_start: date,
    evaluation_end: date,
    config: dict[str, Any],
    result: Any,
) -> dict[str, Any]:
    starting_nav = float(config["starting_nav"])
    daily_returns, ending_nav, geometric_daily, max_drawdown = daily_equity_metrics(
        equity,
        evaluation_start,
        evaluation_end,
        starting_nav,
    )
    pnls = extract_position_pnls(positions)
    wins = sum(value > 0.0 for value in pnls)
    positives = [value for value in pnls if value > 0.0]
    largest_winner_share = max(positives) / sum(positives) if positives else 1.0
    active_days = sum(abs(value) > 1e-12 for value in daily_returns.values())
    trades = len(pnls)
    win_rate = wins / trades if trades else 0.0

    checks = {
        "geometric_daily": geometric_daily >= 0.01,
        "trades": trades >= 7,
        "active_days": active_days >= 4,
        "win_rate": win_rate >= 0.55,
        "max_drawdown": max_drawdown <= 0.20,
        "largest_winner_share": largest_winner_share <= 0.55,
        "positive_nav": ending_nav > 0.0,
        "nautilus_orders": int(result.total_orders) > 0,
        "nautilus_positions": int(result.total_positions) == trades,
    }
    return {
        "engine": "NautilusTrader BacktestNode",
        "starting_nav": starting_nav,
        "ending_nav": ending_nav,
        "total_return": ending_nav / starting_nav - 1.0,
        "calendar_days": (evaluation_end - evaluation_start).days + 1,
        "geometric_daily_growth": geometric_daily,
        "max_drawdown": max_drawdown,
        "trades": trades,
        "wins": wins,
        "win_rate": win_rate,
        "active_days": active_days,
        "largest_winner_share": largest_winner_share,
        "daily_returns": daily_returns,
        "scenario_metrics": match_scenarios_to_positions(output, positions),
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


def run(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))

    build_start = date.fromisoformat(args.build_start)
    build_end = date.fromisoformat(args.build_end)
    evaluation_start = date.fromisoformat(args.evaluation_start)
    evaluation_end = date.fromisoformat(args.evaluation_end)
    if not build_start <= evaluation_start <= evaluation_end <= build_end:
        raise RunnerError("evaluation must be inside build range")

    frame, raw_files = load_week(build_start, build_end, args.cache.resolve())
    catalog_path = output / "catalog"
    _, manifest_path = prepare_catalog(
        frame,
        raw_files,
        catalog_path,
        args.cache.resolve(),
        output,
        config,
    )

    evaluation_start_ts = pd.Timestamp(evaluation_start, tz="UTC")
    evaluation_end_ts = pd.Timestamp(
        evaluation_end + timedelta(days=1),
        tz="UTC",
    ) - pd.Timedelta(nanoseconds=1)
    strategy_config = dict(config)
    strategy_config.update(
        {
            "instrument_id": str(INSTRUMENT_ID),
            "bar_type": str(BAR_TYPE),
            "output_dir": str(output),
            "evaluation_start_ns": int(evaluation_start_ts.value),
            "evaluation_end_ns": int(evaluation_end_ts.value),
        }
    )

    strategy = ImportableStrategyConfig(
        strategy_path="nt_liquidity_strategy:LiquidityTransitionStrategy",
        config_path="nt_liquidity_strategy:LiquidityTransitionConfig",
        config=strategy_config,
    )
    fill_model = ImportableFillModelConfig(
        fill_model_path="nautilus_trader.backtest.models:FillModel",
        config_path="nautilus_trader.backtest.config:FillModelConfig",
        config={
            "prob_fill_on_limit": 1.0,
            "prob_slippage": 1.0,
            "random_seed": 440404,
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
        starting_balances=[f"{int(float(config['starting_nav']))} USDT"],
        default_leverage=1.0,
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
        instrument_id=INSTRUMENT_ID,
        bar_spec="1-MINUTE-LAST",
        start_time=pd.Timestamp(build_start, tz="UTC").isoformat(),
        end_time=(
            pd.Timestamp(build_end + timedelta(days=1), tz="UTC")
            - pd.Timedelta(nanoseconds=1)
        ).isoformat(),
    )
    engine_config = BacktestEngineConfig(
        logging=LoggingConfig(log_level="ERROR"),
        strategies=[strategy],
        run_analysis=True,
    )
    run_config = BacktestRunConfig(
        engine=engine_config,
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
            raise RunnerError(f"expected one Nautilus result, got {len(results)}")
        result = results[0]
        engines = node.get_engines()
        if len(engines) != 1:
            raise RunnerError(f"expected one Nautilus engine, got {len(engines)}")
        engine = engines[0]

        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(Venue("BINANCE"))
        fills.to_csv(output / "orders.csv", index=False)
        positions.to_csv(output / "positions.csv", index=False)
        account.to_csv(output / "account.csv", index=False)

        equity = read_equity(output / "equity.csv")
        metrics = gate_metrics(
            equity,
            positions,
            output,
            evaluation_start,
            evaluation_end,
            config,
            result,
        )
        write_json_atomic(output / "metrics.json", metrics)
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=f"candidate-04-nt-{evaluation_start}-{evaluation_end}",
                candidate="candidate-04-nautilus-liquidity-transition",
                config_path=args.config,
                data_manifest_path=manifest_path,
                extra={
                    "engine": "NautilusTrader BacktestNode",
                    "instrument_id": str(INSTRUMENT_ID),
                    "bar_type": str(BAR_TYPE),
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
    metrics = run(args)
    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
